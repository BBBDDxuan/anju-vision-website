"""Isolated synthetic grouping browser server; not a production model adapter."""
import os
from pathlib import Path

from backend.config import Settings
from backend.events import Extraction
from backend.main import create_app
from backend.providers.base import ProviderResult


class GroupingMock:
    name, model, is_mock = 'TEST_MOCK', 'SYNTHETIC_GROUPING_NOT_AI', True

    def ready(self):
        return None

    def extract(self, *, window, entities, **kwargs):
        ids = {e['label']: e['id'] for e in entities}
        frames = {f['pts_ms']: f['id'] for f in window['frames']}
        first = window['start_ms'] == 0
        subject, target = ('item', 'table') if first else (ids['测试钥匙'], ids['测试桌面'])

        def record(action, *times):
            return {'subject_id':subject, 'target_id':target, 'action':action, 'frame_ids':[frames[t] for t in times]}

        data = {'entities': [{'id':'item','label':'测试钥匙'}, {'id':'table','label':'测试桌面'}] if first else [],
                'events': [record('seen_at',0), record('seen_at',2000), record('pick_up',4000,5000),
                           record('pick_up',4000,5000), record('seen_at',6000)] if first else
                          [record('seen_at',6000), record('unknown',8000), record('seen_at',9000), record('seen_at',11000)]}
        return ProviderResult(Extraction.model_validate(data), {'test_mock':True})


def create_test_app():
    return create_app(Settings(data_dir=Path(os.environ['SHIJI_BROWSER_TEST_DIR'])), GroupingMock())
