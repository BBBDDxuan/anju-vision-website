"""Synthetic model observations only: no weights, video, network or training."""
from dataclasses import asdict, replace
import json
import math

import pytest

from ai_service.config import Config, FireConfig
from ai_service.fire_candidates import ReviewFireDetector
from ai_service.temporal import FireDetector
from ai_service.types import Flame, Signal


def flame(x=10.0, confidence=0.4, *, width=4.0, height=8.0):
    return Flame((x, 10.0, x + width, 10.0 + height), confidence)


def run(detector, frames, *, start=0.0, step=0.125):
    return [(start + i * step, signal)
            for i, detections in enumerate(frames)
            for signal in detector.update(detections, start + i * step)]


def warm(detector, *, start=0.0, detection=None):
    detection = flame() if detection is None else detection
    found = run(detector, [[detection] for _ in range(9)], start=start)
    assert len(found) == 1
    return found[0][1]


def test_default_low_score_eight_fps_is_review_only_and_json_serializable():
    config = Config().fire
    original = asdict(config)
    detector = ReviewFireDetector(config)
    frames = [[flame()] for _ in range(11)]
    assert run(FireDetector(config), frames) == []
    found = run(detector, frames)
    assert len(found) == 1
    t, signal = found[0]
    assert t == 1.0
    assert isinstance(signal, Signal)
    assert signal.event_type == "fire_detected"
    assert signal.track_id.startswith("review-fire-")
    assert signal.confidence == 0.4
    assert signal.details["algorithm"] == "review_persistent_flame_region_v1"
    assert signal.details["review_required"] is True
    assert signal.details["candidate_reason"] == "persistent_model_flame_for_visual_review"
    assert signal.details["frames"] == 9
    assert signal.details["hold_seconds"] == 1.0
    assert signal.details["hit_rate"] == 1.0
    assert signal.details["confidence_threshold"] == pytest.approx(0.39)
    assert "not_calibrated" in signal.details["confidence_kind"]
    assert detector.states[signal.track_id].box == flame().bbox
    assert json.loads(json.dumps(asdict(signal), allow_nan=False)) == asdict(signal)
    assert asdict(config) == original


def test_short_high_score_burst_cannot_replace_hold():
    frames = [[flame(confidence=0.9)] for _ in range(4)] + [[] for _ in range(20)]
    assert run(ReviewFireDetector(Config().fire), frames) == []
    assert run(FireDetector(Config().fire), frames) == []


def test_minimum_frames_and_hold_are_independent_requirements():
    detector = ReviewFireDetector(FireConfig(min_frames=12, hold_seconds=0.25))
    assert run(detector, [[flame()] for _ in range(11)]) == []
    signal, = detector.update([flame()], 11 / 8)
    assert signal.details["frames"] == 12
    detector = ReviewFireDetector(FireConfig(min_frames=2, hold_seconds=1.0))
    assert run(detector, [[flame()] for _ in range(8)]) == []
    assert len(detector.update([flame()], 1.0)) == 1


def test_brief_missing_frame_survives_but_strict_detector_still_resets():
    frames = [[] if i == 4 else [flame(confidence=0.9)] for i in range(11)]
    assert run(FireDetector(Config().fire), frames) == []
    detector = ReviewFireDetector(Config().fire)
    t, signal = run(detector, frames)[0]
    assert t == 1.0
    assert signal.details["frames"] == 8
    assert signal.details["missing_observations"] == 1
    assert signal.details["hit_rate"] == pytest.approx(8 / 9, abs=1e-6)


def test_seventy_percent_boundary_allows_dense_gapped_evidence():
    # At t=1.125: seven actual hits, ten observed frames, 1.125 seconds.
    frames = [[] if i in {1, 4, 7} else [flame()] for i in range(10)]
    found = run(ReviewFireDetector(Config().fire), frames)
    assert len(found) == 1
    t, signal = found[0]
    assert t == 1.125
    assert signal.details["frames"] == 7
    assert signal.details["observations"] == 10
    assert signal.details["hit_rate"] == 0.7


def test_gap_bound_is_inclusive_and_misses_do_not_extend_last_hit():
    detector = ReviewFireDetector(FireConfig(max_gap_seconds=0.25,
                                             hold_seconds=0.25, min_frames=3))
    assert detector.update([flame()], 0.0) == []
    key, = detector.states
    assert detector.update([], 0.125) == []
    assert detector.states[key].last_t == 0.0
    assert detector.update([flame()], 0.25) == []
    signal, = detector.update([flame()], 0.375)
    assert signal.track_id == key
    assert signal.details["frames"] == 3
    assert signal.details["hit_rate"] == 0.75


