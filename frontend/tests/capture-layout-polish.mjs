import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { installWorkspaceFixture } from './workspace-fixture.mjs';

const output = path.resolve('../docs/screenshots/layout-polish');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
const report = { errors: [], layouts: [], evidence: [], notes: 'Camera screenshots read status only; no camera start or model calls. Empty workspaces use the real local backend. The loaded-video screenshot uses an explicitly labelled UI fixture. Mobile screenshots preserve actual fixed navigation.' };
try {
  for (const [width, height] of [[320, 780], [390, 844], [768, 1000], [1024, 900], [1366, 768], [1440, 900], [1920, 1080]]) {
    const context = await browser.newContext({ viewport: { width, height }, reducedMotion: 'reduce' });
    const page = await context.newPage();
    page.on('pageerror', error => report.errors.push(error.message));
    for (const view of ['camera', 'videos']) {
      await page.goto(`http://127.0.0.1:5178/workspace?view=${view}`);
      if (view === 'camera') await page.getByText('等待开启本地摄像头', { exact: true }).waitFor();
      else await page.locator('.configuration summary').waitFor();
      await page.mouse.move(0, 0);
      const result = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth, visibleMainHeadings: [...document.querySelectorAll('main h1')].filter(node => node.getBoundingClientRect().height > 1).length, mainTop: document.querySelector('main').getBoundingClientRect().top, contentTop: document.querySelector('.live-camera-panel,.analysis-layout').getBoundingClientRect().top }));
      assert.ok(result.scrollWidth <= width, `${width}: ${view} overflows`);
      assert.equal(result.visibleMainHeadings, 0);
      if (view === 'camera' && width <= 850) {
        result.preview = await page.evaluate(() => {
          const main = document.querySelector('.live-camera-main').getBoundingClientRect();
          const viewport = document.querySelector('.live-camera-screen').getBoundingClientRect();
          const placeholder = document.querySelector('.live-camera-placeholder').getBoundingClientRect();
          const grid = document.querySelector('.live-camera-grid').getBoundingClientRect();
          return { width: viewport.width, availableWidth: grid.width, mainWidth: main.width, containsPlaceholder: placeholder.top >= viewport.top - 1 && placeholder.bottom <= viewport.bottom + 1 };
        });
        assert.ok(Math.abs(result.preview.width - result.preview.availableWidth) < 1);
        assert.ok(result.preview.containsPlaceholder, 'The camera placeholder must remain fully visible');
      }
      report.layouts.push({ view, height, ...result });
      if (view === 'videos') {
        const upload = page.getByRole('button', { name: '导入居家录像', exact: true });
        if (await upload.count()) {
          await upload.hover();
          const style = await upload.evaluate(node => ({ background: getComputedStyle(node).backgroundImage, color: getComputedStyle(node).color }));
          assert.ok(style.background.includes('radial-gradient'), 'Hover must retain the dark upload gradient');
          report.layouts.at(-1).uploadHover = style;
          await page.mouse.move(0, 0);
        }
      }
      if ([390, 1440].includes(width)) {
        await page.screenshot({ path: path.join(output, `${width}-${view}-viewport.png`) });
        if (view === 'camera' && width === 1440) await page.screenshot({ path: path.join(output, '1440-camera-full.png'), fullPage: true });
      }
    }
    if ([390, 768, 1440].includes(width)) {
      await page.goto('http://127.0.0.1:5178/#capabilities');
      await page.getByRole('button', { name: '查看 AI 分析依据', exact: true }).click();
      await page.locator('#fall-evidence-title').waitFor();
      await page.waitForFunction(() => Math.abs(document.querySelector('.av-evidence-inspector').getBoundingClientRect().top - document.querySelector('.av-header').getBoundingClientRect().bottom - 16) < 2);
      const evidence = await page.evaluate(() => {
        const rect = selector => document.querySelector(selector).getBoundingClientRect();
        const controls = rect('.av-inspector-controls');
        const feedback = rect('.av-inspector-feedback');
        const heading = rect('.av-inspector-frame-heading');
        const frames = rect('.av-inspector-frames');
        return { width: innerWidth, scrollWidth: document.documentElement.scrollWidth, panelTop: rect('.av-evidence-inspector').top, navigationBottom: rect('.av-header').bottom, controlsToFeedback: feedback.top - controls.bottom, feedbackToHeading: heading.top - feedback.bottom, headingToFrames: frames.top - heading.bottom, buttonFont: getComputedStyle(document.querySelector('.av-inspector-controls button')).fontSize, feedbackMargins: getComputedStyle(document.querySelector('.av-inspector-feedback')).margin };
      });
      assert.ok(evidence.controlsToFeedback >= 11 && evidence.feedbackToHeading >= 23 && evidence.headingToFrames >= 11);
      assert.ok(evidence.scrollWidth <= width);
      report.evidence.push(evidence);
      await page.screenshot({ path: path.join(output, `${width}-evidence-auto-scroll.png`) });
      await page.locator('.av-evidence-inspector').screenshot({ path: path.join(output, `${width}-evidence-panel.png`), style: '.av-header,.av-skip{visibility:hidden}' });
      await page.locator('.av-inspector-controls').evaluate(node => window.scrollTo({ top: scrollY + node.getBoundingClientRect().top - 90, behavior: 'instant' }));
      await page.screenshot({ path: path.join(output, `${width}-evidence-spacing.png`) });
      const clip = await page.evaluate(() => {
        const media = document.querySelector('.av-inspector-media'), bounds = media.getBoundingClientRect();
        const padding = parseFloat(getComputedStyle(media).paddingLeft);
        const controls = document.querySelector('.av-inspector-controls').getBoundingClientRect();
        const frames = document.querySelector('.av-inspector-frames').getBoundingClientRect();
        return { x: bounds.left + padding, y: controls.top - 10, width: bounds.width - padding * 2, height: frames.bottom - controls.top + 20 };
      });
      await page.screenshot({ path: path.join(output, `${width}-evidence-spacing-detail.png`), clip });
    }
    if (width === 1440) {
      await installWorkspaceFixture(page);
      await page.goto('http://127.0.0.1:5178/workspace?view=videos&video=v_inspector_fixture');
      await page.locator('.event-select').first().waitFor();
      await page.screenshot({ path: path.join(output, '1440-videos-loaded-fixture.png') });
    }
    await context.close();
  }
  assert.deepEqual(report.errors, []);
} finally {
  await browser.close();
  await writeFile(path.join(output, 'capture-results.json'), JSON.stringify(report, null, 2));
}
console.log(JSON.stringify(report, null, 2));
