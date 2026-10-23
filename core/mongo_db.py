"""MongoDB Atlas connection helpers for Vercel/serverless deployments.

The MongoClient is cached because a Vercel instance may handle multiple
requests. Do not put credentials in source; configure MONGODB_URI in Vercel.
"""
import os
from functools import lru_cache
from pymongo import MongoClient

COLLECTIONS = (
    "projects", "documents", "sheets", "io_rows", "circuits", "devices",
    "wires", "terminals", "cables", "xrefs", "modules", "panel_specs",
    "corrections", "rules", "settings",
)

@lru_cache(maxsize=1)
def client():
    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        return None
    return MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000,
                       maxPoolSize=10, retryWrites=True)

def database():
    c = client()
    if c is None:
        return None
    return c[os.getenv("MONGODB_DATABASE", "prg_automation")]

def enabled():
    return bool(os.getenv("MONGODB_URI", "").strip())

def ping():
    db = database()
    if db is None:
        return {"enabled": False, "ok": False, "message": "MONGODB_URI is not configured"}
    try:
        db.command("ping")
        return {"enabled": True, "ok": True, "database": db.name}
    except Exception as exc:
        return {"enabled": True, "ok": False, "message": str(exc)}

def collection(name):
    if name not in COLLECTIONS:
        raise ValueError(f"Unknown collection: {name}")
    db = database()
    if db is None:
        raise RuntimeError("MONGODB_URI is not configured")
    return db[name]
