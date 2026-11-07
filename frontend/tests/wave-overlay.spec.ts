import { test, expect, type Page } from '@playwright/test';
import path from 'node:path';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import './fire-overlay.spec';

const analysis = JSON.parse(await readFile(path.resolve('public/assets/analysis/wave/wave_analysis.json'), 'utf8'));
const card = (page: Page) => page.locator('[data-case=help]');
const video = (page: Page) => card(page).locator('video');
const overlay = (page: Page) => card(page).locator('.wave-detection-overlay');
const trail = (page: Page) => card(page).locator('[data-trail=left]');
const run = process.env.ANJU_CHECK_DIR || path.resolve('../data/run/wave-overlay-check-20261106');

async function open(page: Page) {
  await page.goto('/#capabilities');
  await card(page).scrollIntoViewIfNeeded();
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(1);
}
async function seek(page: Page, seconds: number) {
  await card(page).getByRole('slider', { name: '挥手演示视频时间' }).fill(String(seconds));
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement, seconds: number) => !v.seeking && v.readyState >= 2 && Math.abs(v.currentTime - seconds) < .01, seconds)).toBe(true);
  await expect.poll(async () => Number(await overlay(page).getAttribute('data-time-ms'))).toBeCloseTo(seconds * 1000, 0);
}

test('挥手原片确为新附件，来源与独立分析文件可核对且无在线推理', async ({ page, request }) => {
  const posts: string[] = [];
  page.on('request', r => { if (r.method() === 'POST') posts.push(r.url()); });
  await open(page);
  const src = await video(page).getAttribute('src');
  expect(src).toBe('/demo/videos/help.mp4?v=f7f45b919cb03fb5');
  expect(createHash('sha256').update(await (await request.get(src!)).body()).digest('hex')).toBe(analysis.source.videoSha256);
  expect(await video(page).getAttribute('poster')).toMatch(/^\/demo\/posters\/help\.jpg\?v=[a-f0-9]{16}$/);
  await expect(overlay(page)).toHaveAttribute('data-source-basis', 'offline_pose_with_temporal_heuristic');
  await expect(overlay(page)).toHaveCSS('pointer-events', 'none');
  await expect(page.locator('.av-demo-intro')).toContainText('离线分析演示');
  await expect(card(page)).toContainText('检测到连续挥手动作');
  await expect(card(page)).toContainText('00:00.73–00:03.87');
  await expect(card(page).locator('video')).toHaveCount(1);
  await expect(card(page).locator('[data-joint]')).toHaveCount(7);
  await expect(card(page).locator('[data-joint="11"],canvas')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '查看 AI 分析依据', exact: true })).toHaveCount(0);
  const exported = await request.get('/assets/analysis/wave/wave_analysis.json');
  expect(await exported.json()).toEqual(analysis);
  const before = (await (await request.get('/api/health')).json()).usage.requests;
  await seek(page, 2);
  expect((await (await request.get('/api/health')).json()).usage.requests).toBe(before);
  expect(posts).toEqual([]);
});

test('挥手框骨架及轨迹使用对应解码帧的真实数值，暂停与反向拖动没有残留', async ({ page }) => {
  await open(page); await seek(page, 2);
  const index = Number(await overlay(page).getAttribute('data-frame-index'));
  const p = analysis.frames[index].people[0];
  expect(Number(await card(page).locator('.detection-box').getAttribute('x'))).toBeCloseTo(p.box.x * 720, 6);
  const wrist = p.keypoints.find((k: { id: number }) => k.id === 9);
  expect(Number(await card(page).locator('[data-joint="9"]').getAttribute('cx'))).toBeCloseTo(wrist.x * 720, 6);
  expect(await trail(page).getAttribute('points')).toBe(p.wave.wrists.find((w: { side: string }) => w.side === 'left').trajectory.map((k: { x: number; y: number }) => `${k.x * 720},${k.y * 1280}`).join(' '));
  const frozen = await overlay(page).innerHTML();
  await page.waitForTimeout(180);
  expect(await overlay(page).innerHTML()).toBe(frozen);
  await seek(page, 3);
  const later = await trail(page).getAttribute('points');
  await seek(page, 1.1);
  expect(await trail(page).getAttribute('points')).not.toBe(later);
  expect((await trail(page).getAttribute('points'))!.split(' ').length).toBeLessThan(12);
  await seek(page, 2);
  expect(await overlay(page).innerHTML()).toBe(frozen);
  await card(page).getByRole('slider').evaluate(node => {
    const range = node as HTMLInputElement;
    for (const value of ['3.4', '.2', '2.5']) { range.value = value; range.dispatchEvent(new Event('input', { bubbles: true })); }
  });
  await expect.poll(async () => Number(await overlay(page).getAttribute('data-time-ms'))).toBe(2500);
});

test('举手之前与片尾后不显示挥手，左右手各自遵守实际证据区间', async ({ page }) => {
  await open(page);
  for (const t of [0, .5, .73]) {
    await seek(page, t);
    await expect(card(page).locator('.wave-risk')).toHaveAttribute('visibility', 'hidden');
    await expect(card(page).locator('[data-trail][visibility=visible]')).toHaveCount(0);
    await expect(card(page).getByRole('status')).toHaveText('正在追踪手部动作');
  }
  await seek(page, .75);
  await expect(card(page).locator('[data-wrist=right]')).toHaveAttribute('visibility', 'visible');
  await expect(card(page).locator('[data-wrist=left]')).toHaveAttribute('visibility', 'hidden');
  await seek(page, 2);
  await expect(card(page).locator('.wave-risk')).toHaveAttribute('visibility', 'visible');
  await expect(card(page).getByRole('status')).toHaveText('检测到连续挥手动作');
  await seek(page, 3.8);
  await expect(card(page).locator('[data-wrist=right]')).toHaveAttribute('visibility', 'hidden');
  await seek(page, 3.88);
  await expect(overlay(page)).toHaveAttribute('opacity', '0');
  await expect(overlay(page)).toHaveAttribute('data-state', 'empty');
});

