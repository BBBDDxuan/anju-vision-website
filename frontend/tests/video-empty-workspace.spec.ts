import { test, expect, type Page } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { installWorkspaceFixture } from './workspace-fixture.mjs';
import './video-layout.spec';

const run = process.env.ANJU_CHECK_DIR || path.resolve('../data/run/video-empty-workspace-check-20261107');
const selectedVideo = (page: Page) => page.locator('#watch video');
const cards = (page: Page) => page.locator('.workspace-capability-card');

async function expectGuide(page: Page) {
  await expect(page.locator('.workspace-workflow-steps h3')).toHaveText(['导入录像', '选择分析模式', '开始分析并查看结果']);
  await expect(cards(page).locator('h2')).toHaveText(['01跌倒识别', '02挥手动作识别', '03明火识别']);
  await expect(cards(page).locator('p')).toHaveText(['发现人体跌倒及异常姿态。', '识别画面中的连续挥手动作。', '发现画面中的明火区域。']);
  await expect(page.locator('.analysis-inspector,.workspace-inspector-tabs,.workspace-welcome')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '导入居家录像', exact: true })).toBeVisible();
  await expect(page.locator('main h1')).toHaveCount(0);
}

for (const [width, height] of [[1366, 768], [1440, 900], [1920, 1080], [1100, 844], [1024, 844], [768, 844], [390, 844], [320, 844]]) {
  test(`${width}px 新空状态两层布局、等高卡片和响应式`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await installWorkspaceFixture(page, { empty: true, analyzed: false });
    await page.goto('/workspace?view=videos');
    await expectGuide(page);
    await expect(page.getByLabel('当前视频', { exact: true })).toBeDisabled();
    const upload = (await page.locator('#watch').boundingBox())!;
    const guide = (await page.locator('.workspace-workflow').boundingBox())!;
    const capabilities = await cards(page).evaluateAll(elements => elements.map(el => {
      const b = el.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height };
    }));
    if (width > 900) {
      expect(upload.height).toBeGreaterThanOrEqual(360); expect(upload.height).toBeLessThanOrEqual(400);
      expect(Math.abs(upload.y - guide.y)).toBeLessThan(1);
      expect(Math.abs(upload.height - guide.height)).toBeLessThan(1);
      expect(upload.width / (upload.width + guide.width)).toBeGreaterThan(.61);
      expect(upload.width / (upload.width + guide.width)).toBeLessThan(.66);
      expect(capabilities[0].y).toBeGreaterThanOrEqual(upload.y + upload.height + 16);
    } else expect(guide.y).toBeGreaterThan(upload.y + upload.height);
    if (width > 600) {
      expect(Math.abs(capabilities[0].y - capabilities[2].y)).toBeLessThan(1);
      const headings = await cards(page).locator('h2').evaluateAll(elements => elements.map(el => el.getBoundingClientRect().y));
      expect(Math.max(...headings) - Math.min(...headings)).toBeLessThan(1);
      for (const b of capabilities) {
        expect(Math.abs(b.width - capabilities[0].width)).toBeLessThan(1);
        expect(Math.abs(b.height - capabilities[0].height)).toBeLessThan(1);
      }
    } else expect(capabilities[2].y).toBeGreaterThan(capabilities[0].y + capabilities[0].height);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: path.join(run, `empty-${width}.png`), fullPage: true });
    await writeFile(path.join(run, `empty-metrics-${width}.json`), JSON.stringify({ width, height, upload, guide, capabilities }, null, 2));
  });
}

test('历史录像未选中仍显示引导，从侧栏与下拉框选择可切换为工作台', async ({ page }) => {
  const fixture = await installWorkspaceFixture(page);
  await page.goto('/workspace?view=videos');
  await expectGuide(page);
  await expect(page.locator('.empty-video-selection-hint')).toContainText('左侧录像列表或顶部下拉框');
  await expect(page.getByLabel('当前视频', { exact: true })).toBeEnabled();
  await expect(page.locator('main .library-item')).toHaveCount(0);
  await page.screenshot({ path: path.join(run, 'history-unselected-1440.png'), fullPage: true });
  await page.locator('.library-item').first().click();
  await expect(selectedVideo(page)).toHaveAttribute('src', `/media/videos/${fixture.ids[0]}`);
  await expect(page.getByRole('tab')).toHaveCount(3);
  await expect(page.locator('.workspace-workflow,.workspace-capability-grid,.upload-dropzone')).toHaveCount(0);
  await page.getByLabel('当前视频', { exact: true }).selectOption('');
  await expectGuide(page);
  await page.getByLabel('当前视频', { exact: true }).selectOption(fixture.ids[1]);
  await expect(selectedVideo(page)).toHaveAttribute('src', `/media/videos/${fixture.ids[1]}`);
  await expect(page.getByRole('tab', { name: 'AI 问答', exact: true })).toBeVisible();
  await expect(page.locator('.workspace-capability-grid')).toHaveCount(0);
});

