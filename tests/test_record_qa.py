"""Synthetic records and mocked HTTP only; these tests do not validate model quality."""
import copy
from dataclasses import replace
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.db import Database
from backend.errors import AppError
from backend.main import create_app
from backend.providers.qwen import QwenProvider
from backend.record_qa import RecordQuestionAnswerer, answer_style
from tests.test_safety import document


JOB = {'id': 'j_RECORD_TEST', 'status': 'completed', 'is_mock': True}


def answerer(settings, memory, reply=None, daily=60):
    calls = []
    settings = replace(settings, api_key='mock-secret-not-real', enable_network=True,
                       billing_mode='request_limited', max_daily_calls=daily)
    db = Database(settings.data_dir)
    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        body = reply if reply is not None else {'observations': [
            {'text': '测试记录显示人物出现疑似跌倒过程。', 'event_ids': [memory['events'][0]['id']]}]}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(body)}}],
            'usage': {'prompt_tokens': 50, 'completion_tokens': 40}})
    provider = QwenProvider(settings, db, httpx.MockTransport(handle))
    return RecordQuestionAnswerer(settings, provider), db, calls


def add_person(memory, simultaneous=False):
    person = {**memory['entities'][0], 'id': 'p_second', 'label': '人物2（测试）'}
    event = copy.deepcopy(memory['events'][0])
    event.update(id='e_second', subject_id=person['id'])
    if not simultaneous:
        # Intervals overlap, but evidence instants do not: do not infer co-presence.
        memory['frames'] += [{'id': 'f_second_0', 'pts_ms': 100, 'kind': 'original_frame'},
                             {'id': 'f_second_1', 'pts_ms': 200, 'kind': 'original_frame'}]
        ev = {**memory['evidence'][0], 'id': 'ev_second', 'start_ms': 100, 'end_ms': 200,
              'frame_ids': ['f_second_0', 'f_second_1']}
        memory['evidence'].append(ev)
        event.update(start_ms=100, end_ms=200, evidence_ids=['ev_second'])
    memory['entities'].append(person)
    memory['events'].append(event)


def test_count_repeated_events_are_one_candidate_and_no_paid_call(settings):
    memory = document()
    memory['events'].append({**memory['events'][0], 'id': 'e_duplicate'})
    qa, db, calls = answerer(settings, memory)
    result = qa.answer(memory, JOB, '视频中出现了几个人？')
    assert result['person_statistics']['candidate_count'] == 1
    assert result['person_statistics']['max_same_sample_candidates'] == 1
    assert result['evidence'] and '准确' in result['answer']
    assert not calls and db.usage_summary()['requests'] == 0


@pytest.mark.parametrize('simultaneous,expected', [(False, 1), (True, 2)])
def test_simultaneous_count_requires_shared_actual_sample(settings, simultaneous, expected):
    memory = document()
    add_person(memory, simultaneous)
    qa, _, _ = answerer(settings, memory)
    result = qa.answer(memory, JOB, '画面里同时有几个人？')
    assert result['person_statistics']['candidate_count'] == 2
    assert result['person_statistics']['max_same_sample_candidates'] == expected


@pytest.mark.parametrize('kind,score', [('inferred', .9), ('observed', .2)])
def test_missing_direct_person_does_not_assert_zero(settings, kind, score):
    memory = document(kind=kind, score=score)
    qa, _, calls = answerer(settings, memory)
    result = qa.answer(memory, JOB, '视频中有几个人？')
    assert not result['observations'] and '不能据此断言' in result['answer']
    assert not calls


def test_count_cutoff_and_gap_boundaries(settings):
    memory = document()
    qa, _, _ = answerer(settings, memory)
    result = qa.answer(memory, {**JOB, 'status': 'partial', 'skipped_static_ranges': [{'start_ms': 0, 'end_ms': 1000}]},
                       '视频有几个人？', as_of_ms=100)
    assert not result['observations'] and '静止时段' in result['answer'] and '尚未完整' in result['answer']
    with pytest.raises(AppError):
        qa.answer(memory, JOB, '人数？', as_of_ms=memory['video_duration_ms'] + 1)


