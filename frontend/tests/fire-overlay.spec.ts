import { test, expect, type Page } from '@playwright/test';
import path from 'node:path';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

// Preserve the complete first-stage browser regression suite.
import './fall-overlay.spec';

const card = (page: Page) => page.locator('[data-case=fire]');
const video = (page: Page) => card(page).locator('video');
const overlay = (page: Page) => card(page).locator('.fire-detection-overlay');
const box = (page: Page) => card(page).locator('.fire-box');
const run = process.env.ANJU_CHECK_DIR || path.resolve('../data/run/fire-overlay-check-20261106');

async function open(page: Page) {
  await page.goto('/#capabilities');
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(1);
}
async function seek(page: Page, seconds: number) {
  await card(page).getByRole('slider', { name: '明火演示视频时间' }).fill(String(seconds));
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(seconds, 2);
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => !v.seeking && v.readyState >= 2)).toBe(true);
  await expect.poll(async () => Number(await overlay(page).getAttribute('data-time-ms'))).toBeCloseTo(seconds * 1000, 0);
}

test('明火剪辑已移除前九秒及末尾2.6秒，来源可见且没有虚假分割、骨架或云端请求', async ({ page, request }) => {
  const posts: string[] = [];
  page.on('request', r => { if (r.method() === 'POST') posts.push(r.url()); });
  await open(page);
  const source = await video(page).getAttribute('src');
  expect(source).toBe('/demo/videos/fire.mp4?v=3105463a9a3785c1');
  const clip = await request.get(source!);
  expect(createHash('sha256').update(await clip.body()).digest('hex')).toBe('3105463a9a3785c1ca38a6d42c7c4e7bed6bb5df59a9d1cf14efc007823d9444');
  expect(await video(page).evaluate((v: HTMLVideoElement) => v.duration)).toBeCloseTo(6.62, 2);
  expect(await video(page).getAttribute('poster')).toMatch(/^\/demo\/posters\/fire\.jpg\?v=[a-f0-9]{16}$/);
  await expect(overlay(page)).toHaveAttribute('opacity', '0');
  await expect(overlay(page)).toHaveAttribute('data-source-basis', 'model_review_with_reviewed_annotations');
  await expect(overlay(page)).toHaveCSS('pointer-events', 'none');
  await expect(page.locator('.av-demo-intro')).toHaveCount(1);
  await expect(page.locator('.av-demo-intro')).toContainText('离线分析演示');
  await expect(card(page)).toContainText('检测到火焰区域');
  await expect(card(page)).toContainText('00:03.03–00:05.76');
  await expect(card(page).locator('video')).toHaveCount(1);
  await expect(card(page).locator('.detection-skeleton,.detection-polygon,canvas')).toHaveCount(0);
  expect(await card(page).innerText()).not.toMatch(/\d+%|温度|热力图|人工已确认/);
  await expect(page.getByRole('button', { name: '查看 AI 分析依据', exact: true })).toHaveCount(0);
  const before = (await (await request.get('/api/health')).json()).usage.requests;
  await seek(page, 4);
  expect((await (await request.get('/api/health')).json()).usage.requests).toBe(before);
  expect(posts).toEqual([]);
});

test('明火框逐帧随媒体变化，暂停静止，反向和快速拖动立即更新', async ({ page }) => {
  await open(page); await seek(page, 3.5);
  await expect(overlay(page)).toHaveAttribute('opacity', '1');
  const first = await box(page).getAttribute('height');
  await seek(page, 5.5);
  expect(await box(page).getAttribute('height')).not.toBe(first);
  await expect(card(page).locator('.fire-risk')).toHaveAttribute('visibility', 'visible');
  const frozen = await overlay(page).innerHTML();
  await page.waitForTimeout(200);
  expect(await overlay(page).innerHTML()).toBe(frozen);
  await seek(page, 3.5);
  expect(await box(page).getAttribute('height')).toBe(first);
  await card(page).getByRole('slider').evaluate(node => {
    const range = node as HTMLInputElement;
    for (const value of ['5.2', '3.9', '4.5']) { range.value = value; range.dispatchEvent(new Event('input', { bubbles: true })); }
  });
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(4.5, 2);
  await expect.poll(async () => Number(await overlay(page).getAttribute('data-time-ms'))).toBe(4500);
});

test('已核实窗口外不补画火焰，最后一帧淡出后保持隐藏', async ({ page }) => {
  await open(page);
  for (const t of [0, 2, 3, 6, 6.5]) {
    await seek(page, t);
    await expect(overlay(page)).toHaveAttribute('opacity', '0');
    await expect(overlay(page)).toHaveAttribute('data-state', 'empty');
  }
  await seek(page, 5.78);
  const opacity = Number(await overlay(page).getAttribute('opacity'));
  expect(opacity).toBeGreaterThan(0); expect(opacity).toBeLessThan(1);
  await seek(page, 4.1);
  await expect(overlay(page)).toHaveAttribute('opacity', '1');
});

