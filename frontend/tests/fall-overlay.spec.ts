import { test, expect, type Page } from '@playwright/test';
import path from 'node:path';

// Existing recorded-model workflow and workspace regressions remain part of
// acceptance. Workspace API fixtures are explicitly simulated; no paid calls.
import './real-video-cases';
import './workspace-v2-cases';

const card = (page: Page) => page.locator('[data-case=fall]');
const video = (page: Page) => card(page).locator('video');
const overlay = (page: Page) => card(page).locator('.detection-overlay');
const box = (page: Page) => card(page).locator('.detection-box');
const run = process.env.ANJU_CHECK_DIR || path.resolve('../data/run/fall-overlay-check-20261106');

async function open(page: Page) {
  await page.goto('/#capabilities');
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(1);
}
async function seek(page: Page, seconds: number) {
  await card(page).getByRole('slider', { name: '跌倒演示视频时间' }).fill(String(seconds));
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(seconds, 2);
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => !v.seeking && v.readyState >= 2)).toBe(true);
  await expect.poll(async () => Number(await overlay(page).getAttribute('data-time-ms'))).toBeCloseTo(seconds * 1000, 0);
}

test('原始封面无检测框，演示来源明确，未发起推理请求', async ({ page, request }) => {
  const posts: string[] = [];
  page.on('request', r => { if (r.method() === 'POST') posts.push(r.url()); });
  await open(page);
  await expect(overlay(page)).toHaveAttribute('opacity', '0');
  await expect(overlay(page)).toHaveAttribute('data-source', 'demo');
  await expect(page.locator('.av-demo-intro')).toHaveText('以下为已保存的离线分析演示，展示识别结果与画面标记。');
  await expect(card(page)).toContainText('检测到跌倒姿态');
  await expect(overlay(page)).toHaveCSS('pointer-events', 'none');
  const before = (await (await request.get('/api/health')).json()).usage.requests;
  await seek(page, 3.1);
  await expect(overlay(page)).toHaveAttribute('opacity', '1');
  await expect(card(page).locator('.detection-risk')).toHaveAttribute('visibility', 'hidden');
  await expect(card(page).locator('[data-joint][visibility=visible]')).not.toHaveCount(0);
  expect((await (await request.get('/api/health')).json()).usage.requests).toBe(before);
  expect(posts).toEqual([]);
});

test('实际视频时间驱动框、骨架与风险标签，暂停后静止，拖动即更新', async ({ page }) => {
  await open(page);
  await seek(page, 4.9);
  await expect(card(page).locator('.detection-risk')).toHaveAttribute('visibility', 'hidden');
  await seek(page, 5.8);
  await expect(card(page).locator('.detection-risk')).toHaveAttribute('visibility', 'visible');
  await expect(card(page).locator('[data-joint][visibility=visible]')).not.toHaveCount(0);
  const before = await box(page).getAttribute('y');
  await card(page).locator('.av-card-actions .av-text-link').click();
  await page.waitForTimeout(180);
  await card(page).locator('.av-card-actions .av-text-link').click();
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  const frozen = await overlay(page).evaluate(node => node.innerHTML);
  await page.waitForTimeout(300);
  expect(await overlay(page).evaluate(node => node.innerHTML)).toBe(frozen);
  await seek(page, 3.1);
  expect(await box(page).getAttribute('y')).not.toBe(before);
  await expect(card(page).locator('.detection-risk')).toHaveAttribute('visibility', 'hidden');
});

test('淡出取决于媒体时间，循环与重播清除上一轮标记，SVG节点保持复用', async ({ page }) => {
  await open(page);
  const handle = await box(page).elementHandle();
  await seek(page, 6.1);
  await expect.poll(async () => Number(await overlay(page).getAttribute('opacity'))).toBeCloseTo(.5, 2);
  await seek(page, 6.3);
  await expect(overlay(page)).toHaveAttribute('opacity', '0');
  await seek(page, 8.35);
  await card(page).locator('.av-card-actions .av-text-link').click();
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 3000 }).toBeLessThan(1);
  await expect(overlay(page)).toHaveAttribute('opacity', '0');
  await card(page).getByRole('button', { name: '跌倒 / 异常姿态：从头重播' }).click();
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.currentTime)).toBeLessThan(1);
  expect(await box(page).evaluate((node, original) => node === original, handle)).toBe(true);
});

