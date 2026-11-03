from datetime import datetime, timezone
import threading
import time

import numpy as np

from ai_service.config import Config
from ai_service.engine import DetectionEngine
from ai_service.realtime import LatestFireWorker
from ai_service.types import Flame, Sample


def wait_result(worker, session=1, now=1., age=1.):
    deadline = time.perf_counter()+2
    while time.perf_counter()<deadline:
        result = worker.take(session, now, age)
        if result is not None:
            return result
        threading.Event().wait(.005)
    raise AssertionError("worker result timeout")


def test_latest_pending_replaces_backlog_and_result_is_consumed_once():
    entered, release = threading.Event(), threading.Event()
    seen=[]
    def infer(image):
        seen.append(image)
        if image == 1:
            entered.set()
            assert release.wait(2)
        return [image]
    worker=LatestFireWorker(infer, interval_seconds=.01)
    try:
        worker.submit(1, .1, 1)
        assert entered.wait(2)
        worker.submit(2, .2, 1)
        worker.submit(3, .3, 1)
        release.set()
        deadline=time.perf_counter()+2
        while 3 not in seen and time.perf_counter()<deadline:
            threading.Event().wait(.005)
        result=wait_result(worker)
        if result.t == .1:
            result=wait_result(worker)
        assert result.t == .3 and result.flames == [3]
        assert seen == [1,3]
        assert worker.take(1,1,1) is None
    finally:
        release.set(); worker.close()
    assert not worker.thread.is_alive()


def test_old_sessions_stale_and_future_results_cannot_reach_engine():
    from ai_service.realtime import FireObservation
    worker=LatestFireWorker(lambda image: [], interval_seconds=.01)
    try:
        for session, now, age in [(2,1,1),(1,2,.5),(1,0,1)]:
            with worker.condition:
                worker.result=FireObservation(1,1,[],1)
            assert worker.take(session,now,age) is None
    finally:
        worker.close()


def test_cached_overlay_never_accumulates_fire_persistence_and_empty_breaks_it():
    engine=DetectionEngine(Config())
    image=np.zeros((2,2,3),np.uint8)
    flame=Flame((0,0,1,1),.9)
    def frame(t, observed=False, detections=None, flame_t=None):
        return Sample(image,t,datetime.now(timezone.utc),flames=detections or [],
                      flames_observed=observed,flame_t=flame_t)
    assert not engine.process(frame(0,True,[flame],0))
    for i in range(1,21):
        assert not engine.process(frame(i*.05,False,[flame],0))
    assert engine.fire.states['fire-1'].count == 1
    # A real negative observation clears fire history.
    assert not engine.process(frame(1.1,True,[],1.0))
    assert not engine.fire.states


def test_independent_fresh_fire_timestamps_can_trigger_with_faster_pose_frames():
    engine=DetectionEngine(Config())
    flame=Flame((0,0,10,10),.9)
    signals=[]
    for i in range(26):
        t=i*.05
        observed=i%5 == 0
        sample=Sample(np.zeros((2,2,3),np.uint8),t,datetime.now(timezone.utc),
                      flames=[flame] if observed else [],flames_observed=observed,
                      flame_t=t-.04 if observed else None)
        signals.extend(engine.process(sample))
    assert len(signals) == 1 and signals[0].event_type == 'fire_detected'
    assert signals[0].details['frames'] == 5


def test_worker_error_does_not_reuse_previous_positive_and_can_recover():
    def infer(image):
        if image == 1: raise RuntimeError('private value')
        return []
    worker=LatestFireWorker(infer, interval_seconds=.01)
    try:
        worker.submit(1,1,1)
        deadline=time.perf_counter()+2
        while not worker.failed and time.perf_counter()<deadline:
            threading.Event().wait(.005)
        assert worker.failed
        assert worker.take(1,1,1) is None
        worker.submit(2,1,1)
        assert wait_result(worker).flames == []
        assert not worker.failed
    finally:
        worker.close()
