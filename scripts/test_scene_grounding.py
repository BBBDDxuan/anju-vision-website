"""Two paid prompt-development regressions on saved real frames, never a held-out eval.

Without --run this only prints an offline plan. Never modifies production prompts/memories.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import Settings
from backend.db import Database
from backend.errors import AppError
from backend.events import normalize, validate_memory
from backend.jobs import JobManager
from backend.media import file_sha256
from backend.prompting import load_event_prompt
from backend.providers.qwen import QwenProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    directory = ROOT / 'data/model-comparison/qwen38-20261018'
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf8'))
    prompt = load_event_prompt(ROOT / 'prompts/candidates/scene_grounding_v0.8.md')
    settings = replace(Settings.from_env(), model='qwen3.8-max', timeout=180)
    db = Database(settings.data_dir)
    start = db.usage_summary()
    report = {'kind': 'PROMPT_DEVELOPMENT_NOT_HELDOUT', 'is_mock': False,
              'model': settings.model, 'prompt_version': prompt.version,
              'prompt_sha256': prompt.sha256, 'maximum_new_requests': 2,
              'starting_usage': start, 'cases': [], 'actual_rmb_cost': None}
    print(json.dumps({**report, 'will_call_model': args.run}, ensure_ascii=False), flush=True)
    if not args.run:
        return
    output = directory / 'prompt-v08-report.json'
    if output.exists():
        raise RuntimeError('An experiment record already exists; refusing duplicate paid calls.')
    if len(manifest['clips']) != 2 or start['requests'] + 2 > settings.max_daily_calls:
        raise RuntimeError('Unexpected inputs or insufficient existing daily request budget.')
    for clip in manifest['clips']:
        if file_sha256(Path(clip['video']['original_path'])) != clip['video']['sha256']:
            raise RuntimeError('Source video no longer matches the prepared real frames.')
        if len(clip['frames']) > 220 or any(not Path(f['path']).is_file() for f in clip['frames']):
            raise RuntimeError('Missing frames or frame budget exceeded.')
    provider = QwenProvider(settings, db, event_prompt=prompt)
    manager = JobManager(settings, db, provider)

    def save():
        report['ending_usage'] = db.usage_summary()
        report['updated_at'] = datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')

    save()
    try:
        for index, clip in enumerate(manifest['clips']):
            video, frames = clip['video'], clip['frames']
            window = {'start_ms': 0, 'end_ms': video['duration_ms'], 'frames': frames}
            case = {'source': video['name'], 'video_sha256': video['sha256'],
                    'frame_ids': [f['id'] for f in frames], 'status': 'running'}
            report['cases'].append(case)
            save()
            print('REAL_REQUEST', index, len(frames), flush=True)
            started = time.monotonic()
            try:
                result = provider.extract(window=window, entities=[], video=video,
                                          job_id=f'grounding-v08-qwen38-{index}', output_tokens=16384)
                case.update(usage=result.usage, extraction=result.extraction.model_dump())
                entities, evidence, events = normalize(result.extraction, video['id'], window, {})
                document = manager.document(video, frames, entities, evidence, events)
                validate_memory(document, {f['id']: f for f in frames})
                memory_file = directory / f'grounding-v08-memory-clip-{index}.json'
                memory_file.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf8')
                labels = {k: v['label'] for k, v in entities.items()}
                refs = {e['id']: e['frame_ids'] for e in evidence}
                case.update(status='schema_and_references_pass', events=len(document['events']),
                            memory_file=memory_file.name,
                            events_summary=[{'subject': labels[e['subject_id']], 'action': e['action'],
                                             'target': labels.get(e['target_id']), 'start_ms': e['start_ms'],
                                             'end_ms': e['end_ms'],
                                             'frame_ids': [f for ref in e['evidence_ids'] for f in refs[ref]],
                                             'uncertainty_reasons': e['uncertainty_reasons']}
                                            for e in document['events']])
            except AppError as exc:
                case.update(status='failed', error=exc.public())
            finally:
                case['elapsed_seconds'] = round(time.monotonic() - started, 2)
                save()
            print(json.dumps({k: case.get(k) for k in ['source', 'status', 'events', 'error']}, ensure_ascii=False), flush=True)
            if case['status'] == 'failed':
                return
        report['new_requests'] = db.usage_summary()['requests'] - start['requests']
        report['sources_unchanged'] = all(file_sha256(Path(c['video']['original_path'])) == c['video']['sha256'] for c in manifest['clips'])
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
    finally:
        manager.close()


if __name__ == '__main__':
    main()
