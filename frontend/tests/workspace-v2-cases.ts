import { test, expect, type Page, type Locator } from '@playwright/test';
import { installWorkspaceFixture } from './workspace-fixture.mjs';

const panelVideo = (page: Page) => page.getByLabel('跌倒案例证据播放器', { exact: true });
const currentTime = (video: Locator) => video.evaluate((node: HTMLVideoElement) => node.currentTime);
const isPaused = (video: Locator) => video.evaluate((node: HTMLVideoElement) => node.paused);
const tab = (page: Page, name: string) => page.getByRole('tab', { name, exact: true });
async function openEvidence(page: Page) {
  await page.goto('/#capabilities');
  await page.getByRole('button', { name: '查看 AI 分析依据', exact: true }).click();
  await expect(page.locator('#fall-evidence-title')).toBeFocused();
  await expect.poll(() => panelVideo(page).evaluate((node: HTMLVideoElement) => node.readyState)).toBeGreaterThanOrEqual(1);
}
async function openWorkspace(page: Page, options = {}) {
  const fixture = await installWorkspaceFixture(page, options);
  await page.goto('/workspace?view=videos&video=v_inspector_fixture');
  await expect.poll(() => page.locator('#watch video').evaluate((node: HTMLVideoElement) => node.readyState)).toBeGreaterThanOrEqual(1);
  await expect(page.locator('.mock-banner')).toBeVisible();
  return fixture;
}

test('A01–A04：全宽面板位于三卡之后，打开暂停其他视频且不自动播放', async ({ page }) => {
  await page.goto('/#capabilities');
  const card = page.locator('[data-case=fall]');
  await card.locator('.av-media-play').click();
  await expect.poll(() => isPaused(card.locator('video'))).toBe(false);
  expect(await currentTime(card.locator('video'))).toBeLessThan(1.5);
  await page.getByRole('button', { name: '查看 AI 分析依据' }).click();
  await expect(page.locator('#fall-evidence-title')).toBeFocused();
  await expect.poll(() => isPaused(card.locator('video'))).toBe(true);
  await expect.poll(() => isPaused(panelVideo(page))).toBe(true);
  const bounds = await page.evaluate(() => {
    const grid = document.querySelector('.av-video-grid')!.getBoundingClientRect();
    const panel = document.querySelector('.av-evidence-inspector')!.getBoundingClientRect();
    return { sameWidth: Math.abs(grid.width - panel.width), sameLeft: Math.abs(grid.left - panel.left), below: panel.top >= grid.bottom, insideCard: !!document.querySelector('.av-video-card .av-evidence-inspector') };
  });
  expect(bounds.sameWidth).toBeLessThan(1); expect(bounds.sameLeft).toBeLessThan(1); expect(bounds.below).toBe(true); expect(bounds.insideCard).toBe(false);
  await expect(page.locator('dialog')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '查看 AI 分析依据' })).toHaveAttribute('aria-expanded', 'true');
});

test('A05–A07：真实区间比例、2.30–6.30自动停止，完整录像解除边界', async ({ page }) => {
  await openEvidence(page);
  const video = panelVideo(page);
  const ratio = await page.locator('.av-evidence-inspector .timebar-evidence').evaluate(node => ({ left: node.getBoundingClientRect().left - node.parentElement!.getBoundingClientRect().left, width: node.getBoundingClientRect().width, total: node.parentElement!.getBoundingClientRect().width }));
  expect(Math.abs(ratio.left / ratio.total - 2.3 / 8.55)).toBeLessThan(.002);
  expect(Math.abs(ratio.width / ratio.total - 4 / 8.55)).toBeLessThan(.002);
  await video.evaluate((node: HTMLVideoElement) => { node.playbackRate = 4; });
  await page.getByRole('button', { name: '定位证据区间', exact: true }).click();
  await expect.poll(() => isPaused(video)).toBe(false);
  await expect.poll(() => isPaused(video), { timeout: 7000 }).toBe(true);
  expect(Math.abs(await currentTime(video) - 6.3)).toBeLessThan(.07);
  await page.getByRole('button', { name: '播放完整录像', exact: true }).click();
  await expect.poll(() => isPaused(video)).toBe(false);
  await expect.poll(() => video.evaluate((node: HTMLVideoElement) => node.ended), { timeout: 7000 }).toBe(true);
  expect(await currentTime(video)).toBeGreaterThan(8.5);
});

