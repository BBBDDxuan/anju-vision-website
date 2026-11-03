"""Read-only production UI check: confirm deletion entry then cancel; never delete user videos."""
import hashlib
import json
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

from backend.config import ROOT, Settings
from backend.db import Database


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode('utf8')).hexdigest()


def main():
    settings = Settings.from_env();db=Database(settings.data_dir)
    backup = Path((settings.data_dir/'home-delete-backup-path.txt').read_text(encoding='utf8'))
    manifest = json.loads((backup/'manifest.json').read_text(encoding='utf8'))
    base='http://127.0.0.1:8000';before=httpx.get(base+'/api/health').json()
    selected=manifest['videos'][0]['video_id'];errors=[]
    assert httpx.get(base+'/workspace').status_code == 200
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1600,'height':1100})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(base)
        page.locator('.landing-page').wait_for()
        assert page.get_by_label('上传 MP4').count() == 0
        page.screenshot(path=str(ROOT/'evidence/home-real-desktop.png'),full_page=True,animations='disabled')
        page.get_by_role('button',name='打开记忆工作台',exact=True).click()
        page.get_by_label('上传 MP4').wait_for()
        page.locator('#video-select').select_option(selected)
        page.wait_for_function('() => document.querySelector("video").readyState >= 2')
        assert page.locator('video').count() == 1
        page.get_by_role('button',name='删除视频',exact=True).click()
        page.get_by_role('dialog',name='删除这段录像？').wait_for()
        assert manifest['videos'][0]['name'] in page.get_by_role('dialog',name='删除这段录像？').inner_text()
        page.get_by_role('button',name='取消',exact=True).click()
        assert httpx.get(f'{base}/api/videos/{selected}').status_code == 200
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(ROOT/'evidence/home-workspace-real.png'),animations='disabled')
        page.get_by_label('你想找回什么线索？',exact=True).fill('笔记本电脑最后在哪里出现？')
        page.get_by_role('button',name='提问',exact=True).click()
        page.locator('.answer-result').wait_for()
        assert page.locator('.answer-result .reference').count() > 0
        page.reload()
        page.get_by_role('button',name='删除视频',exact=True).wait_for()
        assert page.locator('#video-select').input_value()==selected
        page.goto(base)
        page.locator('.landing-page').wait_for()
        page.set_viewport_size({'width':390,'height':844})
        page.wait_for_function('() => document.querySelector(".sidebar").getBoundingClientRect().right <= 0')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(ROOT/'evidence/home-real-mobile.png'),full_page=True,animations='disabled')
        assert not errors,errors
        browser.close()
    assert {v['id'] for v in db.list_videos()} == {v['video_id'] for v in manifest['videos']}
    for video in manifest['videos']:
        assert digest(db.video(video['video_id'])) == video['video_hash']
        assert digest(db.review_records(video['video_id'])) == video['review_hash']
        if 'raw_hash' in video: assert digest(db.raw_memory(video['video_id'])[0]) == video['raw_hash']
    after=httpx.get(base+'/api/health').json()
    assert after['usage']['requests']==before['usage']['requests']==manifest['usage']['requests']
    report={'saved_results_are_real':True,'new_model_calls':0,'actual_user_videos_deleted':0,
            'existing_videos_preserved':len(manifest['videos']),'raw_memories_and_reviews_unchanged':True,
            'passed':['home_default','workspace_entry','delete_entry_and_cancel','saved_evidence_qa',
                      'workspace_reload','mobile_home_no_overflow'],'page_errors':errors}
    (ROOT/'evidence/home-delete-real-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
