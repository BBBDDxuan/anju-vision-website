"""Read-only confidence UI verification on existing real results; no reanalysis or review writes."""
import hashlib
import json
import shutil
import subprocess

import httpx
from playwright.sync_api import sync_playwright

from backend.config import ROOT, Settings
from backend.db import Database


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode('utf8')).hexdigest()


def main():
    settings=Settings.from_env();db=Database(settings.data_dir);base='http://127.0.0.1:8000'
    videos=[v for v in db.list_videos() if v['active_run']]
    assert videos,'No saved real analysis to inspect'
    selected=videos[0]['id'];raw,job=db.raw_memory(selected)
    assert not job['is_mock']
    raw_hash=digest(raw);review_hash=digest(db.review_records(selected))
    before=httpx.get(base+'/api/health').json()
    memory=httpx.get(f'{base}/api/videos/{selected}/events?grouped=true').json()
    counted=subprocess.run([shutil.which('node'),str(ROOT/'scripts/confidence_counts.mjs')],input=json.dumps(memory),
                           capture_output=True,text=True,encoding='utf8',check=True,cwd=ROOT)
    counts=json.loads(counted.stdout);visible=len(counts['high'])+len(counts['medium'])
    errors=[];network=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1600,'height':1100})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('request',lambda request:network.append((request.method,request.url)))
        page.goto(base+'/workspace?video='+selected)
        page.get_by_text(f'{visible} 条线索',exact=True).wait_for()
        assert page.locator('.review-panel').count()==0
        assert page.get_by_role('button',name='人工核对此条',exact=True).count()==0
        page.get_by_role('button',name='展开全部可见事件',exact=True).click()
        expected_first_page=min(20,visible)
        assert page.locator('.event').count()==expected_first_page
        green=page.locator('.event-filters button').filter(has_text='较高可信')
        green.click();assert page.locator('.event').count()==min(20,len(counts['high']))
        assert page.locator('.event.confidence-medium').count()==0
        page.locator('.event-filters button').filter(has_text='谨慎参考').click()
        assert page.locator('.event').count()==min(20,len(counts['medium']))
        assert page.locator('.event.confidence-high').count()==0
        page.locator('.event-filters button').filter(has_text='全部').click()
        page.get_by_role('button',name='折叠重复记录',exact=True).click()
        page.locator('#timeline').scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/'evidence/confidence-real-desktop.png'),animations='disabled')
        page.get_by_label('你想找回什么线索？',exact=True).fill('笔记本电脑最后在哪里出现？')
        page.get_by_role('button',name='提问',exact=True).click();page.locator('.answer-result').wait_for()
        assert '人工复核' not in page.locator('.answer-result').inner_text()
        page.set_viewport_size({'width':390,'height':844})
        page.wait_for_function('() => document.querySelector(".sidebar").getBoundingClientRect().right <= 0')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('#timeline').scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/'evidence/confidence-real-mobile.png'),animations='disabled')
        assert not errors,errors
        browser.close()
    assert digest(db.raw_memory(selected)[0])==raw_hash
    assert digest(db.review_records(selected))==review_hash
    assert not any('/review' in url or '/regions' in url or method in {'DELETE','PUT','PATCH'}
                   or (method=='POST' and not url.endswith('/query')) for method,url in network)
    after=httpx.get(base+'/api/health').json()
    assert before['usage']['requests']==after['usage']['requests']
    report={'saved_results_are_real':True,'new_model_calls':0,'video_id':selected,'raw_events':len(memory['events']),
            'green_events':len(counts['high']),'yellow_events':len(counts['medium']),'hidden_events':len(counts['hidden']),
            'original_memory_and_history_unchanged':True,'passed':['no_manual_review_controls','real_confidence_counts',
            'green_yellow_filters','unfold_visible_only','saved_local_qa','mobile_no_overflow'],'page_errors':errors}
    (ROOT/'evidence/confidence-real-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