test('挥手循环与从头重播重置轨迹，关闭循环后的自然结束不留标记', async ({ page }) => {
  await open(page); await seek(page, 2);
  const original = await trail(page).elementHandle();
  await seek(page, 3.7);
  await card(page).locator('.av-card-actions .av-text-link').click();
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 3000 }).toBeLessThan(.5);
  await expect(card(page).locator('.wave-risk')).toHaveAttribute('visibility', 'hidden');
  await card(page).getByRole('button', { name: '挥手动作线索：从头重播' }).click();
  expect(await trail(page).evaluate((node, before) => node === before, original)).toBe(true);
  await video(page).evaluate((v: HTMLVideoElement) => { v.loop = false; v.currentTime = v.duration - .15; });
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.ended)).toBe(true);
  await expect(overlay(page)).toHaveAttribute('opacity', '0');
});

test('挥手沿用rAF回退、后台和离屏暂停，鼠标移开仍持续同步', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(HTMLVideoElement.prototype, 'requestVideoFrameCallback', { value: undefined, configurable: true }));
  await open(page); await seek(page, 2);
  await card(page).locator('.av-card-actions .av-text-link').click();
  await page.mouse.move(2, 2);
  await expect.poll(async () => Number(await overlay(page).getAttribute('data-time-ms'))).toBeGreaterThan(2150);
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); });
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  const paused = await overlay(page).innerHTML();
  await page.waitForTimeout(120); expect(await overlay(page).innerHTML()).toBe(paused);
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange')); });
  await card(page).locator('.av-card-actions .av-text-link').click();
  await page.locator('#devices').scrollIntoViewIfNeeded();
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
});

for (const width of [1440, 768, 390]) test(`${width}px挥手竖屏视频的框骨架轨迹适配缩放、裁切与拉伸`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 });
  await open(page); await seek(page, 2.5);
  for (const fit of ['contain', 'cover', 'fill']) {
    await card(page).locator('.av-card-media').evaluate((node, fit) => {
      (node as HTMLElement).style.aspectRatio = '1 / 1';
      const v = node.querySelector('video')!; v.style.objectFit = fit; v.style.objectPosition = '25% 70%';
      window.dispatchEvent(new Event('resize'));
    }, fit);
    await page.waitForTimeout(70);
    const error = await card(page).locator('[data-joint="9"]').evaluate(node => {
      const c = node as SVGCircleElement, v = document.querySelector<HTMLVideoElement>('[data-case=help] video')!;
      const b = v.getBoundingClientRect(), fit = getComputedStyle(v).objectFit;
      const scale = fit === 'cover' ? Math.max(b.width / 720, b.height / 1280) : Math.min(b.width / 720, b.height / 1280);
      const w = fit === 'fill' ? b.width : 720 * scale, h = fit === 'fill' ? b.height : 1280 * scale;
      const actual = new DOMPoint(c.cx.baseVal.value, c.cy.baseVal.value).matrixTransform(c.getScreenCTM()!);
      return Math.max(Math.abs(actual.x - (b.x + (b.width - w) * .25 + c.cx.baseVal.value / 720 * w)), Math.abs(actual.y - (b.y + (b.height - h) * .7 + c.cy.baseVal.value / 1280 * h)));
    });
    expect(error).toBeLessThan(1);
  }
  await card(page).locator('.av-card-media').evaluate(node => {
    (node as HTMLElement).style.removeProperty('aspect-ratio');
    const v = node.querySelector('video')!; v.style.removeProperty('object-fit'); v.style.removeProperty('object-position');
    window.dispatchEvent(new Event('resize'));
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const overlaps = await card(page).evaluate(node => {
    const a = node.querySelector('.wave-risk')!.getBoundingClientRect(), b = node.querySelector('.av-media-play, .av-media-pause')!.getBoundingClientRect();
    return Math.min(a.right, b.right) > Math.max(a.left, b.left) && Math.min(a.bottom, b.bottom) > Math.max(a.top, b.top);
  });
  expect(overlaps).toBe(false);
  await card(page).screenshot({ path: path.join(run, `wave-card-${width}.png`) });
  await page.screenshot({ path: path.join(run, `wave-viewport-${width}.png`) });
});

test('减少动态效果下挥手仍由媒体时间淡入，路由卸载无异常', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await open(page); await seek(page, .75);
  const opacity = Number(await card(page).locator('.wave-risk').getAttribute('opacity'));
  expect(opacity).toBeGreaterThan(0); expect(opacity).toBeLessThan(1);
  await expect(trail(page)).toHaveCSS('animation-name', 'none');
  await page.getByRole('banner').getByRole('link', { name: '进入工作台', exact: true }).click();
  await expect(overlay(page)).toHaveCount(0);
  await page.waitForTimeout(100);
  expect(errors).toEqual([]);
});
