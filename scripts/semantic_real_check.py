"""Read-only verification of saved real events; query API is local and never calls a model."""
import hashlib
import json
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def digest(memory):
    # Match the JavaScript snapshot's compact UTF-8 JSON serialization.
    return hashlib.sha256(json.dumps(memory, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def main():
    base = 'http://127.0.0.1:8000'
    before = httpx.get(base+'/api/health').json()['usage']['requests']
    candidates = httpx.get(base+'/api/videos').json()['videos']
    for video in candidates:
        if not video['active_run']: continue
        memory = httpx.get(f"{base}/api/videos/{video['id']}/events").json()
        entities = {e['id']: e for e in memory['entities']}
        people = [e for e in memory['events'] if entities[e['subject_id']].get('entity_type') == 'person']
        move = next((e for e in people if e['action']=='move' and entities.get(e['target_id'],{}).get('entity_type')=='object'), None)
        walk = next((e for e in people if e['action']=='walk' and entities.get(e['target_id'],{}).get('entity_type')=='location'), None)
        seen = next((e for e in people if e['action']=='seen_at' and e['target_id'] is None), None)
        if move and walk and seen: break
    else: raise RuntimeError('No saved video contains all three reported cases')
    memory_hash = digest(memory)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1000})
        page.goto(f"{base}/workspace?video={video['id']}")
        categories=page.get_by_role('navigation',name='事件分类',exact=True)
        categories.get_by_role('button',name='人物事件',exact=True).click()
        search=page.get_by_label('搜索事件物品或动作',exact=True)
        search.fill(entities[move['target_id']]['label'])
        page.get_by_text('有与'+entities[move['target_id']]['label']+'相关的活动',exact=False).first.wait_for()
        assert '移动到 '+entities[move['target_id']]['label'] not in page.locator('#timeline').inner_text()
        search.fill(entities[walk['target_id']]['label'])
        page.get_by_text('在画面中行走（位置参照：'+entities[walk['target_id']]['label']+'）',exact=False).first.wait_for()
        search.fill(entities[seen['subject_id']]['label'])
        page.get_by_text(entities[seen['subject_id']]['label']+' 出现在画面中',exact=False).first.wait_for()
        for text in page.locator('.event p').all_inner_texts():
            assert not text.strip().endswith('出现在')
        page.locator('#timeline').scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/'evidence/semantic-real-desktop.png'))
        browser.close()
    answer=httpx.post(f"{base}/api/videos/{video['id']}/query",json={'question':'人物的活动经过是什么？','entity_id':move['subject_id']}).json()
    assert '具体动作关系未明确' in answer['answer']
    assert '移动到黑色双肩包' not in answer['answer']
    assert digest(httpx.get(f"{base}/api/videos/{video['id']}/events").json())==memory_hash
    assert httpx.get(base+'/api/health').json()['usage']['requests']==before
    report={'video_id':video['id'],'new_model_calls':0,'original_events_unchanged':True,
            'passed':['person_object_move_uncertainty','complete_person_appearance','walk_location_reference','local_qa_wording']}
    (ROOT/'evidence/semantic-real-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__': main()
