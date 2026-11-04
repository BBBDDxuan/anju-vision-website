"""Cross-detector safeguards for extra review candidates and real evidence."""
from datetime import datetime, timezone
import numpy as np

from ai_service.config import Config, FireConfig
from ai_service.engine import DetectionEngine
from ai_service.safety_candidates import SafetyCandidateCollector
from ai_service.types import Flame, Sample


def sample(t, score=.45, *, fresh=True, session=0):
    return Sample(image=np.zeros((100, 200, 3), np.uint8), t=t,
                  occurred_at=datetime.now(timezone.utc), session=session,
                  flames=[Flame((30, 20, 50, 50), score)], flames_observed=fresh)


def config():
    c = Config()
    c.fire = FireConfig(confidence=.6, hold_seconds=1, min_frames=5, max_gap_seconds=.75)
    return c


def test_low_score_review_candidate_never_enters_standalone_engine():
    strict = DetectionEngine(config())
    reviewed = DetectionEngine(config(), allow_review_candidates=True)
    before, after = [], []
    for i in range(17):
        s = sample(i / 8)
        before.extend(strict.process(s))
        after.extend(reviewed.process(s))
    assert not before
    assert len(after) == 1
    assert after[0].details['review_required'] is True


def test_cached_flame_overlays_cannot_supply_review_hits():
    d = DetectionEngine(config(), allow_review_candidates=True)
    found = []
    for i in range(17):
        found.extend(d.process(sample(i / 8, fresh=i == 0)))
    assert not found
    assert sum(s.count for s in d.review_fire.states.values()) == 1


def test_strict_and_review_paths_emit_one_region_with_original_strict_metadata():
    d = DetectionEngine(config(), allow_review_candidates=True)
    found = [v for i in range(25) for v in d.process(sample(i / 8, .9))]
    assert len(found) == 1
    assert found[0].details['algorithm'] == 'persistent_flame_region_v1'


def test_review_first_suppresses_later_strict_duplicate():
    d = DetectionEngine(config(), allow_review_candidates=True)
    found = [v for i in range(33) for v in d.process(sample(i / 8, .45 if i < 10 else .9))]
    assert len(found) == 1
    assert found[0].details['review_required'] is True


def test_review_fire_evidence_has_subject_hint_and_explicit_reset(tmp_path):
    collector = SafetyCandidateCollector(config(), tmp_path)
    ready = []
    for i in range(18):
        ready.extend(collector.process(sample(i / 8)))
    ready.extend(collector.finish())
    assert len(ready) == 1
    c = ready[0]
    assert c['subject_bbox_hint'] == [.15, .2, .25, .5]
    assert c['details']['review_required'] is True
    assert c['t'] == 1.0
    assert len({f['pts_ms'] for f in c['frames']}) >= 2
    collector.reset()
    assert not collector.engine.review_fire.states
    assert not collector.engine.review_fire.recent_alerts


def test_session_change_and_rewind_cannot_combine_fire_evidence():
    d = DetectionEngine(config(), allow_review_candidates=True)
    for i in range(5):
        assert not d.process(sample(i / 8))
    for i in range(5):
        assert not d.process(sample(i / 8, session=2))
