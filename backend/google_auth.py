"""Login "Accedi con Google" per sfogliare le playlist dell'account
dell'utente (solo lettura) e riempire l'URL da passare a yt-dlp — l'accesso
al contenuto video vero e proprio resta gestito da yt-dlp (eventualmente via
cookie del browser, vedi downloader.available_cookie_browsers)."""

import threading
from pathlib import Path

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from . import downloader

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]

CLIENT_SECRET_FILE = Path(__file__).resolve().parent.parent / "google_client_secret.json"
TOKEN_FILE = downloader.APP_DATA_DIR / "google_token.json"

_connect_lock = threading.Lock()
_connect_state = {"in_progress": False, "error": None}


def is_configured() -> bool:
    """True se l'utente ha completato il setup su Google Cloud Console
    e messo il file google_client_secret.json nella cartella dell'app."""
    return CLIENT_SECRET_FILE.exists()


def _load_credentials() -> Credentials | None:
    if not TOKEN_FILE.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleRequest())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        except Exception:  # noqa: BLE001 - token non piu' valido, serve ri-collegare
            return None
    return creds


def is_connected() -> bool:
    creds = _load_credentials()
    return bool(creds and creds.valid)


def connect_status() -> dict:
    with _connect_lock:
        return dict(_connect_state)


def start_connect() -> None:
    """Avvia il flusso OAuth in background: apre il browser di sistema per
    il consenso Google e attende il redirect su un server locale temporaneo."""
    if downloader.is_web_mode():
        raise RuntimeError("il login Google via browser locale e' disponibile solo in modalita' locale")
    if not is_configured():
        raise RuntimeError("google_client_secret.json mancante: completa prima il setup su Google Cloud Console")

    with _connect_lock:
        if _connect_state["in_progress"]:
            return
        _connect_state["in_progress"] = True
        _connect_state["error"] = None

    def worker():
        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            with _connect_lock:
                _connect_state["error"] = str(exc)
        finally:
            with _connect_lock:
                _connect_state["in_progress"] = False

    threading.Thread(target=worker, daemon=True).start()


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


def list_my_playlists() -> list[dict]:
    creds = _load_credentials()
    if not creds or not creds.valid:
        raise RuntimeError("account Google non collegato")

    youtube = build("youtube", "v3", credentials=creds)
    playlists = []
    page_token = None
    while True:
        resp = youtube.playlists().list(
            part="snippet,contentDetails", mine=True, maxResults=50, pageToken=page_token
        ).execute()
        for item in resp.get("items", []):
            playlists.append({
                "id": item["id"],
                "title": item["snippet"]["title"],
                "thumbnail": (item["snippet"].get("thumbnails", {}).get("default") or {}).get("url"),
                "item_count": item["contentDetails"]["itemCount"],
                "url": f"https://www.youtube.com/playlist?list={item['id']}",
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return playlists
