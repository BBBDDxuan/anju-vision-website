import { test, expect, type Page, type Locator } from '@playwright/test';
import path from 'node:path';
import { installWorkspaceFixture } from './workspace-fixture.mjs';

const currentTime = (video: Locator) => video.evaluate((node: HTMLVideoElement) => node.currentTime);
const tab = (page: Page, name: string) => page.getByRole('tab', { name, exact: true });
const run = process.env.ANJU_CHECK_DIR || path.resolve('../data/run/family-workspace-check-20261106');
async function openWorkspace(page: Page, options = {}) {
  const fixture = await installWorkspaceFixture(page, options);
  await page.goto('/workspace?view=videos&video=v_inspector_fixture');
  await expect.poll(() => page.locator('#watch video').evaluate((node: HTMLVideoElement) => node.readyState)).toBeGreaterThanOrEqual(1);
  await expect(page.locator('.mock-banner')).toBeVisible();
  return fixture;
}

test('核心能力展示保持，工作台删除人工操作及专属接口调用', async ({ page }) => {
  await page.goto('/#capabilities');
  await expect(page.getByRole('button', { name: '查看 AI 分析依据', exact: true })).toHaveCount(0);
  await expect(page.locator('[data-case=fall] .detection-overlay')).toHaveCount(1);
  const fixture = await openWorkspace(page);
  for (const name of ['事件提醒','AI 问答','目标追踪']) await tab(page, name).click();
  await expect(page.locator('.review-panel,.region-canvas,.review-history')).toHaveCount(0);
  expect(await page.locator('#main-content').innerText()).not.toMatch(/人工复核|复核历史|人工确认|人工排除|待复核|复核状态|复核不可用|场景标定|较高可信|中等可信|Qwen|模型候选/);
  expect(fixture.calls.some(call => /\/(review|human|regions)(?:\/|$)/.test(call.path))).toBe(false);
});

test('事件、时间轴和问答共享稳定播放器，标签切换保留播放状态', async ({ page }) => {
  const fixture = await openWorkspace(page);
  const video = page.locator('#watch video');
  await expect(page.locator('#timeline .event')).toHaveCount(2);
  await page.screenshot({ path: path.join(run, 'workspace-events-desktop.png'), fullPage: true });
  const handle = await video.elementHandle();
  await page.locator('.event-select').first().click();
  await expect.poll(() => currentTime(video)).toBeCloseTo(2.3, 1);
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  await expect(page.locator('.event.is-selected')).toHaveAttribute('data-event-id', fixture.events[0].id);
  await expect(page.locator('.timebar-markers button').first()).toHaveAttribute('aria-pressed', 'true');
  await tab(page, 'AI 问答').click();
  expect(await video.evaluate((node, previous) => node === previous, handle)).toBe(true);
  expect(await currentTime(video)).toBeCloseTo(2.3, 1);
  await page.getByLabel('你想了解什么情况？').fill('测试录像里有什么异常？');
  await page.getByRole('button', { name: '提问', exact: true }).click();
  await expect(page.locator('.answer-result')).toContainText('界面回归测试');
  await page.locator('.answer-result .reference').first().click();
  await expect.poll(() => currentTime(video)).toBeCloseTo(2.3, 1);
  await video.evaluate((v: HTMLVideoElement) => { v.muted = true; return v.play(); });
  await tab(page, '目标追踪').click();
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
  expect(await video.evaluate((node, previous) => node === previous, handle)).toBe(true);
  await video.evaluate((v: HTMLVideoElement) => v.pause());
  await tab(page, '事件提醒').click();
  await page.locator('.timebar-markers button').last().click();
  await expect.poll(() => currentTime(video)).toBeCloseTo(4.5, 1);
  await expect(page.locator('.event.is-selected')).toHaveAttribute('data-event-id', fixture.events[1].id);
  await tab(page, 'AI 问答').click();
  await expect(page.locator('.answer-result')).toContainText('界面回归测试');
  expect(fixture.calls.filter(call => call.method === 'POST' && !call.path.endsWith('/query'))).toHaveLength(0);
});

test('共用证据仍保留事件选择，筛选不重载视频且挥手不推断求救', async ({ page }) => {
  const fixture = await openWorkspace(page, { sharedEvidence: true });
  await page.locator('.event-select').last().click();
  await expect(page.locator('.event.is-selected')).toHaveAttribute('data-event-id', fixture.events[1].id);
  await expect(page.locator('.timebar-markers button').last()).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: '挥手动作', exact: true }).click();
  await expect(page.locator('#timeline .event')).toHaveCount(1);
  expect(await page.locator('#timeline').innerText()).not.toMatch(/求救|求助|置信|可信/);
  await expect.poll(() => currentTime(page.locator('#watch video'))).toBeCloseTo(2.3, 1);
  await page.getByRole('button', { name: '明火提醒', exact: true }).click();
  await expect(page.locator('#timeline .event')).toHaveCount(0);
  await expect(page.locator('#timeline')).toContainText('当前筛选没有匹配事件');
});

