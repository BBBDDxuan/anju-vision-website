import { test, expect, type Page } from '@playwright/test';
import path from 'node:path';

const sourceVideo = path.resolve('public/demo/videos/fall.mp4');

test('官网锚点、滚动高亮与旧链接兼容', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('h1')).toContainText('让牵挂');
  for (const [id, label] of [['capabilities', '核心能力'], ['workflow', '工作流程'], ['ai-qa', 'AI 问答'], ['devices', '设备接入']]) {
    await page.getByRole('navigation', { name: '官网导航' }).getByRole('link', { name: label, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`#${id}$`));
    await expect(page.locator(`.av-nav a[href="/#${id}"]`)).toHaveAttribute('aria-current', 'location');
    const position = await page.locator(`#${id}`).evaluate(element => {
      const top = element.getBoundingClientRect().top;
      const margin = parseFloat(getComputedStyle(element).scrollMarginTop);
      const maxScroll = document.documentElement.scrollHeight - innerHeight;
      return { top, expected: Math.max(margin, top + scrollY - maxScroll) };
    });
    expect(Math.abs(position.top - position.expected)).toBeLessThan(1);
    expect(position.top).toBeGreaterThanOrEqual(60);
  }
  await page.locator('#workflow').evaluate(element => element.scrollIntoView());
  await expect(page.locator('.av-nav a[href="/#workflow"]')).toHaveAttribute('aria-current', 'location');
  await page.goto('/#how-it-works');
  await expect(page.locator('.av-nav a[href="/#workflow"]')).toHaveAttribute('aria-current', 'location');
  await expect(page.locator('.intro-pagination')).toHaveCount(0);
  expect(errors).toEqual([]);
});

// Real video and exported-model acceptance tests share this suite.
import './real-video-cases';
import './workspace-v2-cases';

test('工作台路由、刷新、返回官网与浏览器后退', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: '开始视频分析' }).click();
  await expect(page).toHaveURL(/workspace\?view=videos/);
  await expect(page.locator('.breadcrumb strong')).toHaveText('AI 视频分析');
  await expect(page.getByRole('region', { name: '当前录像播放器' })).toBeVisible();
  await page.getByRole('tab', { name: 'AI 问答', exact: true }).click();
  await expect(page).toHaveURL(/panel=qa/);
  await expect(page.getByRole('tab', { name: 'AI 问答', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.reload();
  await expect(page.getByRole('tab', { name: 'AI 问答', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '实时摄像头' }).click();
  await expect(page).toHaveURL(/view=camera/);
  await page.goBack();
  await expect(page).toHaveURL(/view=videos.*panel=qa/);
  await page.getByRole('link', { name: '返回产品官网' }).click();
  await expect(page.locator('.av-site')).toBeVisible();
  await expect(page.locator('.sidebar')).toHaveCount(0);
});

test('手机菜单与工作台抽屉支持键盘关闭', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: '打开菜单', exact: true }).click();
  await expect(page.getByRole('navigation', { name: '官网导航' })).toBeVisible();
  await page.getByRole('navigation', { name: '官网导航' }).getByRole('link', { name: 'AI 问答' }).click();
  await expect(page.getByRole('button', { name: '打开菜单', exact: true })).toHaveAttribute('aria-expanded', 'false');
  await page.getByRole('banner').getByRole('link', { name: '进入工作台', exact: true }).click();
  await page.getByRole('button', { name: '打开导航', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '功能导航' })).toBeVisible();
  await expect(page.getByRole('button', { name: '关闭导航', exact: true }).last()).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: '打开导航', exact: true })).toBeFocused();
});

for (const width of [1920, 1440, 1024, 768, 390]) {
  test(`${width}px 官网和两种工作台无横向溢出`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    for (const url of ['/', '/workspace?view=camera', '/workspace?view=videos']) {
      await page.goto(url, { waitUntil: 'networkidle' });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await expect(page.locator('main')).toBeVisible();
    }
  });
}

