import { test, expect } from '@playwright/test';
import path from 'node:path';
import { readFile } from 'node:fs/promises';
import { installWorkspaceFixture } from './workspace-fixture.mjs';
import './wave-overlay.spec';
import './members.spec';

const run = process.env.ANJU_CHECK_DIR || path.resolve('../data/run/product-round2-check-20261107');
const fixture = JSON.parse(await readFile(path.resolve('public/demo/demo-public-fixture.json'), 'utf8'));
const tab = (page: any, name: string) => page.getByRole('tab', { name, exact: true });

for (const [width, height] of [[1366,768],[1440,900],[1920,1080],[390,844]]) {
  test(`${width}px 核心能力导航、历史恢复与真实片段边界`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.goto('/');
    if (width < 600) await page.getByRole('button', { name: '打开菜单', exact:true }).click();
    await page.getByRole('navigation', { name:'官网导航' }).getByRole('link', { name:'核心能力', exact:true }).click();
    await page.evaluate(() => document.fonts.ready);
    // Include the existing entrance/header transitions in the layout check.
    await page.waitForTimeout(900);
    const heading = page.locator('#capabilities-title');
    const checkOffset = async () => {
      const top = (await heading.boundingBox())!.y;
      const header = (await page.locator('.av-header').boundingBox())!;
      expect(top).toBeGreaterThan(header.y + header.height);
      expect(top).toBeLessThan(header.y + header.height + 100);
    };
    await checkOffset();
    await expect(page.locator('.av-demo-intro')).toHaveCount(1);
    const cards = page.locator('.av-video-card');
    await expect(cards).toHaveCount(3);
    if (width >= 1366) for (const card of await cards.all()) {
      const result = (await card.locator('.av-case-result').boundingBox())!;
      const action = (await card.locator('.av-segment-play').first().boundingBox())!;
      expect(result.y + result.height).toBeLessThanOrEqual(height);
      expect(action.y + action.height).toBeLessThanOrEqual(height);
    }
    for (const [i, id] of ['fall','help','fire'].entries()) {
      const card = page.locator(`[data-case=${id}]`);
      await expect(card.locator('.av-case-result dt')).toHaveText(['识别结果','出现时间']);
      await expect(card.locator('.av-card-caveat,.av-real-footage,.av-detection-source')).toHaveCount(0);
      // Use each card's published range; exact fixture-derived values are also
      // checked by the preserved data/overlay suites.
      const published = await card.locator('.av-case-result time').first().innerText();
      const numbers = published.match(/(\d+):(\d+\.\d+)/g)!.map(t => { const [m,s] = t.split(':').map(Number); return m*60+s; });
      await card.locator('.av-segment-play').first().click();
      const video = card.locator('video');
      await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);
      const start = await video.evaluate((v: HTMLVideoElement) => v.currentTime);
      expect(start).toBeGreaterThanOrEqual(numbers[0] - .03);
      expect(start).toBeLessThan(numbers[0] + .9);
      await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused), { timeout:8000 }).toBe(true);
      expect(Math.abs(await video.evaluate((v: HTMLVideoElement) => v.currentTime) - numbers[1])).toBeLessThan(.09);
      expect(await video.evaluate((v: HTMLVideoElement) => v.loop)).toBe(true);
    }
    await page.goto('/#capabilities');
    await page.waitForTimeout(900);
    await checkOffset();
    if (width >= 1366) for (const card of await cards.all()) {
      const action = (await card.locator('.av-segment-play').first().boundingBox())!;
      expect(action.y + action.height).toBeLessThanOrEqual(height);
    }
    if (width < 600) await page.locator('#capabilities').screenshot({ path:path.join(run,`capabilities-round2-${width}.png`) });
    else await page.screenshot({ path:path.join(run,`capabilities-round2-${width}.png`) });
    if (width < 600) await page.getByRole('button', { name:'打开菜单',exact:true }).click();
    await page.getByRole('navigation', { name:'官网导航' }).getByRole('link', { name:'工作流程',exact:true }).click();
    await page.goBack(); await page.waitForTimeout(900); await checkOffset();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test('空工作台展示引导，上传后各 Tab 独立且历史按视频保留', async ({ page }) => {
  const state = await installWorkspaceFixture(page, { empty:true, analyzed:false });
  await page.goto('/workspace?view=videos');
  await expect(page.locator('.topbar')).toContainText('AI 视频分析');
  await expect(page.locator('.video-page-heading')).toHaveCount(0);
  await expect(page.getByLabel('视频分析使用指南')).toContainText('开始分析并查看结果');
  await expect(page.locator('.family-reminders,.workspace-inspector-tabs')).toHaveCount(0);
  await page.screenshot({ path:path.join(run,'workspace-empty-round2.png'),fullPage:true });
  await page.locator('#recording-upload').setInputFiles(path.resolve('public/demo/videos/fall.mp4'));
  await expect(page.getByLabel('视频分析使用指南')).toHaveCount(0);
  await expect(page.getByRole('tab')).toHaveCount(3);
  await expect(page.locator('.reminder-settings')).toHaveCount(1);
  await expect(page.locator('.reminder-settings')).not.toHaveAttribute('open');
  await page.getByRole('button', { name:'开始安全分析',exact:true }).click();
  await expect(page.locator('.analysis-result-summary')).toBeVisible();
  for (const name of ['AI 问答','目标追踪','事件提醒']) {
    await tab(page,name).click();
    await expect(page.locator('.reminder-settings:visible')).toHaveCount(name === '事件提醒' ? 1 : 0);
    await expect(page.locator('.family-reminders:visible')).toHaveCount(name === '事件提醒' ? 1 : 0);
  }
  await tab(page,'AI 问答').click();
  for (const question of ['视频中有没有人摔倒？','有没有人挥手？']) {
    await page.getByLabel('你想了解什么情况？').fill(question);
    await page.getByRole('button', { name:'提问',exact:true }).click();
    await expect(page.locator('.conversation-history li')).toHaveCount(question.includes('摔倒') ? 1 : 2);
  }
  await page.locator('.conversation-history button').first().click();
  await expect(page.getByLabel('你想了解什么情况？')).toHaveValue('视频中有没有人摔倒？');
  await expect(page.locator('.answer-result .reference')).not.toHaveCount(0);
  await page.locator('.answer-result .reference').first().click();
  await expect.poll(() => page.locator('#watch video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(2.3,1);
  await page.screenshot({ path:path.join(run,'workspace-qa-round2.png'),fullPage:true });
  await page.getByLabel('当前视频',{exact:true}).selectOption(state.ids[1]);
  await expect(page.locator('.conversation-history')).toHaveCount(0);
  await page.getByLabel('当前视频',{exact:true}).selectOption(state.ids[0]);
  await expect(page.locator('.conversation-history li')).toHaveCount(2);
  await page.locator('.conversation-history button').first().click();
  await expect(page.locator('.answer-result')).toBeVisible();
});

test('其他 Tab 不显示提醒，但后台轮询继续且切回不重发通知', async ({ page, context }) => {
  await context.grantPermissions(['notifications']);
  await page.addInitScript(() => {
    const Native = Notification;
    (window as any).notifications = [];
    (window as any).Notification = class extends Native { constructor(title: string, options?:NotificationOptions) { super(title,options); (window as any).notifications.push(title); } };
  });
  const fixture = await installWorkspaceFixture(page,{status:'running',autoComplete:false});
  await page.goto('/workspace?view=videos&video=v_inspector_fixture');
  await page.locator('.reminder-settings summary').click();
  await page.getByRole('switch',{name:'浏览器通知',exact:true}).click();
  await expect(page.getByRole('switch',{name:'浏览器通知',exact:true})).toBeChecked();
  await expect(page.locator('.family-reminders')).toContainText('0 条待查看');
  await tab(page,'AI 问答').click();
  fixture.state.alerts.push({id:'round2-late',action:'fall',start_ms:2300,end_ms:6300,acknowledged:false,is_mock:true});
  await expect.poll(() => page.evaluate(() => (window as any).notifications.length)).toBe(1);
  await expect(page.locator('.family-reminders')).toBeHidden();
  await tab(page,'目标追踪').click(); await tab(page,'事件提醒').click();
  await expect(page.locator('.reminder-card')).toHaveCount(1);
  await page.waitForTimeout(2200);
  expect(await page.evaluate(() => (window as any).notifications.length)).toBe(1);
});

for (const width of [1440,390]) test(`${width}px 摄像头搜索只发现设备，显式连接才开启并显示安全状态`, async ({page}) => {
  await page.setViewportSize({width,height:900});
  await installWorkspaceFixture(page);
  const calls:string[] = [];
  let running = false;
  const frame = await readFile(path.resolve('public' + fixture.cases[0].event.referencedFrames[0].image));
  await page.route('**/api/camera/**', route => {
    const p = new URL(route.request().url()).pathname;
    if (p.endsWith('/frame.jpg')) return route.fulfill({body:frame,contentType:'image/jpeg'});
    if (p.endsWith('/start') || p.endsWith('/stop')) { calls.push(p); running = p.endsWith('/start'); }
    return route.fulfill({json:{state:running?'running':'stopped',frames:0,recording:false,review_enabled:true,fire_state:'running',events:[]}});
  });
  await page.route('**/api/lan/cameras',route => { calls.push('discover'); return route.fulfill({json:{cameras:[{host:'192.0.2.10',port:554,username:'admin',path:'stream1'}]}}); });
  let connectAttempts = 0;
  await page.route('**/api/monitoring/camera',route => {
    calls.push('connect');
    if (++connectAttempts === 1) return route.fulfill({status:503,json:{error:{message:'测试：连接失败，请检查设备密码。'}}});
    running=true; return route.fulfill({json:{state:'running',monitoring:true}});
  });
  await page.goto('/workspace?view=camera');
  await expect(page.getByRole('heading',{name:'实时摄像头',exact:true})).toHaveCount(0);
  await expect(page.locator('.topbar .breadcrumb strong')).toHaveText('实时摄像头');
  await expect(page.locator('.camera-safety')).toContainText('跌倒识别');
  await expect(page.locator('.camera-capability-list')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'开启摄像头',exact:true})).toBeVisible();
  if (width > 1000) {
    const header=(await page.locator('.live-camera-heading').boundingBox())!, button=(await page.getByRole('button',{name:'搜索摄像头',exact:true}).boundingBox())!;
    expect(header.height).toBeLessThan(64);
    expect(Math.abs(button.y+button.height/2-header.y-header.height/2)).toBeLessThan(5);
  }
  await page.getByRole('button',{name:'搜索摄像头',exact:true}).click();
  const dialog = page.getByRole('dialog',{name:'添加摄像头'});
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button',{name:'192.0.2.10',exact:true})).toBeVisible();
  expect(calls).toEqual(['discover']);
  await dialog.getByRole('button',{name:'192.0.2.10',exact:true}).click();
  await dialog.getByLabel('设备密码').fill('synthetic-password');
  await dialog.getByRole('button',{name:'连接并开启摄像头',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('连接失败');
  expect(await dialog.getByRole('alert').evaluate(node => parseFloat(getComputedStyle(node).fontSize))).toBeGreaterThanOrEqual(16);
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel('设备密码')).toHaveValue('synthetic-password');
  await dialog.getByRole('button',{name:'连接并开启摄像头',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('.lan-setup > [role=status]')).toContainText('实时看护已开启');
  expect(await page.locator('.lan-setup > [role=status]').evaluate(node => parseFloat(getComputedStyle(node).fontSize))).toBeGreaterThanOrEqual(16);
  expect(calls).toEqual(['discover','connect','connect']);
  await expect(page.getByRole('button',{name:'停止预览',exact:true})).toBeVisible();
  await expect(page.locator('.camera-safety')).toContainText('关注中');
  await page.screenshot({path:path.join(run,`camera-round2-${width}.png`),fullPage:true});
  await page.getByRole('button',{name:'停止预览',exact:true}).click();
  await expect(page.getByRole('button',{name:'开启摄像头',exact:true})).toBeVisible();
  expect(calls.at(-1)).toBe('/api/camera/stop');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
