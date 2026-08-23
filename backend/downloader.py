"""Motore di download basato su yt-dlp. Gestisce analisi URL, coda con
concorrenza limitata, cancellazione, cronologia persistente, progresso
tracciabile per job_id e auto-update di yt-dlp."""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yt_dlp

# "local" = app desktop sul PC dell'utente, output su una cartella reale.
# "web" = servizio ospitato (es. Render): output in cartelle temporanee
# per-job, servite via download HTTP e ripulite automaticamente.
MODE = os.environ.get("YTGRABBER_MODE", "local")


def is_web_mode() -> bool:
    return MODE == "web"


APP_DATA_DIR = Path.home() / ".ytgrabber" if not is_web_mode() else Path("/tmp/.ytgrabber")
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = APP_DATA_DIR / "history.json"
WEB_DOWNLOADS_DIR = APP_DATA_DIR / "web_downloads"

# cookie di sessione YouTube (formato Netscape, esportati dal browser
# dell'utente) usati per TUTTE le richieste quando presenti: risolvono sia
# i contenuti privati sia il blocco "Sign in to confirm you're not a bot"
# che YouTube applica spesso agli IP dei server cloud (Render, AWS, ecc.),
# anche su video pubblici.
COOKIES_FILE = APP_DATA_DIR / "cookies.txt"

DEFAULT_OUTPUT_DIR = str(Path.home() / "Videos" / "YTGrabber")

BUNDLED_FFMPEG_DIR = Path(__file__).resolve().parent.parent / "ffmpeg"

# quante analisi/download simultanei sono permessi. Alzalo se hai una
# connessione veloce e vuoi scaricare piu' URL della coda in parallelo.
MAX_CONCURRENT_DOWNLOADS = 2

# frammenti HLS/DASH scaricati in parallelo per singolo video: il principale
# leva di velocita' per i video ad alta risoluzione (usato se aria2c non c'e').
CONCURRENT_FRAGMENTS = 5

# quanto tenere i file scaricati in modalita' web prima di ripulirli (secondi).
WEB_FILE_RETENTION = 60 * 60  # 1 ora

_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS)

# stato dei job in corso/completati, tenuto in memoria (single-user, single-process)
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_cancel_events: dict[str, threading.Event] = {}


def ffmpeg_location() -> str | None:
    """Preferisce l'ffmpeg incluso nell'app; ripiega su quello di sistema."""
    bundled = BUNDLED_FFMPEG_DIR / "ffmpeg.exe"
    if bundled.exists():
        return str(BUNDLED_FFMPEG_DIR)
    if shutil.which("ffmpeg"):
        return None  # lascia che yt-dlp lo trovi da solo nel PATH
    return None


def ffmpeg_available() -> bool:
    return (BUNDLED_FFMPEG_DIR / "ffmpeg.exe").exists() or shutil.which("ffmpeg") is not None


def yt_dlp_version() -> str:
    return yt_dlp.version.__version__


def aria2c_available() -> bool:
    return shutil.which("aria2c") is not None


def init_cookies_from_env() -> None:
    """Se impostata, decodifica YTGRABBER_COOKIES_B64 (contenuto di un
    cookies.txt esportato dal browser, codificato in base64) e lo scrive su
    disco. Pensato per web mode: il filesystem li' e' effimero (si svuota ad
    ogni riavvio del container), quindi tenerli in una variabile d'ambiente
    e' l'unico modo che sopravvive ai riavvii automatici (es. auto-update)."""
    b64 = os.environ.get("YTGRABBER_COOKIES_B64")
    if not b64:
        return
    import base64
    try:
        COOKIES_FILE.write_bytes(base64.b64decode(b64))
    except Exception:  # noqa: BLE001 - valore malformato: ignora, non deve bloccare l'avvio
        pass


def cookies_file_present() -> bool:
    return COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0


def save_cookies_file(content: bytes) -> None:
    COOKIES_FILE.write_bytes(content)


# browser -> percorsi tipici del profilo su Windows, solo per capire se e'
# installato e proporlo nell'elenco; yt-dlp poi legge i cookie da solo.
_BROWSER_HINT_PATHS = {
    "chrome": Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/User Data",
    "edge": Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/User Data",
    "firefox": Path(os.environ.get("APPDATA", "")) / "Mozilla/Firefox/Profiles",
    "brave": Path(os.environ.get("LOCALAPPDATA", "")) / "BraveSoftware/Brave-Browser/User Data",
}


def available_cookie_browsers() -> list[str]:
    """Browser installati da cui yt-dlp puo' leggere la sessione YouTube
    dell'utente (per contenuti privati/con restrizioni a cui ha accesso)."""
    if is_web_mode():
        return []  # il server non ha il browser dell'utente
    return [name for name, path in _BROWSER_HINT_PATHS.items() if path.exists()]


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name)


