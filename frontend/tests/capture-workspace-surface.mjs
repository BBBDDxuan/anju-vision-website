import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const before = process.argv.includes('--before');
const output = path.resolve('../docs/screenshots/workspace-surface-polish');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
const report = { errors: [], screens: [] };
try {
  for (const [width, height] of before ? [[1440, 900]] : [[320, 780], [390, 844], [768, 1000], [1366, 768], [1440, 900], [1920, 1080]]) {
    const page = await browser.newPage({ viewport: { width, height }, reducedMotion: 'reduce' });
    page.on('pageerror', error => report.errors.push(error.message));
    await page.goto('http://127.0.0.1:5178/workspace?view=camera');
    const cameraTitle = await page.locator('.live-camera-heading h2').evaluate(node => {
      const style = getComputedStyle(node);
      return { fontSize: style.fontSize, fontWeight: style.fontWeight, lineHeight: style.lineHeight, color: style.color };
    });
    await page.goto('http://127.0.0.1:5178/workspace?view=videos&panel=qa');
    await page.getByRole('tab', { name: 'AI 问答', exact: true }).waitFor();
    await page.getByRole('heading', { name: '每个提醒，都回到画面里。' }).waitFor();
    await page.mouse.move(0, 0);
    const styles = await page.evaluate(() => {
      const describe = selector => {
        const node = document.querySelector(selector), style = getComputedStyle(node), bounds = node.getBoundingClientRect();
        return { fontSize: style.fontSize, fontWeight: style.fontWeight, lineHeight: style.lineHeight, color: style.color, background: style.backgroundColor, border: style.border, padding: style.padding, width: bounds.width, height: bounds.height };
      };
      return { width: innerWidth, scrollWidth: document.documentElement.scrollWidth, title: describe('.video-panel-heading strong'), state: describe('.workspace-task-state'), header: describe('.video-panel>.panel-head'), empty: describe('.answer-empty'), separator: describe('.record-qa-settings') };
    });
    report.screens.push({ ...styles, cameraTitle });
    if (!before) {
      assert.equal(styles.title.fontSize, cameraTitle.fontSize);
      assert.equal(styles.title.fontWeight, cameraTitle.fontWeight);
      assert.equal(styles.title.color, cameraTitle.color);
      assert.ok(styles.scrollWidth <= width);
      const rgb = styles.empty.background.match(/\d+/g).slice(0, 3).map(Number);
      assert.ok(Math.max(...rgb) < 100, 'QA placeholder must use a dark surface');
    }
    if (before || [390, 1440].includes(width)) {
      const prefix = before ? 'before-' : '';
      await page.screenshot({ path: path.join(output, `${prefix}${width}-workspace-qa.png`) });
      await page.locator('.video-panel>.panel-head').screenshot({ path: path.join(output, `${prefix}${width}-player-header.png`) });
      await page.locator('.answer-panel').screenshot({ path: path.join(output, `${prefix}${width}-qa-panel.png`) });
      if (!before && width === 390) {
        await page.locator('.answer-empty').scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(output, '390-qa-visible-viewport.png') });
      }
    }
    if (!before && [390, 1440].includes(width)) {
      await page.goto('http://127.0.0.1:5178/');
      for (const [id, removed] of [['capabilities', '从一段日常画面，到一条关键线索。'], ['workflow', '从视频到证据的完整闭环。']]) {
        const heading = page.locator(`#${id} .av-section-top`);
        await heading.scrollIntoViewIfNeeded();
        await page.locator(`#${id} .av-section-top.av-visible`).waitFor();
        assert.ok(!(await heading.innerText()).includes(removed));
        await heading.screenshot({ path: path.join(output, `${width}-${id}-heading.png`), style: '.av-header,.av-skip{visibility:hidden}' });
      }
    }
    await page.close();
  }
  assert.deepEqual(report.errors, []);
} finally {
  await browser.close();
  await writeFile(path.join(output, before ? 'before-results.json' : 'capture-results.json'), JSON.stringify(report, null, 2));
}
console.log(JSON.stringify(report, null, 2));
