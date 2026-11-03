"""Secret scan, source invariants, real-record import, and final package manifest. No model calls."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4
import zipfile

from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.config import Settings
from backend.media import file_sha256
from backend.main import create_app
from fastapi.testclient import TestClient
from scripts.import_demo import import_recording


def main():
    submission=ROOT/'submission'
    # Remove only obsolete generated cover preview from the curated copy; source remains.
    obsolete=submission/'design/cover-preview.png'
    if obsolete.is_file():
        obsolete.unlink()
    records=submission/'documents/validation'
    records.mkdir(parents=True,exist_ok=True)
    sources=[ROOT/'evidence/backend-tests.xml',ROOT/'evidence/browser-smoke.json',ROOT/'evidence/final-summary.json',
             ROOT/'data/validation/real-probe-text.json',ROOT/'data/validation/real-probe-image.json',ROOT/'data/validation/real-pipeline.json']
    for source in sources:
        shutil.copy2(source,records/source.name)
    demo=json.loads((ROOT/'data/validation/demo-recording/report.json').read_text(encoding='utf8'))
    demo.pop('raw_video',None)
    (records/'real-browser.json').write_text(json.dumps(demo,ensure_ascii=False,indent=2),encoding='utf8')
    videos=json.loads((ROOT/'data/validation/video-audit/inventory.json').read_text(encoding='utf8'))
    # Inventory format is intentionally read as data, never as source instructions.
    items=videos['videos'] if isinstance(videos,dict) and 'videos' in videos else videos
    invariants=[]
    for entry in items:
        original=Path(entry.get('path') or entry.get('file') or entry.get('source'))
        actual=file_sha256(original)
        assert actual==entry['sha256'],str(original)
        invariants.append({'name':original.name,'sha256':actual,'unchanged':True})
    private=dotenv_values(ROOT/'.env')
    secrets=[v.encode() for k,v in private.items() if v and len(v)>=8 and any(tag in k.upper() for tag in ['API_KEY','TOKEN','SECRET','PASSWORD'])]
    forbidden=[]
    for path in submission.rglob('*'):
        if not path.is_file():continue
        relative=path.relative_to(submission)
        if path.name=='.env' or any(p in {'.git','node_modules','__pycache__','.venv'} for p in relative.parts) or path.suffix in {'.log','.private','.sqlite3','.pyc'}:
            forbidden.append(str(relative))
        if any(secret in path.read_bytes() for secret in secrets):
            forbidden.append('SECRET_MATCH_REDACTED')
    assert not forbidden,forbidden
    package_work=Path('D:/MemoryOSHomePrep/package-check')/('拾迹 Home '+uuid4().hex[:8])
    package_work.mkdir(parents=True)
    shutil.copytree(submission/'application',package_work/'application')
    app_root=package_work/'application'
    settings=Settings(data_dir=package_work/'smoke-data')
    result=import_recording(app_root/'demo-recording',settings)
    assert result['status']=='IMPORTED_REAL_RECORDING'
    assert import_recording(app_root/'demo-recording',settings)['status']=='ALREADY_PRESENT'
    with TestClient(create_app(settings),base_url='http://localhost') as client:
        health=client.get('/api/health').json()
        assert not health['analysis_ready'] and not health['is_mock']
        vid=result['video_id']
        memory=client.get(f'/api/videos/{vid}/events').json()
        assert memory['job']['imported_recording'] and not memory['is_mock']
        entity=next(e for e in memory['entities'] if e['id']==demo['entity']['id'])
        answer=client.post(f'/api/videos/{vid}/query',json={'question':entity['label']+'最后在哪里出现？'}).json()
        assert answer['observations'] and answer['evidence']
        assert client.get(f'/api/videos/{vid}/replay',params={'entity_id':entity['id']}).status_code==200
        assert client.get(f'/media/videos/{vid}',headers={'Range':'bytes=0-99'}).status_code==206
        for frame in memory['frames']:
            assert client.get(f"/media/frames/{vid}/{frame['id']}").status_code==200
        assert client.app.state.db.usage_summary()['requests']==0
    validation={'status':'PASS','checked_at_utc':datetime.now(timezone.utc).isoformat(),'original_video_hashes':invariants,
                'credential_scan':'PASS_NO_KNOWN_SECRET_OR_FORBIDDEN_FILE','known_secret_values_checked':len(secrets),
                'offline_real_recording_import':'PASS','idempotent_import':'PASS','offline_queries_replay_frames_range':'PASS',
                'model_calls_during_package_check':0,'isolated_app_path':str(app_root),'independent_browser_startup':'PENDING'}
    (submission/'package-validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'status':'PASS','originals_unchanged':len(invariants),'package_smoke_path':str(app_root),'model_calls':0},ensure_ascii=False))


if __name__=='__main__':main()
