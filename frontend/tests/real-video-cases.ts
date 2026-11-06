import { test, expect, type Page, type Locator } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';

const fixture = JSON.parse(await readFile(path.resolve('public/demo/demo-public-fixture.json'), 'utf8'));
const fall = fixture.cases[0];
const video = (page: Page) => page.getByLabel('跌倒案例完整原始视频', { exact: true });
const stage = (page: Page, name: string) => page.getByRole('tab', { name: new RegExp(name) });
const time = (locator: Locator) => locator.evaluate((element: HTMLVideoElement) => element.currentTime);
const paused = (locator: Locator) => locator.evaluate((element: HTMLVideoElement) => element.paused);
const card = (page: Page, id: string) => page.locator(`.av-video-card[data-case=${id}]`);
const playCard = (page: Page, id: string) => card(page, id).locator('.av-card-actions button').first().click();

test('官网采用已批准文案并保持工作台入口去重', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#hero-title')).toHaveText('让牵挂，有迹可循。');
  await expect(page.locator('.av-hero-description')).toHaveText('不用时刻守着监控，也不错过需要留意的瞬间。AI 自动分析居家录像，识别疑似跌倒、举手求助、明火异常等风险线索，以时间线串联完整视频证据，让每一份关心都清晰可查。');
  await expect(page.getByRole('link', { name: /开始视频分析/ })).toHaveCount(1);
  await expect(page.locator('.av-actions').getByRole('link', { name: /了解核心能力/ })).toHaveCount(1);
  await expect(page.locator('.av-hero-footnote')).toHaveText('为独居老人照护与家庭安全而设计');
  await expect(page.locator('.av-value-card')).toHaveCount(3);
  await expect(page.locator('.av-value-card').nth(0)).toContainText('完整原片留存');
  await expect(page.locator('.av-value-card').nth(1)).toContainText('异常智能定位');
  await expect(page.locator('.av-value-card').nth(2)).toContainText('证据随时回看');

  await page.locator('#capabilities').scrollIntoViewIfNeeded();
  await expect(page.locator('#capabilities-title')).toHaveText('风险线索，精准捕捉。');
  await expect(page.locator('#capabilities .av-section-top > p')).toHaveText('自动筛查居家风险场景，省去逐帧翻找录像的麻烦。');
  await expect(page.locator('.av-video-card').nth(0).locator('h3')).toHaveText('跌倒 / 异常姿态识别');
  await expect(page.locator('.av-video-card').nth(1).locator('h3')).toHaveText('举手动作识别');
  await expect(page.locator('.av-video-card').nth(2).locator('h3')).toHaveText('明火异常识别');
  await expect(page.locator('.av-video-card').nth(0).locator('.av-card-description')).toHaveText('智能识别人体跌倒、异常躺卧等姿态，自动标记疑似风险事件。');
  await expect(page.locator('.av-video-card').nth(0).locator('.av-card-caveat')).toContainText('AI 仅提供疑似线索参考，最终情况请以原始画面确认为准。');
  await expect(page.locator('.av-video-card').nth(1).locator('.av-card-description')).toHaveText('捕捉画面中的举手动作，标记疑似求助场景。');
  await expect(page.locator('.av-video-card').nth(1).locator('.av-card-caveat')).toContainText('动作识别不代表真实求助，需结合实际画面判断。');
  await expect(page.locator('.av-video-card').nth(2).locator('.av-card-description')).toHaveText('检测画面中的可见光火焰，提示疑似火情风险。');
    await expect(page.locator('.av-video-card').nth(2).locator('.av-card-caveat')).toContainText('打火机火焰，不代表真实火情');

  await page.locator('#workflow').scrollIntoViewIfNeeded();
  await expect(page.locator('#workflow-title')).toHaveText('完整链路，还原始末。');
  await expect(page.locator('#workflow .av-section-top > p')).toHaveText('从原始录像到事件结论，每一步都清晰可追溯。');
  await expect(page.getByRole('tab')).toHaveCount(4);
  for (const [index, label] of ['原始视频留存', '智能分析复核', '事件自动摘要', '证据定位回看'].entries()) {
    await expect(page.getByRole('tab').nth(index)).toContainText(label);
  }
  await expect(page.getByRole('tab').nth(0)).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.av-workflow-panel-heading h3')).toHaveText('先看原片，再做判断。');
  await expect(page.locator('.av-workflow-panel-heading p')).toHaveText('完整保留事件前后原始录像，可自由播放、拖动时间轴，直观查看事件全过程。');
  for (const fact of ['人物跌倒实景测试', '8.55 秒', 'AI 模型分析导出', '1 条疑似跌倒线索']) {
    await expect(page.locator('#workflow-stage-panel')).toContainText(fact);
  }

  await page.locator('#ai-qa').scrollIntoViewIfNeeded();
  await expect(page.locator('#qa-title')).toHaveText('开口即查，答必有据。');
  await expect(page.locator('.av-qa-copy > p:not(.av-section-label)')).toHaveText('不用耗费时间翻找长录像，用日常对话就能查询居家情况。所有回答均关联对应视频片段，结论清晰、可查可验。');
  for (const question of ['这段视频里有没有需要关注的异常？', '有没有识别到求助动作？', '有没有识别到明火？', '帮我找到相关的视频片段。']) {
    await expect(page.getByRole('button', { name: question, exact: true })).toHaveCount(1);
  }
  await expect(page.getByText('基于 AI 分析结果生成回答', { exact: true })).toHaveCount(1);

  await page.locator('#devices').scrollIntoViewIfNeeded();
  await expect(page.locator('#devices-title')).toHaveText('熟悉设备，轻松接入。');
  await expect(page.locator('.av-device-copy > p:not(.av-section-label)')).toHaveText('无需更换现有设备，支持多种视频源接入。把画面交给 AI 值守，把时间留给家人陪伴。');
  await expect(page.locator('.av-connection-card')).toHaveCount(3);
  await expect(page.getByText('本机摄像头', { exact: true })).toHaveCount(1);
  await expect(page.getByText('导入 MP4 录像', { exact: true })).toHaveCount(1);
  await expect(page.getByText('摄像头协议接入', { exact: true })).toHaveCount(1);
  await expect(page.getByText('前往摄像头工作台', { exact: true })).toHaveCount(0);
  await expect(page.locator('.av-device-copy a')).toHaveCount(0);
  await expect(page.locator('.av-header-cta')).toHaveCount(1);
  await expect(page.locator('.av-final-cta .av-button')).toHaveCount(1);
  await expect(page.locator('.av-final-cta .av-button')).toHaveText('进入工作台');
});

