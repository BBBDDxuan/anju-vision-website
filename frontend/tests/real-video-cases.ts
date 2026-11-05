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
  await expect(card(page, 'fall')).toContainText('不等于人工确认');
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
  await expect(stage(page, '原始视频')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('tabpanel')).toContainText('8.55');
  await stage(page, '风险候选与复核').click();
  await expect(page.getByRole('tabpanel')).toContainText('视觉模型复核支持跌倒判断');
  await expect(page.getByRole('tabpanel')).toContainText('未经人工确认');
  await page.getByRole('button', { name: /00:02.30–00:06.30 证据区间/ }).click();
  await expect.poll(async () => Math.abs(await time(video(page)) - 2.3)).toBeLessThan(.06);
  await stage(page, '事件摘要').click();
  await expect(page.getByRole('group', { name: '模型结论引用关键帧' }).getByRole('button')).toHaveCount(5);
  await stage(page, '证据回看').click();
  await expect(page.getByRole('tabpanel')).toContainText('证据窗口');
  await expect(video(page)).toHaveAttribute('data-persistent', 'yes');
  await page.getByRole('button', { name: '上一阶段' }).click();
  await expect(stage(page, '事件摘要')).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('button', { name: '下一阶段' }).click();
  await expect(stage(page, '证据回看')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('button', { name: '下一阶段' })).toBeDisabled();
});

test('五张真实引用关键帧逐一精确定位，快速点击最后一次生效', async ({ page }) => {
  await page.goto('/#workflow');
  await stage(page, '事件摘要').click();
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
  await stage(page, '证据回看').click();
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
    await stage(page, '证据回看').click();
    await page.getByRole('button', { name: '回看原片', exact: true }).click();
    await stage(page, '事件摘要').click();
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
  for (const name of ['有没有识别到求助？', '有没有识别到明火？']) {
    await page.getByRole('button', { name, exact: true }).click();
    await expect(page.locator('.av-answer-bubble')).toContainText('不能据此判断“有”或“没有”');
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
    await stage(page, '原始视频').focus();
    await page.keyboard.press('ArrowRight');
    await expect(stage(page, '风险候选与复核')).toBeFocused();
    await expect(stage(page, '风险候选与复核')).toHaveAttribute('aria-selected', 'true');
    await page.keyboard.press('End');
    await expect(stage(page, '证据回看')).toHaveAttribute('aria-selected', 'true');
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
