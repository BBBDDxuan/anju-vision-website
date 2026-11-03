"""Import an exported REAL model recording for offline replay; never calls a model."""
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import Settings
from backend.db import Database
from backend.errors import AppError
from backend.events import validate_memory
from backend.media import file_sha256


def import_recording(bundle: Path, settings: Settings):
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf8'))
    for relative, expected in manifest['sha256'].items():
        path = (bundle / relative).resolve()
        if not path.is_relative_to(bundle.resolve()) or file_sha256(path) != expected:
            raise ValueError('Invalid demo asset path or hash')
    document = json.loads((bundle / 'memory.json').read_text(encoding='utf8'))
    job = json.loads((bundle / 'job.json').read_text(encoding='utf8'))
    validate_memory(document)
    if document['fixture_kind'] != 'model_analysis' or job['is_mock'] or job['status'] != 'completed':
        raise ValueError('Only a completed real recording can be imported')
    vid, run = document['video_id'], job['id']
    for identifier in [vid, run, *[f['id'] for f in document['frames']]]:
        if not identifier or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in identifier):
            raise ValueError('Invalid recording identifier')
    derivative = manifest.get('playback_derivative')
    if derivative:
        if (derivative['original_input_sha256'] != document['model_run']['input_video_sha256']
                or derivative['playback_sha256'] != file_sha256(bundle/'playback.mp4')
                or derivative.get('frame_timestamps_verified') is not True):
            raise ValueError('Invalid playback derivative provenance')
    elif file_sha256(bundle / 'video.mp4') != document['model_run']['input_video_sha256']:
        raise ValueError('Recorded video does not match model input')
    metadata = {'imported_real_recording':True,'demo_description':manifest.get('demo_description','公开模拟跌倒样例，已保存真实模型分析。'),
                'source_mapping':manifest['source_mapping'],'demo_gallery_visible':manifest.get('demo_gallery_visible',True),
                'case_type':manifest.get('case_type','day_fall'),'poster_frame_id':manifest.get('poster_frame_id'),
                'playback_derivative':derivative}
    db = Database(settings.data_dir, journal_mode='DELETE' if settings.cloud_mode else 'WAL')
    try:
        previous = db.video(vid)
        if previous['metadata'].get('imported_real_recording') is not True or previous['sha256'] != document['model_run']['input_video_sha256']:
            raise ValueError('Demo ID collides with a non-demo video or different source')
        with db.connect() as c:
            c.execute('UPDATE videos SET name=?,metadata=? WHERE id=?',
                      (manifest.get('display_name',previous['name']),json.dumps(metadata,ensure_ascii=False),vid))
        return {'status': 'ALREADY_PRESENT', 'video_id': vid}
    except AppError as exc:
        if exc.status != 404:
            raise
    directory = settings.data_dir / 'videos' / vid
    frame_dir = settings.data_dir / 'runs' / run / 'frames'
    directory.mkdir(parents=True, exist_ok=True)
    frame_dir.mkdir(parents=True, exist_ok=True)
    if not derivative:
        shutil.copyfile(bundle / 'video.mp4', directory / 'original.mp4')
    shutil.copyfile(bundle / 'playback.mp4', directory / 'playback.mp4')
    for frame in document['frames']:
        shutil.copyfile(bundle / 'frames' / (frame['id']+'.jpg'), frame_dir / (frame['id']+'.jpg'))
    db.add_video({'id': vid, 'sha256': document['model_run']['input_video_sha256'], 'name': manifest.get('display_name', '真实模型结果回放 · 笔记本移动.mp4'),
                  'duration_ms': document['video_duration_ms'], 'original_path': str(directory / ('playback.mp4' if derivative else 'original.mp4')),
                  'playback_path': str(directory / 'playback.mp4'), 'metadata':metadata})
    job['imported_recording'] = True
    db.save_job(job)
    db.save_memory(run, document)
    return {'status': 'IMPORTED_REAL_RECORDING', 'video_id': vid, 'model_calls': 0}


def import_all_recordings(root: Path, settings: Settings):
    results = [import_recording(root/'demo-recording', settings)]
    catalog = root/'demo-catalog.json'
    if catalog.is_file():
        for item in json.loads(catalog.read_text(encoding='utf8'))['demos']:
            folder = (root/item['directory']).resolve()
            if not folder.is_relative_to((root/'demo-recordings').resolve()):
                raise ValueError('Invalid demo catalog directory')
            results.append(import_recording(folder, settings))
    return results


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--all':
        print(json.dumps(import_all_recordings(ROOT, Settings.from_env()), ensure_ascii=True))
        sys.exit(0)
    bundle = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'demo-recording'
    print(json.dumps(import_recording(bundle, Settings.from_env()), ensure_ascii=True))
