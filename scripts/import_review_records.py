"""Import two existing real Qwen candidate records as separate, clearly labelled review copies.

No inference and no human verdicts. Existing video records and original model outputs are preserved.
"""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import Settings
from backend.db import Database
from backend.events import validate_memory
from backend.media import file_sha256


def main():
    settings = Settings.from_env()
    output = settings.data_dir / 'validation/review-demo-import.json'
    if output.exists():
        print(output.read_text(encoding='utf8'))
        return
    directory = settings.data_dir / 'model-comparison/qwen38-20261018'
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf8'))
    report = json.loads((directory / 'report.json').read_text(encoding='utf8'))
    db = Database(settings.data_dir)
    imported = []
    for index, clip in enumerate(manifest['clips']):
        source = clip['video']
        if file_sha256(Path(source['original_path'])) != source['sha256']:
            raise RuntimeError('Video integrity changed; refusing import.')
        vid = 'v_review_' + source['sha256'][:20]
        run_id = 'j_review_' + source['sha256'][:20]
        name = ('人工复核·柜格·3分钟' if index == 0 else '人工复核·桌面·6分34秒') + '（真实历史候选）'
        if any(v['id'] == vid for v in db.list_videos()):
            imported.append({'video_id': vid, 'job_id': run_id, 'name': name, 'existing': True})
            continue
        case = next(c for c in report['cases'] if c['model'] == 'qwen3.8-max' and c['source'] == source['name'])
        document = json.loads((directory / case['memory_file']).read_text(encoding='utf8'))
        document['video_id'] = vid
        for item in document['evidence'] + document['events']:
            item['video_id'] = vid
        validate_memory(document)
        target = settings.data_dir / 'runs' / run_id / 'frames'
        target.mkdir(parents=True, exist_ok=False)
        for frame in clip['frames']:
            shutil.copy2(frame['path'], target / (frame['id'] + '.jpg'))
        video = {**copy.deepcopy(source), 'id': vid, 'name': name}
        db.add_video(video)
        db.save_job({'id': run_id, 'video_id': vid, 'status': 'completed', 'stage': '已导入真实历史候选，等待人工复核',
                     'progress': 100, 'is_mock': False, 'imported_recording': True, 'failed_windows': [],
                     'completed_windows': 1, 'total_windows': 1, 'provider': 'qwen', 'model': 'qwen3.8-max',
                     'prompt_version': case['prompt_version'], 'prompt_sha256': case['prompt_sha256'],
                     'source_record': str(directory / case['memory_file']), 'calls': 0,
                     'created_at': datetime.now(timezone.utc).isoformat()})
        db.save_memory(run_id, document)
        imported.append({'video_id': vid, 'job_id': run_id, 'name': name, 'events': len(document['events']), 'source': case['memory_file']})
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {'is_mock': False, 'paid_calls_on_import': 0, 'human_verdicts_added': 0, 'imported': imported}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
