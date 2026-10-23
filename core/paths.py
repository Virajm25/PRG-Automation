"""Filesystem locations. Source runs keep data next to the code; a frozen (installed) Windows build uses %LOCALAPPDATA%."""
import os, sys

FROZEN = bool(getattr(sys, "frozen", False))
CODE_ROOT = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_home():
    if os.environ.get("VERCEL"):
        return "/tmp"
    if os.environ.get("PRG_HOME"):
        return os.environ["PRG_HOME"]
    if FROZEN:
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PRG-Extractor")
    return os.path.join(CODE_ROOT, "data")


HOME = data_home()
DB_PATH = os.environ.get("PRG_DB") or os.path.join(HOME, "prg.db")
UPLOADS = os.path.join(HOME, "prg-uploads" if os.environ.get("VERCEL") else "uploads")
CACHE = os.path.join(HOME, "prg-cache" if os.environ.get("VERCEL") else "cache")


def load_env():
    """Load KEY=VALUE lines from .env (next to the code, then in the data folder) without overriding real environment variables."""
    for folder in (CODE_ROOT, HOME, os.path.dirname(sys.executable) if FROZEN else CODE_ROOT):
        f = os.path.join(folder, ".env")
        if not os.path.isfile(f):
            continue
        with open(f, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:
                    os.environ.setdefault(k.strip(), v)


load_env()
