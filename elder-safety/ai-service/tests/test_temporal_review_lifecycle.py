"""Real-time candidate timing and bounded follow-up; no models or cloud."""
import numpy as np
import pytest

from ai_service.config import Config
from ai_service.engine import DetectionEngine
from ai_service.safety_candidates import SafetyCandidateCollector
from test_seated_core_candidates import pose, sample


def config():
    c = Config()
    c.fall.observation_seconds = 2.5
    return c


def feed(engine, fps, seconds, action):
    events = []
    for i in range(round(seconds * fps) + 1):
        t = i / fps
        events.extend((t, e) for e in engine.process(sample(t, action(t))))
    return events


@pytest.mark.parametrize('fps', [5, 7, 10, 12, 15, 30])
def test_same_seated_descent_survives_different_frame_rates(fps):
    e = DetectionEngine(config(), allow_review_candidates=True)
    events = feed(e, fps, 3, lambda t: pose() if t < 1 else pose(220, 280, feet=False))
    assert len(events) == 1
    assert events[0][1].details['algorithm'] == 'seated_core_descent_review_v1'
    assert 1.2 - 1e-8 <= events[0][0] <= 1.6 + 1e-8


def test_observed_recovery_allows_new_event_before_fifteen_seconds():
    e = DetectionEngine(config(), allow_review_candidates=True)
    def action(t):
        if .6 <= t <= 1.1:
            return pose(220, 280, feet=False)
        return pose(240, 300, feet=False) if t >= 4 else pose()
    events = feed(e, 10, 8, action)
    assert len(events) == 2
    assert events[0][0] < 2 and events[1][0] >= 4
    assert events[0][1].details['review_episode'] != events[1][1].details['review_episode']


def test_further_observed_descent_can_update_once_without_normal_recovery():
    e = DetectionEngine(config(), allow_review_candidates=True)
    events = feed(e, 10, 8, lambda t: pose() if t < .6 else
                  pose(220, 280, feet=False) if t < 3 else pose(290, 320, feet=False))
    assert len(events) == 2
    assert events[1][1].details['review_stage'] == 'progression'
    assert events[0][1].details['review_episode'] == events[1][1].details['review_episode']


def test_missing_core_does_not_supply_progression():
    e = DetectionEngine(config(), allow_review_candidates=True)
    events = feed(e, 10, 8, lambda t: pose() if t < .6 else
                  pose(220, 280, feet=False) if t < 3 else pose(290, 320, feet=False, core=False))
    assert len(events) == 1


def test_missing_person_breaks_the_observed_progression_window():
    e = DetectionEngine(config(), allow_review_candidates=True)
    def action(t):
        if t < .6:
            return pose()
        if t < 3:
            return pose(220, 280, feet=False)
        if abs(t - 3.1) < 1e-8:
            return None
        return pose(290, 320, feet=False)
    events = feed(e, 10, 3.3, action)
    assert len(events) == 1
    followup = e.process(sample(3.4, pose(290, 320, feet=False)))
    assert len(followup) == 1
    assert followup[0].details['review_stage'] == 'progression'


def test_adaptive_evidence_covers_late_descent_with_twelve_real_frames(tmp_path):
    collector = SafetyCandidateCollector(config(), tmp_path)
    completed = []
    for i in range(91):
        t = i / 10
        # Initial descent then continuing motion; the terminal pose appears at 4s.
        if t < .6:
            current = pose()
        else:
            progress = min(1, (t - .6) / 3.4)
            current = pose(220 + 70 * progress, 280 + 40 * progress, feet=False)
        completed.extend(collector.process(sample(t, current)))
    completed.extend(collector.finish())
    assert len(completed) == 1  # In-flight progression updates the same evidence.
    event = completed[0]
    assert len(event['frames']) <= 12
    times = [f['pts_ms'] for f in event['frames']]
    assert times == sorted(set(times))
    assert max(times) >= 4000
    assert max(times) <= (event['t'] + 5) * 1000


def test_adaptive_evidence_does_not_extend_past_five_seconds(tmp_path):
    collector = SafetyCandidateCollector(config(), tmp_path)
    completed = []
    for i in range(101):
        t = i / 10
        current = pose() if t < .6 else pose(230 + 4 * np.sin(t * 6), 285 + 4 * np.sin(t * 6), feet=False)
        completed.extend(collector.process(sample(t, current)))
    assert completed
    assert len(completed[0]['frames']) <= 12
    assert max(f['pts_ms'] for f in completed[0]['frames']) <= (completed[0]['t'] + 5) * 1000


def test_merged_progression_keeps_one_later_followup_available(tmp_path):
    collector = SafetyCandidateCollector(config(), tmp_path)
    events = []
    for i in range(101):
        t = i / 10
        if t < .6:
            current = pose()
        elif t < 7:
            progress = min(1, (t - .6) / 3.4)
            current = pose(220 + 70 * progress, 280 + 40 * progress, feet=False)
        else:
            current = pose(360, 355, feet=False)
        events.extend(collector.process(sample(t, current)))
    events.extend(collector.finish())
    assert len(events) == 2
    assert 'progression_evidence' in events[0]['details']
    assert events[1]['details']['review_stage'] == 'progression'
    assert events[1]['t'] >= 7
    assert events[0]['details']['review_episode'] == events[1]['details']['review_episode']


def test_session_reset_does_not_reuse_an_old_episode_identity():
    engine = DetectionEngine(config(), allow_review_candidates=True)
    identities = []
    for session in [0, 1]:
        for i in range(21):
            t = i / 10
            current = pose() if t < .6 else pose(220, 280, feet=False)
            identities.extend(e.details['review_episode'] for e in engine.process(sample(t, current, session)))
    assert len(identities) == 2 and identities[0] != identities[1]
