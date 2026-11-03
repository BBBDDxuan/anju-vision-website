"""Test-only server factory. Never use this as the product launcher."""
import os
from pathlib import Path

from backend.config import Settings
from backend.main import create_app
from tests.conftest import MockVisionProvider


def create_test_app():
    return create_app(Settings(data_dir=Path(os.environ["SHIJI_BROWSER_TEST_DIR"])), MockVisionProvider())
