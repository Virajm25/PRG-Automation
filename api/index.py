"""Vercel entry point: the Flask app. The rewrite in vercel.json is undone inside the app (see _RestoreRewrittenPath in app.py),
so it works whichever entry Vercel loads."""
from app import app  # noqa: F401
