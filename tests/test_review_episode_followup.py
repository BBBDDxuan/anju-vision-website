"""A new evidence update may be reviewed, but not re-alarm a confirmed episode."""
from datetime import datetime, timezone
import json
import threading

import pytest

from backend.hybrid_safety import confirmed_episode, duplicate_episode_result
from backend.live_camera import LiveCamera
from tests.test_hybrid_safety import service


def item(root, letter, stage):
    identifier = 'sc_' + letter * 32
    directory = root / identifier
    directory.mkdir()
    frames = []
    for i in range(2):
        path = directory / f'f_{i}.jpg'
        path.write_bytes(b'SYNTHETIC_JPEG')
        frames.append(dict(id=f'f_{i}', pts_ms=i * 250, path=str(path)))
    value = dict(id=identifier, event_type='suspected_fall', track_id='person-1', confidence=.9,
                 t=1 if stage == 'initial' else 4, occurred_at=datetime.now(timezone.utc).isoformat(),
                 frames=frames, details=dict(review_episode='person-1:1', review_stage=stage))
    (directory / 'manifest.json').write_text(json.dumps(value), encoding='utf-8')
    return value


@pytest.mark.parametrize('first_verdict,expected_requests', [('confirmed', 1), ('uncertain', 2), ('rejected', 2)])
def test_live_followup_uses_prior_outcome_without_duplicate_paid_confirmation(tmp_path, first_verdict, expected_requests):
    answer = dict(verdict=first_verdict, event_type='suspected_fall', reason='synthetic fixture', frame_ids=['f_0', 'f_1'])
    pipeline, requests = service(tmp_path, answer)
    first, second = item(tmp_path, 'a', 'initial'), item(tmp_path, 'b', 'progression')
    controller = LiveCamera(tmp_path, safety=pipeline)
    controller.candidate_root = tmp_path
    controller.review_session = 'live_mock'
    process = object()
    controller.process = process
    done = threading.Event()
    sent = []
    def send(message):
        sent.append(json.loads(message))
        if len(sent) == 2:
            done.set()
    controller._send = send
    try:
        controller._queue_review(first['id'], process)
        controller._queue_review(second['id'], process)
        assert done.wait(5)
        assert len(requests) == expected_requests
        assert pipeline.db.usage_summary()['requests'] == expected_requests
        assert sent[1]['verdict'] == ('duplicate' if first_verdict == 'confirmed' else first_verdict)
    finally:
        controller.process = None
        controller.review_pool.shutdown(wait=True)


def test_confirmed_other_episode_or_track_cannot_suppress_followup(tmp_path):
    first, second = item(tmp_path, 'a', 'initial'), item(tmp_path, 'b', 'progression')
    reviews = {first['id']: dict(verdict='confirmed', cloud_images=True)}
    assert confirmed_episode(second, [first], reviews) == first['id']
    second['details']['review_episode'] = 'person-1:2'
    assert confirmed_episode(second, [first], reviews) is None
    second['details']['review_episode'] = 'person-1:1'
    second['track_id'] = 'person-2'
    assert confirmed_episode(second, [first], reviews) is None
    from backend.hybrid_safety import HybridSafety
    assert HybridSafety.extraction(second, duplicate_episode_result(second, first['id'])) is None