def test_language_answer_text_only_validates_evidence_and_caches(settings):
    memory = document()
    qa, db, calls = answerer(settings, memory)
    result = qa.answer(memory, JOB, '概括人物活动和需要注意的情况')
    assert result['answer_engine'] == 'record_llm' and result['evidence']
    assert result['observations'][0]['event_ids'] == [memory['events'][0]['id']]
    assert calls[0]['response_format'] == {'type': 'json_object'}
    assert all(c['type'] == 'text' for c in calls[0]['messages'][0]['content'])
    again = qa.answer(memory, JOB, '概括人物活动和需要注意的情况')
    assert again['cached'] and not again['model_called']
    assert db.usage_summary()['requests'] == len(calls) == 1
    changed = copy.deepcopy(memory)
    changed['events'][0]['uncertainty_reasons'] = ['更新后的证据限制']
    qa.answer(changed, JOB, '概括人物活动和需要注意的情况')
    assert len(calls) == 2


@pytest.mark.parametrize('fault', ['invented_id', 'inference_promoted'])
def test_bad_reference_or_promoted_inference_rejected_without_retry(settings, fault):
    memory = document(kind='inferred' if fault == 'inference_promoted' else 'observed')
    reply = {'observations': [{'text': 'MODEL_BAD_CLAIM', 'event_ids': ['nonexistent'] if fault == 'invented_id' else [memory['events'][0]['id']]}]}
    qa, db, calls = answerer(settings, memory, reply)
    result = qa.answer(memory, JOB, '概括视频发生了什么')
    assert result['answer_engine'] == 'record_rules'
    assert 'MODEL_BAD_CLAIM' not in result['answer']
    assert result['model_error']['code'] == 'QA_REFERENCE_INVALID'
    assert len(calls) == db.usage_summary()['requests'] == 1


def test_inference_separate_and_unknown_preserved(settings):
    memory = document(kind='inferred')
    reply = {'inferences': [{'text': '人物可能出现跌倒，动作部分被遮挡。', 'event_ids': [memory['events'][0]['id']]}],
             'unknowns': ['没有音频记录，无法回答说了什么。']}
    qa, _, _ = answerer(settings, memory, reply)
    result = qa.answer(memory, JOB, '人物怎么了，有说话吗？')
    assert not result['observations'] and result['inferences'][0]['text'].startswith('可能')
    assert '没有音频' in result['answer']


def test_text_model_cannot_invent_new_health_inference(settings):
    memory = document()
    reply = {'observations': [{'text': '人物发生跌倒。', 'event_ids': [memory['events'][0]['id']]}],
             'inferences': [{'text': 'MODEL_INVENTED_HEALTH_RISK', 'event_ids': [memory['events'][0]['id']]}]}
    qa, _, _ = answerer(settings, memory, reply)
    result = qa.answer(memory, JOB, '人物怎么了？')
    assert not result['inferences'] and 'MODEL_INVENTED_HEALTH_RISK' not in result['answer']
    assert '疑似跌倒' in result['observations'][0]['text']


def test_daily_limit_prevents_model_call_and_falls_back(settings):
    memory = document()
    qa, db, calls = answerer(settings, memory, daily=1)
    request_id = db.reserve_request('other_video', 'other_job', 1, 1)
    db.finish_request(request_id, 'success', {})
    result = qa.answer(memory, JOB, '概括视频发生了什么')
    assert result['answer_engine'] == 'record_rules' and result['observations']
    assert not result['model_called'] and not calls
    assert db.usage_summary()['requests'] == 1


def test_user_can_disable_model_and_no_evidence_never_calls(settings):
    memory = document()
    qa, _, calls = answerer(settings, memory)
    assert qa.answer(memory, JOB, '视频发生了什么', use_model=False)['answer_engine'] == 'record_rules'
    memory['events'].clear()
    assert not qa.answer(memory, JOB, '视频发生了什么')['observations']
    assert not calls


def test_query_api_preserves_old_contract_and_opt_out(client, analyzed):
    vid, _ = analyzed
    result = client.post(f'/api/videos/{vid}/query', json={'question': '钥匙最后在哪里？', 'use_model': False}).json()
    assert result['observations'] and result['evidence'] and result['run_id']
    assert result['answer_engine'] == 'record_rules'


