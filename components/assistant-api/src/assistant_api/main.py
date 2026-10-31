"""ASGI entry point for uvicorn (`assistant_api.main:app`); settings come from the environment."""
from .app import create_app

app = create_app()
