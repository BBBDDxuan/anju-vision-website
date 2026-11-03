"""Prepare a relocated team checkout. No camera connection or cloud requests."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def prepare(device='auto', verify=True):
    if verify:
        manifest = json.loads((ROOT / 'repro-assets.json').read_text(encoding='utf8'))
        for asset in manifest['assets']:
            p = (ROOT / asset['path']).resolve()
            if not p.is_relative_to(ROOT) or not p.is_file():
                raise ValueError('Missing bundled asset: ' + asset['path'])
            if p.stat().st_size != asset['bytes'] or digest(p) != asset['sha256']:
                raise ValueError('Asset integrity mismatch: ' + asset['path'])
    if device == 'auto':
        try:
            import torch
            device = '0' if torch.cuda.is_available() else 'cpu'
        except ImportError:
            device = 'cpu'
    template = ROOT / 'elder-safety/ai-service/config.team.json'
    output = ROOT / 'runtime/team-camera.json'
    cfg = json.loads(template.read_text(encoding='utf8'))

    def portable(value):
        source = (template.parent / value).resolve()
        if not source.is_relative_to(ROOT):
            raise ValueError('Configured path escapes the project checkout: ' + value)
        return Path(os.path.relpath(source, output.parent)).as_posix()

    for key in ('pose_weights', 'flame_weights'):
        cfg['models'][key] = portable(cfg['models'][key])
    cfg['models']['device'] = device
    for key in ('directory', 'ffmpeg_path'):
        cfg['recording'][key] = portable(cfg['recording'][key])
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps({'assets_verified': verify, 'device': device, 'config_created': True,
                      'cloud_calls': 0}, ensure_ascii=False))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='auto', choices=['auto', 'cpu', '0'])
    parser.add_argument('--skip-hashes', action='store_true', help='Startup only; setup always verifies hashes')
    args = parser.parse_args()
    prepare(args.device, not args.skip_hashes)


if __name__ == '__main__':
    main()
