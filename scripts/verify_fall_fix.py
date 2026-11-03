"""Reproduce local fall validation without cloud calls or camera access.

All artifacts go to the supplied new G: output directory. Original videos and
weights are read-only. Run using the backend .venv interpreter.
"""
from pathlib import Path
import argparse
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.hybrid_safety import detector_environment


def audit_results(out):
    import hashlib
    import cv2
    import numpy as np
    positive = json.loads((out / 'positive/summary.json').read_text(encoding='utf-8'))
    negative = json.loads((out / 'negative/summary.json').read_text(encoding='utf-8'))
    sources = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
               sorted((ROOT / 'elder-safety/ai-service/ai_service').glob('*.py'))}
    for result in (positive, negative):
        assert result['source_hashes'] == sources, 'Source changed during verification'
        assert not result['replayed'] and result['cloud_calls'] == 0
        for clip in result['clips']:
            assert hashlib.sha256(Path(clip['source']).read_bytes()).hexdigest() == clip['sha256']
    found = [Path(c['source']).stem for c in positive['clips']
             if any(s['event_type'] == 'suspected_fall' for s in c['signals']['review'])]
    missed = [Path(c['source']).stem for c in positive['clips'] if Path(c['source']).stem not in found]
    assert len(found) == 5 and len(positive['clips']) == 9
    assert len(negative['clips']) == 5
    assert not any(c['signals']['review'] for c in negative['clips'])
    scans = {}
    for label, expected in [('demo', [('help_requested', 19.9), ('suspected_fall', 31.4)]),
                             ('public', [('suspected_fall', 5.9)])]:
        result = json.loads((out / label / 'scan.json').read_text(encoding='utf-8'))
        assert result['completed'] and result['frames'] == result['total_frames']
        assert [(c['event_type'], c['t']) for c in result['candidates']] == expected
        for candidate in result['candidates']:
            assert len(candidate['frames']) == 12
            assert len({f['pts_ms'] for f in candidate['frames']}) == 12
            for frame in candidate['frames']:
                path = Path(frame['path'])
                assert path.resolve().is_relative_to(out)
                image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
                assert image is not None and image.size > 100
        scans[label] = {'decoded_frames': result['frames'], 'inferred_frames': result['inferred_frames'],
                        'candidates': expected, 'genuine_jpegs': sum(len(c['frames']) for c in result['candidates']),
                        'timings': result['timings']}
    audit = {'status': 'local_targeted_fix_verified_with_known_misses', 'cloud_calls': 0,
             'positive_candidate_coverage': [len(found), 9], 'known_misses': missed,
             'negative_videos_without_candidates': [5, 5], 'scans': scans,
             'detector_sources': sources,
             'limitations': ['Development corpus, not held-out accuracy',
                             'No live camera, Qwen confirmation or mobile notification verification']}
    (out / 'verification.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT / 'runtime') or out.exists():
        parser.error('Use a new workspace runtime directory')
    out.mkdir(parents=True)
    gpu = ROOT / 'elder-safety/.venv-gpu/Scripts/python.exe'
    env = detector_environment(gpu)
    jobs = []
    for label, directory in [('positive', 'CAUCAFall_validation_9'),
                             ('negative', 'CAUCAFall_negative_5')]:
        jobs.append((label, ['scripts/evaluate_fall_corpus.py', '--source-dir',
            str(ROOT / 'video/public_samples' / directory), '--output', str(out / label)]))
    for label, source in [('demo', 'video/10月30日.mp4'),
                          ('public', 'video/public_samples/CAUCAFall_Subject1_FallForward/CAUCAFall_S1_simulated_fall_forward.avi')]:
        jobs.append((label, ['elder-safety/scripts/scan-safety-video.py', '--source',
            str(ROOT / source), '--config', str(ROOT / 'elder-safety/ai-service/config.camera-gpu.json'),
            '--output', str(out / label)]))
    results = []
    for label, command in jobs:
        print('Starting', label, flush=True)
        start = time.perf_counter()
        with (out / (label + '.log')).open('w', encoding='utf-8') as log:
            result = subprocess.run([str(gpu), *command], cwd=ROOT, env=env,
                                    stdout=log, stderr=subprocess.STDOUT)
        record = {'job': label, 'returncode': result.returncode,
                  'wall_seconds': round(time.perf_counter() - start, 3)}
        results.append(record)
        (out / 'jobs.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(record, flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)
    audit_results(out)
    print('Local validation passed; 4 known misses remain.', flush=True)


if __name__ == '__main__':
    main()
