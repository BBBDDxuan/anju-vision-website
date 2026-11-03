"""Explicit role fixtures only; do not invent actor information for old object events."""
import pytest

from backend.answer import describe
from backend.events import Extraction, normalize
from backend.state import replay
from backend.semantics import event_description
from tests.test_safety import document


@pytest.mark.parametrize('action,verb', [('pick_up','拿起'),('place_on','放下'),('open','打开'),('close','关闭')])
def test_actor_grammar_and_location_state(action, verb):
    memory = document('walk')
    person = memory['entities'][0]['id']
    memory['entities'].extend([
        {'id':'cup','label':'白色保温杯','entity_type':'object','identity_status':'distinct_candidate'},
        {'id':'desk','label':'桌面','entity_type':'location','identity_status':'distinct_candidate'},
    ])
    position = {**memory['events'][0], 'id':'position', 'action':'seen_at','target_id':'desk'}
    interaction = {**memory['events'][0], 'id':'interaction', 'action':action,'target_id':'cup'}
    memory['events'] = [position, interaction]
    entities = {e['id']:e for e in memory['entities']}
    text = describe(interaction, entities)
    assert f'人物1（白色上衣）{verb}白色保温杯' in text and f'人物1（白色上衣）被' not in text
    states = replay(memory,memory['video_duration_ms'])
    assert states[person].last_position['target_id'] == 'desk'
    assert not states[person].ambiguous and states[person].container_event is None
    obj = {**interaction, 'subject_id':'cup','target_id':None,'action':'pick_up'}
    assert '白色保温杯被拿起' in describe(obj,entities)


def test_missing_manipulated_object_is_unknown_not_an_invented_target():
    raw = Extraction.model_validate({'entities':[{'id':'p','label':'人物1','entity_type':'person'}],
                                    'events':[{'subject_id':'p','action':'pick_up','frame_ids':['f0']} ]})
    _, _, events = normalize(raw,'v_TEST',{'start_ms':0,'frames':[{'id':'f0','pts_ms':0}]},{})
    assert events[0]['action'] == 'unknown' and events[0]['uncertainty_reasons']


def test_complete_appearance_and_walking_without_an_invented_direction():
    memory=document('walk');person=memory['entities'][0]['id']
    entities={person:memory['entities'][0],'cabinet':{'id':'cabinet','label':'储物柜','entity_type':'location'}}
    event={**memory['events'][0],'action':'seen_at','target_id':None}
    assert event_description(event,entities).endswith('出现在画面中')
    event.update(action='walk',target_id='cabinet')
    assert '在画面中行走（位置参照：储物柜）' in event_description(event,entities)
    assert '走向' not in event_description(event,entities)


@pytest.mark.parametrize('action',['move','walk','seen_at'])
def test_ambiguous_person_object_target_does_not_become_a_location_or_confident_new_event(action):
    memory=document('walk');person=memory['entities'][0]['id']
    memory['entities'].append({'id':'bag','label':'黑色双肩包','entity_type':'object','identity_status':'distinct_candidate'})
    event={**memory['events'][0],'action':action,'target_id':'bag'}
    memory['events']=[event]
    entities={e['id']:e for e in memory['entities']}
    assert '移动到黑色双肩包' not in event_description(event,entities)
    assert replay(memory,memory['video_duration_ms'])[person].last_position is None
    raw=Extraction.model_validate({'entities':[{'id':'p','label':'人物1','entity_type':'person'},
                                              {'id':'bag','label':'黑色双肩包','entity_type':'object'}],
      'events':[{'subject_id':'p','target_id':'bag','action':action,'frame_ids':['f0','f1']} ]})
    _,_,events=normalize(raw,'v_TEST',{'start_ms':0,'frames':memory['frames']},{})
    assert events[0]['action']=='unknown' and events[0]['uncertainty_reasons']
