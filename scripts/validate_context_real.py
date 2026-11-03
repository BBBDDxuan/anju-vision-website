"""Bounded, opt-in paid smoke: two real source windows, not full-video accuracy evaluation."""
import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from backend.config import ROOT, Settings
from backend.db import Database
from backend.events import normalize, validate_memory
from backend.jobs import JobManager
from backend.media import file_sha256
from backend.providers.qwen import QwenProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-paid', action='store_true', help='Authorize exactly two requests without retries.')
    args = parser.parse_args()
    if not args.run_paid:
        parser.error('Explicit --run-paid is required; requests may incur account charges.')
    settings = replace(Settings.from_env(), timeout=120, max_retries=0)
    if settings.model != 'qwen3.8-max':
        raise RuntimeError('Expected main analysis model qwen3.8-max; configuration was not adopted.')
    db = Database(settings.data_dir)
    provider = QwenProvider(settings, db)
    provider.ready()
    if settings.max_daily_calls - db.usage_summary()['requests'] < 2:
        raise RuntimeError('Insufficient request budget; no request was made.')
    manifest = json.loads((ROOT / 'data/model-comparison/qwen38-20261018/manifest.json').read_text(encoding='utf8'))
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'is_mock': False,
              'scope': 'two_real_video_windows_not_full_reanalysis_or_accuracy_benchmark',
              'maximum_new_requests': 2, 'model': settings.model, 'prompt_version': provider.event_prompt.version,
              'cases': [], 'rmb_cost': None}
    destination = ROOT / 'evidence/context-real-validation.json'
    for index, start, end in [(0, 56000, 84000), (1, 364000, 393633)]:
        clip = manifest['clips'][index]
        video = clip['video']
        if file_sha256(Path(video['original_path'])) != video['sha256']:
            raise RuntimeError('Real source hash differs from saved frame manifest; aborted.')
        frames = [f for f in clip['frames'] if start <= f['pts_ms'] < end]
        if not frames or len(frames) > 16:
            raise RuntimeError('Unexpected source window size; aborted before paid call.')
        run_id = 'context_smoke_' + uuid4().hex
        window = {'start_ms': start, 'end_ms': end, 'frames': frames}
        case = {'video': video['name'], 'input_sha256': video['sha256'], 'start_ms': start,
                'end_ms': end, 'frame_count': len(frames), 'run_id': run_id}
        try:
            result = provider.extract(window=window, entities=[], video=video, job_id=run_id, output_tokens=2048)
            entities, evidence, events = normalize(result.extraction, video['id'], window, {})
            # Same production document/semantic checks; no synthetic source observations are inserted.
            manager = JobManager(settings, db, provider)
            try:
                document = manager.document(video, frames, entities, evidence, events)
            finally:
                manager.close()
            validate_memory(document, {f['id']: f for f in frames})
            response_path = next((settings.data_dir / 'runs' / run_id).glob('response-*.json'))
            response = json.loads(response_path.read_text(encoding='utf8'))
            returned_model = response['response'].get('model')
            if returned_model != 'qwen3.8-max':
                raise RuntimeError(f'Service reported a different model: {returned_model}')
            case.update(passed=True, returned_model=returned_model, usage=result.usage,
                        epistemic_counts=dict(Counter(e['epistemic_type'] for e in events)), memory=document)
        except Exception as exc:
            case.update(passed=False, error=exc.public() if hasattr(exc, 'public') else str(exc))
            report['cases'].append(case)
            destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
            raise
        report['cases'].append(case)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps({k: case[k] for k in ['video', 'passed', 'returned_model', 'frame_count', 'epistemic_counts']}, ensure_ascii=False), flush=True)
    print('Saved ' + str(destination), flush=True)


if __name__ == '__main__':
    main()
