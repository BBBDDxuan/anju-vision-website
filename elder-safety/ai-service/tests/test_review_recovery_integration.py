"""Public collector integration; synthetic poses, no runtime assets or cloud."""
from datetime import datetime, timedelta, timezone
import numpy as np
import pytest

from ai_service.config import Config
from ai_service.engine import DetectionEngine
from ai_service.review_recovery import SlowReview
from ai_service.safety_candidates import SafetyCandidateCollector
from ai_service.types import Pose, Sample


def pose(shoulder, hip, knees, feet, box):
    points = np.zeros((17, 3), float)
    for indices, point in [([5, 6], shoulder), ([11, 12], hip),
                           ([13, 14], knees), ([15, 16], feet)]:
        for index, offset in zip(indices, [-5, 5]):
            points[index] = [point[0] + offset, point[1], .98]
    return Pose(box, points, .98)


STANDING = pose((200, 100), (200, 210), (235, 285), (270, 370), (150, 60, 300, 390))
SEATED = pose((200, 150), (200, 245), (280, 250), (280, 370), (150, 110, 320, 390))
SLUMPED = pose((215, 345), (200, 325), (240, 350), (270, 370), (160, 290, 290, 390))


def samples(start=STANDING, end=SLUMPED):
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(65):
        t = i / 10
        progress = min(1, max(0, (t - 1) / 3))
        current = Pose(tuple(np.asarray(start.bbox) * (1 - progress) + np.asarray(end.bbox) * progress),
                       start.keypoints * (1 - progress) + end.keypoints * progress, .98)
        yield Sample(np.zeros((540, 720, 3), np.uint8), t,
                     origin + timedelta(seconds=t), poses=[current], flames_observed=False)


@pytest.mark.parametrize('start', [STANDING, SEATED])
def test_public_collector_routes_slide_with_motion_precontext(tmp_path, start):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    assert isinstance(collector.engine.review_fall, SlowReview)
    events = []
    for sample in samples(start):
        events.extend(collector.process(sample))
    events.extend(collector.finish())
    falls = [e for e in events if e['event_type'] == 'suspected_fall']
    assert len(falls) == 1
    event = falls[0]
    # Seated descent now proposes review earlier, before a low-posture hold.
    expected = ('slow_descent_low_posture_review_v1' if start is STANDING
                else 'seated_core_descent_review_v1')
    assert event['details']['algorithm'] == expected
    assert event['details']['review_required'] and not event['details']['floor_contact_observed']
    assert 2 <= len(event['frames']) <= 12
    times = [f['pts_ms'] for f in event['frames']]
    assert times == sorted(set(times))
    assert times[0] <= event['details']['reference_t'] * 1000
    if start is STANDING:
        assert times[-1] - times[0] > 4000
    else:
        assert event['t'] < 4.0 and times[-1] > event['t'] * 1000


@pytest.mark.parametrize('end', [SEATED,
    pose((240, 210), (230, 300), (240, 355), (280, 370), (170, 170, 330, 390)),
    pose((280, 300), (200, 210), (200, 285), (200, 370), (150, 170, 330, 390))])
def test_normal_sitting_kneeling_and_bowing_do_not_use_slow_branch(tmp_path, end):
    collector = SafetyCandidateCollector(Config(), tmp_path)
    events = []
    for sample in samples(end=end):
        events.extend(collector.process(sample))
    events.extend(collector.finish())
    assert not any(e['details'].get('algorithm') == 'slow_descent_low_posture_review_v1' for e in events)


def test_standalone_engine_does_not_enable_review_recovery():
    assert DetectionEngine(Config()).review_fall is None
