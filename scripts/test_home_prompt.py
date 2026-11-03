"""Three bounded real-model development checks; NOT training or a held-out accuracy evaluation."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.config import Settings
from backend.db import Database
from backend.events import normalize
from backend.media import windows
from backend.providers.qwen import QwenProvider


def main():
    out=ROOT/'evidence/home-prompt-real.json'
    if out.exists():raise RuntimeError('Saved checks already exist. Do not repeat paid calls without reviewing them.')
    settings=Settings.from_env();db=Database(settings.data_dir);provider=QwenProvider(settings,db)
    long=json.loads((ROOT/'evidence/real-long-qwen.json').read_text(encoding='utf8'))
    long_doc,long_job=db.memory(long['video_id'])
    saved=json.loads((settings.data_dir/'runs'/long_job['id']/'checkpoint.json').read_text(encoding='utf8'))
    chunks=list(windows(saved['frames'],long_doc['video_duration_ms'],30000,28000))
    short=json.loads((ROOT/'data/validation/real-pipeline.json').read_text(encoding='utf8'))['clips'][0]
    short_doc,short_job=db.memory(short['video_id'])
    short_frames=[{**f,'path':str(settings.data_dir/'runs'/short_job['id']/'frames'/(f['id']+'.jpg'))} for f in short_doc['frames']]
    short_window=list(windows(short_frames,short_doc['video_duration_ms']))[0]
    cases=[('room_start',long_doc,chunks[0]),('room_tail',long_doc,chunks[13]),('shelf_transfer',short_doc,short_window)]
    report={'kind':'PROMPT_DEVELOPMENT_REGRESSION_NOT_HELDOUT','is_mock':False,'prompt_version':provider.event_prompt.version,
            'prompt_sha256':provider.event_prompt.sha256,'maximum_new_requests':3,'starting_usage':db.usage_summary(),'cases':[]}
    def save():out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    save()
    for name,document,window in cases:
        print('Real Qwen prompt check:',name,flush=True)
        result=provider.extract(window=window,entities=document['entities'],video=db.video(document['video_id']),job_id='home-prompt-v05-'+name,output_tokens=2048)
        entities,_,events=normalize(result.extraction,document['video_id'],window,{e['id']:e for e in document['entities']})
        labels={k:v['label'] for k,v in entities.items()}
        record={'name':name,'video_id':document['video_id'],'start_ms':window['start_ms'],'end_ms':window['end_ms'],
                'schema_and_references':'PASS','extraction':result.extraction.model_dump(),'usage':result.usage,
                'events_summary':[{'subject':labels[e['subject_id']],'action':e['action'],'target':labels.get(e['target_id']),
                                   'start_ms':e['start_ms'],'end_ms':e['end_ms'],'uncertainty_reasons':e['uncertainty_reasons']} for e in events]}
        # Checks are applied AFTER inference. Gold expectations are never sent to the model.
        record['no_take_out_from_table']=not any(e['action']=='take_out' and e['target_id'] and labels[e['target_id']] in {'桌子','桌面'} for e in events)
        if name=='room_tail':
            notebooks={k for k,v in labels.items() if '笔记本' in v}
            record['no_notebook_into_bag_false_positive']=not any(e['subject_id'] in notebooks and e['action']=='put_in' for e in events)
            record['notebook_visible_on_table_late']=any(e['subject_id'] in notebooks and e['action']=='seen_at' and e['end_ms']>=390000 for e in events)
            record['exit_evidence_after_390s_or_abstained']=all(e['end_ms']>=390000 for e in events if e['action']=='explicit_exit')
        if name=='shelf_transfer':
            record['visible_movement_not_all_unknown']=any(e['action'] in {'pick_up','move','place_on','put_in'} for e in events)
        report['cases'].append(record);save()
        print(json.dumps({k:v for k,v in record.items() if k not in {'extraction','usage','events_summary'}},ensure_ascii=False),flush=True)
    report['ending_usage']=db.usage_summary()
    report['new_requests']=report['ending_usage']['requests']-report['starting_usage']['requests']
    assert report['new_requests']==3
    report['checked_at_utc']=datetime.now(timezone.utc).isoformat()
    report['result']='COMPLETED_CHECK_CASE_RESULTS_NOT_ACCURACY'
    save()
    print(json.dumps({'new_requests':report['new_requests'],'result':report['result']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
