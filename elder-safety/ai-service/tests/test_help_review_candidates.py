"""Review-only oblique gestures; synthetic negatives never assert human intent."""
from dataclasses import replace
from datetime import datetime, timezone
import math

import numpy as np
import pytest

from ai_service.config import Config
from ai_service.engine import DetectionEngine
from ai_service.help_candidates import ReviewHelpDetector
from ai_service.help_gesture import HelpConfig, HelpGestureDetector
from ai_service.types import Pose, Sample


def pose(t=0, *, wave=True):
    kp = np.zeros((17, 3), dtype=float)
    kp[:, 2] = 0.95
    kp[0, :2] = (100, 100)
    kp[5, :2], kp[6, :2] = (60, 150), (140, 150)
    kp[7, :2], kp[8, :2] = (30, 105), (170, 105)
    dx = 20 * math.sin(3 * math.pi * t) if wave else 0
    kp[9, :2], kp[10, :2] = (45 + dx, 60), (155 + dx, 60)
    kp[11, :2], kp[12, :2] = (70, 260), (130, 260)
    return Pose((0, 0, 200, 400), kp, 0.95)


def config():
    return HelpConfig(hold_seconds=0.5, min_frames=5, max_gap_seconds=0.5)


def feed(detector, factory=pose, times=None, track='p1'):
    times = [i / 8 for i in range(25)] if times is None else times
    return [s for t in times if (s := detector.update(track, factory(t), t))]


def test_bent_arms_with_waving_produce_review_candidate_but_strict_stays_strict():
    assert not feed(HelpGestureDetector(config()))
    signals = feed(ReviewHelpDetector(config()))
    assert len(signals) == 1
    assert signals[0].details['review_required'] is True
    assert signals[0].details['hold_seconds'] >= 0.75
    assert signals[0].event_type == 'help_requested'


@pytest.mark.parametrize('kind', ['static', 'one_hand', 'head_touch', 'below_head', 'uncertain', 'jitter', 'translate'])
def test_common_negative_gestures_do_not_propose_help(kind):
    def negative(t):
        p = pose(t, wave=kind not in ('static', 'translate', 'jitter'))
        if kind == 'one_hand':
            p.keypoints[10, 1] = 300
        if kind == 'head_touch':
            p.keypoints[9, :2], p.keypoints[10, :2] = (85, 80), (115, 80)
        if kind == 'below_head':
            p.keypoints[[9, 10], 1] = 125
        if kind == 'uncertain':
            p.keypoints[9, 2] = 0.1
        if kind == 'jitter':
            p.keypoints[[9, 10], 0] += 2 * math.sin(3 * math.pi * t)
        if kind == 'translate':
            p.keypoints[:, 0] += 40 * math.sin(3 * math.pi * t)
        return p
    assert not feed(ReviewHelpDetector(config()), negative)


def test_short_gesture_and_separate_people_cannot_combine_motion():
    d = ReviewHelpDetector(config())
    assert not feed(d, times=[0, .125, .25, .375, .5])
    d.retain(set())
    assert not feed(d, times=[.625, .75, .875, 1, 1.125], track='p2')


@pytest.mark.parametrize('break_kind', ['gap', 'rewind', 'lowered', 'nan', 'reset'])
def test_boundaries_discard_wave_history(break_kind):
    d = ReviewHelpDetector(config())
    assert not feed(d, times=[0, .125, .25, .375, .5])
    start = .625
    if break_kind == 'gap':
        start = 2
    elif break_kind == 'rewind':
        start = .125
    elif break_kind == 'reset':
        d.reset()
    else:
        p = pose(.55)
        if break_kind == 'lowered':
            p.keypoints[[9, 10], 1] = 300
        else:
            p.keypoints[9, 0] = np.nan
        assert d.update('p1', p, .55) is None
    assert not feed(d, times=[start + i / 8 for i in range(5)])


def test_strict_and_review_share_cooldown():
    d = ReviewHelpDetector(config())
    assert d.accept_strict('p1', 0)
    assert not feed(d)
    d = ReviewHelpDetector(config())
    assert feed(d)
    assert not d.accept_strict('p1', 3)


def test_engine_opt_in_is_required_and_reset_clears_review_state():
    c = Config()
    c.help = config()
    strict, reviewed = DetectionEngine(c), DetectionEngine(c, allow_review_candidates=True)
    strict_signals, review_signals = [], []
    for i in range(25):
        t = i / 8
        s = Sample(image=np.zeros((400, 200, 3), dtype=np.uint8), t=t,
                   occurred_at=datetime.now(timezone.utc), poses=[pose(t)])
        strict_signals.extend(strict.process(s))
        review_signals.extend(reviewed.process(s))
    assert not [s for s in strict_signals if s.event_type == 'help_requested']
    assert len([s for s in review_signals if s.event_type == 'help_requested']) == 1
    reviewed.process(Sample(image=np.zeros((400, 200, 3), dtype=np.uint8), t=0,
                            session=2, occurred_at=datetime.now(timezone.utc)))
    assert not reviewed.review_help.states


@pytest.mark.parametrize('face_ok', [True, False])
def test_optional_face_gate_still_requires_reliable_face_points(face_ok):
    d = ReviewHelpDetector(replace(config(), require_facing_camera=True))
    def facing(t):
        p = pose(t)
        p.keypoints[1, :2], p.keypoints[2, :2] = (90, 90), (110, 90)
        if not face_ok:
            p.keypoints[1, 2] = 0.1
        return p
    assert bool(feed(d, facing)) is face_ok


def test_collector_explicit_reset_discards_review_help_history(tmp_path):
    from ai_service.safety_candidates import SafetyCandidateCollector
    c = Config()
    c.help = config()
    collector = SafetyCandidateCollector(c, tmp_path)
    for i in range(4):
        t = i / 8
        collector.process(Sample(image=np.zeros((400, 200, 3), dtype=np.uint8), t=t,
                          occurred_at=datetime.now(timezone.utc), poses=[pose(t)]))
    assert collector.engine.review_help.states
    collector.reset()
    assert not collector.engine.review_help.states
