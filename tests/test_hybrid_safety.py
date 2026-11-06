"""Mock transport tests: no camera, external HTTP, credentials or paid calls."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

import httpx
import pytest

from backend.config import Settings
from backend.db import Database
from backend.errors import AppError
from backend.hybrid_safety import HybridSafety, REVIEW_TOKENS
from backend.live_camera import LiveCamera
from backend.providers.qwen import QwenProvider


def candidate(tmp_path, count=3):
    identifier = 'sc_' + 'a'*32
    directory = tmp_path/identifier
    directory.mkdir(exist_ok=True)
    frames = []
    for i in range(count):
        path = directory/f'{i}.jpg'
        path.write_bytes(b'fake-test-jpeg-not-a-real-person')
        frames.append({'id': f'f_{i}', 'pts_ms': i*250, 'path': str(path)})
    result = dict(id=identifier, event_type='help_requested', track_id='p1', confidence=.9,
                  t=.5, occurred_at=datetime.now(timezone.utc).isoformat(), frames=frames, details={})
    (directory/'manifest.json').write_text(json.dumps(result), encoding='utf-8')
    return result


def service(tmp_path, verdict=None, **overrides):
    requests = []
    answer = verdict or dict(verdict='confirmed', event_type='help_requested', reason='双手持续举起且远离头部，非真实意图判断。', frame_ids=['F01','F02'])
    def respond(request):
        requests.append(json.loads(request.content))
        content = requests[-1]['messages'][0]['content']
        aliases = [item['text'].split(';')[0].split('=',1)[1] for item in content
                   if item['type']=='text' and item['text'].startswith('frame_id=')]
        fixture = {f'f_{i}': alias for i, alias in enumerate(aliases)}
        response_answer = {**answer, 'frame_ids': [fixture.get(ref,ref) for ref in answer.get('frame_ids',[])]}
        return httpx.Response(200, json={'id':'mock-only','choices':[{'finish_reason':'stop','message':{'content':json.dumps(response_answer)}}],
                                         'usage':{'prompt_tokens':100,'completion_tokens':50}})
    settings = Settings(data_dir=tmp_path/'db', api_key='synthetic-not-a-real-key', enable_network=True,
                        billing_mode='request_limited', **overrides)
    db = Database(settings.data_dir)
    provider = QwenProvider(settings, db, transport=httpx.MockTransport(respond))
    return HybridSafety(settings, db, provider), requests


def test_shared_review_uses_requested_model_budget_and_multiframe_evidence(tmp_path):
    pipeline, requests = service(tmp_path)
    result = pipeline.review(candidate(tmp_path), 'v1', 'j1')
    assert result['verdict'] == 'confirmed'
    assert result['frame_ids'] == ['f_0', 'f_1']
    assert requests[0]['model'] == 'qwen3.8-max'
    assert requests[0]['max_tokens'] == REVIEW_TOKENS
    assert len([x for x in requests[0]['messages'][0]['content'] if x['type']=='image_url']) == 3
    assert pipeline.db.usage_summary()['requests'] == 1
    assert pipeline.extraction(candidate(tmp_path), result).events[0].action == 'help_gesture'


def test_single_arm_motion_candidate_uses_updated_multiframe_review_prompt(tmp_path):
    pipeline, requests = service(tmp_path)
    item = candidate(tmp_path)
    item['details'] = dict(algorithm='body_relative_repeated_arm_motion_review_v2',
                           review_required=True, active_hands=['right'])
    result = pipeline.review(item, 'v1', 'j1')
    assert result['verdict'] == 'confirmed'  # Mock response, not Qwen accuracy evidence.
    prompt = requests[0]['messages'][0]['content'][0]['text']
    assert '单手/双手' in prompt
    assert '不要求双手同时举起' in prompt
    assert '躺地姿态' in prompt
    assert '伸懒腰' in prompt and '打招呼' in prompt
    assert '必须看见双手举起的连续动作' not in prompt


@pytest.mark.parametrize('answer', [
    dict(verdict='confirmed',event_type='fire_detected',reason='wrong type',frame_ids=['F01','F02']),
    dict(verdict='confirmed',event_type='help_requested',reason='forged frames',frame_ids=['F01','F02','F04']),
    dict(verdict='confirmed',event_type='help_requested',reason='single',frame_ids=['F01']),
    dict(verdict='confirmed',event_type='help_requested',reason='duplicates',frame_ids=['F01','F01']),
    dict(verdict='approved',event_type='help_requested',reason='unsupported',frame_ids=['F01','F02'])])
def test_invalid_or_forged_review_never_confirms(tmp_path, answer):
    pipeline, _ = service(tmp_path, answer)
    with pytest.raises(AppError, match='安全复核响应无效'):
        pipeline.review(candidate(tmp_path), 'v1', 'j1')


def test_fire_review_cites_short_labels_but_persists_original_frame_ids(tmp_path):
    pipeline, requests = service(tmp_path, dict(verdict='confirmed', event_type='fire_detected',
                                 reason='Two frames show visible flame.', frame_ids=['F02', 'F11']))
    item = candidate(tmp_path, count=12)
    item['event_type'] = 'fire_detected'
    for i, frame in enumerate(item['frames']):
        frame['id'] = f'sf_{i:030x}'
    result = pipeline.review(item, 'v1', 'j1')
    text = [part['text'] for part in requests[0]['messages'][0]['content'] if part['type'] == 'text']
    assert text[1:] == [f"frame_id=F{i:02d}; pts_ms={frame['pts_ms']}" for i, frame in enumerate(item['frames'], 1)]
    expected = [item['frames'][i]['id'] for i in (1, 10)]
    assert result['frame_ids'] == expected
    assert pipeline.extraction(item, result).events[0].frame_ids == expected


@pytest.mark.parametrize('verdict', ['rejected','uncertain'])
def test_negative_reviews_do_not_create_alarm_events(tmp_path, verdict):
    pipeline, _ = service(tmp_path, dict(verdict=verdict,event_type='help_requested',reason='抱头或证据不足',frame_ids=['F01','F02']))
    item=candidate(tmp_path)
    assert pipeline.extraction(item, pipeline.review(item, 'v1', 'j1')) is None


def test_single_frame_is_uncertain_without_network(tmp_path):
    pipeline, requests = service(tmp_path)
    assert pipeline.review(candidate(tmp_path, 1), 'v1', 'j1')['verdict'] == 'uncertain'
    assert requests == []


def test_disabled_or_no_network_never_falls_back_to_local_confirmation(tmp_path):
    pipeline, requests = service(tmp_path)
    pipeline.enabled=False
    with pytest.raises(AppError) as exc:
        pipeline.review(candidate(tmp_path), 'v1', 'j1')
    assert exc.value.code == 'SAFETY_REVIEW_DISABLED'
    pipeline.enabled=True
    pipeline.reviewer.settings=replace(pipeline.reviewer.settings,enable_network=False)
    with pytest.raises(AppError) as exc:
        pipeline.review(candidate(tmp_path), 'v1', 'j1')
    assert exc.value.code == 'NETWORK_DISABLED'
    assert not requests


def test_daily_budget_is_shared_across_video_and_live(tmp_path):
    pipeline, requests = service(tmp_path, max_daily_calls=1)
    item=candidate(tmp_path)
    pipeline.review(item, 'video-id', 'video-job')
    with pytest.raises(AppError) as exc:
        pipeline.review(item, 'live-id', 'live-job')
    assert exc.value.code=='REQUEST_LIMIT'
    assert len(requests)==1


def test_evidence_must_stay_inside_owned_candidate_directory(tmp_path):
    pipeline, _ = service(tmp_path)
    item=candidate(tmp_path)
    assert pipeline.load_candidate(item['id'], tmp_path)['id']==item['id']
    outside=tmp_path/'unrelated.jpg';outside.write_bytes(b'private-data')
    item['frames'][0]['path']=str(outside)
    with pytest.raises(AppError):
        pipeline.validate_candidate(item, tmp_path)


def test_incomplete_scan_never_claims_success(tmp_path):
    pipeline, _ = service(tmp_path)
    with pytest.raises(AppError) as exc:
        pipeline._validate_scan({'completed':False,'candidates':[]},tmp_path)
    assert exc.value.code=='SAFETY_SCAN_INCOMPLETE'


def test_live_review_runs_off_reader_thread_and_returns_only_public_metadata(tmp_path):
    pipeline, requests=service(tmp_path)
    item=candidate(tmp_path)
    controller=LiveCamera(tmp_path, safety=pipeline)
    controller.candidate_root=tmp_path
    controller.review_session='live-test'
    process=object();controller.process=process
    done=threading.Event();sent=[]
    def send(message):
        sent.append(json.loads(message));done.set()
    controller._send=send
    try:
        controller._queue_review(item['id'],process)
        assert done.wait(5)
        assert sent[0]['verdict']=='confirmed'
        assert 'usage' not in sent[0] and 'frames' not in sent[0]
        assert (tmp_path/item['id']/'review.json').is_file()
        controller._queue_review(item['id'],process)
        assert len(requests)==1
    finally:
        controller.process=None
        controller.review_pool.shutdown(wait=True)


def test_stopped_live_session_cannot_send_late_confirmation(tmp_path):
    pipeline, _=service(tmp_path)
    item=candidate(tmp_path);started=threading.Event();release=threading.Event()
    original=pipeline.review
    def blocked(*args):
        started.set();release.wait(5);return original(*args)
    pipeline.review=blocked
    controller=LiveCamera(tmp_path, safety=pipeline)
    controller.candidate_root=tmp_path;controller.review_session='old'
    process=object();controller.process=process
    sent=[];controller._send=sent.append
    try:
        controller._queue_review(item['id'],process)
        assert started.wait(5)
        controller.process=None
        release.set()
        controller.review_pool.shutdown(wait=True)
        assert not sent
    finally:
        release.set();controller.process=None
        controller.review_pool.shutdown(wait=True)


def test_live_session_conservative_output_budget(tmp_path):
    pipeline, requests = service(tmp_path, max_output_tokens=1024)
    item=candidate(tmp_path)
    pipeline.review(item, 'live_session', 'live_session')
    with pytest.raises(AppError) as exc:
        pipeline.review(item, 'live_session', 'live_session')
    assert exc.value.code == 'REQUEST_LIMIT' and len(requests) == 1


def test_scan_cannot_be_complete_without_any_processed_frame(tmp_path):
    pipeline, _=service(tmp_path)
    with pytest.raises(AppError):
        pipeline._validate_scan({'version':1,'frames':0,'completed':True,'candidates':[]},tmp_path)


def test_live_configuration_status_does_not_misrepresent_network_disabled(tmp_path):
    pipeline, _ = service(tmp_path)
    controller=LiveCamera(tmp_path, safety=pipeline)
    try:
        assert controller.status()['cloud_calls'] is True
        pipeline.reviewer.settings=replace(pipeline.reviewer.settings, enable_network=False)
        status=controller.status()
        assert status['cloud_calls'] is False and status['review_error']=='NETWORK_DISABLED'
        assert status['review_enabled'] is True
    finally:
        controller.close()

@pytest.mark.parametrize('verdict',['rejected','uncertain'])
def test_review_only_fall_candidate_cannot_bypass_visual_verdict(tmp_path,verdict):
    item=candidate(tmp_path)
    item.update(event_type='suspected_fall',details={'algorithm':'rapid_collapse_review_v1','review_required':True})
    pipeline,requests=service(tmp_path,dict(verdict=verdict,event_type='suspected_fall',reason='缺少可靠倒地过程',frame_ids=['F01','F02']))
    result=pipeline.review(item,'v1','j1')
    assert result['verdict']==verdict
    assert pipeline.extraction(item,result) is None
    assert len(requests)==1  # MockTransport only.


def test_detector_source_change_invalidates_video_scan_signature(tmp_path,monkeypatch):
    import backend.hybrid_safety as module
    root=tmp_path/'source'
    package=root/'elder-safety/ai-service/ai_service'
    package.mkdir(parents=True)
    scanner=root/'elder-safety/scripts/scan-safety-video.py'
    scanner.parent.mkdir(parents=True);scanner.write_text('# scanner',encoding='utf-8')
    policy=package/'fall_candidates.py';policy.write_text('# policy v1',encoding='utf-8')
    config=root/'config.json';config.write_text('{}',encoding='utf-8')
    pipeline,_=service(tmp_path,safety_model_config=config)
    monkeypatch.setattr(module,'ROOT',root)
    before=pipeline.signature()
    policy.write_text('# policy v2',encoding='utf-8')
    after=pipeline.signature()
    assert before[:-1]==after[:-1]
    assert before[-1]['detector_sources']!=after[-1]['detector_sources']


def test_review_receives_explicit_missing_transition_evidence(tmp_path):
    pipeline, requests = service(tmp_path, dict(verdict='uncertain', event_type='suspected_fall',
        reason='only lying posture, transition missing', frame_ids=['F01', 'F02']))
    item = candidate(tmp_path)
    item['event_type'] = 'suspected_fall'
    item['details'] = {'candidate_reason': 'sustained_prone_without_observed_transition',
        'fall_transition_observed': False, 'posture_projection': 'horizontal',
        'hold_seconds': .8, 'unrelated': 'not part of the review metadata'}
    result = pipeline.review(item, 'v1', 'j1')
    text = requests[0]['messages'][0]['content'][0]['text']
    assert '"fall_transition_observed": false' in text
    assert '"posture_projection": "horizontal"' in text
    assert 'unrelated' not in text
    assert pipeline.extraction(item, result) is None


@pytest.mark.parametrize('event_type,gesture', [
    ('help_requested', 'both_hands_raised_waving_candidate'),
    ('fire_detected', None),
])
@pytest.mark.parametrize('verdict', ['rejected', 'uncertain'])
def test_extra_review_candidates_forward_context_without_becoming_alarms(tmp_path, event_type, gesture, verdict):
    answer = dict(verdict=verdict, event_type=event_type,
                  reason='候选视觉证据不足，离线复核测试', frame_ids=['F01', 'F02'])
    pipeline, requests = service(tmp_path, answer)
    item = candidate(tmp_path)
    item['event_type'] = event_type
    item['details'] = dict(candidate_reason='persistent_model_evidence', review_required=True,
                           hold_seconds=1.0, frames=8, unrelated='do not forward')
    if gesture:
        item['details']['gesture'] = gesture
    else:
        item['details'].update(hit_rate=.7, missing_observations=3)
    result = pipeline.review(item, 'v1', 'j1')
    text = requests[0]['messages'][0]['content'][0]['text']
    assert '"review_required": true' in text
    assert '"frames": 8' in text
    assert 'unrelated' not in text
    if gesture:
        assert gesture in text
        assert '斜俯视下允许屈肘' in text
        assert '伸懒腰' in text
    else:
        assert '"hit_rate": 0.7' in text
        assert '"missing_observations": 3' in text
    assert pipeline.extraction(item, result) is None
