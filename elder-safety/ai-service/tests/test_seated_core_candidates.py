"""Review candidates need observed motion, but not straight legs or floor proof."""
from datetime import datetime, timedelta, timezone
import numpy as np
import pytest

from ai_service.config import Config
from ai_service.engine import DetectionEngine
from ai_service.types import Pose, Sample


ALGORITHM = 'seated_core_descent_review_v1'


def pose(shoulder_y=150, hip_y=245, *, feet=True, core=True):
    k = np.zeros((17, 3), float)
    for ids, xy in [([5, 6], (200, shoulder_y)), ([11, 12], (200, hip_y)),
                    ([13, 14], (280, 250)), ([15, 16], (280, 370))]:
        for index, offset in zip(ids, [-5, 5]):
            k[index] = [xy[0] + offset, xy[1], .98]
    if not feet:
        k[[15, 16], 2] = 0
    if not core:
        k[5, 2] = 0
    return Pose((150, 110, 320, 390), k, .98)


def sample(t, current, session=0):
    return Sample(np.zeros((480, 720, 3), np.uint8), t,
                  datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=t),
                  poses=[] if current is None else [current], session=session, flames_observed=False)


def run_tail(tail, *, strict=False):
    engine = DetectionEngine(Config(), allow_review_candidates=not strict)
    events = []
    for i in range(6):
        events.extend(engine.process(sample(i / 10, pose())))
    for i, p in enumerate(tail):
        events.extend(engine.process(sample(.6 + i / 10, p)))
    return engine, [e for e in events if e.details.get('algorithm') == ALGORITHM]


@pytest.mark.parametrize('feet', [True, False])
def test_folded_seated_descent_proposes_review_without_whole_body_tail(feet):
    _, events = run_tail([pose(220, 280, feet=feet)] * 4)
    assert len(events) == 1
    assert events[0].details['review_required']
    assert events[0].details['whole_body_required'] is False
    assert events[0].details['floor_contact_observed'] is False


def test_missing_detection_cannot_complete_a_three_frame_motion_tail():
    _, events = run_tail([pose(220, 280, feet=False), None, pose(220, 280, feet=False)])
    assert not events


@pytest.mark.parametrize('tail', [
    [pose()] * 5,
    [pose(220, 245)] * 5,  # shoulder-only bow
    [pose(150, 280)] * 5,  # hip motion alone
    [pose(220, 280, core=False)] * 5,
    [pose(220, 280)] * 2,  # short single twitch is insufficient
])
def test_stationary_incomplete_or_weak_core_evidence_is_not_a_candidate(tail):
    _, events = run_tail(tail)
    assert not events


def test_standalone_strict_engine_stays_unchanged():
    engine, events = run_tail([pose(220, 280, feet=False)] * 4, strict=True)
    assert engine.review_fall is None and not events