def _base_opts(browser_cookies: str | None = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "retries": 10,
        "fragment_retries": 10,
        "skip_unavailable_fragments": True,
        "continuedl": True,
        "concurrent_fragment_downloads": CONCURRENT_FRAGMENTS,
    }
    loc = ffmpeg_location()
    if loc:
        opts["ffmpeg_location"] = loc

    # se aria2c e' installato lo usiamo al posto del downloader nativo:
    # multi-connessione per singolo file, generalmente piu' veloce sui
    # file grandi (video 1080p+). Del tutto opzionale, nessun errore se manca.
    if aria2c_available():
        opts["external_downloader"] = "aria2c"
        opts["external_downloader_args"] = {
            "aria2c": ["-x", "16", "-s", "16", "-k", "1M"]
        }

    # autentica come l'utente leggendo i cookie di sessione dal suo browser:
    # necessario per playlist private/"Watch Later" e video con eta' ristretta
    # a cui l'account ha accesso. Mai usato per bypassare video non suoi.
    if browser_cookies and not is_web_mode():
        opts["cookiesfrombrowser"] = (browser_cookies,)
    elif cookies_file_present():
        # file cookies.txt caricato dall'utente (o da YTGRABBER_COOKIES_B64):
        # unico modo di autenticarsi quando non c'e' un browser locale
        # (web mode) — risolve anche il blocco anti-bot di YouTube sugli IP
        # dei server cloud, che scatta pure su video pubblici.
        opts["cookiefile"] = str(COOKIES_FILE)

    return opts


def check_and_update_yt_dlp(timeout: int = 20) -> dict:
    """Controlla ed eventualmente aggiorna yt-dlp via pip. Pensata per essere
    chiamata ad ogni avvio in locale, o giornalmente in web mode. Non solleva
    mai eccezioni: un fallimento (es. niente internet) viene solo segnalato."""
    old_version = yt_dlp_version()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "yt-dlp"],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return {"ok": False, "updated": False, "old_version": old_version, "error": result.stderr[-500:]}
    except Exception as exc:  # noqa: BLE001 - nessuna connessione, timeout, ecc: non deve bloccare l'avvio
        return {"ok": False, "updated": False, "old_version": old_version, "error": str(exc)}

    # rileggi la versione effettivamente installata ora
    import importlib
    importlib.reload(yt_dlp.version)
    new_version = yt_dlp.version.__version__
    return {"ok": True, "updated": new_version != old_version, "old_version": old_version, "new_version": new_version}


