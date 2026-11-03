"""Local screening of saved real video only; no new semantic model request or DB writes."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from uuid import uuid4

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.activity import ActivityGate
from backend.config import Settings
from backend.media import extract_segment_frames


def main():
    settings=Settings.from_env();base='http://127.0.0.1:8000'
    before=httpx.get(base+'/api/health').json()['usage']['requests']
    with sqlite3.connect(f"file:{(settings.data_dir/'memory.sqlite3').as_posix()}?mode=ro",uri=True) as connection:
        connection.row_factory=sqlite3.Row
        row=connection.execute('SELECT id,duration_ms,original_path,metadata FROM videos WHERE active_run IS NOT NULL ORDER BY rowid DESC LIMIT 1').fetchone()
    assert row,'No saved video'
    video=dict(row);video['metadata']=json.loads(video['metadata'])
    url=f"{base}/api/videos/{video['id']}/events"
    memory=httpx.get(url).json()
    digest=lambda data:hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    original=digest(memory);entities={e['id']:e for e in memory['entities']}
    gate=ActivityGate(settings);work=settings.data_dir/'activity-real-screen'/uuid4().hex
    results=[];started=time.monotonic()
    for index in range(4):
        start=index*settings.long_window_stride_seconds*1000
        end=min(start+settings.long_window_seconds*1000,video['duration_ms'])
        if start>=end:break
        frames=extract_segment_frames(video,work/'frames',settings,start,end,settings.activity_filter_fps,max_width=320)
        decision=gate.evaluate(frames,start,end)
        results.append({'start_ms':start,'end_ms':end,**decision})
        if not decision['skip']:
            gate.analyzed(start,end,[e for e in memory['events'] if start<=e['start_ms'] and e['end_ms']<=end],entities)
        if end==video['duration_ms']:break
    assert digest(httpx.get(url).json())==original
    assert httpx.get(base+'/api/health').json()['usage']['requests']==before
    report={'local_screening_only':True,'video_id':video['id'],'new_model_calls':0,'original_events_unchanged':True,
            'reused_saved_events_for_context':True,'elapsed_seconds':round(time.monotonic()-started,2),'sampled_windows':results}
    (ROOT/'evidence/activity-real-screen.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
