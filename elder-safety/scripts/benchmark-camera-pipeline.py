"""Compare old synchronous and latest-frame preview on the same local HD image.

No camera, network, recordings or alarm delivery. An upscaled prerecorded frame
exercises compute/overlay load, not real-camera latency or detection accuracy.
"""
from __future__ import annotations
from collections import deque
import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ai-service'))


def stats(values):
    if not values: return None
    values=sorted(values)
    return {'median':round(statistics.median(values),2),
            'p95':round(values[max(0,int(len(values)*.95)-1)],2)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=float,default=8)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    from ai_service.runtime_env import configure_model_runtime
    configure_model_runtime()
    import cv2
    import numpy as np
    from ai_service.config import Config
    from ai_service.models import LocalModels
    from ai_service.realtime import LatestFireWorker
    spec=importlib.util.spec_from_file_location('ai_service.models_before',
        ROOT/'runtime/performance-20261029/models-before.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    config=Config.load(str(ROOT/'ai-service/config.camera-gpu.json'))
    media=ROOT/'runtime/validation-media/urfd-fall-06-cam0.mp4'
    capture=cv2.VideoCapture(str(media));ok,image=capture.read();capture.release()
    if not ok: raise RuntimeError('local benchmark input unavailable')
    image=cv2.resize(image,(2880,1620))
    results={}
    for mode, factory in [('before',module.LocalModels),('after',LocalModels)]:
        models=factory(config.models)
        for _ in range(4): models.infer(image)
        fire=None
        if mode=='after': fire=LatestFireWorker(models.infer_flames)
        start=time.perf_counter();times=[];infer_ms=[];fire_times=[];fire_ms=[];pose_counts=[]
        try:
            while time.perf_counter()-start<args.seconds:
                t0=time.perf_counter()
                if fire:
                    fire.submit(image,t0,1)
                    poses=models.infer_pose(image)
                    observation=fire.take(1,t0,config.fire.max_gap_seconds)
                    if observation:
                        fire_times.append(observation.t);fire_ms.append(observation.inference_ms)
                else:
                    poses,_=models.infer(image)
                    fire_times.append(t0)
                infer_ms.append((time.perf_counter()-t0)*1000)
                pose_counts.append(len(poses))
                preview=cv2.resize(image,(1280,720)) if fire else image.copy()
                sx=preview.shape[1]/image.shape[1];sy=preview.shape[0]/image.shape[0]
                for pose in poses:
                    box=(np.array(pose.bbox)*[sx,sy,sx,sy]).astype(int)
                    cv2.rectangle(preview,tuple(box[:2]),tuple(box[2:]),(80,230,80),2)
                    for x,y,c in pose.keypoints:
                        if c>=.55: cv2.circle(preview,(int(x*sx),int(y*sy)),3,(0,230,255),-1)
                if not fire: preview=cv2.resize(preview,(1280,720))
                assert cv2.imencode('.jpg',preview,[cv2.IMWRITE_JPEG_QUALITY,78])[0]
                times.append(time.perf_counter())
                threading.Event().wait(max(0,(.05 if fire else .1)-(time.perf_counter()-t0)))
            elapsed=time.perf_counter()-start
        finally:
            if fire: fire.close()
        results[mode]={'frames':len(times),'elapsed_seconds':round(elapsed,3),
            'processed_fps':round(len(times)/elapsed,3),'inference_ms':stats(infer_ms),
            'frame_gap_ms':stats([(b-a)*1000 for a,b in zip(times,times[1:])]),
            'fire_observations':len(fire_times),'fire_fps':round((len(fire_times)-1)/(fire_times[-1]-fire_times[0]),3) if len(fire_times)>1 else 0,
            'fire_ms':stats(fire_ms),'person_count_min':min(pose_counts),'person_count_max':max(pose_counts)}
        del models
    report={'source':'same upscaled prerecorded frame','input_size':[2880,1620],
        'real_gpu_models':True,'fire_tiling_preserved':True,'network':False,'recording':False,
        'accuracy_evaluation':False,'camera_latency_evaluation':False,'results':results}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
