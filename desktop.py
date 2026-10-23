"""Native Windows window (needs pywebview). Falls back to the default browser."""
import os, sys, socket, threading, webbrowser, time
from app import app

PORT = int(os.environ.get("PORT", 5057))


def _free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def serve():
    app.run(host="127.0.0.1", port=PORT, threaded=True)


if __name__ == "__main__":
    if _free(PORT):
        threading.Thread(target=serve, daemon=True).start()
        time.sleep(1.2)
    url = f"http://127.0.0.1:{PORT}"
    try:
        import webview
        webview.create_window("PRG Drawing Data Extractor", url, width=1500, height=900)
        webview.start()
    except ImportError:
        webbrowser.open(url)
        if not getattr(sys, "frozen", False):
            input("Running - press Enter to quit\n")
        else:
            while True:
                time.sleep(3600)
