import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { installWorkspaceFixture } from './workspace-fixture.mjs';

const base = process.env.ANJU_TEST_BASE_URL || 'http://127.0.0.1:5178';
const output = path.resolve('../docs/screenshots/nine-reference');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.ANJU_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
});
const report = { viewport: { width: 1600, height: 900 }, mockFixtureUsedFor: ['07_工作台_事件与证据.png', '08_工作台_AI问答.png', '09_工作台_追踪与人工复核.png'], errors: [], screenshots: [] };

async function capture(page, name, locator) {
  if (locator) await locator.screenshot({ path: path.join(output, name) });
  else await page.screenshot({ path: path.join(output, name) });
  report.screenshots.push(name);
}

try {
  const page = await browser.newPage({ viewport: report.viewport, deviceScaleFactor: 1, reducedMotion: 'reduce' });
  page.on('pageerror', error => report.errors.push(error.message));

  await page.goto(`${base}/#home`, { waitUntil: 'networkidle' });
  await capture(page, '01_首页.png');
  await page.goto(`${base}/#capabilities`, { waitUntil: 'networkidle' });
  await expect(page.locator('.av-video-card')).toHaveCount(3);
  await capture(page, '02_核心能力.png', page.locator('#capabilities'));
  await page.goto(`${base}/#workflow`, { waitUntil: 'networkidle' });
  await page.getByRole('tab', { name: /事件自动摘要/ }).click();
  await expect(page.getByRole('tabpanel')).toContainText('未经人工确认');
  await capture(page, '03_工作流程.png', page.locator('#workflow'));
  await page.goto(`${base}/#ai-qa`, { waitUntil: 'networkidle' });
  await expect(page.locator('.av-answer-bubble')).toContainText('00:02.30–00:06.30');
  await capture(page, '04_AI问答.png', page.locator('#ai-qa'));
  await page.goto(`${base}/#devices`, { waitUntil: 'networkidle' });
  await expect(page.locator('.av-connection-card')).toHaveCount(3);
  await capture(page, '05_设备接入.png', page.locator('.av-devices'));
  await page.goto(`${base}/workspace?view=camera`, { waitUntil: 'networkidle' });
  await expect(page.locator('.camera-page-heading h1')).toHaveText('实时摄像头');
  await expect(page.getByRole('button', { name: '开启摄像头', exact: true }).or(page.getByRole('button', { name: '停止预览', exact: true }))).toBeVisible();
  await capture(page, '06_工作台_实时摄像头.png');
  await page.close();

  const workspace = await browser.newPage({ viewport: report.viewport, deviceScaleFactor: 1, reducedMotion: 'reduce' });
  workspace.on('pageerror', error => report.errors.push(error.message));
  await installWorkspaceFixture(workspace);
  await workspace.goto(`${base}/workspace?view=videos&panel=events&video=v_inspector_fixture`, { waitUntil: 'networkidle' });
  await expect(workspace.getByRole('alert')).toContainText('测试模拟模式');
  await expect(workspace.locator('#timeline .event')).toHaveCount(2);
  await capture(workspace, '07_工作台_事件与证据.png');

  await workspace.getByRole('tab', { name: 'AI 问答', exact: true }).click();
  await workspace.getByLabel('你想了解什么情况？').fill('测试录像里有什么异常？');
  await workspace.getByRole('button', { name: '提问', exact: true }).click();
  await expect(workspace.locator('.answer-result')).toContainText('界面回归测试');
  await capture(workspace, '08_工作台_AI问答.png');

  await workspace.getByRole('tab', { name: '追踪与复核', exact: true }).click();
  await workspace.getByRole('button', { name: '人工复核', exact: true }).click();
  await workspace.getByLabel('选择待复核事件').selectOption('fixture_event_fall');
  await expect(workspace.locator('.review-panel')).toContainText('待人工核对');
  await expect(workspace.locator('.review-panel')).toContainText('测试');
  await capture(workspace, '09_工作台_追踪与人工复核.png');

  const layoutChecks = [];
  for (const width of [390, 1024, 1280, 1440, 1600]) {
    await workspace.setViewportSize({ width, height: Math.round(width * .5625) });
    await workspace.goto(`${base}/workspace?view=videos&panel=events&video=v_inspector_fixture`, { waitUntil: 'domcontentloaded' });
    await expect(workspace.locator('#watch video')).toBeVisible();
    const check = await workspace.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
    expect(check.scrollWidth).toBeLessThanOrEqual(width);
    layoutChecks.push(check);
  }
  report.layoutChecks = layoutChecks;
  expect(report.errors).toEqual([]);
  await workspace.close();
} finally {
  await writeFile(path.join(output, 'capture-results.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
console.log(JSON.stringify(report, null, 2));
