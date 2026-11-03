"""Synthetic tests for replacing cached output; no real or paid calls."""
import copy

import pytest

from backend.errors import AppError
from backend.events import Extraction
from backend.window_review import replace_window


def setup_window(client, analyzed):
    vid,_=analyzed
    document,_=client.app.state.db.memory(vid)
    evidence={e['id']:e for e in document['evidence']}
    originals=[e for e in document['events'] if e['end_ms']<8000 and e['subject_id']==document['events'][0]['subject_id']]
    raw=Extraction.model_validate({'entities':[],'events':[
        {'subject_id':e['subject_id'],'target_id':e['target_id'],'action':e['action'],
         'frame_ids':evidence[e['evidence_ids'][0]]['frame_ids']} for e in originals]})
    window={'start_ms':0,'end_ms':8000,'frames':[f for f in document['frames'] if f['pts_ms']<8000]}
    return document,raw,window,originals


def test_replace_only_unambiguous_window(client, analyzed):
    document,raw,window,originals=setup_window(client,analyzed)
    replacement=Extraction.model_validate({'entities':[],'events':[{'subject_id':originals[0]['subject_id'],
        'action':'unknown','frame_ids':[window['frames'][0]['id']],'uncertainty_reasons':['测试：原动作证据不充分']}]})
    updated=replace_window(document,window,raw,replacement)
    old={e['id'] for e in originals}
    assert not any(e['id'] in old for e in updated['events'])
    assert {e['id'] for e in document['events'] if e['id'] not in old}.issubset({e['id'] for e in updated['events']})
    assert len(updated['events'])==len(document['events'])-len(originals)+1
    assert any(e['action']=='unknown' for e in updated['events'])
    assert document['events'][0]['action']!='unknown'  # Input remains unchanged.


def test_ambiguous_cached_mapping_is_not_replaced(client, analyzed):
    document,raw,window,originals=setup_window(client,analyzed)
    duplicate=copy.deepcopy(originals[0]);duplicate['id']='test_duplicate'
    document['events'].append(duplicate)
    with pytest.raises(AppError,match='无法唯一对应'):
        replace_window(document,window,raw,raw)


def test_long_profile_reduces_full_six_minute_windows(client):
    manager=client.app.state.manager
    long_profile=manager.profile({'duration_ms':393633})
    assert long_profile.sampling_fps==.5 and long_profile.window_seconds==30 and long_profile.window_stride_seconds==28
    assert manager.profile({'duration_ms':72000}).sampling_fps==1
