"""Explicit mock-only confidence fixtures; not a production provider."""
import os
from pathlib import Path

from backend.config import Settings
from backend.events import Extraction
from backend.main import create_app
from backend.providers.base import ProviderResult


class ConfidenceMock:
    name, model, is_mock = 'TEST_MOCK', 'SYNTHETIC_CONFIDENCE_NOT_AI', True

    def ready(self):
        return None

    def extract(self, *,window,entities,**kwargs):
        first=window['start_ms']==0
        ids={e['label']:e['id'] for e in entities}
        subject,target=('item','table') if first else (ids['测试钥匙'],ids['测试桌面'])
        frames={f['pts_ms']:f['id'] for f in window['frames']}

        def record(time,score,*,action='seen_at',kind='observed',end=None,reason=None,object_id=None):
            return {'subject_id':object_id or subject,'target_id':target,'action':action,'epistemic_type':kind,
                    'frame_ids':[frames[t] for t in sorted({time,end if end is not None else time})],
                    'model_score':score,'uncertainty_reasons':[reason] if reason else []}

        if first:
            data={'entities':[{'id':'item','label':'测试钥匙'},{'id':'table','label':'测试桌面'}],
                  'events':[record(0,.95),record(1000,.95),record(2000,.7),record(3000,.1),record(4000,.95),
                            record(4000,.99,action='move',kind='inferred',end=5000,reason='依据前后画面推测移动，手部遮挡了部分过程。'),
                            record(6000,None)]}
        else:
            data={'entities':[{'id':'low_only','label':'TEST_LOW_ONLY'}],
                  'events':[record(7000,.95),record(8000,1,action='unknown'),record(10000,.1,object_id='low_only'),record(11000,.1)]}
        return ProviderResult(Extraction.model_validate(data),{'test_mock':True})


def create_test_app():
    return create_app(Settings(data_dir=Path(os.environ['SHIJI_BROWSER_TEST_DIR'])),ConfidenceMock())