test('A08–A11：三张真实帧精确暂停定位，关闭卸载、恢复焦点且可快速重开', async ({ page }) => {
  await openEvidence(page);
  const video = panelVideo(page);
  for (const [label, time] of [['00:02.30', 2.3], ['00:05.00', 5], ['00:06.00', 6]] as const) {
    await page.getByRole('button', { name: `核对 ${label} 关键帧` }).click();
    await expect.poll(() => currentTime(video)).toBeCloseTo(time, 1);
    await expect.poll(() => isPaused(video)).toBe(true);
    await expect(page.locator('.av-inspector-feedback')).toContainText(`已定位至 ${label}`);
  }
  await expect(page.locator('.av-inspector-report')).toContainText('模型复核支持跌倒判断');
  await expect(page.locator('.av-inspector-report')).toContainText('尚无人工确认记录');
  expect(await page.locator('.av-inspector-report').innerText()).not.toMatch(/\d+%|人工已确认/);
  await expect(page.getByRole('button', { name: '在工作台查看' })).toHaveCount(0);
  await page.getByRole('button', { name: '播放完整录像', exact: true }).click();
  const handle = await video.elementHandle();
  await page.getByRole('button', { name: '关闭证据面板' }).click();
  await expect(page.getByRole('button', { name: '查看 AI 分析依据' })).toBeFocused();
  await expect(video).toHaveCount(0);
  expect(await handle!.evaluate((node: HTMLVideoElement) => node.paused)).toBe(true);
  for (let i = 0; i < 2; i++) {
    await page.getByRole('button', { name: '查看 AI 分析依据' }).click();
    await page.getByRole('button', { name: '关闭证据面板' }).click();
  }
  await page.getByRole('button', { name: '查看 AI 分析依据' }).click();
  await expect(panelVideo(page)).toHaveCount(1);
  await expect.poll(() => isPaused(panelVideo(page))).toBe(true);
});

test('A09：缺失帧与媒体错误诚实反馈，不显示定位成功', async ({ page }) => {
  await page.route('**/demo/keyframes/*', route => route.abort());
  await page.route('**/demo/videos/fall.mp4', route => route.abort());
  await page.goto('/#capabilities');
  await page.getByRole('button', { name: '查看 AI 分析依据' }).click();
  await expect(page.locator('.av-frame-missing')).toHaveCount(3);
  await expect(page.locator('.av-inspector-feedback')).toContainText('视频加载失败');
  await expect(page.getByRole('button', { name: '定位证据区间' })).toBeDisabled();
  await expect(page.locator('.av-inspector-feedback')).not.toContainText('已定位至');
});

test('A06：手动拖到证据区间外解除停止边界，继续播放可到自然结尾', async ({ page }) => {
  await openEvidence(page);
  const video = panelVideo(page);
  await page.getByRole('button', { name: '定位证据区间' }).click();
  await page.getByRole('slider', { name: '证据面板视频时间定位' }).fill('7.2');
  await expect.poll(() => currentTime(video)).toBeCloseTo(7.2, 1);
  await page.getByRole('button', { name: '继续播放', exact: true }).click();
  await expect.poll(() => video.evaluate((node: HTMLVideoElement) => node.ended), { timeout: 5000 }).toBe(true);
});

for (const width of [1440, 768, 390]) test(`A13：${width}px 证据面板自动定位、可操作、无溢出并支持 Escape`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await openEvidence(page);
  await expect.poll(() => page.locator('.av-evidence-inspector').evaluate(node => {
    const navigationBottom = document.querySelector('.av-header')!.getBoundingClientRect().bottom;
    return Math.abs(node.getBoundingClientRect().top - navigationBottom - 16);
  })).toBeLessThan(2);
  const columns = await page.locator('.av-inspector-columns').evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ').length);
  expect(columns).toBe(width > 900 ? 2 : 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator('.av-inspector-collapse').click();
  await expect(panelVideo(page)).toHaveCount(0);
  await expect.poll(() => page.getByRole('button', { name: '查看 AI 分析依据' }).evaluate(node => {
    const bounds = node.getBoundingClientRect(); return bounds.top >= 90 && bounds.bottom <= innerHeight;
  })).toBe(true);
  await page.getByRole('button', { name: '查看 AI 分析依据' }).click();
  await expect(page.locator('#fall-evidence-title')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: '查看 AI 分析依据' })).toBeFocused();
});

