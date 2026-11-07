"""Generate Linux/cloud detector configuration; no camera or network requests."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'elder-safety/ai-service'))


def choose_device(device):
    if device == 'cpu':
        return device
    if device not in {'auto', '0'}:
        raise ValueError('SAFETY_DEVICE must be cpu, auto, or 0.')
    import torch
    available = torch.cuda.is_available()
    if device == '0' and not available:
        raise ValueError('CUDA requested but unavailable. Use a CUDA image and GPU hardware, or select cpu.')
    return '0' if available else 'cpu'


def prepare(root: Path, output: Path, device='cpu'):
    from ai_service.config import Config

    root = root.resolve()
    config = Config.load(str(root/'elder-safety/ai-service/config.team.json'))
    manifest = json.loads((root/'elder-safety/weights/manifest.json').read_text(encoding='utf8'))
    hashes = {item['file']: item.get('sha256', item.get('downloaded_sha256')) for item in manifest['models']}
    for value in (config.models.pose_weights, config.models.flame_weights):
        path = Path(value).resolve()
        if not path.is_relative_to(root/'elder-safety/weights') or not path.is_file():
            raise ValueError('Bundled detector weight is missing or outside the weights directory.')
        if hashlib.sha256(path.read_bytes()).hexdigest() != hashes.get(path.name):
            raise ValueError('Bundled detector weight hash does not match the manifest.')
    config.models.device = choose_device(device)
    # Cloud mode accepts uploaded video only. Never retain Windows recorder paths.
    config.recording.enabled = False
    config.recording.directory = config.recording.ffmpeg_path = None
    config.validate()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    temporary.replace(output)
    return {'device': config.models.device, 'assets_verified': True, 'cloud_calls': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', choices=['cpu', 'auto', '0'], default='cpu')
    args = parser.parse_args()
    print(json.dumps(prepare(ROOT, args.output, args.device)), flush=True)