test('后端不可用时明确报错，摄像头不会显示虚假在线', async ({ page }) => {
  await page.route('**/api/**', route => route.fulfill({ status: 503, json: { error: { message: '验收：后端暂时不可用' } } }));
  await page.goto('/workspace?view=camera');
  await expect(page.getByText('服务连接异常', { exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('运行环境读取失败，请重试。');
  await expect(page.getByRole('button', { name: '开启摄像头', exact: true })).toBeDisabled();
  await expect(page.getByText('在线', { exact: true })).toHaveCount(0);
});

test('减少动画模式仍可浏览并操作', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  expect(await page.locator('h1').evaluate(element => getComputedStyle(element).animationName)).toBe('none');
  await page.locator('#devices').scrollIntoViewIfNeeded();
  await expect(page.locator('.av-device-copy')).toHaveCSS('opacity', '1');
  await expect(page.locator('.av-stage-indicator')).toHaveCSS('transition-duration', '0s');
  await page.locator('.av-qa-evidence').click();
  await expect(page.locator('#av-qa-evidence-detail')).toHaveCSS('animation-name', 'none');
  await page.locator('.av-qa-evidence').click();
  await expect(page.locator('#av-qa-evidence-detail')).toBeHidden();
  await page.locator('#home').scrollIntoViewIfNeeded();
  await page.getByRole('link', { name: '开始视频分析' }).click();
  await expect(page.locator('.upload-dropzone')).toBeVisible();
});

test('标准动画模式的首屏渐入与平滑导航', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.goto('/');
  await expect(page.locator('h1')).toHaveCSS('opacity', '1');
  await page.locator('.av-nav').getByRole('link', { name: 'AI 问答', exact: true }).click();
  await expect.poll(() => page.locator('#ai-qa').evaluate(element => Math.abs(element.getBoundingClientRect().top - 78))).toBeLessThan(1);
  await expect(page.locator('.av-nav a[href="/#ai-qa"]')).toHaveAttribute('aria-current', 'location');
  await expect(page.locator('.av-chat-demo')).toHaveCSS('opacity', '1');
  await expect(page.locator('.av-stage-indicator')).toHaveCSS('transition-duration', '0.28s');
  await page.locator('.av-qa-evidence').click();
  await expect(page.locator('#av-qa-evidence-detail')).toHaveCSS('animation-name', 'av-evidence-reveal');
  await page.locator('.av-qa-evidence').click();
  await expect(page.locator('#av-qa-evidence-detail')).toHaveCSS('animation-name', 'av-evidence-dismiss');
  await expect(page.locator('#av-qa-evidence-detail')).toBeHidden();
  await expect(page.locator('#av-qa-evidence-detail img')).toHaveCount(0);

});

test('真实本地后端：上传、播放、未配置状态与删除清理', async ({ page, request }) => {
  await page.goto('/workspace?view=videos');
  const uploaded = page.waitForResponse(response => response.url().endsWith('/api/videos/upload') && response.request().method() === 'POST');
  await page.getByLabel('上传 MP4', { exact: true }).setInputFiles({ name: `redesign-acceptance-${Date.now()}.mp4`, mimeType: 'video/mp4', buffer: await (await import('node:fs/promises')).readFile(sourceVideo) });
  const response = await uploaded;
  expect(response.ok()).toBe(true);
  const { video_id: videoId } = await response.json();
  try {
    expect(videoId).toMatch(/^v_/);
    await expect(page).toHaveURL(new RegExp(`video=${videoId}`));
    await expect(page.locator('#watch video')).toHaveAttribute('src', `/media/videos/${videoId}`);
    await expect.poll(() => page.locator('video').evaluate((element: HTMLVideoElement) => element.readyState), { timeout: 20000 }).toBeGreaterThanOrEqual(1);
    await page.locator('video').evaluate(async (element: HTMLVideoElement) => { element.muted = true; await element.play(); });
    await expect.poll(() => page.locator('video').evaluate((element: HTMLVideoElement) => element.currentTime)).toBeGreaterThan(.1);
    await page.locator('video').evaluate((element: HTMLVideoElement) => element.pause());
    const health = await (await request.get('/api/health')).json();
    await page.locator('.workspace-task-tray>summary').click();
    if (!health.analysis_ready) await expect(page.getByRole('button', { name: '开始安全分析', exact: true })).toBeDisabled();
    await page.screenshot({ path: '../docs/screenshots/workspace-upload-real.png', fullPage: true });
  } finally {
    await page.locator('video').evaluateAll(elements => elements.forEach(element => { element.pause(); element.removeAttribute('src'); element.load(); }));
    if (/^v_/.test(videoId || '')) {
      const deleted = await request.post(`/api/videos/${videoId}/delete`);
      expect(deleted.ok()).toBe(true);
    }
  }
});

async function installFixture(page: Page) {
  let analyzed = false, jobRunning = false, posts = 0;
  const id = 'v_browser_fixture';
  const event = { id: 'event_fixture', start_ms: 1200, end_ms: 3200, subject_id: 'person_fixture', target_id: null, action: 'fall', epistemic_type: 'observed', evidence_ids: ['ev_fixture'], uncertainty_reasons: [], review_status: 'unreviewed', model_score: .81 };
  const evidence = { id: 'ev_fixture', start_ms: 1200, end_ms: 3200, frame_ids: ['frame1', 'frame2'] };
  const entities = [{ id: 'person_fixture', label: '人物', aliases: [], appearance: '测试人物', entity_type: 'person', identity_status: 'confirmed' }];
  const job = () => ({ id: 'j_browser_fixture', video_id: id, status: jobRunning ? 'running' : 'completed', stage: jobRunning ? 'safety_scan' : 'completed', analysis_mode: 'safety', progress: jobRunning ? 35 : 100, is_mock: true, failed_windows: [], safety: { processed_frames: jobRunning ? 14 : 40, total_frames: 40, candidate_count: 1, reviewed: 1, confirmed: 1, rejected: 0, uncertain: 0, unavailable: 0 } });
  const video = () => ({ id, name: '接口测试录像.mp4', duration_ms: 30000, active_run: analyzed ? 'j_browser_fixture' : null, read_only_demo: false });
  const memory = () => ({ entities, events: analyzed ? [event] : [], evidence: [evidence], frames: [{ id: 'frame1', pts_ms: 1200, url: '/assets/home-care.webp' }, { id: 'frame2', pts_ms: 2600, url: '/assets/home-care.webp' }], job: job(), is_mock: true, event_groups: [] });
  const media = await (await import('node:fs/promises')).readFile(sourceVideo);
  await page.route('**/media/videos/v_browser_fixture', route => {
    const range = route.request().headers().range?.match(/bytes=(\d+)-(\d*)/);
    if (!range) return route.fulfill({ body: media, contentType: 'video/mp4', headers: { 'Accept-Ranges': 'bytes' } });
    const start = Number(range[1]), end = range[2] ? Number(range[2]) : media.length - 1;
    return route.fulfill({ status: 206, body: media.subarray(start, end + 1), contentType: 'video/mp4', headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${media.length}`, 'Content-Length': String(end - start + 1) } });
  });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/health') return route.fulfill({ json: { status: 'ok', analysis_ready: true, cloud_mode: false, is_mock: true, model: 'browser-fixture', configuration_error: null, limits: { max_video_seconds: 86400, max_upload_mb: 100 } } });
    if (url.pathname === '/api/videos') return route.fulfill({ json: { videos: [video()] } });
    if (url.pathname === '/api/demos') return route.fulfill({ json: { demos: [] } });
    if (url.pathname === `/api/videos/${id}`) return route.fulfill({ json: { ...video(), latest_job: analyzed ? job() : null } });
    if (url.pathname.endsWith('/analysis-plan')) return route.fulfill({ json: { analysis_mode: url.searchParams.get('mode'), total_windows: 0, max_windows_per_batch: 2, daily_requests_remaining: 8, estimated_batches: null } });
    if (url.pathname.endsWith('/analyze')) { posts++; analyzed = true; jobRunning = true; return route.fulfill({ status: 202, json: job() }); }
    if (url.pathname.startsWith('/api/jobs/')) { await new Promise(resolve => setTimeout(resolve, 500)); jobRunning = false; return route.fulfill({ json: job() }); }
    if (url.pathname.endsWith('/events')) return route.fulfill({ json: memory() });
    if (url.pathname.endsWith('/alerts')) return route.fulfill({ json: { alerts: [], evidence: [], frames: [], absence_alert_seconds: 900 } });
    if (url.pathname.endsWith('/review')) return route.fulfill({ json: { revision: 1, run_id: 'j_browser_fixture', regions: [], entries: [], entities, busy: false, history: [] } });
    if (url.pathname.endsWith('/query')) return route.fulfill({ json: { status: 'answered', observations: [{ text: '测试数据中的疑似跌倒线索。', event_ids: ['event_fixture'], evidence_ids: ['ev_fixture'], record_summary: true }], inferences: [], unknowns: [], evidence: [evidence], coverage_note: '接口测试数据，未执行真实识别。', is_mock: true, candidates: [], answer_engine: 'record_statistics' } });
    return route.fulfill({ json: {} });
  });
  return { posts: () => posts };
}

test('接口测试数据：分析任务、问答、证据定位与切页保留', async ({ page }) => {
  const fixture = await installFixture(page);
  await page.goto('/workspace?view=videos&video=v_browser_fixture');
  await expect(page.locator('.mock-banner')).toBeVisible();
  await page.locator('.workspace-task-tray>summary').click();
  await page.getByRole('button', { name: '开始安全分析', exact: true }).click();
  await expect(page.getByRole('button', { name: '当前录像任务进行中…', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '物体追踪', exact: true })).toBeDisabled();
  await expect(page.locator('.analysis-job-status')).toContainText('分析完成');
  expect(fixture.posts()).toBe(1);
  await expect(page.locator('#timeline .event')).toHaveCount(1);
  await page.getByRole('tab', { name: 'AI 问答', exact: true }).click();
  await page.getByRole('textbox', { name: '你想了解什么情况？' }).fill('视频中有疑似跌倒吗？');
  await page.getByRole('button', { name: '提问', exact: true }).click();
  await expect(page.locator('.answer-result')).toContainText('测试数据中的疑似跌倒线索');
  await expect.poll(() => page.locator('video').evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThanOrEqual(1);
  await page.getByRole('tab', { name: '事件', exact: true }).click();
  await page.locator('#timeline .reference').first().click();
  await expect.poll(() => page.locator('video').evaluate((element: HTMLVideoElement) => element.currentTime)).toBeCloseTo(1.2, 1);
  await expect(page.locator('#timeline .reference').first()).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('tab', { name: 'AI 问答', exact: true }).click();
  await expect(page.locator('.answer-result')).toContainText('测试数据中的疑似跌倒线索');
  await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); window.scrollTo({ top: 0, behavior: 'instant' }); });
  await page.screenshot({ path: '../docs/screenshots/workspace-interaction-fixture.png', fullPage: true });
  await page.getByRole('tab', { name: '追踪与复核', exact: true }).click();
  await expect(page.locator('#inspector-panel-tools')).toBeVisible();
  await page.getByRole('button', { name: '人工复核', exact: true }).click();
  await expect(page.locator('#review summary').first()).toBeVisible();
});