test('切换录像清空旧问答，迟到的答案不能串台', async ({ page }) => {
  const fixture = await openWorkspace(page, { queryDelay: 700 });
  await tab(page, 'AI 问答').click();
  await page.getByLabel('你想了解什么情况？').fill('一个延迟的测试问题');
  await page.getByRole('button', { name: '提问', exact: true }).click();
  await page.getByLabel('当前视频', { exact: true }).selectOption(fixture.ids[1]);
  await expect(page.locator('#watch video')).toHaveAttribute('src', `/media/videos/${fixture.ids[1]}`);
  await expect(page.getByLabel('你想了解什么情况？')).toHaveValue('');
  await page.waitForTimeout(850);
  await expect(page.locator('.answer-result')).toHaveCount(0);
  await tab(page, '事件提醒').click();
  await expect(page.locator('#timeline .event')).toHaveCount(0);
  await expect(page.locator('.timebar-markers button[aria-pressed=true]')).toHaveCount(0);
});

test('分析进度就地展开，暂停继续和目标追踪沿用原接口', async ({ page }) => {
  const fixture = await openWorkspace(page, { analyzed: false, autoComplete: false });
  await expect(page.getByRole('button', { name: '安全分析', exact: true })).toHaveAttribute('aria-pressed','true');
  await page.getByRole('button', { name: '开始安全分析', exact: true }).click();
  await expect(page.locator('.workspace-analysis-area')).toContainText('正在分析视频中的人物和环境安全情况');
  await expect(page.getByRole('progressbar', { name: '视频分析进度' })).toBeVisible();
  await expect(page.getByRole('button', { name: '物体追踪', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '暂停当前任务', exact: true }).click();
  await expect(page.getByRole('button', { name: '继续上次安全分析', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '继续上次安全分析', exact: true }).click();
  await page.getByRole('button', { name: '暂停当前任务', exact: true }).click();
  await tab(page, '目标追踪').click();
  await page.getByLabel('选择要回顾的对象').selectOption('fixture_person');
  await expect(page.locator('.replay-entry')).toHaveCount(2);
  await page.locator('.replay-entry .reference').first().click();
  await expect.poll(() => currentTime(page.locator('#watch video'))).toBeCloseTo(2.3, 1);
  await page.getByRole('button', { name: '物体追踪', exact: true }).click();
  await expect(page.getByRole('button', { name: '开始物体追踪', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '开始物体追踪', exact: true }).click();
  expect(fixture.calls.some(call => call.path.endsWith('/analyze?mode=objects') && call.method === 'POST')).toBe(true);
  expect(fixture.calls.some(call => call.path.endsWith('/pause') && call.method === 'POST')).toBe(true);
  expect(fixture.calls.some(call => call.path.endsWith('/resume') && call.method === 'POST')).toBe(true);
});

test('问答快捷入口、未分析、证据不足及连接失败均保留真实状态', async ({ page }) => {
  const fixture = await openWorkspace(page, { analyzed: false });
  await tab(page, 'AI 问答').click();
  await expect(page.locator('.qa-prerequisite')).toHaveText('请先完成视频分析，我再为你介绍录像中的情况。');
  await page.getByRole('button', { name: '视频中有几个人？', exact: true }).click();
  await expect(page.getByLabel('你想了解什么情况？')).toHaveValue('视频中有几个人？');
  await expect(page.getByRole('button', { name: '提问', exact: true })).toBeDisabled();
  expect(fixture.calls.filter(call => call.path.endsWith('/query'))).toHaveLength(0);
  await page.getByRole('button', { name: '开始安全分析', exact: true }).click();
  await expect(page.locator('.analysis-job-status')).toContainText('分析完成');
  fixture.state.unknown = true;
  await page.getByRole('button', { name: '提问', exact: true }).click();
  await expect(page.locator('.answer-result')).toContainText('这段视频中暂时没有找到相关信息。');
  fixture.state.queryError = true;
  await page.getByRole('button', { name: '提问', exact: true }).click();
  await expect(page.locator('.workspace-notices [role=alert]')).toContainText('问答连接失败');
});

test('提醒分类计数、已查看保存和真实浏览器通知权限及开关', async ({ page, context }) => {
  await context.grantPermissions(['notifications']);
  await page.addInitScript(() => {
    const nativeRequest = Notification.requestPermission.bind(Notification);
    (window as any).permissionRequests = 0;
    Notification.requestPermission = (...args) => { (window as any).permissionRequests++; return nativeRequest(...args); };
    const Native = Notification;
    (window as any).sentNotifications = [];
    (window as any).Notification = class extends Native { constructor(title: string, options?: NotificationOptions) { super(title, options); (window as any).sentNotifications.push(title); } };
  });
  const fixture = await openWorkspace(page, { autoComplete: false, status: 'running' });
  await page.locator('.reminder-settings summary').click();
  await page.getByRole('switch', { name: '浏览器通知', exact: true }).click();
  await expect(page.getByRole('switch', { name: '浏览器通知', exact: true })).toBeChecked();
  await expect(page.locator('.reminder-settings')).toContainText('浏览器通知已开启');
  expect(await page.evaluate(() => (window as any).permissionRequests)).toBe(1);
  fixture.state.alerts.push({ id:'a_fall', action:'fall', start_ms:2300, end_ms:6300, acknowledged:false, is_mock:true });
  await expect(page.locator('.reminder-card')).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => (window as any).sentNotifications.length)).toBe(1);
  await page.getByRole('button', { name: '标记为已查看', exact: true }).click();
  await expect(page.locator('.reminder-card')).toContainText('已查看');
  await expect(page.locator('.family-reminders > .inspector-section-heading')).toContainText('0 条待查看');
  await page.getByRole('switch', { name: '挥手提醒', exact: true }).uncheck();
  fixture.state.alerts.push({ id:'a_wave', action:'help_gesture', start_ms:4500, end_ms:6300, acknowledged:false, is_mock:true });
  await expect(page.locator('.reminder-card')).toHaveCount(2);
  expect(await page.evaluate(() => (window as any).sentNotifications.length)).toBe(1);
  await page.getByRole('switch', { name: '浏览器通知', exact: true }).uncheck();
  fixture.state.alerts.push({ id:'a_fire', action:'flame', start_ms:4500, end_ms:6300, acknowledged:false, is_mock:true });
  await expect(page.locator('.reminder-card')).toHaveCount(3);
  expect(await page.evaluate(() => (window as any).sentNotifications.length)).toBe(1);
  await expect(page.locator('.family-reminders > .inspector-section-heading')).toContainText('2 条待查看');
  await page.getByRole('button', { name:'暂停当前任务', exact:true }).click();
  await page.reload();
  await page.locator('.reminder-settings summary').click();
  await expect(page.getByRole('switch', { name: '挥手提醒', exact: true })).not.toBeChecked();
  await expect(page.getByRole('switch', { name: '浏览器通知', exact: true })).not.toBeChecked();
  expect(fixture.calls.some(call => call.path.endsWith('/acknowledge') && call.method === 'POST')).toBe(true);
  expect(fixture.calls.some(call => /\/(human|review|regions)/.test(call.path))).toBe(false);
});

test('浏览器拒绝通知权限时展示真实反馈', async ({ page, context }) => {
  await context.clearPermissions();
  await page.addInitScript(() => { Object.defineProperty(Notification, 'permission', { get: () => 'denied' }); Notification.requestPermission = async () => 'denied'; });
  await openWorkspace(page);
  await page.locator('.reminder-settings summary').click();
  await page.getByRole('switch', { name: '浏览器通知', exact: true }).click();
  await expect(page.getByRole('switch', { name: '浏览器通知', exact: true })).not.toBeChecked();
  await expect(page.locator('.reminder-settings')).toContainText('通知权限已被浏览器阻止');
});

test('旧锚点、刷新、浏览器前后退仍定位相同录像与标签', async ({ page }) => {
  await installWorkspaceFixture(page);
  for (const [hash, name] of [['#timeline', '事件提醒'], ['#ask', 'AI 问答'], ['#analysis-tools', '目标追踪']]) {
    await page.goto(`/workspace?view=videos&video=v_inspector_fixture${hash}`);
    await expect(tab(page, name)).toHaveAttribute('aria-selected', 'true');
  }
  await tab(page, '事件提醒').click(); await tab(page, 'AI 问答').click();
  await expect(page).toHaveURL(/panel=qa/); await page.reload();
  await expect(tab(page, 'AI 问答')).toHaveAttribute('aria-selected', 'true');
  await tab(page, '目标追踪').click(); await page.goBack();
  await expect(tab(page, 'AI 问答')).toHaveAttribute('aria-selected', 'true');
  await page.goForward(); await expect(tab(page, '目标追踪')).toHaveAttribute('aria-selected', 'true');
});

for (const [width, height] of [[1366,768],[1440,900],[1920,1080]]) test(`${width}×${height} 上传即见主按钮，分析期间左右栏统一整页滚动`, async ({ page }) => {
  await page.setViewportSize({ width, height });
  const fixture = await installWorkspaceFixture(page, { empty:true, analyzed:false, autoComplete:false });
  await page.goto('/workspace?view=videos');
  await expect(page.getByRole('button', { name:'导入居家录像', exact:true })).toContainText('从一段居家录像开始');
  await expect(page.getByLabel('视频分析使用指南')).toContainText('选择分析模式');
  await expect(page.getByRole('tablist', { name: '当前视频功能' })).toHaveCount(0);
  await page.locator('#recording-upload').setInputFiles(path.resolve('public/demo/videos/fall.mp4'));
  const start = page.getByRole('button', { name:'开始安全分析', exact:true });
  await expect(start).toBeVisible();
  const bounds = await start.boundingBox();
  expect(bounds!.y).toBeGreaterThanOrEqual(0); expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(height);
  expect(await page.evaluate(() => scrollY)).toBe(0);
  await page.screenshot({ path:path.join(run,`workspace-upload-${width}.png`) });
  await start.click();
  await expect(page.getByRole('progressbar', { name:'视频分析进度' })).toBeVisible();
  for (const selector of ['.analysis-player-column','.analysis-inspector','.workspace-inspector-scroll','.workspace-analysis-area']) {
    expect(await page.locator(selector).evaluate(node => ['auto','scroll','hidden'].includes(getComputedStyle(node).overflowY))).toBe(false);
    expect(await page.locator(selector).evaluate(node => node.scrollHeight > node.clientHeight + 1)).toBe(false);
  }
  const before = await page.locator('.analysis-inspector').boundingBox();
  await page.locator('.workspace-inspector-scroll').evaluate(node => { const p=document.createElement('p'); p.style.height='1300px'; p.textContent='整页滚动检查'; node.append(p); });
  await page.evaluate(() => scrollTo(0,300));
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(300);
  const after = await page.locator('.analysis-inspector').boundingBox();
  expect(before!.y-after!.y).toBe(300);
  expect(await page.locator('.analysis-player-column').evaluate(node => node.scrollTop)).toBe(0);
  expect(await page.locator('.workspace-inspector-scroll').evaluate(node => node.scrollTop)).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(fixture.calls.some(call => call.path.endsWith('/analyze?mode=safety'))).toBe(true);
});

for (const width of [768,390]) test(`${width}px 手机布局、键盘标签及导航抽屉可用`, async ({ page }) => {
  await page.setViewportSize({ width,height:844 });
  await openWorkspace(page);
  await tab(page,'事件提醒').focus(); await page.keyboard.press('ArrowRight');
  await expect(tab(page,'AI 问答')).toHaveAttribute('aria-selected','true');
  await page.keyboard.press('End'); await expect(tab(page,'目标追踪')).toBeFocused();
  await page.getByLabel('选择要回顾的对象').selectOption('fixture_person');
  await expect(page.locator('.replay-entry')).toHaveCount(2);
  await page.getByRole('button', { name:'打开导航',exact:true }).click();
  await expect(page.getByRole('dialog', { name:'功能导航' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name:'打开导航',exact:true })).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path:path.join(run,`workspace-mobile-${width}.png`),fullPage:true });
});


