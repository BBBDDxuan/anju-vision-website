"""A projected upright torso with feet above hips is not standing recovery."""
from dataclasses import replace
import numpy as np

from ai_service.config import Config
from ai_service.review_lifecycle import ReviewLifecycle
from ai_service.types import Pose, Signal
from test_seated_core_candidates import pose


def projected_prone():
    k=np.zeros((17,3),float)
    for indices,xy in [([5,6],(356,370)),([11,12],(353,395)),
                       ([13,14],(346,322)),([15,16],(362,267))]:
        for index,offset in zip(indices,[-5,5]):k[index]=[xy[0]+offset,xy[1],.98]
    return Pose((286,227,433,479),k,.98)


def begin(lifecycle,current):
    lifecycle.observe('p',current,0,(540,720,3))
    lifecycle.accept(Signal('suspected_fall','p',.98,{}),current,0)


def test_feet_above_hips_cannot_rearm_an_episode_even_with_straight_legs():
    lifecycle=ReviewLifecycle(Config().fall)
    current=projected_prone();begin(lifecycle,current)
    recovered=[]
    for i in range(1,11):
        _,normal=lifecycle.observe('p',current,i/10,(540,720,3))
        recovered.append(normal)
    assert not any(recovered)
    assert lifecycle.episodes['p'].number==1


def test_reliable_seated_recovery_still_rearms_before_cooldown():
    lifecycle=ReviewLifecycle(Config().fall)
    begin(lifecycle,projected_prone())
    current=pose();recovered=[]
    for i in range(1,8):
        _,normal=lifecycle.observe('p',current,i/10,(540,720,3))
        recovered.append(normal)
    assert any(recovered) and 'p' not in lifecycle.episodes


def test_unreliable_or_out_of_bounds_feet_cannot_prove_recovery():
    for invalid in ['confidence','bounds']:
        lifecycle=ReviewLifecycle(Config().fall);current=pose()
        if invalid=='confidence':current.keypoints[[15,16],2]=0
        else:current.keypoints[[15,16],1]=600
        begin(lifecycle,current)
        for i in range(1,8):
            _,normal=lifecycle.observe('p',current,i/10,(540,720,3))
            assert not normal
        assert 'p' in lifecycle.episodes
