"""Replace one window only when its cached real output maps unambiguously to stored evidence."""
from collections import Counter

from .errors import AppError
from .events import normalize, validate_memory


def replace_window(document, window, previous_extraction, replacement_extraction):
    known={entity['id']:entity for entity in document['entities']}
    _, old_evidence, old_events=normalize(previous_extraction,document['video_id'],window,known)
    def key(event,evidence):
        refs={e['id']:e for e in evidence}
        frames=tuple(tuple(refs[r]['frame_ids']) for r in event['evidence_ids'])
        return event['subject_id'],event['target_id'],event['action'],event['start_ms'],event['end_ms'],frames
    expected=Counter(key(e,old_evidence) for e in old_events)
    matches=[e for e in document['events'] if key(e,document['evidence']) in expected]
    if Counter(key(e,document['evidence']) for e in matches)!=expected:
        raise AppError('REVIEW_AMBIGUOUS','无法唯一对应旧窗口事件，未替换结果。',409)
    known,new_evidence,new_events=normalize(replacement_extraction,document['video_id'],window,known)
    remove_events={e['id'] for e in matches}
    remove_evidence={ref for e in matches for ref in e['evidence_ids']}
    result={**document,'entities':list(known.values()),
            'events':sorted([e for e in document['events'] if e['id'] not in remove_events]+new_events,key=lambda e:(e['start_ms'],e['end_ms'])),
            'evidence':[e for e in document['evidence'] if e['id'] not in remove_evidence]+new_evidence}
    validate_memory(result)
    return result
