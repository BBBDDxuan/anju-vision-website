"""Replay local models and saved keypoints without alerts, uploads or media writes."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ai-service'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--infer',action='store_true')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    from ai_service.runtime_env import configure_model_runtime
    configure_model_runtime()
    import numpy as np
    from ai_service.config import Config
    from ai_service.engine import DetectionEngine
    from ai_service.models import LocalModels
    from ai_service.types import Sample, Pose, Flame
    config=Config.load(str(ROOT/'ai-service/config.camera-gpu.json'))
    models=LocalModels(config.models) if args.infer else None
    previous=ROOT/'runtime/v1-local-validation-20261028-181635'
    summary=json.loads((previous/'summary.json').read_text(encoding='utf-8'))
    reports=[]
    for item in summary:
        source=Path(item['source'])
        if args.infer:
            from ai_service.local_validate import media_frames
            rows=[]
            for t,fps,image in media_frames(source):
                poses,flames=models.infer(image)
                rows.append({'t':t,'poses':poses,'flames':flames})
        else:
            rows=[]
            for line in (Path(item['output'])/'trace.jsonl').read_text(encoding='utf-8').splitlines():
                row=json.loads(line)
                rows.append({'t':row['t'],
                    'poses':[Pose(tuple(p['bbox']),np.array(p['keypoints']),p['confidence']) for p in row['poses']],
                    'flames':[Flame(tuple(f['bbox']),f['confidence'],f['label']) for f in row['flames']]})
        for stride in ([1] if args.infer else [1,3,5]):
            engine=DetectionEngine(config);events=[];states=Counter()
            for row in rows[::stride]:
                sample=Sample(np.zeros((2,2,3),np.uint8),row['t'],datetime.now(timezone.utc),
                    poses=row['poses'],flames=row['flames'])
                events.extend({'event_type':s.event_type,'t':row['t']} for s in engine.process(sample))
                states.update(d['state'] for d in engine.fall.diagnostics.values())
            reports.append({'source_name':source.name,'frames':len(rows[::stride]),'stride':stride,
                'events':events,'fall_diagnostic_counts':dict(states),
                'previous_events':[e['event_type'] for e in item['events']]})
    report={'real_gpu_inference':args.infer,'saved_pose_replay':not args.infer,
            'alarm_delivery':False,'media_saved':False,'independent_accuracy_estimate':False,'results':reports}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
