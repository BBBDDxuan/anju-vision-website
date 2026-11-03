"""Face-direction proxy tests: synthetic geometry, never labelled gaze accuracy."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from ai_service.config import Config, update
from ai_service.engine import DetectionEngine
from ai_service.help_gesture import HelpConfig, HelpGestureDetector
from ai_service.types import Pose, Sample


def pose():
    kp=np.zeros((17,3),dtype=float);kp[:,2]=.9
    kp[0,:2]=(100,100)
    kp[1,:2]=(110,90);kp[2,:2]=(90,90)
    kp[5,:2]=(60,150);kp[6,:2]=(140,150)
    kp[9,:2]=(20,40);kp[10,:2]=(180,40)
    kp[7,:2]=(kp[5,:2]+kp[9,:2])/2
    kp[8,:2]=(kp[6,:2]+kp[10,:2])/2
    return Pose((0,0,200,400),kp,.95)


def config(**kwargs):
    return HelpConfig(require_facing_camera=True,hold_seconds=.5,min_frames=3,
                      max_gap_seconds=.25,cooldown_seconds=2.,**kwargs)


def feed(detector, times, person=None):
    person=pose() if person is None else person
    return [s for t in times if (s:=detector.update('p',person,t))]


def test_frontal_extended_arms_trigger_with_explicit_non_gaze_metadata():
    detector=HelpGestureDetector(config())
    person=pose();person.keypoints[1,2]=.65
    signal,=feed(detector,[0,.25,.5],person)
    assert signal.details['algorithm']=='temporal_two_hands_raised_extended_front_v3'
    assert signal.details['require_facing_camera'] is True
    assert signal.details['gaze_estimated'] is False
    assert signal.confidence==pytest.approx(.65)
    assert 'gaze' in signal.details['limitation']


@pytest.mark.parametrize('index',[0,1,2])
def test_occluded_or_low_confidence_face_fails_closed(index):
    person=pose();person.keypoints[index,2]=.2
    detector=HelpGestureDetector(config())
    assert detector.facing_camera_confidence(person) is None
    assert not feed(detector,[i/10 for i in range(30)],person)


@pytest.mark.parametrize('offset',[-15,15])
def test_asymmetric_nose_eye_geometry_rejects_turned_head_proxy(offset):
    person=pose();person.keypoints[0,0]+=offset
    assert not feed(HelpGestureDetector(config()),[0,.25,.5, .75],person)


@pytest.mark.parametrize('mode',['coincident_eyes','tiny_eyes','huge_eyes','nose_above',
                                  'nose_too_low','rolled_face','missing_shoulders'])
def test_degenerate_or_implausible_face_points_are_unknown(mode):
    person=pose();kp=person.keypoints
    if mode=='coincident_eyes': kp[1,:2]=kp[2,:2]
    elif mode=='tiny_eyes': kp[[1,2],0]=(100.1,99.9)
    elif mode=='huge_eyes': kp[[1,2],0]=(180,20)
    elif mode=='nose_above': kp[0,1]=80
    elif mode=='nose_too_low': kp[0,1]=140
    elif mode=='rolled_face': kp[[1,2],:2]=((107,97),(93,83))
    elif mode=='missing_shoulders': kp[5,2]=.1
    detector=HelpGestureDetector(config())
    assert detector.facing_camera_confidence(person) is None
    assert not feed(detector,[0,.25,.5],person)


@pytest.mark.parametrize('scale,offset',[(.25,(10,-50)),(3,(-300,200))])
def test_face_gate_preserves_scale_and_translation(scale,offset):
    person=pose();person.keypoints[:,:2]=person.keypoints[:,:2]*scale+offset
    person.bbox=tuple(np.array(person.bbox)*scale+np.tile(offset,2))
    assert len(feed(HelpGestureDetector(config()),[0,.25,.5],person))==1


def test_optional_gate_preserves_legacy_behavior_and_missing_eyes_when_disabled():
    person=pose();person.keypoints[[1,2],2]=0
    assert not feed(HelpGestureDetector(config()),[0,.25,.5],person)
    signal,=feed(HelpGestureDetector(replace(config(),require_facing_camera=False)),[0,.25,.5],person)
    assert signal.details['algorithm']=='temporal_two_hands_raised_extended_v2'
    assert signal.details['require_facing_camera'] is False


def test_turning_away_breaks_pending_hold_and_requires_a_new_complete_hold():
    detector=HelpGestureDetector(config())
    assert not feed(detector,[0,.25])
    away=pose();away.keypoints[0,0]=115
    assert detector.update('p',away,.5) is None
    assert not feed(detector,[.75,1.])
    assert detector.update('p',pose(),1.25)


def test_turning_away_never_bypasses_cooldown():
    detector=HelpGestureDetector(config())
    assert len(feed(detector,[0,.25,.5]))==1
    away=pose();away.keypoints[1,2]=.1
    assert detector.update('p',away,.75) is None
    assert not feed(detector,[i*.25 for i in range(4,10)])
    assert detector.update('p',pose(),2.5)


def test_front_face_alone_cannot_replace_raised_extended_arms():
    person=pose();person.keypoints[[9,10],1]=200
    detector=HelpGestureDetector(config())
    assert detector.facing_camera_confidence(person) is not None
    assert not feed(detector,[0,.25,.5],person)


def test_front_face_cannot_make_hands_on_head_qualify():
    person=pose();person.keypoints[[9,10],:2]=((95,60),(105,60))
    person.keypoints[[7,8],:2]=((25,85),(175,85))
    detector=HelpGestureDetector(config())
    assert detector.facing_camera_confidence(person) is not None
    assert not feed(detector,[0,.25,.5],person)


@pytest.mark.parametrize('value',[0,1,None,'true',[],{},1.0])
def test_flag_requires_literal_boolean_in_update_and_direct_config(value):
    with pytest.raises(ValueError):
        update(HelpConfig(),{'require_facing_camera':value})
    with pytest.raises(ValueError):
        HelpGestureDetector(HelpConfig(require_facing_camera=value))


@pytest.mark.parametrize('value',[True,False])
def test_config_validation_accepts_boolean_and_rejects_numeric_bool_thresholds(value):
    c=Config();update(c,{'help':{'require_facing_camera':value}});c.validate()
    assert c.help.require_facing_camera is value
    with pytest.raises(ValueError): update(c,{'help':{'confidence':True}})


def test_camera_config_enables_gate_and_engine_uses_it():
    c=Config.load(str(Path(__file__).parents[1]/'config.team.json'))
    assert c.help.require_facing_camera is True
    engine=DetectionEngine(c)
    def sample(t,person):
        return Sample(np.zeros((2,2,3),np.uint8),t,datetime.now(timezone.utc),poses=[person],flames_observed=False)
    away=pose();away.keypoints[1,2]=.1
    assert not [s for i in range(10) for s in engine.process(sample(i*.1,away))]
    signals=[s for i in range(10,21) for s in engine.process(sample(i*.1,pose()))]
    assert [s.event_type for s in signals]==['help_requested']
