"""One real paid re-extraction after a visual audit, preserving original and revised outputs."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.config import Settings
from backend.db import Database
from backend.events import Extraction
from backend.media import windows
from backend.providers.qwen import QwenProvider
from backend.window_review import replace_window


def main():
    out=ROOT/'evidence/real-long-window-review.json'
    if out.exists():raise RuntimeError('Review already exists, do not repeat paid call')
    report=json.loads((ROOT/'evidence/real-long-qwen.json').read_text(encoding='utf8'))
    settings=Settings.from_env();db=Database(settings.data_dir)
    document,job=db.memory(report['video_id'])
    assert job['status']=='completed'
    run=settings.data_dir/'runs'/job['id']
    checkpoint=json.loads((run/'checkpoint.json').read_text(encoding='utf8'))
    window=list(windows(checkpoint['frames'],document['video_duration_ms'],30000,28000))[13]
    cache=json.loads((run/'window-00013.json').read_text(encoding='utf8'))
    before=db.usage_summary()
    (run/'memory-before-window-review.json').write_text(json.dumps(document,ensure_ascii=False),encoding='utf8')
    replacement=QwenProvider(settings,db).extract(window=window,entities=document['entities'],video=db.video(document['video_id']),job_id=job['id']+'-review',output_tokens=2048)
    candidate=replace_window(document,window,Extraction.model_validate(cache['extraction']),replacement.extraction)
    candidate['model_run']['prompt_version']='event-v0.3 + event-v0.4(window13 re-extraction)'
    db.save_memory(job['id'],candidate)
    checkpoint.update(entities={e['id']:e for e in candidate['entities']},events=candidate['events'],evidence=candidate['evidence'])
    (run/'checkpoint.json').write_text(json.dumps(checkpoint,ensure_ascii=False),encoding='utf8')
    cache['previous_extraction']=cache['extraction'];cache['extraction']=replacement.extraction.model_dump();cache['review_prompt']='event-v0.4'
    (run/'window-00013.json').write_text(json.dumps(cache,ensure_ascii=False),encoding='utf8')
    after=db.usage_summary()
    review={'status':'REEXTRACTED_REQUIRES_VISUAL_QA','is_mock':False,'checked_at_utc':datetime.now(timezone.utc).isoformat(),'window_index':13,
            'video_id':document['video_id'],'job_id':job['id'],'new_requests':after['requests']-before['requests'],'usage':replacement.usage,
            'before_extraction':cache['previous_extraction'],'after_extraction':cache['extraction'],
            'resulting_events':len(candidate['events']),'original_preserved':True,'review_is_human_verified':False}
    job['window_review']={'index':13,'reason':'Visual audit found put_in contradiction; re-extracted using original frames and stricter general prompt','prompt':'event-v0.4'}
    db.save_job(job)
    out.write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf8')
    (ROOT/'data/validation/real-long-memory-reviewed.json').write_text(json.dumps(candidate,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'status':review['status'],'new_requests':review['new_requests'],'events':len(candidate['events']),'after':cache['extraction']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
