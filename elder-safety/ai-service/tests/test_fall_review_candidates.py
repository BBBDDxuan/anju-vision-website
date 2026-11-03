"""Review-only fall proposals: geometry, identity, negatives and evidence gate."""
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import pytest

from ai_service.config import Config
from ai_service.engine import DetectionEngine
from ai_service.safety_candidates import SafetyCandidateCollector
from ai_service.types import Pose, Sample


def pose(p=0., *, tilt=False, feet_move=True, hidden_feet=False):
    kp = np.zeros((17, 3), dtype=float)
    shoulder = (250., 180.+120*p)
    hip = (250.+(65*p if tilt else 0), 260.+110*p)
    for left, right, xy in ((5, 6, shoulder), (11, 12, hip)):
        for i, offset in ((left, -10), (right, 10)):
            kp[i] = [xy[0]+offset, xy[1], .98]
    # In a side collapse the shoulder descends farther than the hip.
    if tilt:
        kp[[5, 6], 1] += 45*p
    for i in (15, 16):
        kp[i] = [250., 390.+(80*p if feet_move else 0), .1 if hidden_feet else .98]
    return Pose((190., 100.+160*p, 350., 400.+70*p), kp, .98)


def sample(t, p=None, session=0):
    return Sample(np.zeros((540,640,3),np.uint8), t, datetime.now(timezone.utc),
                  poses=[] if p is None else [p], session=session, flames_observed=False)


def standing(engine):
    for i in range(11):
        assert not engine.process(sample(i*.1, pose()))


def fall_signals(engine, **kwargs):
    return [s for i in range(1,4) for s in engine.process(sample(1+i*.1,pose(i/3,**kwargs)))
            if s.event_type=='suspected_fall']


def test_forward_collapse_goes_to_review_but_never_standalone_alarm():
    standalone=DetectionEngine(Config());standing(standalone)
    assert not fall_signals(standalone)
    engine=DetectionEngine(Config(),allow_review_candidates=True);standing(engine)
    signals=fall_signals(engine)
    assert len(signals)==1
    assert signals[0].details['review_required'] is True
    assert signals[0].details['candidate_reason']=='foreshortened_forward_descent'
    assert signals[0].details['torso_angle_deg']==0


def test_cropped_tilt_can_propose_before_pose_disappears():
    engine=DetectionEngine(Config(),allow_review_candidates=True);standing(engine)
    signals=fall_signals(engine,tilt=True,hidden_feet=True)
    assert len(signals)==1
    assert signals[0].details['candidate_reason']=='rapid_tilt_with_body_compression'
    assert signals[0].details['foot_drop_normalized'] is None
    assert not engine.process(sample(1.4))
    assert not engine.review_fall.states


@pytest.mark.parametrize('mode',['squat','bend','walk','slow','feet_unreliable'])
def test_nonfalls_do_not_propose(mode):
    engine=DetectionEngine(Config(),allow_review_candidates=True);standing(engine)
    signals=[]
    for i in range(1,16):
        progress=min(i/3,1)
        p=pose(progress,feet_move=False)
        if mode=='bend':
            p.keypoints[[11,12],1]=260
        elif mode=='walk':
            p=pose();p.keypoints[:,1]+=100*progress
            p=replace(p,bbox=(190,100+100*progress,350,400+100*progress))
        elif mode=='slow':
            p=pose(i/15)
        elif mode=='feet_unreliable':
            p=pose(progress,hidden_feet=True)
        signals.extend(engine.process(sample(1+i*.1,p)))
    assert not signals


@pytest.mark.parametrize('break_kind',['missing','invalid','session','gap','rewind','new_person'])
def test_no_collapse_history_crosses_identity_or_stream_boundaries(break_kind):
    engine=DetectionEngine(Config(),allow_review_candidates=True);standing(engine)
    engine.process(sample(1.1,pose(1/3)))
    p=pose(2/3)
    if break_kind=='missing':
        engine.process(sample(1.2))
    elif break_kind=='invalid':
        p.keypoints[11,2]=.1;engine.process(sample(1.2,p))
    elif break_kind=='session':
        engine.process(sample(1.2,p,session=1))
    elif break_kind=='gap':
        engine.process(sample(2.,p))
    elif break_kind=='rewind':
        engine.process(sample(.5,p))
    else:
        p=replace(p,bbox=(450,240,610,480));p.keypoints[:,0]+=260
        engine.process(sample(1.2,p))
    if break_kind in {'session','gap','rewind','new_person'}:
        assert not engine.process(sample(2.1 if break_kind=='gap' else 1.3,pose(1),session=1 if break_kind=='session' else 0))
    else:
        assert not engine.process(sample(1.3,pose(1)))


