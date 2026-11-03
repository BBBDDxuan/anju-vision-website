"""Review gate is tested without a camera, GPU or network."""
import importlib.util
from pathlib import Path
import pytest

script=Path(__file__).resolve().parents[2]/'scripts/web-camera-worker.py'
spec=importlib.util.spec_from_file_location('review_gate_worker',script)
worker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

class Alerts:
    def __init__(self): self.sent=[]
    def alarm(self, signal, when):
        self.sent.append((signal,when));return 'test-event'

def item():
    return {'id':'sc_'+'a'*32,'event_type':'help_requested','track_id':'p1','confidence':.9,
            'occurred_at':'2026-10-31T00:00:00+00:00'}

@pytest.mark.parametrize('verdict',['pending','rejected','uncertain','unavailable','invented'])
def test_nonconfirmation_never_notifies(verdict):
    alerts=Alerts();entry={}
    worker.apply_review_result(item(),entry,dict(candidate_id=item()['id'],verdict=verdict),alerts)
    assert alerts.sent==[]

@pytest.mark.parametrize('extra',[{'candidate_id':'wrong'}, {'cloud_images':False}, {'model':None}])
def test_mismatched_or_noncloud_confirmation_is_not_an_alarm(extra):
    result=dict(candidate_id=item()['id'],verdict='confirmed',cloud_images=True,model='test-review-model')
    result.update(extra);alerts=Alerts();entry={}
    worker.apply_review_result(item(),entry,result,alerts)
    assert entry['review_status']=='unavailable' and not alerts.sent

def test_reviewed_candidate_enters_notification_outbox_once():
    alerts=Alerts();entry={}
    result=dict(candidate_id=item()['id'],verdict='confirmed',cloud_images=True,model='test-review-model',reason='test')
    worker.apply_review_result(item(),entry,result,alerts)
    assert len(alerts.sent)==1 and entry['event_id']=='test-event'
    assert alerts.sent[0][0].details['review_status']=='confirmed'