def test_gap_expires_from_last_hit_even_when_misses_keep_arriving():
    detector = ReviewFireDetector(Config().fire)
    assert detector.update([flame()], 0.0) == []
    key, = detector.states
    assert detector.update([], 0.125) == []
    assert detector.update([], 0.25) == []
    assert detector.states[key].last_t == 0.0
    assert detector.update([], 0.375) == []
    assert not detector.states
    assert detector.update([flame()], 0.5) == []
    new_key, = detector.states
    assert new_key != key
    assert detector.states[new_key].count == 1
    assert detector.states[new_key].since == 0.5


def test_no_update_gap_also_starts_a_fresh_track():
    detector = ReviewFireDetector(Config().fire)
    assert run(detector, [[flame()] for _ in range(8)]) == []
    old_ids = set(detector.states)
    assert detector.update([flame()], 1.25) == []
    assert old_ids.isdisjoint(detector.states)
    assert all(state.count == 1 for state in detector.states.values())


@pytest.mark.parametrize("period", [2, 4, 8])
def test_isolated_or_alternating_model_hits_are_not_persistence(period):
    frames = [[flame()] if i % period == 0 else [] for i in range(200)]
    assert run(ReviewFireDetector(Config().fire), frames) == []


def test_recent_hit_rate_rejects_sparse_tail_despite_dense_old_history():
    detector = ReviewFireDetector(FireConfig(hold_seconds=20.0))
    frames = [[flame()] if i < 64 or i % 2 == 0 else [] for i in range(163)]
    assert run(detector, frames) == []
    state, = detector.states.values()
    assert state.count >= detector.config.min_frames
    assert state.observations == 163
    assert len(state.recent_hits) == 64
    assert sum(state.recent_hits) / len(state.recent_hits) == 0.5


@pytest.mark.parametrize("confidence", [0.0, 0.2, 0.38, -1.0, 1.01,
                                         math.nan, math.inf, -math.inf, None, True])
def test_invalid_or_subthreshold_scores_never_create_tracks(confidence):
    detector = ReviewFireDetector(Config().fire)
    assert run(detector, [[flame(confidence=confidence)] for _ in range(20)]) == []
    assert not detector.states


def test_confidence_threshold_tracks_config_and_has_quarter_floor():
    config = FireConfig()
    threshold = max(0.25, config.confidence * 0.65)
    assert len(run(ReviewFireDetector(config), [[flame(confidence=threshold)]] * 9)) == 1
    low_config = replace(config, confidence=0.1)
    assert run(ReviewFireDetector(low_config), [[flame(confidence=0.249)]] * 20) == []
    assert len(run(ReviewFireDetector(low_config), [[flame(confidence=0.25)]] * 9)) == 1


def test_explicit_review_threshold_and_hold_stay_fixed_when_strict_gates_change():
    config = FireConfig(confidence=0.50, review_confidence=0.39, strict_hold_seconds=0.8,
                        confirmation_window_seconds=1.2)
    assert run(ReviewFireDetector(config), [[flame(confidence=0.38)]] * 12) == []
    t, signal = run(ReviewFireDetector(config), [[flame(confidence=0.39)]] * 12)[0]
    assert t == 1.0
    assert signal.details["confidence_threshold"] == 0.39


@pytest.mark.parametrize("step", [0.1, 0.2])
def test_strict_window_tolerates_one_missing_sample_and_small_region_motion(step):
    config = FireConfig(confidence=0.50, strict_hold_seconds=0.8, confirmation_window_seconds=1.2,
                        max_gap_seconds=0.75)
    frames = [[] if i == 2 else [flame(x=10 + i * 2, confidence=0.55)]
              for i in range(round(1.2 / step) + 1)]
    t, signal = run(FireDetector(config), frames, step=step)[0]
    assert t == pytest.approx(0.8 if step == 0.1 else 1.0)
    assert signal.details["frames"] >= 5


def test_strict_window_rejects_alternating_hits_and_obsolete_evidence():
    config = FireConfig(confidence=0.50, strict_hold_seconds=0.8, confirmation_window_seconds=1.2)
    hit = [flame(confidence=0.55)]
    assert run(FireDetector(config), [hit, []] * 16, step=0.2) == []
    detector = FireDetector(config)
    assert run(detector, [hit] * 4, step=0.2) == []
    assert run(detector, [hit] * 4, start=1.4, step=0.2) == []
    assert len(detector.update(hit, 2.2)) == 1


