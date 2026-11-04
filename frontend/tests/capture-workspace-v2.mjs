import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { installWorkspaceFixture } from './workspace-fixture.mjs';

const output = path.resolve('../docs/screenshots/evidence-workspace-v2');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.ANJU_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
const report = { errors: [], layouts: [], workspaceScreenshotSource: 'Explicit is_mock UI fixture; yellow test banner retained. Landing evidence uses verified real export.', mobileCaptures: 'Viewport images retain actual sticky/fixed navigation. Full-content images temporarily make sticky headers static and hide fixed bottom navigation/skip link to avoid long-screenshot duplication; product CSS is unchanged.' };
async function captureWorkspace(page, label, name, width) {
  await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo({ top: 0, behavior: 'instant' }); });
  if (width > 900) {
    await page.screenshot({ path: path.join(output, `${label}-workspace-${name}-fixture.png`) });
    return;
  }
  await page.screenshot({ path: path.join(output, `${label}-workspace-${name}-fixture.png`), fullPage: true, style: '.skip-link,.mobile-nav{visibility:hidden}.topbar,.workspace-inspector-tabs{position:static}' });
  if (name === 'qa') await page.evaluate(() => window.scrollTo({ top: document.querySelector('.analysis-inspector').getBoundingClientRect().top + scrollY - 62, behavior: 'instant' }));
  await page.screenshot({ path: path.join(output, `${label}-workspace-${name}-viewport.png`) });
}
try {
  for (const [label, width, height] of [['desktop', 1440, 900], ['tablet', 768, 1000], ['mobile', 390, 844]]) {
    const context = await browser.newContext({ viewport: { width, height }, reducedMotion: 'reduce' });
    const page = await context.newPage();
    page.on('pageerror', error => report.errors.push({ label, message: error.message }));
    page.on('console', message => { if (message.type() === 'error') report.errors.push({ label, message: message.text() }); });
    await page.goto('http://127.0.0.1:5178/#capabilities');
    await page.getByRole('button', { name: '查看 AI 分析依据' }).click();
    await page.locator('#fall-evidence-title').waitFor();
    await page.getByRole('button', { name: '核对 00:05.00 关键帧' }).click();
    await page.waitForFunction(() => document.querySelector('.av-inspector-feedback')?.textContent?.includes('已定位至 00:05.00'));
    await page.locator('#capabilities').screenshot({ path: path.join(output, `${label}-capabilities-evidence.png`), style: '.av-header,.av-skip{visibility:hidden}' });
    await page.locator('.av-evidence-inspector').screenshot({ path: path.join(output, `${label}-evidence-panel.png`), style: '.av-header,.av-skip{visibility:hidden}' });
    report.layouts.push({ label, screen: 'landing evidence', ...(await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }))) });
    await page.getByRole('button', { name: '关闭证据面板' }).click();
    await page.goto('http://127.0.0.1:5178/workspace?view=videos');
    if (label === 'desktop') await page.screenshot({ path: path.join(output, 'desktop-workspace-real-empty.png') });
    const fixture = await installWorkspaceFixture(page);
    await page.goto('http://127.0.0.1:5178/workspace?view=videos&video=v_inspector_fixture');
    await page.locator('.event-select').first().click();
    await page.waitForFunction(() => Math.abs(document.querySelector('#watch video').currentTime - 2.3) < .05);
    await captureWorkspace(page, label, 'events', width);
    await page.getByRole('tab', { name: 'AI 问答', exact: true }).click();
    await page.getByLabel('你想了解什么情况？').fill('这段测试录像里有哪些值得关注的情况？');
    await page.getByRole('button', { name: '提问', exact: true }).click();
    await page.locator('.answer-result').waitFor();
    await page.locator('.answer-result .reference').first().click();
    await captureWorkspace(page, label, 'qa', width);
    report.layouts.push({ label, screen: 'workspace UI fixture', ...(await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth, height: innerHeight, scrollHeight: document.documentElement.scrollHeight, brokenImages: Array.from(document.images).filter(image => !image.complete || image.naturalWidth === 0).map(image => image.src) }))) });
    if (width <= 900) {
      await page.getByRole('button', { name: '打开导航', exact: true }).click();
      await page.screenshot({ path: path.join(output, `${label}-workspace-drawer.png`) });
      await page.keyboard.press('Escape');
    } else {
      await page.getByRole('tab', { name: '追踪与复核', exact: true }).click();
      await page.getByRole('button', { name: '人工复核', exact: true }).click();
      await page.screenshot({ path: path.join(output, 'desktop-workspace-review-fixture.png') });
    }
    fixture.state.analyzed = false;
    await context.close();
  }
} finally {
  await browser.close();
  await writeFile(path.join(output, 'capture-results.json'), JSON.stringify(report, null, 2));
}
if (report.errors.length || report.layouts.some(layout => layout.scrollWidth > layout.width || layout.brokenImages?.length)) throw new Error(JSON.stringify(report));
console.log(JSON.stringify(report, null, 2));
