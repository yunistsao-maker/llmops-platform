"""ASGI entry point for uvicorn (`request_api.main:app`). Reads the environment at import time."""
from request_api.app import create_app

app = create_app()
