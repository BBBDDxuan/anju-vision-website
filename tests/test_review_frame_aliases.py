"""Short request-local references must map exactly to the original evidence."""
import json

import pytest

from backend.errors import AppError
from tests.test_hybrid_safety import candidate, service


def long_ids(item):
    for i, frame in enumerate(item['frames']):
        frame['id'] = 'sf_' + f'{i + 1:032x}'
    return item


def test_model_uses_short_ids_but_storage_and_events_keep_canonical_ids(tmp_path):
    item = long_ids(candidate(tmp_path))
    answer = dict(verdict='confirmed', event_type=item['event_type'], reason='synthetic', frame_ids=['F01','F02'])
    pipeline, requests = service(tmp_path, answer)
    result = pipeline.review(item, 'v', 'j')
    content = requests[0]['messages'][0]['content']
    references = [part['text'] for part in content if part['type']=='text' and part['text'].startswith('frame_id=')]
    assert references == ['frame_id=F01; pts_ms=0','frame_id=F02; pts_ms=250','frame_id=F03; pts_ms=500']
    assert all(frame['id'] not in json.dumps(content) for frame in item['frames'])
    assert result['frame_ids'] == [f['id'] for f in item['frames'][:2]]
    assert result['model_frame_ids'] == ['F01','F02']
    assert pipeline.extraction(item,result).events[0].frame_ids == result['frame_ids']


@pytest.mark.parametrize('ids', [['F01','F04'], ['F01','F01'], ['F01'], ['F01','sf_'+'f'*32], ['f01','F02']])
def test_invalid_duplicate_or_invented_short_references_still_fail(tmp_path, ids):
    item=long_ids(candidate(tmp_path))
    pipeline,_=service(tmp_path,dict(verdict='confirmed',event_type=item['event_type'],reason='synthetic',frame_ids=ids))
    with pytest.raises(AppError) as error:
        pipeline.review(item,'v','j')
    assert error.value.code=='SAFETY_REVIEW_INVALID'


def test_alias_twelve_is_supported_and_mapping_is_request_local(tmp_path):
    item=long_ids(candidate(tmp_path,12))
    pipeline,_=service(tmp_path,dict(verdict='confirmed',event_type=item['event_type'],reason='synthetic',frame_ids=['F12','F01']))
    result=pipeline.review(item,'v','j')
    assert result['frame_ids']==[item['frames'][11]['id'],item['frames'][0]['id']]
    item['frames'][11]['id']='sf_'+'b'*32
    other=pipeline.review(item,'v','j')
    assert other['frame_ids'][0]=='sf_'+'b'*32
    assert result['frame_ids'][0]!=other['frame_ids'][0]