def test_collector_publishes_multiframe_review_metadata_and_reset_clears_state(tmp_path):
    collector=SafetyCandidateCollector(Config(),tmp_path)
    for i in range(11):assert not collector.process(sample(i*.1,pose()))
    ready=[]
    for i in range(1,4):ready+=collector.process(sample(1+i*.1,pose(i/3)))
    ready+=collector.finish()
    assert len(ready)==1
    assert ready[0]['details']['review_required'] is True
    assert len(ready[0]['frames'])>=2
    collector.reset()
    assert not collector.engine.review_fall.states


def test_early_and_strict_candidates_share_cooldown():
    engine=DetectionEngine(Config(),allow_review_candidates=True);standing(engine)
    # Trigger the new branch, then feed a normal sustained horizontal fall.
    assert len(fall_signals(engine,tilt=True))==1
    assert not engine.review_fall.accept_strict('person-1',2.)
    assert engine.review_fall.accept_strict('person-1',20.)


def collapse_pose(shoulder, hip, feet, *, height=210., valid_feet=True):
    """Normalized, non-video-specific trajectories for the support guard."""
    kp = np.zeros((17, 3), dtype=float)
    for pair, point in (((5, 6), shoulder), ((11, 12), hip), ((15, 16), feet)):
        for i, offset in zip(pair, (-10., 10.)):
            kp[i] = [point[0] + offset, point[1], .98]
    if not valid_feet:
        kp[[15, 16], 2] = .1
    return Pose((190., 100., 350., 100. + height), kp, .98)


def propose(shoulder, hip, feet, **kwargs):
    from ai_service.fall_candidates import ReviewFallDetector
    detector = ReviewFallDetector(Config().fall)
    detector.update('person', pose(), 0., armed=True)
    return detector.update('person', collapse_pose(shoulder, hip, feet, **kwargs),
                           .3, armed=True)


def test_lateral_rotation_requires_independent_support_loss():
    signal = propose((250., 245.), (310., 285.), (250., 390.))
    assert signal.details['candidate_reason'] == 'rapid_lateral_rotation_and_body_collapse'
    assert signal.details['review_required'] is True
    # Same descent/rotation, with hips remaining over shifted feet: kneel/bend.
    assert propose((250., 245.), (310., 285.), (310., 390.)) is None
    assert propose((250., 245.), (310., 285.), (250., 390.), valid_feet=False) is None


def test_upward_hip_projection_requires_foot_lift_not_ordinary_bowing():
    assert propose((260., 265.), (310., 230.), (250., 390.)) is None
    signal = propose((260., 265.), (310., 230.), (250., 330.))
    assert signal.details['candidate_reason'] == 'rapid_lateral_rotation_and_body_collapse'
    assert signal.details['hip_drop_normalized'] < 0


def test_projected_vertical_descent_uses_hip_motion_relative_to_feet():
    signal = propose((290., 250.), (320., 320.), (250., 390.))
    assert signal.details['candidate_reason'] == 'rapid_descent_outside_foot_support'
    assert signal.details['torso_angle_deg'] < 30
    assert propose((290., 250.), (320., 320.), (320., 390.)) is None


def lying_pose():
    return collapse_pose((330., 240.), (260., 245.), (190., 250.), height=100.)


def test_prone_after_lost_transition_is_review_only_and_one_per_hold():
    standalone = DetectionEngine(Config())
    review = DetectionEngine(Config(), allow_review_candidates=True)
    found = []
    for i in range(201):
        assert not standalone.process(sample(i * .1, lying_pose()))
        found.extend(review.process(sample(i * .1, lying_pose())))
    assert len(found) == 1
    signal = found[0]
    assert signal.details['candidate_reason'] == 'sustained_prone_without_observed_transition'
    assert signal.details['review_required'] is True
    assert signal.details['fall_transition_observed'] is False
    assert signal.details['hold_seconds'] >= .8