test('B05–B08：标签、事件、时间线、QA共享稳定播放器和同一选择状态', async ({ page }) => {
  const fixture = await openWorkspace(page);
  const video = page.locator('#watch video');
  await expect(page.locator('#timeline .event')).toHaveCount(2);
  const handle = await video.elementHandle();
  await page.locator('.event-select').first().click();
  await expect.poll(() => currentTime(video)).toBeCloseTo(2.3, 1);
  await expect(page.locator('.event.is-selected')).toHaveAttribute('data-event-id', fixture.events[0].id);
  await expect(page.locator('.timebar-markers button').first()).toHaveAttribute('aria-pressed', 'true');
  const initialCalls = fixture.calls.filter(call => /\/events|analysis-plan|\/api\/videos\/v_/.test(call.path)).length;
  await tab(page, 'AI 问答').click();
  expect(await video.evaluate((node, previous) => node === previous, handle)).toBe(true);
  expect(await currentTime(video)).toBeCloseTo(2.3, 1);
  await page.getByLabel('你想了解什么情况？').fill('测试录像里有什么异常？');
  await page.getByRole('button', { name: '提问', exact: true }).click();
  await expect(page.locator('.answer-result')).toContainText('界面回归测试');
  await page.locator('.answer-result .reference').first().click();
  await expect.poll(() => currentTime(video)).toBeCloseTo(2.3, 1);
  await tab(page, '追踪与复核').click();
  await tab(page, '事件').click();
  const noQueryCalls = fixture.calls.filter(call => /\/events|analysis-plan|\/api\/videos\/v_/.test(call.path) && !call.path.endsWith('/query')).length;
  expect(noQueryCalls).toBe(initialCalls);
  await page.locator('.timebar-markers button').last().click();
  await expect.poll(() => currentTime(video)).toBeCloseTo(4.5, 1);
  await expect(page.locator('.event.is-selected')).toHaveAttribute('data-event-id', fixture.events[1].id);
  await tab(page, 'AI 问答').click();
  await expect(page.locator('.answer-result')).toContainText('界面回归测试');
  expect(await video.evaluate((node, previous) => node === previous, handle)).toBe(true);
  expect(fixture.calls.filter(call => call.method === 'POST' && !call.path.endsWith('/query'))).toHaveLength(0);
});

test('B06–B07：两个事件共用证据时仍保留用户选择的事件', async ({ page }) => {
  const fixture = await openWorkspace(page, { sharedEvidence: true });
  await page.locator('.event-select').last().click();
  await expect(page.locator('.event.is-selected')).toHaveAttribute('data-event-id', fixture.events[1].id);
  await expect(page.locator('.timebar-markers button').last()).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => currentTime(page.locator('#watch video'))).toBeCloseTo(2.3, 1);
  await page.locator('.timebar-markers button').first().click();
  await expect(page.locator('.event.is-selected')).toHaveAttribute('data-event-id', fixture.events[0].id);
});

test('B09：切换视频清空旧问答和选择，迟到的答案不能串台', async ({ page }) => {
  const fixture = await openWorkspace(page, { queryDelay: 700 });
  await tab(page, 'AI 问答').click();
  await page.getByLabel('你想了解什么情况？').fill('一个延迟的测试问题');
  await page.getByRole('button', { name: '提问', exact: true }).click();
  await page.getByLabel('当前视频', { exact: true }).selectOption(fixture.ids[1]);
  await expect(page.locator('#watch video')).toHaveAttribute('src', `/media/videos/${fixture.ids[1]}`);
  await expect(page.getByLabel('你想了解什么情况？')).toHaveValue('');
  await page.waitForTimeout(850);
  await expect(page.locator('.answer-result')).toHaveCount(0);
  await tab(page, '事件').click();
  await expect(page.locator('#timeline .event')).toHaveCount(0);
  await expect(page.locator('.timebar-markers button[aria-pressed=true]')).toHaveCount(0);
});

