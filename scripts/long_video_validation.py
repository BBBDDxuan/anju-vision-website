"""Real full-length media + explicit mock vision test. It NEVER invokes Qwen."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.config import Settings
from backend.main import create_app
from backend.media import file_sha256
from tests.test_long_video import LongVideoMock


def main():
    source=ROOT/'video/桌面_逆时针90度.mp4'
    work=Path('D:/MemoryOSHomePrep/long-real-media')/datetime.now().strftime('%Y%m%d-%H%M%S')
    settings=Settings(data_dir=work/'data',max_calls=100,max_frame_references=2048,max_output_tokens=200000)
    provider=LongVideoMock()
    started=time.monotonic()
    before=file_sha256(source)
    with TestClient(create_app(settings,provider),base_url='http://localhost') as client:
        with source.open('rb') as stream:
            response=client.post('/api/videos',files={'file':(source.name,stream,'video/mp4')})
        assert response.status_code==201,response.text
        vid=response.json()['video_id']
        upload_ms=round((time.monotonic()-started)*1000)
        response=client.post(f'/api/videos/{vid}/analyze')
        assert response.status_code==202,response.text
        job_id=response.json()['id']
        deadline=time.monotonic()+600
        while time.monotonic()<deadline:
            job=client.get(f'/api/jobs/{job_id}').json()
            print(job['status'],job['stage'],flush=True)
            if job['status'] not in {'queued','running'}:break
            time.sleep(5)
        assert job['status']=='completed',job
        memory=client.get(f'/api/videos/{vid}/events').json()
        assert memory['is_mock'] and memory['events'][-1]['end_ms']>390000
        entity=memory['entities'][0]
        answer=client.post(f'/api/videos/{vid}/query',json={'question':entity['label']+'最后在哪里出现？'}).json()
        assert answer['evidence'][-1]['end_ms']>390000
        assert client.get(f'/media/videos/{vid}',headers={'Range':'bytes=0-99'}).status_code==206
        with client.app.state.db.connect() as c:
            assert c.execute('SELECT COUNT(*) FROM requests').fetchone()[0]==0
        report={'status':'PASS','checked_at_utc':datetime.now(timezone.utc).isoformat(),'source':str(source),'source_sha256':before,
                'source_unchanged':file_sha256(source)==before,'duration_ms':memory['video_duration_ms'],'sampled_frames':len(memory['frames']),
                'windows':job['total_windows'],'completed_windows':job['completed_windows'],'upload_and_prepare_ms':upload_ms,
                'elapsed_ms':round((time.monotonic()-started)*1000),'last_evidence_ms':answer['evidence'][-1]['end_ms'],
                'vision':'EXPLICIT_TEST_MOCK_NOT_REAL_QWEN','mock_events':len(memory['events']),'paid_model_calls':0,
                'limitations':'Validates real long-video media, frame timestamps, batching/storage/query pipeline; NOT AI recognition accuracy or full-length Qwen performance.'}
        (ROOT/'evidence/long-real-media.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
