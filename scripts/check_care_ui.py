import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path('data/validation/fire-density-20261021'); out.mkdir(parents=True,exist_ok=True)
errors=[]; results=[]
with sync_playwright() as pw:
    browser=pw.chromium.launch(channel='chrome',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1080})
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8000/')
    page.get_by_role('heading',name=re.compile('多一份关注')).wait_for()
    page.screenshot(path=str(out/'home-desktop.png'),full_page=True)
    results.append({'view':'home-desktop','overflow':page.evaluate('document.documentElement.scrollWidth > innerWidth')})
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(out/'home-mobile.png'),full_page=True)
    results.append({'view':'home-mobile','overflow':page.evaluate('document.documentElement.scrollWidth > innerWidth')})
    page.get_by_role('button',name='打开监测工作台',exact=True).click()
    page.goto('http://127.0.0.1:8000/workspace?video=v_bcd1ac849034412cb54b02120bdfdf08')
    page.locator('.safety-alert').first.wait_for()
    assert page.get_by_role('button',name='安全概览',exact=True).get_attribute('aria-pressed')=='true'
    page.screenshot(path=str(out/'workspace-mobile.png'),full_page=True)
    results.append({'view':'workspace-mobile','overflow':page.evaluate('document.documentElement.scrollWidth > innerWidth')})
    page.set_viewport_size({'width':1440,'height':1080})
    page.screenshot(path=str(out/'workspace-desktop.png'),full_page=True)
    page.locator('#question').fill('视频中什么时候有人跌倒？')
    page.get_by_role('button',name='提问',exact=True).click()
    page.locator('.answer-result').wait_for()
    assert '出现疑似跌倒过程' in page.locator('.answer-result').inner_text()
    page.get_by_role('button',name='查看视频证据 ↗',exact=True).first.click()
    page.wait_for_function('document.querySelector("video").currentTime > 1')
    page.locator('.safety-frames img').first.wait_for(state='visible')
    page.get_by_role('button',name='物体事件',exact=True).click()
    assert page.locator('#safety').count()==0
    page.get_by_role('navigation',name='事件分类').get_by_role('button',name='全部事件',exact=True).click()
    assert page.locator('#safety').count()==1
    page.get_by_role('button',name='人物事件',exact=True).click()
    assert page.get_by_role('button',name='重要事件',exact=True).get_attribute('aria-pressed')=='true'
    results.append({'view':'interactions','generic_fall_qa':True,'evidence_seek':True,'object_category':True,'person_important_default':True})
    page.goto('http://127.0.0.1:8000/workspace?video=v_adfc02b0829849a296ca7c5de0eda766')
    page.locator('.safety-alert').first.wait_for()
    assert page.locator('.safety-alert').count()==3
    page.locator('.event.priority-event').first.wait_for()
    assert page.locator('.event.priority-event').count()==3
    for i, start in enumerate([7.5,4.767,1.267]):
        card=page.locator('.safety-alert').nth(i)
        card.get_by_role('button',name='查看视频证据 ↗',exact=True).click()
        card.locator('.safety-frames img').first.wait_for()
        assert card.locator('.safety-frames img').count()==(5 if i==0 else 2)
        page.wait_for_function('(s) => Math.abs(document.querySelector("video").currentTime-s)<.08',arg=start)
    page.locator('#question').fill('视频中什么时候出现明火？')
    page.get_by_role('button',name='提问',exact=True).click()
    page.locator('.answer-result').wait_for()
    assert page.locator('.answer-result .claim').count()==3
    assert page.locator('.answer-result .reference').count()==3
    page.screenshot(path=str(out/'fire-workspace-desktop.png'),full_page=True)
    results.append({'view':'real-fire','timeline_events':3,'alerts':3,'qa_claims':3,'evidence_seek_all':True,'middle_evidence_frames_preserved':True})
    browser.close()
assert not errors, errors
assert all(not x.get('overflow') for x in results),results
(out/'ui-check.json').write_text(json.dumps({'results':results,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(results,ensure_ascii=False))