def test_query_api_grounded_model_and_count_share_contract(settings):
    memory = document()
    qa, db, calls = answerer(settings, memory)
    video_id = memory['video_id']
    db.add_video({'id': video_id, 'sha256': 'mock-only', 'name': 'MOCK_RECORDS_ONLY.mp4',
                  'duration_ms': memory['video_duration_ms'], 'original_path': 'not-used',
                  'playback_path': 'not-used', 'metadata': {}})
    job = {**JOB, 'video_id': video_id}
    db.save_job(job)
    db.save_memory(job['id'], memory, job)
    with TestClient(create_app(qa.settings, qa.provider), base_url='http://localhost') as client:
        result = client.post(f'/api/videos/{video_id}/query', json={'question': '概括人物活动'}).json()
        assert result['answer_engine'] == 'record_llm' and result['evidence'] and result['run_id'] == job['id']
        result = client.post(f'/api/videos/{video_id}/query', json={'question': '视频有几个人？'}).json()
        assert result['person_statistics']['candidate_count'] == 1
        assert db.usage_summary()['requests'] == len(calls) == 1


def test_context_limit_is_disclosed_and_cannot_cite_excluded_record(settings):
    memory = document()
    memory['events'] = [{**memory['events'][0], 'id': f'e_{i:03}'} for i in range(170)]
    qa, _, _ = answerer(settings, memory)
    result = qa.answer(memory, JOB, '概括人物活动')
    assert result['answer_engine'] == 'record_llm' and '只读取部分记录' in result['answer']
    qa, _, calls = answerer(settings, memory, {'observations': [{'text': 'EXCLUDED_CLAIM', 'event_ids': ['e_169']}]})
    result = qa.answer(memory, JOB, '概括人物活动')
    assert result['answer_engine'] == 'record_rules' and 'EXCLUDED_CLAIM' not in result['answer']


def test_short_references_resolve_to_actual_events_and_evidence(settings):
    memory = document()
    reply = {'observations': [{'text': '记录中的人物出现疑似跌倒。', 'event_ids': ['r1']}]}
    qa, _, calls = answerer(settings, memory, reply)
    result = qa.answer(memory, JOB, '概括这段录像里发生的事情')
    assert result['answer_engine'] == 'record_llm'
    assert result['observations'][0]['event_ids'] == [memory['events'][0]['id']]
    assert result['evidence']
    payload = calls[0]
    assert payload['max_tokens'] == 3072
    assert 'observations最多5条' in payload['messages'][0]['content'][0]['text']


@pytest.mark.parametrize('question', ['概括这段录像里发生的事情', '视频中有哪些值得关注的情况？', '录像中的人物做了什么？'])
def test_open_questions_have_evidence_backed_local_fallback(settings, question):
    memory = document()
    qa, _, calls = answerer(settings, memory)
    result = qa.answer(memory, JOB, question, use_model=False)
    assert result['observations'] and result['evidence'] and not calls


def test_truncation_explained_and_local_summary_available_without_retry(settings):
    memory = document()
    qa, db, calls = answerer(settings, memory)
    def truncated(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length',
            'message': {'content': '{"observations":[{"text":"incomplete'}}],
            'usage': {'prompt_tokens': 50, 'completion_tokens': 3072}})
    qa.provider.transport = httpx.MockTransport(truncated)
    result = qa.answer(memory, JOB, '概括这段录像里发生的事情')
    assert result['model_error']['code'] == 'MODEL_TRUNCATED'
    assert '输出长度上限' in result['answer']
    assert result['observations'] and result['evidence']
    assert len(calls) == db.usage_summary()['requests'] == 1


@pytest.mark.parametrize('question,style', [
    ('概括这段录像里发生的事情', 'summary'), ('总结人物的主要活动', 'summary'),
    ('按时间顺序概括这段录像', 'timeline'), ('逐条列出录像中的事件', 'timeline'),
    ('视频中什么时候有人跌倒？', 'question')])
def test_summary_and_timeline_are_different_intents(question, style):
    assert answer_style(question) == style


