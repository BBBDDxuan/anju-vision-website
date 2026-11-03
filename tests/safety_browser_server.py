"""Synthetic browser-only fixture; deliberately not a real person recognition result."""
import os
from pathlib import Path
import threading

from backend.config import Settings
from backend.main import create_app
from backend.events import RawEntity, RawEvent
from tests.test_safety import SafetyMock


class GatedSafetyMock(SafetyMock):
    def __init__(self): self.release = threading.Event()
    def extract(self, **kwargs):
        assert self.release.wait(30), 'browser fixture was not released'
        result = super().extract(**kwargs)
        if kwargs['window']['start_ms'] == 0:
            result.extraction.events[0].epistemic_type = 'inferred'
            result.extraction.events[0].uncertainty_reasons = ['模拟：跌倒过程局部被遮挡']
            result.extraction.events[0].model_score = .65
            result.extraction.entities.extend([RawEntity(id='cup',label='白色保温杯',entity_type='object'),
                                               RawEntity(id='desk',label='测试桌面',entity_type='location')])
            frames = result.extraction.events[0].frame_ids
            person = result.extraction.events[0].subject_id
            result.extraction.events.extend([
                RawEvent(subject_id=person,target_id='cup',action='pick_up',frame_ids=frames,model_score=.9),
                RawEvent(subject_id='cup',target_id='desk',action='seen_at',frame_ids=frames,model_score=.9),
            ])
        return result


def create_test_app():
    provider = GatedSafetyMock()
    app = create_app(Settings(data_dir=Path(os.environ['SHIJI_BROWSER_TEST_DIR'])), provider)
    @app.post('/test/release')
    def release():
        provider.release.set()
        return {'released': True}
    return app
