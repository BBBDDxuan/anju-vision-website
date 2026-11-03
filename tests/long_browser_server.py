"""Explicitly mocked browser test app; never used by production startup."""
import os
from pathlib import Path

from backend.config import Settings
from backend.main import create_app
from tests.test_long_video import LongVideoMock


def create_test_app():
    return create_app(Settings(data_dir=Path(os.environ['SHIJI_LONG_BROWSER_DIR']), max_calls=2), LongVideoMock())
