"""Entry point for `flask run` and WSGI servers."""

from app import create_app

app = create_app()