test('隔离真实服务支持原片上传、播放、暂停和拖动，未配置分析时反馈真实状态', async ({ page }) => {
  // No API interception: exercises the production auth, upload, metadata,
  // persistence, Range media endpoint and App together, without paid inference.
  await page.goto('/workspace?view=videos');
  await page.locator('#recording-upload').setInputFiles({ name:'家庭工作台真实上传测试.mp4', mimeType:'video/mp4', buffer:await (await import('node:fs/promises')).readFile(path.resolve('public/demo/videos/fall.mp4')) });
  const video = page.locator('#watch video');
  await expect(video).toBeVisible({ timeout:30000 });
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(1);
  await expect(page.getByRole('button', { name:'开始安全分析',exact:true })).toBeDisabled();
  await expect(page.locator('.workspace-analysis-area')).toContainText('分析服务暂未就绪');
  await video.evaluate((v: HTMLVideoElement) => { v.muted=true; return v.play(); });
  await expect.poll(() => currentTime(video)).toBeGreaterThan(0);
  await video.evaluate((v: HTMLVideoElement) => v.pause());
  await page.getByRole('slider', { name:'工作台视频时间定位',exact:true }).fill('3');
  await expect.poll(() => currentTime(video)).toBeCloseTo(3,1);
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
  await page.reload();
  await expect(page.getByLabel('当前视频', { exact:true })).toContainText('家庭工作台真实上传测试.mp4');
  await expect(page.locator('#watch video')).toBeVisible();
});
