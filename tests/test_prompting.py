"""Prompt traceability tests; no real API calls."""
from dataclasses import replace

from fastapi.testclient import TestClient
import httpx

from backend.db import Database
from backend.config import ROOT
from backend.main import create_app
from backend.prompting import EventPrompt, load_event_prompt
from backend.providers.qwen import QwenProvider
from tests.conftest import wait_job
from tests.test_long_video import LongVideoMock
from tests.test_provider import arguments, configured, response


def test_home_prompt_is_sent_and_fingerprinted(settings,tmp_path):
    captured=[]
    def handler(request):
        captured.append(request.content.decode())
        return httpx.Response(200,json=response())
    spec=load_event_prompt(ROOT/'prompts/candidates/home_camera_v0.5.md')
    provider=QwenProvider(configured(settings),Database(settings.data_dir),httpx.MockTransport(handler),event_prompt=spec)
    result=provider.extract(**arguments(tmp_path))
    assert '居家固定摄像头' in captured[0] and '分清平面与容器' in captured[0]
    assert result.usage['prompt_version']==spec.version=='event-v0.5'
    assert result.usage['prompt_sha256']==spec.sha256 and len(spec.sha256)==64


def test_prompt_change_cannot_silently_resume_checkpoint(settings,sample_video,monkeypatch):
    settings=replace(settings,max_calls=1)
    with TestClient(create_app(settings,LongVideoMock()),base_url='http://localhost') as client:
        vid=client.post('/api/videos',files={'file':('test.mp4',sample_video.read_bytes())}).json()['video_id']
        job=wait_job(client,client.post(f'/api/videos/{vid}/analyze?auto_continue=false').json()['id'])
        assert job['status']=='paused'
    old=load_event_prompt()
    monkeypatch.setattr('backend.jobs.load_event_prompt',lambda:EventPrompt(old.text+'\nchanged',old.version,'0'*64))
    with TestClient(create_app(settings,LongVideoMock()),base_url='http://localhost') as client:
        answer=client.post(f'/api/videos/{vid}/resume')
        assert answer.status_code==409 and answer.json()['error']['code']=='CHECKPOINT_CONFIG'


def test_actual_memory_records_current_prompt(client,analyzed):
    _,doc=analyzed
    assert doc['model_run']['prompt_version']==load_event_prompt().version
    assert doc['job']['prompt_sha256']==load_event_prompt().sha256
