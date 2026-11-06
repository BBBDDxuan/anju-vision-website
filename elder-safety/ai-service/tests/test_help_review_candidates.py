"""Body-relative motion candidates; synthetic poses never establish help intent."""
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


def pose(t=0, *, wave=True, both=False):
    kp = np.zeros((17, 3), dtype=float)
    kp[:, 2] = 0.95
    kp[0, :2] = (300, 245)
    kp[5, :2], kp[6, :2] = (250, 300), (350, 300)
    kp[11, :2], kp[12, :2] = (260, 440), (340, 440)
    kp[7, :2], kp[9, :2] = (230, 365), (240, 420)
    dx = 65 * math.sin(2 * math.pi * t) if wave else 0
    kp[8, :2], kp[10, :2] = (395, 160), (370 + dx, 110)
    if both:
        kp[7, :2], kp[9, :2] = (205, 160), (230 - dx, 110)
    return Pose((100, 50, 500, 550), kp, 0.95)


def config():
    return HelpConfig(hold_seconds=0.5, min_frames=5, max_gap_seconds=0.5)


def feed(detector, factory=pose, times=None, track='p1'):
    times = [i / 8 for i in range(25)] if times is None else times
    return [s for t in times if (s := detector.update(track, factory(t), t))]


@pytest.mark.parametrize('both', [False, True])
def test_bent_arms_with_waving_produce_review_candidate_but_strict_stays_strict(both):
    factory = lambda t: pose(t, both=both)
    assert not feed(HelpGestureDetector(config()), factory)
    signals = feed(ReviewHelpDetector(config()), factory)
    assert len(signals) == 1
    assert signals[0].details['review_required'] is True
    assert signals[0].details['hold_seconds'] >= 1.0
    assert signals[0].event_type == 'help_requested'


@pytest.mark.parametrize('kind', ['static', 'head_touch', 'below_head', 'uncertain', 'jitter', 'translate'])
def test_common_negative_gestures_do_not_propose_help(kind):
    def negative(t):
        p = pose(t, wave=kind not in ('static', 'translate', 'jitter'))
        if kind == 'head_touch':
            p.keypoints[10, :2] = (310, 240)
        if kind == 'below_head':
            p.keypoints[10, 1] = 290
        if kind == 'uncertain':
            p.keypoints[10, 2] = 0.1
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


@pytest.mark.parametrize('break_kind', ['gap', 'rewind', 'nan', 'reset', 'low_head'])
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
        if break_kind == 'low_head':
            p.keypoints[0, 2] = 0.1
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
def test_review_head_geometry_does_not_require_strict_front_facing_eye_gate(face_ok):
    d = ReviewHelpDetector(replace(config(), require_facing_camera=True))
    def facing(t):
        p = pose(t)
        p.keypoints[1, :2], p.keypoints[2, :2] = (290, 235), (310, 235)
        if not face_ok:
            p.keypoints[1, 2] = 0.1
        return p
    assert feed(d, facing)


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


def test_translation_scale_and_sideways_body_preserve_single_arm_candidate():
    reference = feed(ReviewHelpDetector(config()))
    def sideways(t):
        p = pose(t)
        p.keypoints[:, :2] = p.keypoints[:, :2] @ np.array([[0, 1], [-1, 0]]) * 1.5 + [100, 1000]
        return p
    transformed = feed(ReviewHelpDetector(config()), sideways)
    assert transformed and reference
    assert transformed[0].details['active_hands'] == reference[0].details['active_hands'] == ['right']
    assert transformed[0].details['hold_seconds'] == reference[0].details['hold_seconds']


def test_one_missing_wrist_does_not_disable_the_other_reliable_waving_arm():
    def one_visible(t):
        p = pose(t)
        p.keypoints[9, 2] = .1
        return p
    assert feed(ReviewHelpDetector(config()), one_visible)


def test_intermittent_lowering_is_tolerated_but_current_low_hand_cannot_trigger():
    d = ReviewHelpDetector(config())
    def intermittent(t):
        p = pose(t)
        if .25 <= t <= .5:
            p.keypoints[10, :2] = (370, 420)
        return p
    assert feed(d, intermittent)
    d = ReviewHelpDetector(config())
    assert not feed(d, times=[0, .125, .25, .375, .5])
    def lowered(t):
        p = pose(t)
        p.keypoints[10, :2] = (370, 420)
        return p
    assert not feed(d, lowered, times=[.625 + i / 8 for i in range(25)])


def test_positive_motion_reaches_existing_candidate_manifest_schema(tmp_path):
    from pathlib import Path
    from ai_service.safety_candidates import SafetyCandidateCollector
    c = Config()
    c.help = config()
    collector = SafetyCandidateCollector(c, tmp_path)
    ready = []
    for i in range(33):
        t = i / 8
        ready.extend(collector.process(Sample(
            image=np.zeros((600, 600, 3), dtype=np.uint8), t=t,
            occurred_at=datetime.now(timezone.utc), poses=[pose(t)])))
    ready.extend(collector.finish())
    candidates = [x for x in ready if x['event_type'] == 'help_requested']
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate['details']['review_required'] is True
    assert candidate['details']['active_hands'] == ['right']
    assert 2 <= len(candidate['frames']) <= 12
    assert all(Path(f['path']).is_file() for f in candidate['frames'])
    assert len(candidate['subject_bbox_hint']) == 4
