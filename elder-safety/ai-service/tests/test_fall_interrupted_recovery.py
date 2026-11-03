"""Interrupted inverted-pose evidence is review-only, never a bridged fall."""
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import pytest

from ai_service.config import Config
from ai_service.engine import DetectionEngine
from ai_service.types import Pose, Sample


def inverted(*, weak=False, offset=0.):
    kp = np.full((17, 3), .98)
    kp[:, :2] = (250. + offset, 200.)
    kp[[5, 6], :2] = [(240. + offset, 280.), (260. + offset, 280.)]
    kp[[11, 12], :2] = [(240. + offset, 210.), (260. + offset, 210.)]
    kp[[15, 16], :2] = [(240. + offset, 120.), (260. + offset, 120.)]
    if weak:
        kp[[5, 6, 11, 12, 15, 16], 2] = .1
    return Pose((190. + offset, 100., 350. + offset, 400.), kp, .98)


def sample(t, poses=(), session=0):
    return Sample(np.zeros((2, 2, 3), np.uint8), t, datetime.now(timezone.utc),
                  poses=list(poses), session=session, flames_observed=False)


def engine(review=True):
    c = Config()
    c.fall.observation_seconds = 2.5
    c.fall.max_gap_seconds = c.tracking.max_gap_seconds = .5
    return DetectionEngine(c, allow_review_candidates=review)


def interrupted(e, *, kind=None):
    found = []
    ids = []
    for i in range(18):
        t = i / 10
        poses = [inverted()] if i in (0, 5, 15, 16, 17) else []
        if i in (9, 10):
            poses = [inverted(weak=True)]
        if kind == 'no_bridge_box' and i in (9, 10):
            poses = []
        if kind == 'upright' and i == 10:
            p = inverted()
            p.keypoints[[5, 6], 1] = 120.
            p.keypoints[[15, 16], 1] = 390.
            poses = [p]
        if kind == 'bad_feet' and i in (15, 16, 17):
            poses[0].keypoints[15, 2] = .1
        if kind == 'different_person' and i >= 15:
            poses = [inverted(offset=500.)]
        if kind == 'malformed' and i == 10:
            poses = [replace(inverted(), keypoints=np.zeros((16, 3)))]
        if kind == 'overlap_ambiguity' and i == 10:
            poses = [inverted(weak=True), inverted(offset=5.)]
        session = 1 if kind == 'session' and i >= 10 else 0
        found.extend(e.process(sample(t, poses, session)))
        ids.append(list(e.tracker.tracks))
    return found, ids


def test_sparse_inverted_pose_recovers_a_review_candidate_on_same_track():
    found, _ = interrupted(engine())
    assert len(found) == 1
    d = found[0].details
    assert d['algorithm'] == 'interrupted_inverted_pose_review_v1'
    assert d['fall_transition_observed'] is False
    assert d['review_required'] is True
    assert d['reliable_frames'] >= 5
    assert d['consecutive_reliable_frames'] >= 3
    assert d['largest_observation_gap_seconds'] == pytest.approx(1.)
    assert 'hold_seconds' not in d  # Missing frames are not a continuous hold.


def test_interrupted_recovery_never_arms_or_emits_standalone_alarm():
    e = engine(False)
    assert not interrupted(e)[0]
    assert not any(s.armed_at is not None for s in e.fall.states.values())


@pytest.mark.parametrize('kind', ['no_bridge_box', 'upright', 'bad_feet',
    'different_person', 'malformed', 'overlap_ambiguity', 'session'])
def test_recovery_rejects_invalid_identity_posture_or_stream(kind):
    assert not interrupted(engine(), kind=kind)[0]


def test_isolated_inverted_observations_without_reliable_tail_do_not_propose():
    e = engine()
    for i in range(23):
        poses = [inverted()] if i % 5 == 0 else [inverted(weak=True)]
        assert not e.process(sample(i / 10, poses))


def test_normal_inverted_hold_keeps_existing_candidate_and_cooldown():
    e = engine()
    found = []
    for i in range(80):
        found.extend(e.process(sample(i / 10, [inverted()])))
    assert len(found) == 1
    assert found[0].details['algorithm'] == 'sustained_prone_review_v1'


def test_continuous_inverted_projection_is_not_an_upright_standing_reference():
    e = engine(False)
    for i in range(21):
        assert not e.process(sample(i / 10, [inverted()]))
    assert e.fall.states
    assert all(s.armed_at is None for s in e.fall.states.values())
