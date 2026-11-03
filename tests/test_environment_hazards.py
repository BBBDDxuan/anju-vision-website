"""Synthetic environmental candidates only. No real fire/smoke detection claims."""
import copy

import pytest
from fastapi.testclient import TestClient

from backend.answer import query_memory
from backend.errors import AppError
from backend.events import Extraction, normalize, validate_memory
from backend.main import create_app
from backend.providers.base import ProviderResult
from backend.safety import derive_alerts
from tests.conftest import wait_job


def document(action='smoke', kind='observed', score=.9):
    frames=[{'id':'f0','pts_ms':0,'kind':'original_frame'},{'id':'f1','pts_ms':1000,'kind':'original_frame'}]
    raw=Extraction.model_validate({'entities':[{'id':'region','label':'灶台区域','entity_type':'location'}],
        'events':[{'subject_id':'region','action':action,'epistemic_type':kind,'frame_ids':['f0','f1'],
                   'model_score':score,'uncertainty_reasons':['可能是水蒸气或反光，现象尚未证实'] if kind=='inferred' else []}]})
    entities,evidence,events=normalize(raw,'v_ENV_TEST',{'start_ms':0,'frames':frames},{})
    return {'schema_version':'0.1','fixture_kind':'manual_design_example','video_id':'v_ENV_TEST',
            'video_duration_ms':2000,'entities':list(entities.values()),'events':events,'evidence':evidence,'frames':frames}


@pytest.mark.parametrize('action,question,phrase',[('smoke','视频有没有烟雾？','烟雾'),('flame','视频什么时候出现明火？','明火'),
                                                  ('sparks','画面有没有火花？','火花')])
@pytest.mark.parametrize('kind',['observed','inferred'])
def test_environment_schema_red_alert_and_global_query(action, question, phrase, kind):
    memory=document(action,kind)
    validate_memory(memory)
    alert=derive_alerts(memory,'j_ENV_TEST')[0]
    assert alert['category']=='environment' and alert['severity']=='urgent'
    assert alert['epistemic_type']==kind and alert['is_mock'] and alert['evidence_ids']
    assert '不等同于确认火灾' in alert['message']
    if kind=='inferred': assert alert['reasons']
    result=query_memory(memory,{'status':'completed','is_mock':True},question)
    assert result['evidence'] and phrase in result['answer'] and result['candidates']==[]
    assert result['inferences'] if kind=='inferred' else result['observations']


@pytest.mark.parametrize('fault',['person_subject','one_frame','missing_frame'])
def test_invalid_environment_evidence_never_becomes_an_alert(fault):
    raw=Extraction.model_validate({'entities':[{'id':'region','label':'TEST','entity_type':'person' if fault=='person_subject' else 'location'}],
        'events':[{'subject_id':'region','action':'sparks','frame_ids':['f0'] if fault=='one_frame' else ['f0','MISSING' if fault=='missing_frame' else 'f1']}]})
    with pytest.raises(AppError):
        normalize(raw,'v_ENV_TEST',{'start_ms':0,'frames':[{'id':'f0','pts_ms':0},{'id':'f1','pts_ms':1000}]},{})


@pytest.mark.parametrize('fault',['low_score','unknown','non_original'])
def test_low_unknown_and_placeholder_environment_candidates_are_not_alerts(fault):
    memory=document()
    if fault=='low_score': memory['events'][0]['model_score']=.1
    elif fault=='unknown': memory['events'][0]['epistemic_type']='unknown'
    else: memory['frames'][0]['kind']='manual_placeholder'
    assert not derive_alerts(memory,'j_ENV_TEST')


def test_interleaved_environment_types_deduplicate_independently():
    memory=document();original=memory['events'][0]
    memory['events']=[{**copy.deepcopy(original),'id':f'e{i}','action':action}
                      for i,action in enumerate(['smoke','flame','sparks','smoke','flame','sparks'])]
    alerts=derive_alerts(memory,'j_ENV_TEST')
    assert len(alerts)==3 and all(len(a['event_ids'])==2 for a in alerts)
    assert len({a['id'] for a in alerts})==3


class EnvironmentMock:
    name,model,is_mock='TEST_MOCK','SYNTHETIC_ENVIRONMENT_NOT_REAL_DETECTION',True
    def ready(self): pass
    def extract(self,*,window,entities,**kwargs):
        region=entities[0]['id'] if entities else 'region'
        raw={'entities':[] if entities else [{'id':region,'label':'测试灶台区域','entity_type':'location'}],
             'events':[{'subject_id':region,'action':action,'frame_ids':[window['frames'][0]['id'],window['frames'][-1]['id']],
                        'model_score':.7 if action=='smoke' else .9,'epistemic_type':'inferred' if action=='smoke' else 'observed',
                        'uncertainty_reasons':['模拟候选：也可能是水蒸气'] if action=='smoke' else []}
                       for action in ['smoke','flame','sparks']]}
        return ProviderResult(Extraction.model_validate(raw),{'test_mock':True})


def test_environment_upload_alert_acknowledge_evidence_and_delete(settings,sample_video):
    with TestClient(create_app(settings,EnvironmentMock()),base_url='http://localhost') as client:
        vid=client.post('/api/videos/upload',content=sample_video.read_bytes()).json()['video_id']
        job=wait_job(client,client.post(f'/api/videos/{vid}/analyze').json()['id'])
        assert job['status']=='completed',job
        report=client.get(f'/api/videos/{vid}/alerts').json()
        assert len(report['alerts'])==3 and all(a['category']=='environment' for a in report['alerts'])
        assert report['frames'] and client.get(report['frames'][0]['url']).status_code==200
        smoke=next(a for a in report['alerts'] if a['action']=='smoke')
        assert smoke['epistemic_type']=='inferred' and smoke['reasons']
        assert client.post(f"/api/videos/{vid}/alerts/{smoke['id']}/acknowledge").status_code==200
        result=client.post(f'/api/videos/{vid}/query',json={'question':'视频有没有火花？'}).json()
        assert result['observations'] and result['evidence']
        assert client.app.state.db.usage_summary()['requests']==0
        assert client.delete(f'/api/videos/{vid}').status_code==200
