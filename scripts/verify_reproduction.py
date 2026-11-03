"""Verify the bundled local safety pipeline. No paid requests, camera or notifications."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_team import prepare


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', choices=['auto', 'cpu', '0'], default='auto')
    p.add_argument('--assets-only', action='store_true')
    p.add_argument('--source-dir', type=Path, help='Local folder containing the three named development videos; no videos are bundled')
    args = p.parse_args()
    config = prepare(args.device)
    if args.assets_only:
        return
    source_dir = (args.source_dir or ROOT / 'examples/repro-video').resolve()
    names = ['11月3日 (1).mp4', '11月3日 (2).mp4', '10月30日.mp4']
    if not all((source_dir / name).is_file() for name in names):
        p.error('No videos are shipped. Supply --source-dir with your LOCAL development-video folder, or use --assets-only.')
    # Reproduce the same DLL search path as the production detector subprocess.
    env = os.environ.copy()
    for key in list(env):
        if any(marker in key.upper() for marker in ('API_KEY', 'TOKEN', 'RTSP', 'PASSWORD')):
            env.pop(key, None)
    venv = Path(sys.executable).parent.parent / 'pyvenv.cfg'
    if venv.is_file():
        home = next((l.split('=', 1)[1].strip() for l in venv.read_text(encoding='utf-8-sig').splitlines() if l.startswith('home =')), '')
        if home:
            env['PATH'] = os.pathsep.join([home, str(Path(home) / 'Library/bin'), str(Path(home) / 'Scripts'), env.get('PATH', '')])
    env['PYTHONUTF8'] = '1'
    output = ROOT / 'runtime' / ('reproduce-' + uuid.uuid4().hex[:12])
    output.mkdir()
    results = []
    for label, name in [('missed', '11月3日 (1).mp4'), ('control', '11月3日 (2).mp4'), ('mixed_demo', '10月30日.mp4')]:
        folder = output / label
        with (output / (label + '.log')).open('w', encoding='utf8') as log:
            run = subprocess.run([sys.executable, str(ROOT / 'elder-safety/scripts/scan-safety-video.py'),
                '--source', str(source_dir / name), '--config', str(config),
                '--output', str(folder)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        if run.returncode:
            raise RuntimeError('Detector failed for ' + label + '; inspect local runtime log (no secrets).')
        scan = json.loads((folder / 'scan.json').read_text(encoding='utf8'))
        falls = [c for c in scan['candidates'] if c['event_type'] == 'suspected_fall']
        if not scan['completed'] or len(falls) != 1:
            raise AssertionError('Expected exactly one fall candidate in ' + label)
        if label == 'missed' and falls[0]['details']['algorithm'] != 'interrupted_inverted_pose_review_v1':
            raise AssertionError('Pose-loss recovery branch not reproduced')
        for candidate in scan['candidates']:
            frames = candidate['frames']
            if len(frames) < 2 or len({f['pts_ms'] for f in frames}) != len(frames):
                raise AssertionError('Invalid multi-frame review evidence')
            if not all(Path(f['path']).is_file() for f in frames):
                raise AssertionError('Missing review evidence')
        row = {'clip': name, 'falls': len(falls), 'fall_times': [f['t'] for f in falls],
               'algorithms': [f['details']['algorithm'] for f in falls],
               'frames': scan['frames'], 'model_passes': scan['pose_model_passes'],
               'source_sha256': scan['source_sha256']}
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    report = {'status': 'local_candidates_verified', 'cloud_calls': 0, 'results': results,
              'limitations': ['Development examples, not independent accuracy',
                              'Local proposals only; Qwen confirmation and real camera not tested']}
    (output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print('Verification report: ' + str(output / 'verification.json'))


if __name__ == '__main__':
    main()
