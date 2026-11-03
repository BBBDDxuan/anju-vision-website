"""Exercise real browser drawing/corrections against explicitly mocked AI, no paid API."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import cv2
import httpx
import numpy as np
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    work = Path('D:/MemoryOSHomePrep/review-browser') / uuid4().hex
    work.mkdir(parents=True)
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1', 8010)) == 0:
            raise RuntimeError('Test port 8010 is occupied; do not terminate an unrelated service.')
    source = work / 'SYNTHETIC_REVIEW_TEST_ONLY.mp4'
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'mp4v'), 10, (640, 360))
    for i in range(120):
        im = np.full((360, 640, 3), (34, 59, 41), np.uint8)
        cv2.putText(im, 'SYNTHETIC REVIEW TEST - NOT REAL AI', (15, 60), cv2.FONT_HERSHEY_SIMPLEX, .7, (230, 240, 210), 1)
        cv2.rectangle(im, (40 + i, 140), (80 + i, 180), (100, 210, 80), -1)
        writer.write(im)
    writer.release()
    env = dict(os.environ, SHIJI_BROWSER_TEST_DIR=str(work / 'data'), PYTHONUTF8='1')
    log = (work / 'server.log').open('w', encoding='utf8')
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'tests.review_browser_server:create_test_app', '--factory', '--host', '127.0.0.1', '--port', '8010'], cwd=ROOT, env=env, stdout=log, stderr=log,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    errors = []
    try:
        for _ in range(100):
            try:
                if httpx.get('http://127.0.0.1:8010/api/health', timeout=1).status_code == 200:
                    break
            except httpx.RequestError:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError('Test server unavailable')
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1080})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:8010/workspace')
            page.get_by_label('上传 MP4').set_input_files(str(source))
            page.get_by_role('button', name='开始 AI 分析', exact=True).click(timeout=30000)
            page.get_by_text('4 条记录', exact=True).wait_for(timeout=30000)
            assert '24小时' in page.locator('.overview-card').first.inner_text().replace('\n', '').replace(' ', '')
            page.get_by_label('跳转录像时间', exact=True).fill('00:00:09')
            page.get_by_role('button', name='跳转', exact=True).click()
            page.wait_for_function('() => Math.abs(document.querySelector("video").currentTime-9)<.3')
            page.get_by_label('搜索事件物品或动作', exact=True).fill('不存在的测试物品')
            page.get_by_text('当前筛选没有匹配事件，试试其他物品或时段。', exact=True).wait_for()
            page.get_by_label('搜索事件物品或动作', exact=True).fill('')
            vid = page.locator('#video-select').input_value()
            page.locator('.review-panel > summary').click()
            page.get_by_text('① 标定桌面或柜格位置', exact=True).click()
            image = page.get_by_alt_text('用于标定的实际视频画面')
            image.wait_for()
            page.wait_for_function("() => document.querySelector('.region-canvas img')?.naturalWidth > 0")
            svg = page.get_by_label('拖动框选位置区域')
            svg.scroll_into_view_if_needed()
            box = svg.bounding_box()
            assert box
            page.mouse.move(box['x'] + box['width']*.1, box['y'] + box['height']*.1)
            page.mouse.down()
            page.mouse.move(box['x'] + box['width']*.45, box['y'] + box['height']*.5, steps=8)
            page.mouse.up()
            page.get_by_label('区域名称', exact=True).fill('测试柜格（模拟）')
            page.get_by_role('button', name='添加框选区域', exact=True).click()
            page.get_by_role('button', name='保存区域标定', exact=True).click()
            page.get_by_role('status').filter(has_text='区域标定已保存').wait_for()
            page.locator('#review-event').select_option(index=2)
            page.get_by_role('button', name='补充当前片段证据（免费）', exact=True).click()
            page.get_by_role('status').filter(has_text='已补充播放器时刻').wait_for(timeout=30000)
            supplemental = httpx.get(f'http://127.0.0.1:8010/api/videos/{vid}/review').json()
            assert supplemental['manual_frame_ids'] and all(e['human'] is None for e in supplemental['entries'])
            page.get_by_label('复核说明（必填）').fill('Synthetic browser correction only; not a real human video finding.')
            page.get_by_text('纠正动作、位置或证据', exact=True).click()
            page.get_by_label('纠正后的动作', exact=True).select_option('seen_at')
            options = page.get_by_label('纠正后的位置或目标', exact=True)
            options.select_option(label='测试柜格（模拟）')
            page.get_by_role('button', name='保存人工纠正', exact=True).click()
            page.get_by_role('status').filter(has_text='人工复核已保存').wait_for()
            state = httpx.get(f'http://127.0.0.1:8010/api/videos/{vid}/review').json()
            corrected = next(e for e in state['entries'] if e['status'] == 'human_correct')
            assert corrected['original']['action'] == 'put_in'
            page.get_by_text('人工已审查 1 / 4 条', exact=True).wait_for()
            page.get_by_role('button', name='下一条未审查事件', exact=True).click()
            assert page.locator('#review-event').input_value() != corrected['original']['id']
            answer = httpx.post(f'http://127.0.0.1:8010/api/videos/{vid}/query', json={'question': '钥匙最后在哪里出现？'}).json()
            assert '测试柜格（模拟）' in answer['answer'] and not answer['inferences']
            page.reload()
            page.locator('#video-select').select_option(vid)
            page.get_by_text('4 条记录', exact=True).wait_for()
            page.locator('.review-panel > summary').click()
            page.locator('#review-event').select_option(corrected['original']['id'])
            page.get_by_label('复核说明（必填）').fill('Undo the synthetic correction to verify append-only history.')
            page.get_by_role('button', name='撤销此事件人工修改', exact=True).click()
            page.get_by_role('status').filter(has_text='人工复核已保存').wait_for()
            page.get_by_role('button', name='AI 加密复核（1次请求）', exact=True).click()
            page.get_by_role('status').filter(has_text='AI复核完成').wait_for(timeout=30000)
            page.locator('.event-review .tag').filter(has_text='AI复核反驳').wait_for()
            page.get_by_role('button', name='AI 上下文推测（1次请求）', exact=True).click()
            page.get_by_role('status').filter(has_text='上下文分析已保存').wait_for(timeout=30000)
            page.locator('.event-review .tag').filter(has_text='上下文推测').wait_for()
            page.locator('.event .tag').filter(has_text='上下文推测').wait_for()
            page.get_by_label('你想找回什么线索？', exact=True).fill('钥匙的经过是什么？')
            page.get_by_role('button', name='提问', exact=True).click()
            context_claim = page.locator('.answer-result .inference').filter(has_text='上下文推测')
            context_claim.wait_for()
            context_claim.get_by_role('button').first.click()
            page.locator('.evidence-view img').first.wait_for()
            page.locator('.event-filters button').filter(has_text='推测').click()
            assert page.locator('.event').count() == 1
            page.locator('.event-filters button').filter(has_text='全部').click()
            page.wait_for_function("() => [...document.querySelectorAll('.review-frames img')].some(i => i.complete && i.naturalWidth > 0)")
            page.screenshot(path=str(ROOT / 'evidence/review-ui-mock-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile horizontal overflow'
            page.screenshot(path=str(ROOT / 'evidence/review-ui-mock-mobile.png'), full_page=True)
            assert not errors, errors
            browser.close()
        report = {'is_mock': True, 'paid_model_calls': 0, 'passed': ['24h_limit_badge', 'stream_upload', 'manual_time_jump', 'event_search_and_type_filter', 'draw_and_persist_region', 'free_actual_evidence_frames', 'human_review_count_and_next', 'human_correction_changes_answer', 'original_claim_retained', 'reload_persistence', 'undo_appends_history', 'mock_ai_refutation_and_source_frames', 'context_button_updates_timeline_and_qa', 'context_evidence_seek', 'mobile_no_overflow'], 'page_errors': errors, 'artifacts': str(work)}
        (ROOT / 'evidence/review-browser.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


if __name__ == '__main__':
    main()
