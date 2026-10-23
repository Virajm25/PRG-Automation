from app import app as flask_app

# Vercel discovers the Flask WSGI application from this module.
app = flask_app
