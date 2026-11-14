"""Vercel entrypoint.

Vercel's Python runtime looks for a FastAPI instance named ``app`` at a
supported entrypoint (``app.py``, ``index.py``, ``server.py``, ``main.py``,
``wsgi.py``, ``asgi.py``). This file re-exports the application defined in
``backend/main.py`` so the whole project is deployed as a single function.
"""

from backend.main import app  # noqa: F401  (re-exported for Vercel)