test('明火循环、从头重播与自然结束清理标记，SVG元素持续复用', async ({ page }) => {
  await open(page); await seek(page, 4);
  const original = await box(page).elementHandle();
  await seek(page, 6.47);
  await card(page).locator('.av-card-actions .av-text-link').click();
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 3000 }).toBeLessThan(1);
  await expect(overlay(page)).toHaveAttribute('opacity', '0');
  await card(page).getByRole('button', { name: '可见火焰场景：从头重播' }).click();
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.currentTime)).toBeLessThan(1);
  expect(await box(page).evaluate((node, before) => node === before, original)).toBe(true);
  await video(page).evaluate((v: HTMLVideoElement) => { v.loop = false; v.currentTime = v.duration - .15; });
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.ended)).toBe(true);
  await expect(overlay(page)).toHaveAttribute('opacity', '0');
});

test('明火播放与鼠标无关，rAF回退、离屏及页面隐藏暂停均沿用共享机制', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(HTMLVideoElement.prototype, 'requestVideoFrameCallback', { value: undefined, configurable: true });
  });
  await open(page); await seek(page, 4);
  await card(page).locator('.av-card-actions .av-text-link').click();
  await page.mouse.move(5, 5);
  await expect.poll(async () => Number(await overlay(page).getAttribute('data-time-ms'))).toBeGreaterThan(4100);
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await card(page).locator('.av-card-actions .av-text-link').click();
  await page.locator('#devices').scrollIntoViewIfNeeded();
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
});

for (const width of [1440, 768, 390]) test(`${width}px明火框缩放、黑边、裁切映射准确且页面无溢出`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 });
  await open(page); await seek(page, 4);
  for (const fit of ['contain', 'cover', 'fill']) {
    await card(page).locator('.av-card-media').evaluate((node, fit) => {
      (node as HTMLElement).style.aspectRatio = '1 / 1';
      const v = node.querySelector('video')!;
      v.style.objectFit = fit; v.style.objectPosition = '25% 70%';
      window.dispatchEvent(new Event('resize'));
    }, fit);
    await page.waitForTimeout(80);
    const error = await box(page).evaluate(node => {
      const rect = (node as SVGRectElement).getBoundingClientRect(), v = document.querySelector<HTMLVideoElement>('[data-case=fire] video')!;
      const bounds = v.getBoundingClientRect(), fit = getComputedStyle(v).objectFit;
      const sx = bounds.width / v.videoWidth, sy = bounds.height / v.videoHeight;
      const scale = fit === 'cover' ? Math.max(sx, sy) : Math.min(sx, sy);
      const w = fit === 'fill' ? bounds.width : v.videoWidth * scale;
      const h = fit === 'fill' ? bounds.height : v.videoHeight * scale;
      const svg = node as SVGRectElement;
      return Math.max(Math.abs(rect.x - (bounds.left + (bounds.width - w) * .25 + svg.x.baseVal.value / 854 * w)),
        Math.abs(rect.y - (bounds.top + (bounds.height - h) * .7 + svg.y.baseVal.value / 480 * h)),
        Math.abs(rect.width - svg.width.baseVal.value / 854 * w), Math.abs(rect.height - svg.height.baseVal.value / 480 * h));
    });
    expect(error).toBeLessThan(1);
  }
  await card(page).locator('.av-card-media').evaluate(node => {
    (node as HTMLElement).style.removeProperty('aspect-ratio');
    const v = node.querySelector('video')!; v.style.removeProperty('object-fit'); v.style.removeProperty('object-position');
    window.dispatchEvent(new Event('resize'));
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await card(page).screenshot({ path: path.join(run, `fire-card-${width}.png`) });
  await page.screenshot({ path: path.join(run, `fire-viewport-${width}.png`) });
});

test('明火卸载无页面异常，减少动态效果下仍然同步，跌倒与新版挥手素材正确', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await open(page); await seek(page, 4);
  await expect(box(page)).toHaveCSS('animation-name', 'none');
  await expect(page.locator('[data-case=fall] .detection-skeleton')).toHaveCount(1);
  await expect(page.locator('[data-case=help] .wave-detection-overlay')).toHaveCount(1);
  for (const [name, expected] of [['fall', '24bc42907926bf3981bdedc2a4f54bc32454fa06a1228b7cf02ac181ba584938'],
    ['help', 'f7f45b919cb03fb59fec122f92040cdc9a13e94d05f4c152bb60f092eb3c15a3']]) {
    expect(createHash('sha256').update(await readFile(path.resolve(`public/demo/videos/${name}.mp4`))).digest('hex')).toBe(expected);
  }
  await page.getByRole('banner').getByRole('link', { name: '进入工作台', exact: true }).click();
  await expect(overlay(page)).toHaveCount(0);
  await page.waitForTimeout(150);
  expect(errors).toEqual([]);
});