def probe(url: str, browser_cookies: str | None = None) -> dict:
    """Analizza l'URL e ritorna titolo, durata, thumbnail e formati
    video/audio disponibili, ordinati dalla qualita' piu' alta alla piu' bassa."""
    ydl_opts = {**_base_opts(browser_cookies), "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info is None:
        raise ValueError("Impossibile analizzare l'URL")

    if "entries" in info:
        entries = [e for e in info["entries"] if e]
        first = entries[0] if entries else info
        is_playlist = True
        playlist_count = len(entries)
    else:
        first = info
        is_playlist = False
        playlist_count = 1

    heights = set()
    for f in first.get("formats", []):
        h = f.get("height")
        if h and f.get("vcodec") != "none":
            heights.add(h)

    video_qualities = sorted(heights, reverse=True)
    if not video_qualities:
        video_qualities = [1080, 720, 480, 360, 240]  # fallback prudente

    has_subs = bool(first.get("subtitles")) or bool(first.get("automatic_captions"))

    return {
        "title": info.get("title", "Senza titolo"),
        "duration": info.get("duration") or 0,
        "thumbnail": info.get("thumbnail"),
        "is_playlist": is_playlist,
        "playlist_count": playlist_count,
        "video_qualities": video_qualities,
        "audio_qualities": [320, 256, 192, 128],
        "has_subtitles": has_subs,
    }


def _progress_hook(job_id):
    def hook(d):
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is None:
                return
            info = d.get("info_dict") or {}
            playlist_index = info.get("playlist_index")
            playlist_count = info.get("playlist_count") or info.get("n_entries")
            if playlist_index and playlist_count:
                job["playlist_progress"] = f"{playlist_index}/{playlist_count}"

            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes", 0)
                job["percent"] = round(done / total * 100, 1) if total else job.get("percent", 0)
                job["speed"] = d.get("speed") or 0
                job["eta"] = d.get("eta") or 0
                job["status"] = "downloading"
                job["filename"] = os.path.basename(d.get("filename", ""))
            elif d["status"] == "finished":
                job["status"] = "processing"
                job["percent"] = 100

        event = _cancel_events.get(job_id)
        if event is not None and event.is_set():
            raise yt_dlp.utils.DownloadCancelled("Annullato dall'utente")
    return hook


def start_download(
    url: str,
    mode: str,
    quality,
    output_dir: str | None = None,
    embed_subs: bool = False,
    whole_playlist: bool = False,
    browser_cookies: str | None = None,
) -> str:
    """Accoda un download. Ritorna un job_id da interrogare con get_progress()."""
    job_id = uuid.uuid4().hex

    if is_web_mode():
        # in web mode l'output_dir richiesto dal client non ha senso (e' un
        # percorso sul SUO computer, non sul server): ogni job scarica in una
        # sua cartella temporanea, ripulita automaticamente dopo un po'.
        cleanup_old_web_downloads()
        output_dir = str(WEB_DOWNLOADS_DIR / job_id)
    else:
        output_dir = output_dir or DEFAULT_OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    with _jobs_lock:
        _jobs[job_id] = {
            "status": "queued",
            "percent": 0,
            "speed": 0,
            "eta": 0,
            "filename": "",
            "error": None,
            "output_dir": output_dir,
            "url": url,
            "mode": mode,
            "file_path": None,
            "playlist_progress": None,
        }
    _cancel_events[job_id] = threading.Event()

    _executor.submit(
        _run_download, job_id, url, mode, quality, output_dir, embed_subs, whole_playlist, browser_cookies
    )
    return job_id


def cancel_download(job_id: str) -> bool:
    event = _cancel_events.get(job_id)
    if event is None:
        return False
    event.set()
    return True


def _run_download(job_id, url, mode, quality, output_dir, embed_subs, whole_playlist, browser_cookies=None):
    try:
        common = _base_opts(browser_cookies)
        common["progress_hooks"] = [_progress_hook(job_id)]
        common["noplaylist"] = not whole_playlist

        if mode == "audio":
            ydl_opts = {
                **common,
                "format": "bestaudio/best",
                "outtmpl": os.path.join(output_dir, "%(title)s.%(ext)s"),
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": str(quality),
                    },
                    {"key": "EmbedThumbnail"},
                    {"key": "FFmpegMetadata", "add_metadata": True},
                ],
                "postprocessor_args": ["-ar", "44100"],
                "writethumbnail": True,
            }
        else:
            # H.264 + AAC in contenitore MP4: compatibile con iPod Touch / iOS.
            height = int(quality)
            fmt = (
                f"bestvideo[height<={height}][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
                f"/bestvideo[height<={height}]+bestaudio/best[height<={height}]"
            )
            postprocessors = [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]
            ydl_opts = {
                **common,
                "format": fmt,
                "merge_output_format": "mp4",
                "outtmpl": os.path.join(output_dir, "%(title)s.%(ext)s"),
                "postprocessors": postprocessors,
            }
            if embed_subs:
                ydl_opts["writesubtitles"] = True
                ydl_opts["writeautomaticsub"] = True
                ydl_opts["subtitleslangs"] = ["it", "en"]
                postprocessors.append({"key": "FFmpegEmbedSubtitle"})

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        title = info.get("title", "Senza titolo") if isinstance(info, dict) else ""

        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["percent"] = 100
            _jobs[job_id]["title"] = title
            if is_web_mode():
                _jobs[job_id]["file_path"] = _find_output_file(output_dir, mode)

        _notify("YTGrabber", f"Download completato: {title}")

        _append_history({
            "title": title,
            "url": url,
            "mode": mode,
            "quality": quality,
            "output_dir": output_dir,
            "timestamp": time.time(),
        })

    except yt_dlp.utils.DownloadCancelled:
        with _jobs_lock:
            _jobs[job_id]["status"] = "cancelled"
    except Exception as exc:  # noqa: BLE001 - vogliamo riportare qualunque errore al frontend
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(exc)
        _notify("YTGrabber", "Download fallito: controlla l'app per i dettagli")
    finally:
        _cancel_events.pop(job_id, None)


def _find_output_file(output_dir: str, mode: str) -> str | None:
    """In web mode ogni job ha una cartella tutta sua: individua il file
    multimediale finale (ignora eventuali sidecar come .jpg/.json rimasti)."""
    wanted_ext = ".mp3" if mode == "audio" else ".mp4"
    for name in os.listdir(output_dir):
        if name.lower().endswith(wanted_ext):
            return os.path.join(output_dir, name)
    return None


def cleanup_old_web_downloads() -> None:
    """Rimuove le cartelle dei job web piu' vecchie della retention configurata."""
    if not WEB_DOWNLOADS_DIR.exists():
        return
    cutoff = time.time() - WEB_FILE_RETENTION
    for entry in WEB_DOWNLOADS_DIR.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass


def _notify(title: str, message: str) -> None:
    try:
        from plyer import notification
        notification.notify(title=title, message=message, timeout=5)
    except Exception:  # noqa: BLE001 - le notifiche sono un extra, non devono mai far fallire il download
        pass


def get_progress(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _append_history(entry: dict) -> None:
    history = _load_history()
    history.insert(0, entry)
    history = history[:200]  # non far crescere il file all'infinito
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def get_history() -> list[dict]:
    return _load_history()


def start_daily_update_scheduler() -> None:
    """Solo per la modalita' web: ogni 24h controlla se c'e' una nuova
    versione di yt-dlp e, se la installa, riavvia il processo cosi' la nuova
    versione viene effettivamente caricata in memoria. Il supervisore del
    servizio (es. Render) rilancia il processo automaticamente."""

    def loop():
        while True:
            time.sleep(24 * 60 * 60)
            result = check_and_update_yt_dlp()
            if result.get("updated"):
                os._exit(0)  # il supervisore della piattaforma riavvia il processo

    threading.Thread(target=loop, daemon=True).start()