test('摄像头页在面板外展示标题与能力标签，指标仍由面板状态提供', async ({ page }) => {
  await page.goto('/workspace?view=camera');
  await expect(page.locator('.camera-page-heading h1')).toHaveText('实时摄像头');
  await expect(page.locator('.camera-capability-list')).toContainText('跌倒');
  await expect(page.locator('.camera-capability-list')).toContainText('举手求助');
  await expect(page.locator('.camera-capability-list')).toContainText('可见明火');
  await expect(page.locator('.live-camera-heading h2')).toHaveCount(0);
  await expect(page.locator('.live-camera-workspace')).toHaveCount(1);
  await expect(page.locator('.live-camera-metrics')).toContainText('画面人数');
});

test('三段真实视频与公开资产可加载，模型结果只来自跌倒真实导出', async ({ page, request }) => {
  const expectedHashes: Record<string, string> = {
    fall: '24bc42907926bf3981bdedc2a4f54bc32454fa06a1228b7cf02ac181ba584938',
    help: '4e2d01616a078617f18d33ac61444250e8ea3c227e61e945011817aef5e3fb3b',
    fire: '8390088c4c5f5bde352dc5e68bbbc536cb707de0e235c2cadaee4bb9b7347c41',
  };
  await page.goto('/#capabilities');
  await expect(page.locator('.av-video-card')).toHaveCount(3);
  for (const item of fixture.cases) {
    const response = await request.get(item.videoSrc);
    expect(response.ok()).toBe(true);
    expect(createHash('sha256').update(await response.body()).digest('hex')).toBe(expectedHashes[item.id]);
    await expect.poll(() => card(page, item.id).locator('video').evaluate((element: HTMLVideoElement) => Number.isFinite(element.duration) && element.readyState >= 1)).toBe(true);
    const properties = await card(page, item.id).locator('video').evaluate((element: HTMLVideoElement) => ({ muted: element.muted, inline: element.playsInline, duration: element.duration }));
    expect(properties.muted).toBe(true); expect(properties.inline).toBe(true);
    expect(Math.abs(properties.duration * 1000 - item.durationMs)).toBeLessThan(60);
    if (item.id !== 'fall') {
      await expect(card(page, item.id)).toHaveAttribute('data-result-status', 'pending_model_fix');
      await expect(card(page, item.id)).toContainText('识别结果待接入');
      await expect(card(page, item.id).locator('.av-card-result')).toHaveCount(0);
      expect(await card(page, item.id).innerText()).not.toMatch(/\d+%|00:\d|识别成功/);
    }
  }
  await expect(card(page, 'fire')).toContainText('打火机');
  await expect(card(page, 'fall')).toContainText('未经人工确认');
  await expect(page.locator('.av-mini-target,.av-workflow-image,.av-demo-events,dialog')).toHaveCount(0);
  for (const frame of fall.event.referencedFrames) {
    expect((await request.get(frame.image)).ok()).toBe(true);
  }
  const privateRequest = await request.get('/demo/fall_analysis/raw/events.json');
  expect((await privateRequest.text())).not.toContain('prompt_sha256');
});

