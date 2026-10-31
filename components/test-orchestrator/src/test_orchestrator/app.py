"""uvicorn target: test_orchestrator.app:app (configuration from the environment)."""
from .api import create_app

app = create_app()