test('B04、B10：任务暂停继续、物品回溯和人工复核提交保留原接口', async ({ page }) => {
  const fixture = await openWorkspace(page, { autoComplete: false, status: 'running' });
  await page.locator('.workspace-task-tray>summary').click();
  await expect(page.getByRole('button', { name: '物体追踪', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '暂停当前任务', exact: true }).click();
  await expect(page.getByRole('button', { name: '继续上次安全分析', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '继续上次安全分析', exact: true }).click();
  await expect(page.getByRole('button', { name: '暂停当前任务', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '暂停当前任务', exact: true }).click();
  await tab(page, '追踪与复核').click();
  await page.getByLabel('选择要回顾的对象').selectOption('fixture_person');
  await expect(page.locator('.replay-entry')).toHaveCount(2);
  await page.locator('.replay-entry .reference').first().click();
  await expect.poll(() => currentTime(page.locator('#watch video'))).toBeCloseTo(2.3, 1);
  await page.getByRole('button', { name: '人工复核', exact: true }).click();
  await page.getByLabel('选择待复核事件').selectOption(fixture.events[0].id);
  await page.getByLabel('复核说明（必填）').fill('仅验证界面提交流程，不是实际人工确认。');
  await page.getByRole('button', { name: '人工确认原事件', exact: true }).click();
  await expect(page.getByText('人工复核已保存。原始AI输出保留，时间线和后续问答已更新。')).toBeVisible();
  expect(fixture.calls.some(call => call.path.endsWith('/pause') && call.method === 'POST')).toBe(true);
  expect(fixture.calls.some(call => call.path.endsWith('/resume') && call.method === 'POST')).toBe(true);
  expect(fixture.calls.some(call => call.path.endsWith('/human') && call.body?.decision === 'confirm')).toBe(true);
});

test('B11：未知回答与问答网络失败保留真实状态', async ({ page }) => {
  const fixture = await openWorkspace(page, { unknown: true });
  await tab(page, 'AI 问答').click();
  await page.getByLabel('你想了解什么情况？').fill('证据是否足够？');
  await page.getByRole('button', { name: '提问', exact: true }).click();
  await expect(page.locator('.answer-result')).toContainText('现有证据不足，无法确定');
  fixture.state.queryError = true;
  await page.getByRole('button', { name: '提问', exact: true }).click();
  await expect(page.locator('.workspace-notices [role=alert]')).toContainText('问答连接失败');
});

test('B01、B12：旧锚点、panel 参数、刷新与浏览器前后退均切换局部标签', async ({ page }) => {
  await installWorkspaceFixture(page);
  for (const [hash, name] of [['#timeline', '事件'], ['#ask', 'AI 问答'], ['#analysis-tools', '追踪与复核']]) {
    await page.goto(`/workspace?view=videos&video=v_inspector_fixture${hash}`);
    await expect(tab(page, name)).toHaveAttribute('aria-selected', 'true');
    expect(await page.evaluate(() => scrollY)).toBe(0);
  }
  await tab(page, '事件').click(); await tab(page, 'AI 问答').click();
  await expect(page).toHaveURL(/panel=qa/); await page.reload();
  await expect(tab(page, 'AI 问答')).toHaveAttribute('aria-selected', 'true');
  await tab(page, '追踪与复核').click(); await page.goBack();
  await expect(tab(page, 'AI 问答')).toHaveAttribute('aria-selected', 'true');
  await page.goForward(); await expect(tab(page, '追踪与复核')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('navigation', { name: '主要导航' }).locator('a')).toHaveCount(2);
});

for (const [width, height] of [[1366, 768], [1440, 900], [1920, 1080]]) test(`B02、B13：${width}×${height} 播放器、Inspector、侧栏底区同屏可达`, async ({ page }) => {
  await page.setViewportSize({ width, height });
  await openWorkspace(page);
  for (const selector of ['#watch video', '.workspace-inspector-tabs', '.av-back-home', '.help-button', '.timebar-markers']) {
    const bounds = await page.locator(selector).boundingBox();
    expect(bounds).not.toBeNull(); expect(bounds!.y).toBeGreaterThanOrEqual(0); expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(height + 1);
  }
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight + 1)).toBe(true);
  await page.locator('.workspace-inspector-scroll').evaluate(node => { const paragraph = document.createElement('p'); paragraph.style.height = '2000px'; paragraph.textContent = '仅用于局部滚动检查'; node.append(paragraph); node.scrollTop = 1000; });
  expect(await page.evaluate(() => scrollY)).toBe(0);
});

for (const width of [768, 390]) test(`B14、B16：${width}px 标签键盘、手机抽屉焦点与所有工具可达`, async ({ page }) => {
  await page.setViewportSize({ width, height: 844 });
  await openWorkspace(page);
  await tab(page, '事件').focus(); await page.keyboard.press('ArrowRight');
  await expect(tab(page, 'AI 问答')).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('End'); await expect(tab(page, '追踪与复核')).toBeFocused();
  await page.getByRole('button', { name: '人工复核', exact: true }).click();
  await expect(page.locator('.review-panel')).toBeVisible();
  await page.getByRole('button', { name: '打开导航', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '功能导航' })).toBeVisible();
  await expect(page.getByRole('button', { name: '使用说明', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: '打开导航', exact: true })).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('正常动画模式只在打开和标签切换时入场，关键帧定位不重建面板', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await openEvidence(page);
  await expect.poll(() => page.locator('.av-evidence-inspector').evaluate(node => {
    const navigationBottom = document.querySelector('.av-header')!.getBoundingClientRect().bottom;
    return Math.abs(node.getBoundingClientRect().top - navigationBottom - 16);
  })).toBeLessThan(2);
  const report = await page.locator('.av-inspector-report').elementHandle();
  await expect(page.locator('.av-inspector-report')).toHaveCSS('animation-duration', '.46s'.replace(/^\./, '0.'));
  await page.getByRole('button', { name: '核对 00:05.00 关键帧' }).click();
  expect(await page.locator('.av-inspector-report').evaluate((node, previous) => node === previous, report)).toBe(true);
  await page.getByRole('button', { name: '关闭证据面板' }).click();
  await expect(panelVideo(page)).toHaveCount(0);
});
