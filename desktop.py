"""Native Windows window (needs pywebview). Falls back to the default browser."""
import threading, webbrowser, time
from app import app
def serve(): app.run(host="127.0.0.1", port=5057, threaded=True)
if __name__ == "__main__":
    threading.Thread(target=serve, daemon=True).start(); time.sleep(1.2)
    try:
        import webview
        webview.create_window("MS-PRG Drawing Data Extractor", "http://127.0.0.1:5057", width=1500, height=900); webview.start()
    except ImportError:
        webbrowser.open("http://127.0.0.1:5057"); input("Running - press Enter to quit\n")
