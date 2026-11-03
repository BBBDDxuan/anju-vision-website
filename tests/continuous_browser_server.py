"""Isolated synthetic browser fixture. Never used by the production application."""
import os
from pathlib import Path
import threading
import time

from backend.config import Settings
from backend.main import create_app
from tests.test_long_video import LongVideoMock


class GatedMock(LongVideoMock):
    def __init__(self):
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()

    def extract(self, **kwargs):
        self.entered.set()
        if not self.release.wait(30):
            raise RuntimeError('Browser fixture was not released')
        time.sleep(.5)
        return super().extract(**kwargs)


def create_test_app():
    provider = GatedMock()
    app = create_app(Settings(data_dir=Path(os.environ['SHIJI_BROWSER_TEST_DIR']), max_calls=1), provider)

    @app.get('/test/state')
    def state():
        return {'entered': provider.entered.is_set(), 'windows': provider.windows}

    @app.post('/test/release')
    def release():
        provider.release.set()
        return {'released': True}

    return app
