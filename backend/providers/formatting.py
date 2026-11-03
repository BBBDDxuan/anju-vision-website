"""Lossless field normalization only. Missing visual facts must be re-observed."""
import copy
import json

from ..events import Extraction


def parse_extraction(raw: str):
    try:
        return Extraction.model_validate_json(raw), []
    except ValueError:
        pass
    data = json.loads(raw)  # Incomplete JSON is never completed by guessing.
    if not isinstance(data, dict):
        raise ValueError('Expected object')
    fixed, notes = copy.deepcopy(data), []
    for entity in fixed.get('entities', []):
        if isinstance(entity, dict) and 'name' in entity and 'label' not in entity:
            entity['label'] = entity.pop('name')
            notes.append('entity.name→label')
    for event in fixed.get('events', []):
        if not isinstance(event, dict):
            continue
        if isinstance(event.get('frame_id'), str) and 'frame_ids' not in event:
            event['frame_ids'] = [event.pop('frame_id')]
            notes.append('event.frame_id→frame_ids')
        # IDs and times are assigned from actual evidence by the backend. Do not
        # remove narrative/action fields or infer a subject from an entity list.
        if all(k in event for k in ['subject_id', 'action', 'frame_ids']):
            for key in ['id', 'timestamp_ms', 'source_pts_ms', 'start_ms', 'end_ms']:
                if key in event:
                    del event[key]
                    notes.append('ignored_model_metadata:' + key)
    result = Extraction.model_validate(fixed)
    if not notes:
        raise ValueError('No lossless format repair applies')
    return result, list(dict.fromkeys(notes))
