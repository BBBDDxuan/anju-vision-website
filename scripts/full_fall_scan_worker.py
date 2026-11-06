"""Fresh whole-video GPU scanning with the public scanner and reusable weights.

One collector per video. No cloud credentials, model changes, or notifications.
"""
from pathlib import Path
import argparse
from contextlib import redirect_stdout
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'elder-safety/ai-service'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    from ai_service.runtime_env import configure_model_runtime
    configure_model_runtime()
    from ai_service.config import Config
    from ai_service import models as model_module
    config = Config.load(str(args.config))
    cases = json.loads((args.output / 'manifest.json').read_text(encoding='utf-8'))
    spec = importlib.util.spec_from_file_location('public_full_fall_scanner', ROOT / 'elder-safety/scripts/scan-safety-video.py')
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    original = model_module.LocalModels
    cached = []

    def shared_models(model_config):
        if not cached:
            cached.append(original(model_config))
        model = cached[0]
        assert asdict(model.config) == asdict(model_config)
        for name in ('pose_frames', 'pose_predict_calls', 'pose_recovery_frames'):
            setattr(model, name, 0)
        return model

    model_module.LocalModels = shared_models
    with open(os.devnull, 'w', encoding='utf-8') as quiet:
        for index, case in enumerate(cases, 1):
            output = args.output / 'clips' / case['id'] / 'safety'
            try:
                assert hashlib.sha256(Path(case['source']).read_bytes()).hexdigest() == case['source_sha256']
                with redirect_stdout(quiet):
                    result = scanner.scan_video(Path(case['source']), config, output, scan_fps=10)
                assert result['completed'] and result['frames'] == case['source_frames']
                assert result['source_sha256'] == case['source_sha256']
                message = dict(status='clip_complete', id=case['id'], index=index,
                               frames=result['frames'], candidates=len(result['candidates']))
            except Exception as exc:
                message = dict(status='clip_error', id=case['id'], index=index, error_type=type(exc).__name__)
            print(json.dumps(message), flush=True)
    print(json.dumps(dict(status='all_scans_finished', total=len(cases))), flush=True)


if __name__ == '__main__':
    main()
