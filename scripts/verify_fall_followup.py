"""Verify the remaining fall fixes with real local inference; no cloud or camera.

Uses the development corpus and does not estimate held-out accuracy.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.hybrid_safety import detector_environment


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    paths = list((ROOT / 'elder-safety/ai-service/ai_service').glob('*.py'))
    paths += [ROOT / p for p in ['backend/hybrid_safety.py',
        'elder-safety/scripts/scan-safety-video.py', 'elder-safety/ai-service/config.camera-gpu.json']]
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}


def audit(out, hashes, scan_sources):
    import cv2
    import numpy as np
    assert source_hashes() == hashes, 'Implementation changed during verification'
    positive = json.loads((out/'positive/summary.json').read_text(encoding='utf-8'))
    negative = json.loads((out/'negative/summary.json').read_text(encoding='utf-8'))
    assert len(positive['clips']) == 9 and len(negative['clips']) == 5
    for item in positive['clips']:
        assert len([s for s in item['signals']['review'] if s['event_type']=='suspected_fall']) == 1
    assert not any(item['signals']['review'] for item in negative['clips'])
    for group in (positive, negative):
        assert not group['replayed'] and group['cloud_calls'] == 0
        for clip in group['clips']:
            assert digest(Path(clip['source'])) == clip['sha256']
    scans = {}
    for name, source in scan_sources.items():
        result = json.loads((out/name/'scan.json').read_text(encoding='utf-8'))
        assert result['completed'] and result['frames']==result['total_frames']
        assert digest(source) == result['source_sha256']
        falls = [c for c in result['candidates'] if c['event_type']=='suspected_fall']
        assert len(falls) == 1, (name, len(falls))
        if name == 'demo':
            assert [(c['event_type'],c['t']) for c in result['candidates']] == [('help_requested',19.9),('suspected_fall',31.4)]
        for candidate in result['candidates']:
            frames = candidate['frames']
            assert len(frames) == 12 and len({f['pts_ms'] for f in frames}) == 12
            for f in frames:
                p=Path(f['path'])
                assert p.resolve().is_relative_to(out)
                image=cv2.imdecode(np.frombuffer(p.read_bytes(), np.uint8),cv2.IMREAD_COLOR)
                assert image is not None and image.size > 100
        scans[name] = {k: result[k] for k in ['frames','inferred_frames','pose_model_passes','pose_recovery_frames','timings','source_sha256']}
        scans[name]['candidates'] = [{'type': c['event_type'], 't':c['t'], 'details': c['details'],
            'evidence_start_ms':c['frames'][0]['pts_ms'], 'evidence_end_ms':c['frames'][-1]['pts_ms'],
            'frames':len(c['frames'])} for c in result['candidates']]
    verification = {'status':'local_candidates_verified', 'cloud_calls':0,
        'positive_coverage':[9,9], 'negative_without_candidates':[5,5],
        'source_hashes':hashes, 'production_scans':scans,
        'limitations':['Development examples used for tuning, not held-out accuracy',
            'Sustained posture candidates do not establish an observed fall',
            'No live-camera, Qwen confirmation or mobile notification test']}
    (out/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
    return verification


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve()
    if not out.is_relative_to(ROOT/'runtime') or out.exists():
        parser.error('Use a new workspace runtime directory')
    out.mkdir(parents=True)
    hashes=source_hashes()
    (out/'source-hashes.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
    gpu=ROOT/'elder-safety/.venv-gpu/Scripts/python.exe';env=detector_environment(gpu)
    jobs=[]
    for label,folder in [('positive','CAUCAFall_validation_9'),('negative','CAUCAFall_negative_5')]:
        jobs.append((label,['scripts/evaluate_fall_corpus.py','--source-dir',str(ROOT/'video/public_samples'/folder),'--output',str(out/label)]))
    sources={'demo':ROOT/'video/10月30日.mp4',
        'public':ROOT/'video/public_samples/CAUCAFall_Subject1_FallForward/CAUCAFall_S1_simulated_fall_forward.avi'}
    for prefix,label in [('01_','s2'),('02_','s3'),('05_','s6'),('07_','s8')]:
        matches=list((ROOT/'video/public_samples/CAUCAFall_validation_9').glob(prefix+'*.mp4'))
        assert len(matches)==1
        sources[label]=matches[0]
    for label,source in sources.items():
        jobs.append((label,['elder-safety/scripts/scan-safety-video.py','--source',str(source),
            '--config',str(ROOT/'elder-safety/ai-service/config.camera-gpu.json'),'--output',str(out/label)]))
    completed=[]
    for label,command in jobs:
        print('Starting',label,flush=True);start=time.perf_counter()
        with (out/(label+'.log')).open('w',encoding='utf-8') as log:
            result=subprocess.run([str(gpu),*command],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        completed.append({'job':label,'returncode':result.returncode,'wall_seconds':round(time.perf_counter()-start,3)})
        (out/'jobs.json').write_text(json.dumps(completed,indent=2),encoding='utf-8')
        print(completed[-1],flush=True)
        if result.returncode:raise SystemExit(result.returncode)
    audit(out,hashes,sources)
    print('Verified 9/9 positive candidates, 0/5 negative candidates and six production scans.',flush=True)


if __name__=='__main__':main()
