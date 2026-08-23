"""Entry point app desktop: avvia il server FastAPI in background e lo apre
in una finestra nativa con pywebview. Lo stesso backend puo' essere esposto
in rete lanciando direttamente `uvicorn backend.main:app --host 0.0.0.0`."""

import threading

import uvicorn
import webview

HOST = "127.0.0.1"
PORT = 8756


def run_server():
    uvicorn.run("backend.main:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    webview.create_window("YTGrabber", f"http://{HOST}:{PORT}", width=720, height=780, min_size=(480, 600))
    webview.start()