test('卡片原位播放互斥、暂停、重播，不改变锚点或页面位置', async ({ page }) => {
  await page.goto('/#capabilities');
  await expect.poll(() => page.locator('#capabilities').evaluate(element => Math.abs(element.getBoundingClientRect().top - 78))).toBeLessThan(1);
  const initialScroll = await page.evaluate(() => scrollY);
  for (const id of ['fall', 'help', 'fire']) {
    await playCard(page, id);
    await expect.poll(() => paused(card(page, id).locator('video'))).toBe(false);
    await expect.poll(() => page.locator('.av-site video').evaluateAll(elements => elements.filter(element => !(element as HTMLVideoElement).paused).length)).toBe(1);
    await expect(page).toHaveURL(/#capabilities$/);
  }
  expect(Math.abs(await page.evaluate(() => scrollY) - initialScroll)).toBeLessThan(2);
  await playCard(page, 'fire');
  await expect.poll(() => paused(card(page, 'fire').locator('video'))).toBe(true);
  await card(page, 'fire').getByRole('button', { name: '可见火焰场景：从头重播' }).click();
  await expect.poll(() => time(card(page, 'fire').locator('video'))).toBeGreaterThanOrEqual(0);
  expect(await time(card(page, 'fire').locator('video'))).toBeLessThan(.8);
});

test('卡片播放完整原片后结束，离屏时暂停，待接入状态仍可播放', async ({ page }) => {
  await page.goto('/#capabilities');
  await playCard(page, 'help');
  const help = card(page, 'help').locator('video');
  await expect.poll(() => paused(help)).toBe(false);
  await help.evaluate((element: HTMLVideoElement) => { element.currentTime = element.duration - .15; });
  await expect.poll(() => paused(help)).toBe(true);
  expect(Math.abs(await time(help) - await help.evaluate((element: HTMLVideoElement) => element.duration))).toBeLessThan(.08);
  await card(page, 'help').getByRole('button', { name: '举手动作线索：从头重播' }).click();
  await expect.poll(() => paused(help)).toBe(false);
  await page.locator('#devices').scrollIntoViewIfNeeded();
  await expect.poll(() => paused(help)).toBe(true);
});

test('工作流程四阶段手动切换，共用唯一视频并区分本地候选和模型复核', async ({ page }) => {
  await page.goto('/#workflow');
  await expect(page.locator('.av-workflow-demo video')).toHaveCount(1);
  await video(page).evaluate(element => { element.setAttribute('data-persistent', 'yes'); });
  await expect(stage(page, '原始视频留存')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('tabpanel')).toContainText('8.55');
  await stage(page, '智能分析复核').click();
  await expect(page.getByRole('tabpanel')).toContainText('视觉模型复核支持跌倒判断');
  await expect(page.getByRole('tabpanel')).toContainText('未经人工确认');
  await page.getByRole('button', { name: /00:02.30–00:06.30 证据区间/ }).click();
  await expect.poll(async () => Math.abs(await time(video(page)) - 2.3)).toBeLessThan(.06);
  await stage(page, '事件自动摘要').click();
  await expect(page.getByRole('group', { name: '模型结论引用关键帧' }).getByRole('button')).toHaveCount(5);
  await stage(page, '证据定位回看').click();
  await expect(page.getByRole('tabpanel')).toContainText('证据窗口');
  await expect(video(page)).toHaveAttribute('data-persistent', 'yes');
  await page.getByRole('button', { name: '上一阶段' }).click();
  await expect(stage(page, '事件自动摘要')).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('button', { name: '下一阶段' }).click();
  await expect(stage(page, '证据定位回看')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('button', { name: '下一阶段' })).toBeDisabled();
});

test('五张真实引用关键帧逐一精确定位，快速点击最后一次生效', async ({ page }) => {
  await page.goto('/#workflow');
  await stage(page, '事件自动摘要').click();
  const buttons = page.getByRole('group', { name: '模型结论引用关键帧' }).getByRole('button');
  for (let index = 0; index < fall.event.referencedFrames.length; index++) {
    await buttons.nth(index).click();
    await expect.poll(async () => Math.abs(await time(video(page)) - fall.event.referencedFrames[index].time_ms / 1000)).toBeLessThan(.06);
    await expect.poll(() => paused(video(page))).toBe(true);
  }
  await buttons.evaluateAll(elements => { for (const index of [3, 0, 2, 4]) (elements[index] as HTMLButtonElement).click(); });
  await expect.poll(async () => Math.abs(await time(video(page)) - 6.3)).toBeLessThan(.06);
});

test('回看原片从2.3秒播放至6.3秒自动暂停，可重播和手动暂停', async ({ page }) => {
  await page.goto('/#workflow');
  await stage(page, '证据定位回看').click();
  await page.getByRole('button', { name: '回看原片', exact: true }).click();
  await expect.poll(() => paused(video(page))).toBe(false);
  expect(await time(video(page))).toBeGreaterThanOrEqual(2.3);
  expect(await time(video(page))).toBeLessThan(3.1);
  await expect.poll(() => paused(video(page)), { timeout: 8000 }).toBe(true);
  expect(Math.abs(await time(video(page)) - 6.3)).toBeLessThan(.08);
  await page.getByRole('button', { name: '重播证据', exact: true }).click();
  await expect.poll(() => paused(video(page))).toBe(false);
  await page.getByRole('button', { name: '暂停案例视频' }).click();
  await expect.poll(() => paused(video(page))).toBe(true);
});

test('首次媒体加载延迟时仍能定位；切换阶段取消尚未完成的播放', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/demo/videos/fall.mp4', async route => { await gate; await route.continue(); });
  try {
    await page.goto('/#workflow', { waitUntil: 'domcontentloaded' });
    await stage(page, '证据定位回看').click();
    await page.getByRole('button', { name: '回看原片', exact: true }).click();
    await stage(page, '事件自动摘要').click();
    release();
    await expect.poll(() => video(page).evaluate((element: HTMLVideoElement) => element.readyState >= 1)).toBe(true);
    await expect.poll(() => paused(video(page))).toBe(true);
    await page.getByRole('button', { name: '定位到 00:05.00 关键帧' }).click();
    await expect.poll(async () => Math.abs(await time(video(page)) - 5)).toBeLessThan(.06);
  } finally { release(); }
});

