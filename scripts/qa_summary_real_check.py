"""Read-only browser check of an existing inference; query is local, no model calls."""
import hashlib
import json
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright


def main():
    root=Path(__file__).resolve().parents[1]
    base='http://127.0.0.1:8000'; video='v_ea9e847b90034dbfa32a2731f93c6ba5'
    url=f'{base}/api/videos/{video}/events'
    memory=httpx.get(url).json()
    digest=lambda data:hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    original=digest(memory)
    before=httpx.get(base+'/api/health').json()['usage']['requests']
    errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(f'{base}/workspace?video={video}')
        page.get_by_label('你想找回什么线索？',exact=True).fill('人物1什么时候移动？')
        page.get_by_role('button',name='提问',exact=True).click()
        card=page.locator('.claim.inference').filter(has_text='00:08.0–00:10.0')
        card.wait_for()
        expected='可能：人物1 与黑色双肩包相关的活动（动作未明确）'
        assert card.locator(':scope > p').inner_text()==expected
        assert card.get_attribute('data-confidence')=='medium'
        details=card.locator('.claim-explanations')
        assert details.get_attribute('open') is None
        assert not card.locator(':scope > p').inner_text().startswith('在')
        details.locator('summary').click()
        assert 'sf_000000008000' in details.inner_text() and '人物1（黑色短袖短裤）' in details.inner_text()
        details.locator('summary').click()
        card.locator('.reference').first.click()
        page.wait_for_function('() => Math.abs(document.querySelector("video").currentTime-8)<.3')
        card.scroll_into_view_if_needed()
        page.screenshot(path=str(root/'evidence/qa-summary-real.png'))
        assert not errors,errors
        browser.close()
    assert digest(httpx.get(url).json())==original
    assert httpx.get(base+'/api/health').json()['usage']['requests']==before
    report={'is_mock':False,'new_model_calls':0,'original_events_unchanged':True,'title':expected,
            'passed':['short_inference_title','separate_time_range','reason_collapsed_and_expandable','yellow_preserved','evidence_seek']}
    (root/'evidence/qa-summary-real.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__': main()
