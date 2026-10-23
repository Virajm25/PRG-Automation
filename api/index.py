"""Vercel entry point.

vercel.json rewrites every URL to this function as /api/index?__path=<original path>. The Python runtime hands the app the
rewritten path, so the original one is restored here before Flask routes the request."""
from urllib.parse import parse_qsl, urlencode
from app import app as flask_app

PATH_PARAM = "__path"


def app(environ, start_response):
    query = parse_qsl(environ.get("QUERY_STRING", ""), keep_blank_values=True)
    original = [v for k, v in query if k == PATH_PARAM]
    if original:
        environ["PATH_INFO"] = "/" + original[0].lstrip("/")
        environ["QUERY_STRING"] = urlencode([(k, v) for k, v in query if k != PATH_PARAM])
    return flask_app(environ, start_response)