def test_summary_prompt_and_local_fallback_are_not_a_timeline(settings):
    memory = document('walk')
    memory['events'].extend([{**memory['events'][0], 'id':'sit', 'action':'sit_down'},
                             {**memory['events'][0], 'id':'rise', 'action':'stand_up'}])
    qa, _, calls = answerer(settings, memory)
    result = qa.answer(memory, JOB, '概括这段录像里发生的事情', use_model=False)
    assert len(result['observations']) == 1 and result['answer_style'] == 'summary'
    assert all(action in result['observations'][0]['text'] for action in ['行走', '坐下', '起身'])
    assert '00:' not in result['observations'][0]['text']
    assert result['evidence'] and not calls
    qa.answer(memory, JOB, '概括这段录像里发生的事情')
    assert 'observations仅写1条连贯的中文短段落' in calls[0]['messages'][0]['content'][0]['text']
    qa.answer(memory, JOB, '按时间顺序回顾这段录像')
    assert '用户明确要求按时间回顾' in calls[1]['messages'][0]['content'][0]['text']


def test_model_timeline_cannot_masquerade_as_summary(settings):
    memory = document()
    reply = {'observations': [{'text':'00:02–00:04人物出现疑似跌倒。','event_ids':['r1']}]}
    qa, _, calls = answerer(settings, memory, reply)
    result = qa.answer(memory, JOB, '概括这段录像里发生的事情')
    assert result['answer_engine'] == 'record_rules' and result['model_error']['code'] == 'QA_SUMMARY_STYLE'
    assert result['observations'] and '00:02' not in result['observations'][0]['text']
    assert len(calls) == 1


def test_attention_excludes_normal_people_objects_and_secondary_alerts(settings):
    memory = document('fall')
    memory['entities'].append({'id':'hazard','label':'测试环境区域','entity_type':'location',
                              'identity_status':'distinct_candidate','aliases':[],'appearance':''})
    memory['entities'].append({'id':'cup','label':'测试水杯','entity_type':'object',
                              'identity_status':'distinct_candidate','aliases':[],'appearance':''})
    template = memory['events'][0]
    for action in ['walk','sit_down','lying_on_floor','climb','help_gesture','smoke','flame','sparks','seen_at','pick_up']:
        subject = 'hazard' if action in {'smoke','flame','sparks'} else 'cup' if action in {'seen_at','pick_up'} else template['subject_id']
        memory['events'].append({**template,'id': 'event_'+action,'action':action,'subject_id':subject})
    qa, db, calls = answerer(settings, memory)
    result = qa.answer(memory, JOB, '视频中有哪些值得关注的情况？')
    claims = result['observations'] + result['inferences']
    actions = {e['action'] for e in memory['events'] if any(e['id'] in c['event_ids'] for c in claims)}
    assert actions == {'fall','help_gesture','smoke','flame','sparks'}
    assert result['answer_style'] == 'attention' and result['evidence']
    assert '测试水杯' not in result['answer'] and '攀爬' not in result['answer'] and '躺卧' not in result['answer']
    assert not calls and db.usage_summary()['requests'] == 0


def test_attention_without_priority_events_does_not_substitute_ordinary_activity(settings):
    memory = document('walk')
    qa, _, calls = answerer(settings, memory)
    result = qa.answer(memory, JOB, '视频中有哪些值得关注的情况？')
    assert not result['observations'] and not result['inferences'] and not result['evidence']
    assert '未检索到符合条件' in result['answer'] and '没有风险' in result['answer']
    assert not calls


def test_attention_includes_rule_absence_and_respects_cutoff(settings):
    from tests.test_safety import absence_document
    memory, coverage = absence_document()
    qa, _, calls = answerer(settings, memory)
    result = qa.answer(memory, {**JOB,**coverage}, '视频中有哪些值得关注的情况？')
    assert len(result['inferences']) == 1 and '长时间未出现' in result['inferences'][0]['text']
    assert result['evidence'] and not calls
    early = qa.answer(memory, {**JOB,**coverage}, '视频中有哪些值得关注的情况？', as_of_ms=1000)
    assert not early['observations'] and not early['inferences']
