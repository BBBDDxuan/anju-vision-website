"""Synthetic screening clips and mock semantic outputs only; no paid API calls."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.activity import ActivityGate
from backend.errors import AppError
from backend.main import create_app
from tests.conftest import wait_job
from tests.test_long_video import LongVideoMock


def frames(tmp_path, *images, start=0):
    records=[]
    for i,image in enumerate(images):
        path=tmp_path/f'screen-{start+i}.jpg'
        ok,encoded=cv2.imencode('.jpg',image);assert ok
        path.write_bytes(encoded.tobytes())
        records.append({'pts_ms':start+i*1000,'path':str(path)})
    return records


def initial_gate(settings,tmp_path):
    gate=ActivityGate(settings)
    still=np.full((180,320,3),40,np.uint8)
    assert gate.evaluate(frames(tmp_path,still,still),0,2000)['reason']=='initial_review'
    gate.analyzed(0,2000,[],{})
    return gate,still


def test_static_periodic_review_and_checkpoint_roundtrip(settings,tmp_path):
    gate,still=initial_gate(settings,tmp_path)
    assert gate.evaluate(frames(tmp_path,still,still,start=2000),2000,4000)['skip']
    restored=ActivityGate(settings,gate.state())
    assert restored.evaluate(frames(tmp_path,still,still,start=4000),4000,6000)['skip']
    assert restored.evaluate(frames(tmp_path,still,still,start=300000),300000,302000)['reason']=='periodic_review'


def test_persistent_object_change_between_windows_and_activity_tail(settings,tmp_path):
    gate,still=initial_gate(settings,tmp_path)
    moved=still.copy();moved[70:100,100:130]=(180,50,40)
    decision=gate.evaluate(frames(tmp_path,moved,moved,start=2000),2000,4000)
    assert decision['reason']=='frame_change' and decision['max_change_ratio']>=settings.activity_filter_change_ratio
    gate.analyzed(2000,4000,[],{})
    assert gate.evaluate(frames(tmp_path,moved,moved,start=4000),4000,6000)['reason']=='activity_followup'
    assert gate.evaluate(frames(tmp_path,moved,moved,start=36000),36000,38000)['skip']


def test_slow_and_transient_changes_are_not_only_compared_with_the_last_frame(settings,tmp_path):
    gate,still=initial_gate(settings,tmp_path)
    gradual=[np.full_like(still,40+step*8) for step in range(4)]
    assert gate.evaluate(frames(tmp_path,*gradual,start=2000),2000,6000)['reason']=='frame_change'
    gate,still=initial_gate(settings,tmp_path)
    changed=still.copy();changed[70:110,100:140]=220
    assert gate.evaluate(frames(tmp_path,still,changed,still,start=2000),2000,5000)['reason']=='frame_change'


def test_screening_requires_multiple_samples(settings,tmp_path):
    gate,still=initial_gate(settings,tmp_path)
    assert not gate.evaluate(frames(tmp_path,still,start=2000),2000,3000)['skip']
    with pytest.raises((OSError,ValueError)):
        gate.evaluate([{'path':str(tmp_path/'missing.jpg'),'pts_ms':2000},{'path':str(tmp_path/'missing.jpg'),'pts_ms':3000}],2000,4000)
    (tmp_path/'empty.jpg').write_bytes(b'')
    with pytest.raises(ValueError):
        gate.evaluate([{'path':str(tmp_path/'empty.jpg'),'pts_ms':2000},{'path':str(tmp_path/'empty.jpg'),'pts_ms':3000}],2000,4000)


@pytest.mark.parametrize('action',['fall','lying_on_floor','help_gesture','smoke','flame','sparks'])
def test_static_risk_candidates_keep_semantic_analysis_on(settings,tmp_path,action):
    gate,still=initial_gate(settings,tmp_path)
    gate.analyzed(0,2000,[{'action':action,'subject_id':'p','end_ms':2000,'epistemic_type':'inferred','model_score':.8}],{})
    assert gate.evaluate(frames(tmp_path,still,still,start=2000),2000,4000)['reason']=='risk_followup'
    assert ActivityGate(settings,gate.state()).risk_hold


def test_person_context_shortens_periodic_review_and_explicit_negative_clears_it(settings,tmp_path):
    gate,still=initial_gate(settings,tmp_path)
    entities={'p':{'entity_type':'person'},'scene':{'entity_type':'location'}}
    gate.analyzed(0,2000,[{'action':'seen_at','subject_id':'p','end_ms':2000,'epistemic_type':'observed','model_score':.9}],entities)
    assert gate.evaluate(frames(tmp_path,still,still,start=60000),60000,62000)['reason']=='person_periodic_review'
    gate.analyzed(60000,62000,[{'action':'no_person_visible','subject_id':'scene','end_ms':62000,
        'epistemic_type':'observed','model_score':.9,'uncertainty_reasons':[]}],entities)
    assert not gate.person_present
    assert gate.evaluate(frames(tmp_path,still,still,start=120000),120000,122000)['reason']=='absence_followup'
    gate.analyzed(360000,362000,[{'action':'no_person_visible','subject_id':'scene','end_ms':362000,
        'epistemic_type':'observed','model_score':.9,'uncertainty_reasons':[]}],entities)
    assert gate.evaluate(frames(tmp_path,still,still,start=364000),364000,366000)['skip']


@pytest.fixture
def still_video(tmp_path):
    path=tmp_path/'SYNTHETIC_STATIC_FILTER_TEST.mp4'
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),10,(160,96));assert writer.isOpened()
    image=np.full((96,160,3),60,np.uint8)
    cv2.putText(image,'MOCK ONLY',(8,30),cv2.FONT_HERSHEY_SIMPLEX,.4,(200,200,200),1)
    for _ in range(120):writer.write(image)
    writer.release()
    return path


def filtered_settings(settings,**kwargs):
    return replace(settings,long_video_threshold_seconds=1,long_window_seconds=2,long_window_stride_seconds=2,
        long_sampling_fps=1,activity_filter_review_seconds=6,activity_filter_person_review_seconds=4,
        activity_filter_postroll_seconds=0,**kwargs)


def upload(client,path):
    response=client.post('/api/videos/upload',content=path.read_bytes())
    assert response.status_code==201,response.text
    return response.json()['video_id']


def test_static_filter_end_to_end_counts_and_honest_coverage(settings,still_video):
    provider=LongVideoMock();settings=filtered_settings(settings)
    with TestClient(create_app(settings,provider),base_url='http://localhost') as client:
        vid=upload(client,still_video)
        assert client.get(f'/api/videos/{vid}/analysis-plan').json()['estimated_requests_is_upper_bound']
        job=wait_job(client,client.post(f'/api/videos/{vid}/analyze').json()['id'])
        assert job['status']=='completed' and job['completed_windows']==6 and job['calls']==2
        assert job['activity_filter']['skipped_windows']==4 and job['activity_filter']['model_analyzed_windows']==2
        assert provider.windows==[0,6000]
        assert job['analyzed_ranges']==[{'start_ms':0,'end_ms':2000},{'start_ms':6000,'end_ms':8000}]
        memory=client.get(f'/api/videos/{vid}/events?grouped=true').json()
        assert len(memory['events'])==2 and len(memory['event_groups'])==2
        assert all(f['pts_ms'] in {0,1000,6000,7000} for f in memory['frames'])
        assert not list((settings.data_dir/'runs'/job['id']/'activity-frames').glob('*.jpg'))
        result=client.post(f'/api/videos/{vid}/query',json={'question':'视频发生了什么？'}).json()
        assert '未调用视觉模型' in result['coverage_note'] and result['status']=='partial'
        early=client.post(f'/api/videos/{vid}/query',json={'question':'视频发生了什么？','as_of_ms':1000}).json()
        assert '未调用视觉模型' not in early['coverage_note']
        entity=memory['events'][0]['subject_id']
        replay=client.get(f'/api/videos/{vid}/replay',params={'entity_id':entity}).json()
        assert any('静止时段' in text for text in replay['unknowns'])
        assert client.get('/api/health').json()['usage']['requests']==0


def test_static_filter_resume_keeps_decisions_and_rejects_changed_settings(settings,still_video):
    provider=LongVideoMock();settings=filtered_settings(settings,max_calls=1)
    with TestClient(create_app(settings,provider),base_url='http://localhost') as client:
        vid=upload(client,still_video)
        paused=wait_job(client,client.post(f'/api/videos/{vid}/analyze?auto_continue=false').json()['id'])
        assert paused['status']=='paused' and paused['completed_windows']==3 and paused['calls']==1
    with TestClient(create_app(replace(settings,activity_filter_change_ratio=.01),provider),base_url='http://localhost') as client:
        assert client.post(f'/api/videos/{vid}/resume').json()['error']['code']=='CHECKPOINT_CONFIG'
    with TestClient(create_app(settings,provider),base_url='http://localhost') as client:
        done=wait_job(client,client.post(f'/api/videos/{vid}/resume').json()['id'])
        assert done['status']=='completed' and done['calls']==2 and provider.windows==[0,6000]
        assert done['activity_filter']['skipped_windows']==4


def test_screening_error_falls_back_to_model_and_can_be_disabled(settings,still_video):
    for enabled in [True,False]:
        provider=LongVideoMock()
        with patch('backend.jobs.ActivityGate.evaluate',side_effect=ValueError('SYNTHETIC screening failure')):
            with TestClient(create_app(filtered_settings(settings,activity_filter_enabled=enabled),provider),base_url='http://localhost') as client:
                vid=upload(client,still_video)
                job=wait_job(client,client.post(f'/api/videos/{vid}/analyze').json()['id'])
                assert job['status']=='completed' and job['calls']==6
                assert not job.get('skipped_static_ranges')


def test_static_filter_does_not_bypass_daily_limit(settings,still_video):
    provider=LongVideoMock();provider.is_mock=False  # Metered job path, still no network/provider API.
    settings=filtered_settings(settings,max_daily_calls=1)
    with TestClient(create_app(settings,provider),base_url='http://localhost') as client:
        vid=upload(client,still_video)
        with patch.object(client.app.state.db,'usage_summary',side_effect=lambda:{'requests':len(provider.windows)}):
            job=wait_job(client,client.post(f'/api/videos/{vid}/analyze').json()['id'])
        assert job['status']=='paused' and job['pause_reason']=='daily_limit'
        assert job['calls']==1 and job['completed_windows']==3 and provider.windows==[0]


def test_static_filter_manual_pause_keeps_first_result(settings,still_video):
    from tests.test_continuous_analysis import BlockingMock
    provider=BlockingMock();settings=filtered_settings(settings)
    with TestClient(create_app(settings,provider),base_url='http://localhost') as client:
        vid=upload(client,still_video)
        started=client.post(f'/api/videos/{vid}/analyze').json()
        try:
            assert provider.entered.wait(10)
            assert client.post(f'/api/videos/{vid}/pause').status_code==202
        finally:
            provider.release.set()
        paused=wait_job(client,started['id'])
        assert paused['status']=='paused' and paused['calls']==1 and paused['completed_windows']==1
        done=wait_job(client,client.post(f'/api/videos/{vid}/resume').json()['id'])
        assert done['status']=='completed' and provider.windows==[0,6000]
