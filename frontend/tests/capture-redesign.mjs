import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const output = path.resolve('../docs/screenshots/real-video');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.ANJU_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
const errors = [];
const layouts = [];
// Section screenshots can be taller than the viewport. Hide the fixed shell
// only during these captures, so it is not painted across the middle of a crop.
const sectionCapture = { style: '.av-header,.av-skip{visibility:hidden!important}' };
try {
  for (const [label, width, height] of [['desktop', 1440, 1000], ['tablet', 768, 1000], ['mobile', 390, 844]]) {
    const page = await browser.newPage({ viewport: { width, height }, reducedMotion: 'reduce' });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:5178/#capabilities', { waitUntil: 'networkidle' });
    await expect(page.locator('.av-video-card')).toHaveCount(3);
    await page.locator('#capabilities').screenshot({ ...sectionCapture, path: path.join(output, `${label}-capabilities.png`) });
    await page.locator('#workflow').scrollIntoViewIfNeeded();
    for (const [index, name] of ['原始视频', '风险候选与复核', '事件摘要', '证据回看'].entries()) {
      await page.getByRole('tab', { name: new RegExp(name) }).click();
      if (index === 2) {
        await page.getByRole('button', { name: '定位到 00:05.00 关键帧' }).click();
        await expect.poll(() => page.getByLabel('跌倒案例完整原始视频').evaluate(element => Math.abs(element.currentTime - 5))).toBeLessThan(.06);
      }
      await page.locator('#workflow').screenshot({ ...sectionCapture, path: path.join(output, `${label}-workflow-${index + 1}.png`) });
    }
    await page.locator('.av-qa-evidence').click();
    await expect(page.getByRole('region', { name: '示例证据详情' })).toBeVisible();
    await page.locator('#ai-qa').screenshot({ ...sectionCapture, path: path.join(output, `${label}-qa-evidence.png`) });
    await page.evaluate(async () => {
      for (let y = 0; y < document.body.scrollHeight; y += 500) {
        scrollTo(0, y); await new Promise(resolve => setTimeout(resolve, 40));
      }
      scrollTo(0, 0);
    });
    await page.screenshot({ path: path.join(output, `${label}-home.png`), fullPage: true });
    const layout = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth, brokenImages: [...document.images].filter(image => !image.complete || !image.naturalWidth).map(image => image.src) }));
    layouts.push({ label, ...layout });
    expect(layout.scrollWidth).toBeLessThanOrEqual(width);
    expect(layout.brokenImages).toEqual([]);
    await page.close();
  }
  expect(errors).toEqual([]);
} finally {
  await writeFile(path.join(output, 'capture-results.json'), JSON.stringify({ errors, layouts }, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ errors, layouts }, null, 2));
