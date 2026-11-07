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
const playCard = (page: Page, id: string) => card(page, id).locator('.av-card-actions .av-text-link').click();

test('家庭账号头像和核心能力演示在合并后共同可用', async ({ page }) => {
  await page.goto('/#capabilities');
  await expect(page.locator('.av-video-card')).toHaveCount(3);
  await page.getByRole('button', { name: '个人信息', exact: true }).click();
  await expect(page.getByRole('region', { name: '当前账号' })).toContainText('演示验收');
  await expect(page.getByRole('button', { name: '家庭管理', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: '个人信息', exact: true })).toBeFocused();
});

test('官网采用已批准文案并保持工作台入口去重', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#hero-title')).toHaveText('让牵挂，有迹可循。');
  const homeImage = page.locator('.av-hero-image');
  await expect(homeImage).toHaveAttribute('src', '/assets/home-care-watering.webp');
  await expect(homeImage).toHaveAttribute('alt', '温暖客厅中，老人微笑着为花草浇水的居家生活画面');
  await expect.poll(() => homeImage.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth === 1448 && img.naturalHeight === 1086)).toBe(true);
  await expect(page.locator('.av-hero-description')).toHaveText('不用时刻守着监控，也不错过需要留意的瞬间。AI 自动分析居家录像，提供跌倒、挥手和明火安全提醒，标记需要关注的时刻，让每一份关心都清晰可查。');
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
  await expect(page.locator('.av-video-card').nth(1).locator('h3')).toHaveText('挥手动作识别');
  await expect(page.locator('.av-video-card').nth(2).locator('h3')).toHaveText('明火异常识别');
  await expect(page.locator('.av-video-card').nth(0).locator('.av-card-description')).toHaveText('识别人体跌倒及异常躺卧姿态。');
  await expect(page.locator('.av-card-caveat,.av-detection-source')).toHaveCount(0);
  await expect(page.locator('.av-video-card').nth(1).locator('.av-card-description')).toHaveText('识别连续挥手动作及手部运动轨迹。');
  await expect(page.locator('.av-demo-intro')).toHaveCount(1);
  await expect(page.locator('.av-video-card').nth(2).locator('.av-card-description')).toHaveText('识别画面中的火焰区域，及时发现明火风险。');
  await expect(page.locator('.av-demo-intro')).toContainText('已保存的离线分析演示');

  await page.locator('#workflow').scrollIntoViewIfNeeded();
  await expect(page.locator('#workflow-title')).toHaveText('完整链路，还原始末。');
  await expect(page.locator('#workflow .av-section-top > p')).toHaveText('从原始录像到事件结论，每一步都清晰可追溯。');
  await expect(page.getByRole('tab')).toHaveCount(4);
  for (const [index, label] of ['原始视频留存', '智能安全分析', '事件自动摘要', '证据定位回看'].entries()) {
    await expect(page.getByRole('tab').nth(index)).toContainText(label);
  }
  await expect(page.getByRole('tab').nth(0)).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.av-workflow-panel-heading h3')).toHaveText('需要关注的时刻，一键回看。');
  await expect(page.locator('.av-workflow-panel-heading p')).toHaveText('完整保留事件前后原始录像，可自由播放、拖动时间轴，直观查看事件全过程。');
  for (const fact of ['人物跌倒实景测试', '8.55 秒', '已保存的 AI 分析', '1 条跌倒提醒']) {
    await expect(page.locator('#workflow-stage-panel')).toContainText(fact);
  }

  await page.locator('#ai-qa').scrollIntoViewIfNeeded();
  await expect(page.locator('#qa-title')).toHaveText('开口即查，答必有据。');
  await expect(page.locator('.av-qa-copy > p:not(.av-section-label)')).toHaveText('不用耗费时间翻找长录像，用日常对话就能查询居家情况。所有回答均关联对应视频片段，结论清晰、可查可验。');
  for (const question of ['这段视频里有没有需要关注的异常？', '有没有识别到挥手动作？', '有没有识别到明火？', '帮我找到相关的视频片段。']) {
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

test('摄像头页去除重复大标题，保留面包屑、操作和安全状态', async ({ page }) => {
  await page.goto('/workspace?view=camera');
  await expect(page.getByRole('heading', { name: '实时摄像头', exact: true })).toHaveCount(0);
  await expect(page.locator('.topbar .breadcrumb strong')).toHaveText('实时摄像头');
  await expect(page.getByRole('button', { name: '搜索摄像头', exact: true })).toBeVisible();
  await expect(page.locator('.camera-capability-list')).toHaveCount(0);
  await expect(page.locator('.camera-safety')).toContainText('跌倒识别');
  await expect(page.locator('.camera-safety')).toContainText('挥手识别');
  await expect(page.locator('.camera-safety')).toContainText('明火识别');
  await expect(page.locator('.live-camera-heading > h2')).toHaveCount(0);
  await expect(page.locator('.live-camera-workspace')).toHaveCount(1);
  await expect(page.locator('.live-camera-metrics')).toContainText('画面人数');
});

test('三段真实视频与公开资产可加载，跌倒与明火使用各自真实导出', async ({ page, request }) => {
  const expectedHashes: Record<string, string> = {
    fall: '24bc42907926bf3981bdedc2a4f54bc32454fa06a1228b7cf02ac181ba584938',
    help: 'f7f45b919cb03fb59fec122f92040cdc9a13e94d05f4c152bb60f092eb3c15a3',
    fire: '3105463a9a3785c1ca38a6d42c7c4e7bed6bb5df59a9d1cf14efc007823d9444',
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
    if (item.id === 'help') {
      await expect(card(page, item.id)).toHaveAttribute('data-result-status', 'pose_based_heuristic');
      await expect(page.locator('.av-demo-intro')).toContainText('离线分析演示');
      await expect(card(page, item.id)).toContainText('连续挥手动作');
      expect(await card(page, item.id).innerText()).not.toMatch(/\d+%|模型复核支持|识别成功/);
    }
  }
  await expect(card(page, 'fire')).toContainText('检测到火焰区域');
  await expect(card(page, 'fire')).toHaveAttribute('data-result-status', 'available');
  await expect(card(page, 'fall')).toContainText('检测到跌倒姿态');
  await expect(page.locator('.av-mini-target,.av-workflow-image,.av-demo-events,dialog')).toHaveCount(0);
  for (const frame of fall.event.referencedFrames) {
    expect((await request.get(frame.image)).ok()).toBe(true);
  }
  const privateRequest = await request.get('/demo/fall_analysis/raw/events.json');
  expect((await privateRequest.text())).not.toContain('prompt_sha256');
});

test('卡片原位播放互斥、暂停、重播，不改变锚点或页面位置', async ({ page }) => {
  await page.goto('/#capabilities');
  await expect.poll(() => page.locator('#capabilities .av-section-top').evaluate(element => Math.abs(element.getBoundingClientRect().top - 100))).toBeLessThan(2);
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

test('三个核心能力视频都有画面内暂停按钮，进度条使用统一绿色且挥手来源标签在左上角', async ({ page }) => {
  await page.goto('/#capabilities');
  const accents: string[] = [];
  for (const id of ['fall', 'help', 'fire']) {
    const current = card(page, id), media = current.locator('.av-card-media'), video = current.locator('video');
    await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThanOrEqual(1);
    accents.push(await current.locator('input[type=range]').evaluate(element => getComputedStyle(element).accentColor));
    await current.locator('.av-card-actions .av-text-link').click();
    await expect.poll(() => paused(video)).toBe(false);
    await expect(current.locator('.av-media-pause')).toBeVisible();
    await current.locator('.av-media-pause').click();
    await expect.poll(() => paused(video)).toBe(true);
    if (id === 'help') {
      await expect(current.locator('.av-real-footage')).toHaveCount(0);
      await expect(page.locator('.av-demo-intro')).toHaveCount(1);
    }
  }
  expect(new Set(accents).size).toBe(1);
});

test('关闭循环后卡片完整原片正常结束，重播与离屏暂停正常', async ({ page }) => {
  await page.goto('/#capabilities');
  await playCard(page, 'help');
  const help = card(page, 'help').locator('video');
  await expect.poll(() => paused(help)).toBe(false);
  await help.evaluate((element: HTMLVideoElement) => { element.loop = false; element.currentTime = element.duration - .15; });
  await expect.poll(() => paused(help)).toBe(true);
  expect(Math.abs(await time(help) - await help.evaluate((element: HTMLVideoElement) => element.duration))).toBeLessThan(.08);
  await card(page, 'help').getByRole('button', { name: '挥手动作线索：从头重播' }).click();
  await expect.poll(() => paused(help)).toBe(false);
  await page.locator('#devices').scrollIntoViewIfNeeded();
  await expect.poll(() => paused(help)).toBe(true);
});

test('工作流程四阶段手动切换，共用唯一视频并保留真实画面和疑似提示', async ({ page }) => {
  await page.goto('/#workflow');
  await expect(page.locator('.av-workflow-demo video')).toHaveCount(1);
  await video(page).evaluate(element => { element.setAttribute('data-persistent', 'yes'); });
  await expect(stage(page, '原始视频留存')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('tabpanel')).toContainText('8.55');
  await stage(page, '智能安全分析').click();
  await expect(page.getByRole('tabpanel')).toContainText('检测到跌倒姿态');
  await expect(page.getByRole('tabpanel')).toContainText('出现时间');
  await page.getByRole('button', { name: /00:04.50–00:06.00 播放相关片段/ }).click();
  await expect.poll(async () => Math.abs(await time(video(page)) - 4.5)).toBeLessThan(.06);
  await stage(page, '事件自动摘要').click();
  await expect(page.getByRole('group', { name: '相关视频画面' }).getByRole('button')).toHaveCount(4);
  await stage(page, '证据定位回看').click();
  await expect(page.getByRole('tabpanel')).toContainText('相关片段');
  await expect(video(page)).toHaveAttribute('data-persistent', 'yes');
  await page.getByRole('button', { name: '上一阶段' }).click();
  await expect(stage(page, '事件自动摘要')).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('button', { name: '下一阶段' }).click();
  await expect(stage(page, '证据定位回看')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('button', { name: '下一阶段' })).toBeDisabled();
});

test('四张最新引用关键帧逐一精确定位，快速点击最后一次生效', async ({ page }) => {
  await page.goto('/#workflow');
  await stage(page, '事件自动摘要').click();
  const buttons = page.getByRole('group', { name: '相关视频画面' }).getByRole('button');
  for (let index = 0; index < fall.event.referencedFrames.length; index++) {
    await buttons.nth(index).click();
    await expect.poll(async () => Math.abs(await time(video(page)) - fall.event.referencedFrames[index].time_ms / 1000)).toBeLessThan(.06);
    await expect.poll(() => paused(video(page))).toBe(true);
  }
  await buttons.evaluateAll(elements => { for (const index of [2, 0, 1, 3]) (elements[index] as HTMLButtonElement).click(); });
  await expect.poll(async () => Math.abs(await time(video(page)) - 6)).toBeLessThan(.06);
});

test('回看原片从4.5秒播放至6.0秒自动暂停，可重播和手动暂停', async ({ page }) => {
  await page.goto('/#workflow');
  await stage(page, '证据定位回看').click();
  await page.getByRole('button', { name: '回看原片', exact: true }).click();
  await expect.poll(() => paused(video(page))).toBe(false);
  expect(await time(video(page))).toBeGreaterThanOrEqual(4.5);
  expect(await time(video(page))).toBeLessThan(5.3);
  await expect.poll(() => paused(video(page)), { timeout: 8000 }).toBe(true);
  expect(Math.abs(await time(video(page)) - 6)).toBeLessThan(.08);
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
  await page.route('**/demo/videos/help.mp4*', route => route.abort());
  await page.goto('/#capabilities');
  await expect(card(page, 'help').getByRole('alert')).toContainText('视频加载失败');
  await playCard(page, 'fire');
  await expect.poll(() => paused(card(page, 'fire').locator('video'))).toBe(false);
});

test('问答沿用跌倒与明火证据，挥手时序规则不声称确认求助意图', async ({ page }) => {
  const posts: string[] = [];
  page.on('request', request => { if (request.method() === 'POST') posts.push(request.url()); });
  await page.goto('/#ai-qa');
  await expect(page.locator('.av-chat-demo')).toContainText('预设问答演示');
  await expect(page.locator('.av-answer-bubble')).toContainText('00:04.50–00:06.00');
  await page.locator('.av-qa-evidence').click();
  await expect(page.getByRole('region', { name: '示例证据详情' })).toContainText('真实原片关键帧');
  for (const name of ['有没有识别到挥手动作？']) {
    await page.getByRole('button', { name, exact: true }).click();
    await expect(page.locator('.av-answer-bubble')).toContainText('连续挥手动作');
    expect(await page.locator('.av-answer-bubble').innerText()).not.toMatch(/正在求救|正在求助|不能据此/);
    await expect(page.locator('.av-qa-evidence,#av-qa-evidence-detail img')).toHaveCount(0);
  }
  await page.getByRole('button', { name: '有没有识别到明火？', exact: true }).click();
  await expect(page.locator('.av-answer-bubble')).toContainText('检测到明火，请及时关注');
  await expect(page.locator('.av-answer-bubble')).toContainText('00:03.03–00:05.76');
  await page.locator('.av-qa-evidence').click();
  await expect(page.getByRole('region', { name: '示例证据详情' }).locator('img')).toHaveAttribute('src', fixture.cases[2].event.referencedFrames[0].image);
  await page.getByRole('button', { name: '帮我找到相关的视频片段。', exact: true }).click();
  await expect(page.locator('.av-qa-evidence')).toHaveAttribute('aria-expanded', 'false');
  expect(await page.locator('#ai-qa').innerText()).not.toMatch(/00:08|00:26|00:41|\d+%/);
  expect(posts).toEqual([]);
});

for (const width of [1440, 768, 390]) {
  test(`${width}px 视频与四阶段支持键盘，证据展开无溢出`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/#capabilities');
    const trigger = card(page, 'help').locator('.av-card-actions .av-text-link');
    await trigger.focus(); await page.keyboard.press('Space');
    await expect.poll(() => paused(card(page, 'help').locator('video'))).toBe(false);
    await page.locator('#workflow').scrollIntoViewIfNeeded();
    await expect.poll(() => paused(card(page, 'help').locator('video'))).toBe(true);
    await stage(page, '原始视频留存').focus();
    await page.keyboard.press('ArrowRight');
    await expect(stage(page, '智能安全分析')).toBeFocused();
    await expect(stage(page, '智能安全分析')).toHaveAttribute('aria-selected', 'true');
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
