import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { installWorkspaceFixture } from './workspace-fixture.mjs';

const output = path.resolve('../docs/screenshots/swiss-spa');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.ANJU_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
});
const errors = [];
const layouts = [];
const sectionStyle = '.av-header,.av-skip{visibility:hidden!important}';
const base = process.env.ANJU_TEST_BASE_URL || 'http://127.0.0.1:5178';
async function screenshot(page, name, options = {}) {
  if (options.fullPage) {
    await page.evaluate(() => scrollTo(0, 0));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  }
  return page.screenshot({ path: path.join(output, name), ...options,
    ...(options.fullPage && page.viewportSize().width <= 900 ? { style: '.mobile-nav{visibility:hidden!important}' } : {}),
  });
}
async function audit(page, name) {
  const result = await page.evaluate(() => ({
    width: innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    brokenImages: [...document.images].filter(image => !image.complete || !image.naturalWidth).map(image => image.getAttribute('src')),
  }));
  expect(result.scrollWidth, name).toBeLessThanOrEqual(result.width);
  expect(result.brokenImages, name).toEqual([]);
  layouts.push({ name, ...result });
}

try {
  for (const [label, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    const page = await browser.newPage({ viewport: { width, height }, reducedMotion: 'reduce' });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base, { waitUntil: 'networkidle' });
    await screenshot(page, `${label}-home.png`);
    for (const id of ['capabilities', 'workflow', 'ai-qa', 'devices']) {
      await page.locator(`#${id}`).scrollIntoViewIfNeeded();
      await page.locator(`#${id}`).screenshot({ path: path.join(output, `${label}-${id}.png`), style: sectionStyle });
    }
    await page.getByRole('button', { name: '查看 AI 分析依据', exact: true }).click();
    await expect(page.locator('#fall-evidence-title')).toBeFocused();
    await page.getByRole('button', { name: '核对 00:05.00 关键帧', exact: true }).click();
    await expect(page.locator('.av-inspector-feedback')).toContainText('已定位至 00:05.00');
    await page.locator('.av-evidence-inspector').screenshot({ path: path.join(output, `${label}-evidence.png`), style: sectionStyle });
    await audit(page, `${label}-evidence`);
    await page.getByRole('button', { name: '关闭证据面板', exact: true }).click();
    await expect(page.getByLabel('跌倒案例证据播放器', { exact: true })).toHaveCount(0);
    await page.getByRole('tab', { name: '事件摘要', exact: false }).click();
    await page.locator('#workflow').screenshot({ path: path.join(output, `${label}-workflow-summary.png`), style: sectionStyle });
    await page.locator('.av-qa-evidence').click();
    await expect(page.getByRole('region', { name: '示例证据详情' })).toBeVisible();
    await page.locator('#ai-qa').screenshot({ path: path.join(output, `${label}-qa-expanded.png`), style: sectionStyle });
    await audit(page, `${label}-home`);
    await page.evaluate(async () => {
      for (let y = 0; y < document.body.scrollHeight; y += 600) {
        scrollTo(0, y);
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      scrollTo(0, 0);
    });
    await screenshot(page, `${label}-home-full.png`, { fullPage: true });

    await page.goto(`${base}/workspace?view=camera`, { waitUntil: 'networkidle' });
    await screenshot(page, `${label}-camera.png`, { fullPage: true });
    await audit(page, `${label}-camera`);
    await page.goto(`${base}/workspace?view=videos`, { waitUntil: 'networkidle' });
    await screenshot(page, `${label}-workspace-empty.png`, { fullPage: true });
    await audit(page, `${label}-workspace-empty`);
    await page.close();

    const populated = await browser.newPage({ viewport: { width, height }, reducedMotion: 'reduce' });
    populated.on('pageerror', error => errors.push(error.message));
    await installWorkspaceFixture(populated);
    await populated.goto(`${base}/workspace?view=videos&video=v_inspector_fixture`, { waitUntil: 'networkidle' });
    await expect(populated.locator('#watch video')).toBeVisible();
    await expect(populated.locator('#timeline .event')).toHaveCount(2);
    await populated.locator('.event-select').first().click();
    await expect.poll(() => populated.locator('#watch video').evaluate(video => video.currentTime)).toBeCloseTo(2.3, 1);
    await expect(populated.locator('.workspace-seek-status')).toContainText('已定位');
    await expect.poll(() => populated.locator('#watch video').evaluate(video => video.readyState)).toBeGreaterThanOrEqual(3);
    if (label === 'mobile') {
      await populated.evaluate(() => scrollTo(0, 0));
      await screenshot(populated, 'mobile-workspace-events-viewport.png');
    }
    await screenshot(populated, `${label}-workspace-events-fixture.png`, { fullPage: true });
    await populated.getByRole('tab', { name: 'AI 问答', exact: true }).click();
    await populated.getByLabel('你想了解什么情况？').fill('测试录像里有什么异常？');
    await populated.getByRole('button', { name: '提问', exact: true }).click();
    await expect(populated.locator('.answer-result')).toContainText('界面回归测试');
    await screenshot(populated, `${label}-workspace-qa-fixture.png`, { fullPage: true });
    await audit(populated, `${label}-workspace-fixture`);
    await populated.close();
  }
  expect(errors).toEqual([]);
} finally {
  await writeFile(path.join(output, 'capture-results.json'), JSON.stringify({ errors, layouts }, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ errors, layouts }, null, 2));