@pytest.mark.parametrize("box", [
    (0, 0, 0, 8), (0, 0, 4, 0), (4, 0, 0, 8), (0, 0, math.nan, 8),
    (0, 0, 4, math.inf), (0, 0, 4), (0, 0, 4, 8, 9), None,
    ("bad", 0, 4, 8), (-1e308, 0, 1e308, 8),
])
def test_invalid_boxes_are_misses_and_never_create_evidence(box):
    detector = ReviewFireDetector(Config().fire)
    assert run(detector, [[Flame(box, 0.9)]] * 20) == []
    assert not detector.states
    assert detector.update([flame()], 3.0) == []
    key, = detector.states
    assert detector.update([Flame(box, 0.9)], 3.125) == []
    assert detector.states[key].count == 1
    assert detector.states[key].last_t == 3.0
    assert detector.states[key].observations == 2


def test_small_relative_center_motion_can_match_without_iou():
    # Six pixels per step, bounded by .75 * sqrt(4**2 + 8**2).
    frames = [[flame(x=10.0 + i * 6, confidence=0.9)] for i in range(11)]
    detector = ReviewFireDetector(Config().fire)
    assert run(FireDetector(Config().fire), frames) == []
    found = run(detector, frames)
    assert len(found) == 1
    assert found[0][0] == 1.0
    key = found[0][1].track_id
    assert detector.states[key].count == 11
    assert detector.states[key].box == frames[-1][0].bbox


def test_far_regions_cannot_be_joined_into_a_candidate():
    frames = [[flame(x=10.0 + 100 * i)] for i in range(40)]
    detector = ReviewFireDetector(Config().fire)
    assert run(detector, frames) == []
    assert all(state.count == 1 for state in detector.states.values())


def test_same_center_extreme_size_changes_do_not_match():
    detector = ReviewFireDetector(Config().fire)
    small = Flame((48, 46, 52, 54), 0.4)
    large = Flame((30, 10, 70, 90), 0.4)
    assert detector.update([small], 0.0) == []
    key, = detector.states
    assert detector.update([large], 0.125) == []
    assert detector.states[key].count == 1
    assert len(detector.states) == 2


def test_many_boxes_in_one_frame_do_not_supply_many_hits():
    detector = ReviewFireDetector(Config().fire)
    for i in range(4):
        assert detector.update([flame()] * 25, i / 8) == []
        assert len(detector.states) == 25
        assert {state.count for state in detector.states.values()} == {i + 1}
    found = run(detector, [[flame()] * 25] * 7, start=0.5)
    assert len(found) == 1  # Shared spatial cooldown also deduplicates the tracks.


def test_one_detection_cannot_update_two_existing_tracks():
    detector = ReviewFireDetector(Config().fire)
    assert detector.update([flame(), flame()], 0.0) == []
    assert detector.update([flame()], 0.125) == []
    assert sorted(state.count for state in detector.states.values()) == [1, 2]
    assert sorted(state.observations for state in detector.states.values()) == [2, 2]


def test_reordered_detections_match_two_regions_one_to_one():
    detector = ReviewFireDetector(FireConfig(min_frames=3, hold_seconds=0.25))
    assert detector.update([flame(x=10), flame(x=100)], 0.0) == []
    first_boxes = {key: state.box for key, state in detector.states.items()}
    assert detector.update([flame(x=101), flame(x=11)], 0.125) == []
    signals = detector.update([flame(x=12), flame(x=102)], 0.25)
    assert len(signals) == 2
    assert {signal.track_id for signal in signals} == set(first_boxes)
    for signal in signals:
        state = detector.states[signal.track_id]
        assert state.box[0] == first_boxes[signal.track_id][0] + 2
        assert state.count == 3


@pytest.mark.parametrize("bad_time", [0.875, 0.5, math.nan, math.inf, -math.inf, None, True])
def test_duplicate_backward_or_invalid_update_discards_persistence(bad_time):
    detector = ReviewFireDetector(Config().fire)
    assert run(detector, [[flame()]] * 8) == []
    previous_ids = set(detector.states)
    assert detector.update([flame()], bad_time) == []
    assert not detector.states
    assert detector.update([flame()], 1.0) == []
    assert previous_ids.isdisjoint(detector.states)
    assert all(state.count == 1 for state in detector.states.values())


@pytest.mark.parametrize("bad_time", [1.0, 0.5, math.nan, math.inf])
def test_bad_update_time_cannot_erase_recent_alert_cooldown(bad_time):
    detector = ReviewFireDetector(Config().fire)
    warm(detector)
    assert detector.update([flame()], bad_time) == []
    assert not detector.states
    assert not detector.accept_strict(flame().bbox, 1.125)
    assert run(detector, [[flame()]] * 9, start=1.25) == []
    assert not detector.accept_strict(flame().bbox, 15.999)
    assert detector.accept_strict(flame().bbox, 16.0)


