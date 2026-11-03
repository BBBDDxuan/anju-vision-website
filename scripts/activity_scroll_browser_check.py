"""Browser-only synthetic activity fixtures; production events remain read-only."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.grouping import build_event_groups


def main():
    base = 'http://127.0.0.1:8000'
    video = 'v_ea9e847b90034dbfa32a2731f93c6ba5'
    url = f'{base}/api/videos/{video}/events'
    original = httpx.get(url).json()
    digest = lambda data: hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    before = httpx.get(base+'/api/health').json()['usage']['requests']
    fixture = copy.deepcopy(original)
    person = next(e for e in fixture['entities'] if e.get('entity_type') == 'person')
    person.update(label='测试人物1', identity_status='distinct_candidate')
    fixture['events'], fixture['evidence'] = [], []
    for index,(start,end) in enumerate([(52000,56000),(56000,60000)]):
        frames = [f['id'] for f in fixture['frames'] if start <= f['pts_ms'] <= end]
        assert len(frames) >= 2
        fixture['evidence'].append({'id':f'test_ev_{index}','video_id':video,'start_ms':start,'end_ms':end,'frame_ids':frames})
        fixture['events'].append({'id':f'test_walk_{index}','video_id':video,'subject_id':person['id'],'target_id':None,
            'action':'walk','start_ms':start,'end_ms':end,'epistemic_type':'observed','model_score':.9,
            'uncertainty_reasons':[],'review_status':'model_unreviewed','rule_id':None,'source_event_ids':[],
            'evidence_ids':[f'test_ev_{index}']})
    fixture.update(is_mock=True,event_groups=build_event_groups(fixture,fixture['job']))
    errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route(url+'*',lambda route:route.fulfill(json=fixture))
        page.route(f'{base}/api/videos/{video}/replay*',lambda route:route.fulfill(json={
            'entity':person,'entries':[{'id':e['id'],'start_ms':e['start_ms'],'end_ms':e['end_ms'],
                'epistemic_type':e['epistemic_type'],'event':e,'evidence_ids':e['evidence_ids'],
                'rule_id':None,'source_event_ids':[e['id']]} for e in fixture['events']],'unknowns':[]}))
        claim={'text':'测试人物1在画面中行走（仅供浏览器布局测试）','event_ids':['test_walk_0','test_walk_1'],
               'evidence_ids':['test_ev_0','test_ev_1'],'epistemic_type':'observed'}
        page.route(f'{base}/api/videos/{video}/query',lambda route:route.fulfill(json={
            'status':'answered','observations':[claim]*24,'inferences':[],'unknowns':[],
            'evidence':fixture['evidence'],'coverage_note':'测试模拟回答，不代表真实识别。','is_mock':True,'candidates':[]}))
        page.goto(f'{base}/workspace?video={video}')
        page.locator('.event-group').wait_for()
        group=page.locator('.event-group')
        assert group.count()==1 and group.get_attribute('data-source-count')=='2'
        assert '00:52.0' in group.inner_text() and '01:00.0' in group.inner_text()
        group.locator('summary').click()
        assert group.locator('.event-source').count()==2
        assert group.locator('.reference').count()==2
        page.get_by_role('button',name='展开全部可见事件',exact=True).click()
        assert page.locator('.event').count()==2
        page.get_by_role('button',name='折叠重复记录',exact=True).click()
        page.get_by_role('button',name='踪迹回溯',exact=True).click()
        page.locator('#entity-select').select_option(person['id'])
        page.locator('.replay-entry[data-source-count="2"]').wait_for()
        replay=page.locator('.replay-entry')
        assert replay.count()==1 and replay.get_attribute('data-source-count')=='2'
        replay.get_by_text('展开 2 条原始记录',exact=True).click()
        assert '00:52.0–00:56.0' in replay.inner_text() and '00:56.0–01:00.0' in replay.inner_text()
        page.get_by_label('你想找回什么线索？',exact=True).fill('测试人物1的经过是什么？')
        page.get_by_role('button',name='提问',exact=True).click()
        page.locator('.answer-result').wait_for()
        left=page.locator('#watch');right=page.locator('#ask')
        for panel in [left,right]:
            assert panel.evaluate('(e)=>e.scrollHeight>e.clientHeight && getComputedStyle(e).overflowY==="scroll"')
        left.evaluate('(e)=>e.scrollTop=0');right.evaluate('(e)=>e.scrollTop=0')
        left.evaluate('(e)=>e.scrollTop=180')
        assert left.evaluate('(e)=>e.scrollTop')>0 and right.evaluate('(e)=>e.scrollTop')==0
        left_top=left.evaluate('(e)=>e.scrollTop')
        right.evaluate('(e)=>e.scrollTop=200')
        assert right.evaluate('(e)=>e.scrollTop')>0 and left.evaluate('(e)=>e.scrollTop')==left_top
        right.locator('.reference').first.click()
        page.wait_for_function('() => Math.abs(document.querySelector("video").currentTime-52)<.3')
        page.screenshot(path=str(ROOT/'evidence/activity-scroll-desktop.png'))
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        for panel in [left,right]:
            assert panel.evaluate('(e)=>e.clientHeight<=innerHeight && getComputedStyle(e).overflowY==="scroll"')
        assert not errors,errors
        browser.close()
    assert digest(httpx.get(url).json())==digest(original)
    assert httpx.get(base+'/api/health').json()['usage']['requests']==before
    report={'is_mock':True,'new_model_calls':0,'original_events_unchanged':True,
            'passed':['adjacent_activity_time_range','expand_sources_and_evidence','unfold_toggle','replay_grouping',
                      'independent_desktop_scroll','evidence_seek','mobile_no_overflow'],'page_errors':errors}
    (ROOT/'evidence/activity-scroll-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__': main()
