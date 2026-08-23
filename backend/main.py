"""API FastAPI: espone analisi URL, coda download, progresso, cronologia
e auto-update di yt-dlp. Pensata per girare sia dietro pywebview (app
desktop, MODE=local) sia esposta in rete (MODE=web, es. Render) senza
modifiche al codice — solo la variabile d'ambiente YTGRABBER_MODE cambia."""

import os
import time
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import downloader, google_auth

app = FastAPI(title="YTGrabber")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# se impostata (solo rilevante in web mode), le richieste che modificano
# stato devono includere l'header X-API-Key con questo valore.
API_KEY = os.environ.get("YTGRABBER_API_KEY")

# rate limiting minimo per uso pubblico: N richieste per IP per finestra.
_rate_buckets: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_DOWNLOAD = 8   # download avviati per IP per ora
RATE_LIMIT_PROBE = 30     # analisi URL per IP per ora
RATE_WINDOW = 60 * 60


def _check_rate_limit(request: Request, bucket: str, limit: int) -> None:
    if not downloader.is_web_mode():
        return
    ip = request.client.host if request.client else "unknown"
    key = f"{bucket}:{ip}"
    now = time.time()
    hits = [t for t in _rate_buckets[key] if now - t < RATE_WINDOW]
    if len(hits) >= limit:
        raise HTTPException(status_code=429, detail="Troppe richieste, riprova piu' tardi")
    hits.append(now)
    _rate_buckets[key] = hits


def _check_auth(x_api_key: str | None) -> None:
    if downloader.is_web_mode() and API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="API key mancante o non valida")


@app.on_event("startup")
def on_startup():
    if downloader.is_web_mode():
        downloader.cleanup_old_web_downloads()
        downloader.check_and_update_yt_dlp()  # controllo immediato all'avvio del container
        downloader.start_daily_update_scheduler()
    else:
        downloader.check_and_update_yt_dlp()  # ad ogni avvio locale, silenzioso e non bloccante a lungo


class ProbeRequest(BaseModel):
    url: str
    browser_cookies: str | None = None


class DownloadRequest(BaseModel):
    url: str
    mode: str  # "video" | "audio"
    quality: int
    output_dir: str | None = None
    embed_subs: bool = False
    whole_playlist: bool = False
    browser_cookies: str | None = None


@app.get("/api/status")
def status():
    return {
        "mode": downloader.MODE,
        "ffmpeg_available": downloader.ffmpeg_available(),
        "aria2c_available": downloader.aria2c_available(),
        "default_output_dir": downloader.DEFAULT_OUTPUT_DIR,
        "yt_dlp_version": downloader.yt_dlp_version(),
        "auth_required": downloader.is_web_mode() and bool(API_KEY),
        "cookie_browsers": downloader.available_cookie_browsers(),
    }


@app.post("/api/probe")
def probe(req: ProbeRequest, request: Request, x_api_key: str | None = Header(default=None)):
    _check_auth(x_api_key)
    _check_rate_limit(request, "probe", RATE_LIMIT_PROBE)
    try:
        return downloader.probe(req.url, req.browser_cookies)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/download")
def download(req: DownloadRequest, request: Request, x_api_key: str | None = Header(default=None)):
    _check_auth(x_api_key)
    _check_rate_limit(request, "download", RATE_LIMIT_DOWNLOAD)
    if req.mode not in ("video", "audio"):
        raise HTTPException(status_code=400, detail="mode deve essere 'video' o 'audio'")
    job_id = downloader.start_download(
        req.url, req.mode, req.quality, req.output_dir, req.embed_subs, req.whole_playlist, req.browser_cookies
    )
    return {"job_id": job_id}


@app.post("/api/cancel/{job_id}")
def cancel(job_id: str, x_api_key: str | None = Header(default=None)):
    _check_auth(x_api_key)
    ok = downloader.cancel_download(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="job non trovato o gia' concluso")
    return {"cancelled": True}


@app.get("/api/progress/{job_id}")
def progress(job_id: str):
    job = downloader.get_progress(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job non trovato")
    return job


@app.get("/api/file/{job_id}")
def get_file(job_id: str):
    """Solo web mode: scarica il file risultante di un job completato."""
    job = downloader.get_progress(job_id)
    if job is None or job.get("status") != "done" or not job.get("file_path"):
        raise HTTPException(status_code=404, detail="file non disponibile")
    path = job["file_path"]
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file non piu' disponibile (scaduto)")
    return FileResponse(path, filename=os.path.basename(path))


@app.get("/api/history")
def history():
    return downloader.get_history()


class OpenFolderRequest(BaseModel):
    path: str


@app.post("/api/open-folder")
def open_folder(req: OpenFolderRequest):
    if downloader.is_web_mode():
        raise HTTPException(status_code=400, detail="non disponibile in modalita' web")
    if not os.path.isdir(req.path):
        raise HTTPException(status_code=404, detail="cartella non trovata")
    os.startfile(req.path)  # noqa: S606 - apre solo la cartella di destinazione dei download dell'utente
    return {"opened": True}


@app.get("/api/google/status")
def google_status():
    connect_state = google_auth.connect_status()
    return {
        "configured": google_auth.is_configured(),
        "connected": google_auth.is_connected(),
        "connecting": connect_state["in_progress"],
        "error": connect_state["error"],
    }


@app.post("/api/google/connect")
def google_connect():
    try:
        google_auth.start_connect()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"started": True}


@app.post("/api/google/disconnect")
def google_disconnect():
    google_auth.disconnect()
    return {"disconnected": True}


@app.get("/api/google/playlists")
def google_playlists():
    try:
        return google_auth.list_my_playlists()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/update-yt-dlp")
def update_yt_dlp(x_api_key: str | None = Header(default=None)):
    _check_auth(x_api_key)
    result = downloader.check_and_update_yt_dlp(timeout=60)
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result.get("error", "aggiornamento fallito"))
    return result


# file statici del frontend (index.html, css, js)
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