def test_backward_time_watermark_requires_reset_for_new_time_origin():
    detector = ReviewFireDetector(Config().fire)
    assert detector.update([flame()], 10.0) == []
    assert detector.update([flame()], 0.0) == []
    assert detector.update([flame()], 0.125) == []
    assert not detector.states
    assert not detector.accept_strict(flame().bbox, 0.25)
    detector.reset()
    assert detector.update([flame()], 0.0) == []
    assert len(detector.states) == 1


def test_strict_first_same_timestamp_preserves_strict_signal_fields():
    config = Config().fire
    strict = FireDetector(config)
    baseline = FireDetector(config)
    review = ReviewFireDetector(config)
    actual = []
    expected = []
    for i in range(30):
        detections, t = [flame(confidence=0.9)], i / 8
        expected.extend(baseline.update(detections, t))
        for signal in strict.update(detections, t):
            original = asdict(signal)
            if review.accept_strict(strict.states[signal.track_id].box, t):
                actual.append(signal)
            assert asdict(signal) == original
        actual.extend(review.update(detections, t))
    assert len(expected) == 1
    assert [asdict(signal) for signal in actual] == [asdict(signal) for signal in expected]
    assert actual[0].track_id == "fire-1"
    assert "review_required" not in actual[0].details
    assert len(review.states) == 1
    assert next(iter(review.states.values())).count == 30


def test_review_first_suppresses_later_strict_and_duplicate_review():
    detector = ReviewFireDetector(Config().fire)
    warm(detector)
    assert not detector.accept_strict(flame().bbox, 1.0)
    assert not detector.accept_strict(flame().bbox, 1.125)
    assert detector.update([flame(confidence=0.9)], 1.125) == []
    assert run(detector, [[flame()]] * 30, start=1.25) == []


def test_strict_gate_does_not_create_hits_and_spatial_cooldown_is_not_global():
    detector = ReviewFireDetector(Config().fire)
    assert detector.accept_strict(flame().bbox, 0.0)
    assert not detector.states
    assert not detector.accept_strict(flame(x=16).bbox, 0.0)  # Bounded center fallback.
    assert detector.accept_strict(flame(x=100).bbox, 0.0)
    assert not detector.states
    assert detector.update([flame()], 0.0) == []  # First update at gate time is valid.
    assert next(iter(detector.states.values())).count == 1
    assert not detector.accept_strict(flame().bbox, 14.999)
    assert detector.accept_strict(flame().bbox, 15.0)


def test_missing_region_retains_shared_cooldown_after_track_expires():
    detector = ReviewFireDetector(Config().fire)
    warm(detector)
    assert detector.update([], 1.375) == []
    assert not detector.states
    assert not detector.accept_strict(flame().bbox, 2.0)
    assert detector.accept_strict(flame(x=100).bbox, 2.0)
    assert detector.update([flame()], 2.0) == []
    assert run(detector, [[flame()]] * 9, start=2.125) == []


def test_cooldown_follows_small_motion_and_survives_time_discard():
    detector = ReviewFireDetector(Config().fire)
    found = run(detector, [[flame(x=10 + 6 * i)] for i in range(17)])
    assert len(found) == 1
    latest_box = flame(x=106).bbox
    assert detector.states[found[0][1].track_id].box == latest_box
    assert not detector.accept_strict(latest_box, 2.0)
    assert detector.update([flame(x=106)], 2.0) == []  # Duplicate clears tracks.
    assert not detector.states
    assert not detector.accept_strict(latest_box, 2.125)


def test_review_cooldown_expires_at_exact_boundary_without_rebuilding_history():
    detector = ReviewFireDetector(FireConfig(cooldown_seconds=1.0))
    found = run(detector, [[flame()]] * 17)
    assert [t for t, _ in found] == [1.0, 2.0]
    assert found[0][1].track_id == found[1][1].track_id


@pytest.mark.parametrize("box,t", [(None, 0.0), ((0, 0, 0, 4), 0.0),
                                  ((0, 0, 4, 8), math.nan),
                                  ((0, 0, 4, 8), math.inf)])
def test_invalid_strict_gate_does_not_reserve_a_region(box, t):
    detector = ReviewFireDetector(Config().fire)
    assert detector.accept_strict(box, t) is False
    assert not detector.recent_alerts
    assert detector.accept_strict(flame().bbox, 0.0)


def test_reset_clears_history_watermarks_and_cooldown_without_reusing_ids():
    detector = ReviewFireDetector(Config().fire)
    previous = warm(detector)
    detector.reset()
    assert not detector.states
    assert not detector.recent_alerts
    current = warm(detector)
    assert current.track_id != previous.track_id
    detector.reset()
    assert detector.accept_strict(flame().bbox, 0.0)
