"""Explicit synthetic review server for browser testing; never a product entrypoint."""
import os
from pathlib import Path

from backend.config import Settings
from backend.main import create_app
from tests.test_context import ContextMock


def create_test_app():
    return create_app(Settings(data_dir=Path(os.environ['SHIJI_BROWSER_TEST_DIR'])), ContextMock())
