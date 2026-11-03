"""Isolated environmental mock; generated video contains no actual fire or smoke."""
import os
from pathlib import Path
import threading

from backend.config import Settings
from backend.main import create_app
from tests.test_environment_hazards import EnvironmentMock


class GatedEnvironmentMock(EnvironmentMock):
    def __init__(self): self.release = threading.Event()
    def extract(self, **kwargs):
        assert self.release.wait(30), 'browser fixture was not released'
        return super().extract(**kwargs)


def create_test_app():
    provider=GatedEnvironmentMock()
    app=create_app(Settings(data_dir=Path(os.environ['SHIJI_BROWSER_TEST_DIR'])),provider)
    @app.post('/test/release')
    def release():
        provider.release.set()
        return {'released':True}
    return app