test('视频加载失败有可读错误，其他视频仍可播放', async ({ page }) => {
  await page.route('**/demo/videos/help.mp4', route => route.abort());
  await page.goto('/#capabilities');
  await expect(card(page, 'help').getByRole('alert')).toContainText('视频加载失败');
  await playCard(page, 'fire');
  await expect.poll(() => paused(card(page, 'fire').locator('video'))).toBe(false);
});

test('问答仅引用真实跌倒证据，待接入问题无成功事件或证据卡', async ({ page }) => {
  const posts: string[] = [];
  page.on('request', request => { if (request.method() === 'POST') posts.push(request.url()); });
  await page.goto('/#ai-qa');
  await expect(page.locator('.av-chat-demo')).toContainText('预设问答演示');
  await expect(page.locator('.av-answer-bubble')).toContainText('00:02.30–00:06.30');
  await page.locator('.av-qa-evidence').click();
  await expect(page.getByRole('region', { name: '示例证据详情' })).toContainText('真实原片关键帧');
  for (const name of ['有没有识别到求助动作？', '有没有识别到明火？']) {
    await page.getByRole('button', { name, exact: true }).click();
    await expect(page.locator('.av-answer-bubble')).toContainText('不能判断“有”或“没有”');
    await expect(page.locator('.av-qa-evidence,#av-qa-evidence-detail img')).toHaveCount(0);
  }
  await page.getByRole('button', { name: '帮我找到相关的视频片段。', exact: true }).click();
  await expect(page.locator('.av-qa-evidence')).toHaveAttribute('aria-expanded', 'false');
  expect(await page.locator('#ai-qa').innerText()).not.toMatch(/00:08|00:26|00:41|\d+%/);
  expect(posts).toEqual([]);
});

for (const width of [1440, 768, 390]) {
  test(`${width}px 视频与四阶段支持键盘，证据展开无溢出`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/#capabilities');
    const trigger = card(page, 'help').locator('.av-card-actions button').first();
    await trigger.focus(); await page.keyboard.press('Space');
    await expect.poll(() => paused(card(page, 'help').locator('video'))).toBe(false);
    await page.locator('#workflow').scrollIntoViewIfNeeded();
    await expect.poll(() => paused(card(page, 'help').locator('video'))).toBe(true);
    await stage(page, '原始视频留存').focus();
    await page.keyboard.press('ArrowRight');
    await expect(stage(page, '智能分析复核')).toBeFocused();
    await expect(stage(page, '智能分析复核')).toHaveAttribute('aria-selected', 'true');
    await page.keyboard.press('End');
    await expect(stage(page, '证据定位回看')).toHaveAttribute('aria-selected', 'true');
    await page.getByRole('button', { name: '回看原片', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => paused(video(page))).toBe(false);
    await page.locator('.av-qa-evidence').click();
    await expect(page.getByRole('region', { name: '示例证据详情' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.keyboard.press('Space');
    await expect(page.locator('#av-qa-evidence-detail')).toBeHidden();
    await expect(page.locator('.av-qa-evidence')).toBeFocused();
  });
}