test('新空状态上传过程中卡片高度和第二层位置稳定，成功后进入分析工作台', async ({ page }) => {
  const fixture = await installWorkspaceFixture(page, { empty: true, analyzed: false });
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { finish = resolve; });
  await page.route('**/api/videos/upload', async route => {
    await pending; fixture.state.empty = false;
    await route.fulfill({ json: { video_id: fixture.ids[0] } });
  });
  await page.goto('/workspace?view=videos');
  await expectGuide(page);
  const before = (await page.locator('#watch').boundingBox())!;
  const cardsBefore = (await page.locator('.workspace-capability-grid').boundingBox())!;
  await page.locator('#recording-upload').setInputFiles(path.resolve('public/demo/videos/fall.mp4'));
  await expect(page.getByRole('progressbar', { name: '上传进度' })).toBeVisible();
  expect((await page.locator('#watch').boundingBox())!.height).toBe(before.height);
  expect((await page.locator('.workspace-capability-grid').boundingBox())!.y).toBe(cardsBefore.y);
  await page.screenshot({ path: path.join(run, 'uploading-empty-1440.png'), fullPage: true });
  finish();
  await expect(selectedVideo(page)).toBeVisible();
  await expect(page.locator('.workspace-workflow,.workspace-capability-grid')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '开始安全分析', exact: true })).toBeEnabled();
});

test('已有录像上传失败保留当前播放器与分析结果，重试入口仍可用', async ({ page }) => {
  await installWorkspaceFixture(page);
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { finish = resolve; });
  await page.route('**/api/videos/upload', async route => { await pending; await route.fulfill({ status: 503, json: { error: { message: '测试上传失败，请重试。' } } }); });
  await page.goto('/workspace?view=videos&video=v_inspector_fixture');
  await expect(page.locator('#timeline .event')).toHaveCount(2);
  const handle = await selectedVideo(page).elementHandle();
  const before = (await page.locator('.recording-player').boundingBox())!;
  await page.locator('#recording-upload').setInputFiles(path.resolve('public/demo/videos/fall.mp4'));
  await expect(page.getByRole('progressbar', { name: '上传进度' })).toBeVisible();
  expect((await page.locator('.recording-player').boundingBox())!.y).toBe(before.y);
  finish();
  await expect(page.locator('.workspace-notices .error[role=alert]')).toContainText('测试上传失败');
  expect(await selectedVideo(page).evaluate((v, original) => v === original, handle)).toBe(true);
  await expect(page.locator('#timeline .event')).toHaveCount(2);
  await expect(page.getByRole('button', { name: '导入新录像', exact: true })).toBeEnabled();
});

test('真实隔离服务：导入、16比9原片播放、删除当前录像返回引导', async ({ page }) => {
  // Real upload/play/delete endpoints on the test server; never invokes model analysis.
  await page.goto('/workspace?view=videos');
  await expect(page.getByLabel('视频分析使用指南')).toBeVisible();
  await page.locator('#recording-upload').setInputFiles({ name: '空状态布局验收.mp4', mimeType: 'video/mp4', buffer: await readFile(path.resolve('public/demo/videos/fall.mp4')) });
  const video = selectedVideo(page);
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2);
  await expect(page.getByRole('tab')).toHaveCount(3);
  await video.evaluate((v: HTMLVideoElement) => { v.muted = true; return v.play(); });
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(.1);
  await video.evaluate((v: HTMLVideoElement) => v.pause());
  await page.getByLabel('跳转录像时间').fill('00:00:02');
  await page.getByRole('button', { name: '跳转', exact: true }).click();
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(2, 1);
  await page.getByRole('button', { name: '物体追踪', exact: true }).click();
  await page.getByRole('button', { name: '安全分析', exact: true }).click();
  await page.screenshot({ path: path.join(run, 'selected-real-upload-1440.png') });
  await page.getByRole('button', { name: '删除视频', exact: true }).click();
  await page.getByRole('button', { name: '确认删除', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expectGuide(page);
  await expect(video).toHaveCount(0);
  await expect(page.getByLabel('当前视频', { exact: true })).not.toContainText('空状态布局验收.mp4');
});
