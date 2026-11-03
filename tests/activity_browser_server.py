"""Test-only static-filter settings and mock vision provider, never production configuration."""
import os
from pathlib import Path

from backend.config import Settings
from backend.main import create_app
from tests.test_activity import filtered_settings
from tests.test_long_video import LongVideoMock


def create_test_app():
    directory=Path(os.environ['SHIJI_BROWSER_TEST_DIR'])
    return create_app(filtered_settings(Settings(data_dir=directory)),LongVideoMock())