test('requestAnimationFrame回退仍同步视频，鼠标移出与暂停不会独立计时', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(HTMLVideoElement.prototype, 'requestVideoFrameCallback', { value: undefined, configurable: true });
  });
  await open(page);
  await seek(page, 5.7);
  await card(page).locator('.av-card-actions .av-text-link').click();
  await page.mouse.move(5, 5);
  await expect.poll(async () => Number(await overlay(page).getAttribute('data-time-ms'))).toBeGreaterThan(5800);
  await video(page).evaluate((v: HTMLVideoElement) => v.pause());
  const stamp = await overlay(page).getAttribute('data-time-ms');
  await page.waitForTimeout(250);
  expect(await overlay(page).getAttribute('data-time-ms')).toBe(stamp);
});

for (const width of [1440, 768, 390]) test(`${width}px缩放、黑边与裁切保持视频坐标映射`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 });
  await open(page); await seek(page, 5.8);
  for (const fit of ['contain', 'cover', 'fill']) {
    await card(page).locator('.av-card-media').evaluate((node, fit) => {
      const container = node as HTMLElement;
      container.style.aspectRatio = '1 / 1';
      const v = container.querySelector('video')!;
      v.style.objectFit = fit; v.style.objectPosition = '25% 70%';
      window.dispatchEvent(new Event('resize'));
    }, fit);
    await page.waitForTimeout(80);
    const error = await box(page).evaluate(node => {
      const svg = node as SVGRectElement;
      const rect = svg.getBoundingClientRect();
      const v = document.querySelector<HTMLVideoElement>('[data-case=fall] video')!;
      const vrect = v.getBoundingClientRect();
      const style = getComputedStyle(v), fit = style.objectFit;
      const sx = vrect.width / v.videoWidth, sy = vrect.height / v.videoHeight;
      const scale = fit === 'cover' ? Math.max(sx, sy) : Math.min(sx, sy);
      const rw = fit === 'fill' ? vrect.width : v.videoWidth * scale;
      const rh = fit === 'fill' ? vrect.height : v.videoHeight * scale;
      const x = vrect.left + (vrect.width - rw) * .25 + svg.x.baseVal.value / 720 * rw;
      const y = vrect.top + (vrect.height - rh) * .7 + svg.y.baseVal.value / 480 * rh;
      return Math.max(Math.abs(rect.x - x), Math.abs(rect.y - y),
        Math.abs(rect.width - svg.width.baseVal.value / 720 * rw), Math.abs(rect.height - svg.height.baseVal.value / 480 * rh));
    });
    expect(error).toBeLessThan(1);
  }
  await card(page).locator('.av-card-media').evaluate(node => {
    (node as HTMLElement).style.removeProperty('aspect-ratio');
    const v = node.querySelector('video')!; v.style.removeProperty('object-fit'); v.style.removeProperty('object-position');
    window.dispatchEvent(new Event('resize'));
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: path.join(run, `capabilities-viewport-${width}.png`) });
  await card(page).screenshot({ path: path.join(run, `fall-card-${width}.png`) });
  await page.screenshot({ path: path.join(run, `capabilities-${width}.png`), fullPage: true });
});

test('减少动态效果下仍可定位，离屏暂停且卸载后不再更新', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await open(page); await seek(page, 5.8);
  await expect(card(page).locator('.detection-risk')).toHaveCSS('animation-name', 'none');
  await card(page).locator('.av-card-actions .av-text-link').click();
  await page.locator('#devices').scrollIntoViewIfNeeded();
  await expect.poll(() => video(page).evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  await page.getByRole('banner').getByRole('link', { name: '进入工作台', exact: true }).click();
  await expect(overlay(page)).toHaveCount(0);
  await page.waitForTimeout(150);
  expect(errors).toEqual([]);
});