@pytest.mark.parametrize('interruption', ['missing', 'bad_core', 'bad_feet', 'upright', 'gap', 'session'])
def test_prone_hold_must_restart_after_interruption(interruption):
    e = DetectionEngine(Config(), allow_review_candidates=True)
    for i in range(6):
        assert not e.process(sample(i * .1, lying_pose()))
    p = lying_pose()
    session, t = 0, .6
    if interruption == 'missing': p = None
    elif interruption == 'bad_core': p.keypoints[5, 2] = .1
    elif interruption == 'bad_feet': p.keypoints[15, 2] = .1
    elif interruption == 'upright': p = pose()
    elif interruption == 'gap': t = 2.
    elif interruption == 'session': session = 1
    assert not e.process(sample(t, p, session=session))
    for i in range(1, 7):
        assert not e.process(sample(t + i * .1, lying_pose(), session=session))


def test_bowing_or_folded_body_is_not_whole_body_prone():
    from ai_service.fall_candidates import ReviewFallDetector
    for p in (collapse_pose((330., 240.), (260., 245.), (260., 390.), height=100.),
              collapse_pose((330., 240.), (260., 245.), (350., 250.), height=100.)):
        d = ReviewFallDetector(Config().fall)
        for i in range(20):
            assert d.update('p', p, i * .1, armed=False) is None


def test_oblique_descent_speed_includes_horizontal_hip_motion():
    from ai_service.fall_candidates import ReviewFallDetector
    detector = ReviewFallDetector(Config().fall)
    detector.update('p', pose(), 0., armed=True)
    assert detector.update('p', collapse_pose((304., 205.), (312., 283.), (250., 390.), height=270.), .25, armed=True) is None
    assert detector.update('p', collapse_pose((358., 232.), (373., 306.), (250., 390.), height=240.), .5, armed=True) is None
    # Downward speed alone is .3 bbox heights/s; total projected hip speed is
    # greater than .6, with a compressed box and feet left behind.
    p = collapse_pose((390., 250.), (410., 320.), (250., 390.))
    signal = detector.update('p', p, .65, armed=True)
    assert signal.details['candidate_reason'] == 'rapid_descent_outside_foot_support'
    assert signal.details['hip_motion_speed_normalized'] >= .6


def test_inverted_projection_requires_complete_ordered_body_and_hold():
    from ai_service.fall_candidates import ReviewFallDetector
    detector = ReviewFallDetector(Config().fall)
    inverse = collapse_pose((250., 280.), (255., 210.), (265., 120.))
    found = [s for i in range(201) if (s := detector.update('p', inverse, i * .1, armed=False))]
    assert len(found) == 1
    assert found[0].details['candidate_reason'] == 'sustained_inverted_body_without_observed_transition'
    assert found[0].details['fall_transition_observed'] is False
    # Bowing keeps the feet below the hips, even when the head dips below them.
    detector.reset()
    bowed = collapse_pose((250., 280.), (255., 210.), (265., 330.))
    assert not any(detector.update('p', bowed, i * .1, armed=False) for i in range(12))


def test_prone_after_track_id_change_does_not_repeat_recent_overlapping_alert():
    from ai_service.fall_candidates import ReviewFallDetector
    detector = ReviewFallDetector(Config().fall)
    p = lying_pose()
    assert detector.accept_strict('old', 0., p.bbox)
    detector.retain(set())
    for i in range(11):
        assert detector.update('new', p, 1. + i * .1, armed=False) is None
    # Suppression is spatial only; a different region has its own candidate.
    shifted = replace(p, bbox=tuple(v + (300 if j % 2 == 0 else 0) for j, v in enumerate(p.bbox)),
                      keypoints=p.keypoints + np.array([300., 0., 0.]))
    signals = [s for i in range(11) if (s := detector.update('other', shifted, 1. + i * .1, armed=False))]
    assert len(signals) == 1
    detector.reset()
    assert not detector.recent_alerts
