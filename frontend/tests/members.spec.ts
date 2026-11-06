import { test, expect, type Page } from '@playwright/test';
// @ts-ignore Shared JS browser fixture.
import { installWorkspaceFixture } from './workspace-fixture.mjs';

// Browser fixtures only. No real household photos, backend data, camera or cloud calls.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
const photo = (name = 'synthetic.png') => ({ name, mimeType: 'image/png', buffer: png });
const seed = () => ({ id: 'web-fixture-member', number: 1, name: '测试家人', relationship: '家人', notes: '', bindings: [], revision: 1, face_sample_count: 0 });

async function fixture(page: Page, options: { existing?: boolean; missing?: boolean; cloud?: boolean; conflict?: boolean; partial?: boolean } = {}) {
  const state = { members: options.existing ? [seed()] : [] as ReturnType<typeof seed>[], enroll: 0, recognize: 0, bodies: [] as Buffer[], writes: [] as any[], errors: [] as string[] };
  page.on('pageerror', error => state.errors.push(error.message));
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    const send = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path === '/api/health') return send({ analysis_ready: false, is_mock: true, cloud_mode: !!options.cloud, model: 'UI fixture', configuration_error: { code: 'OFFLINE', message: '测试：云分析关闭' }, limits: { max_video_seconds: null, max_upload_mb: null } });
    if (path === '/api/videos' || path === '/api/demos') return send({ [path.endsWith('videos') ? 'videos' : 'demos']: [] });
    if (path === '/api/faces/status') return send({ enabled: true, ready: !options.missing, error: options.missing ? 'FACE_MODEL_MISSING' : undefined, max_samples_per_member: 10, max_image_bytes: 5242880 });
    if (path === '/api/household/members') return send({ members: state.members });
    if (path.startsWith('/api/household/members/')) {
      const id = path.split('/').at(-1);
      if (method === 'DELETE') { state.members = state.members.filter(member => member.id !== id); return send({ deleted: true }); }
      const body = request.postDataJSON(); state.writes.push(body);
      if (options.conflict) return send({ error: { code: 'HOUSEHOLD_REVISION_CONFLICT', message: '档案已更新，请刷新后重试。' } }, 409);
      const member = { ...body, face_sample_count: state.members.find(m => m.id === id)?.face_sample_count || 0, revision: body.revision + 1 };
      state.members = [...state.members.filter(m => m.id !== id), member]; return send({ member });
    }
    if (path.endsWith('/samples')) {
      const member = state.members.find(m => m.id === path.split('/')[4])!;
      if (method === 'DELETE') { member.face_sample_count = 0; member.revision++; return send({ removed_sample_count: 2, deleted: true }); }
      expect(request.headers()['content-type']).toBe('image/png');
      expect(url.searchParams.get('consent')).toBe('true');
      state.enroll++; state.bodies.push(request.postDataBuffer()!);
      if (options.partial && state.enroll === 2) return send({ error: { code: 'FACE_ENROLLMENT_QUALITY', message: '照片需包含一张清晰人脸。' } }, 422);
      member.face_sample_count++; member.revision++; return send({ member: { id: member.id, sample_count: member.face_sample_count } }, 201);
    }
    if (path === '/api/faces/recognize') {
      state.recognize++;
      return send({ face_count: 2, faces: [
        { status: 'matched', member_id: state.members[0].id, name: state.members[0].name, bbox: [.1, .1, .45, .8] },
        { status: 'unknown', member_id: null, bbox: [.55, .1, .9, .8] },
      ] });
    }
    return send({});
  });
  return state;
}

test('网页建档、同意录入、批量上传、试识别、清除和删除', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/workspace?view=members');
  await expect(page.getByText('本地人脸识别已就绪')).toBeVisible();
  await page.getByRole('button', { name: '添加成员', exact: true }).click();
  await page.getByLabel('姓名', { exact: true }).fill('网页测试家人');
  await page.getByLabel('关系', { exact: true }).fill('父亲');
  await page.getByRole('button', { name: '保存档案', exact: true }).click();
  await expect(page.getByRole('button', { name: '选择照片录入', exact: true })).toBeDisabled();
  await page.getByRole('checkbox').check();
  await page.getByLabel('录入人脸照片', { exact: true }).setInputFiles([photo('front.png'), photo('side.png')]);
  await expect(page.getByText('2/10 张', { exact: true })).toBeVisible();
  expect(state.enroll).toBe(2); expect(state.bodies.every(body => body.equals(png))).toBe(true);
  await page.getByLabel('选择试识别照片', { exact: true }).setInputFiles(photo('check.png'));
  await expect(page.getByText('网页测试家人（人脸识别候选，请核对）', { exact: false })).toBeVisible();
  await expect(page.getByText('2 · 身份未确认', { exact: true })).toBeVisible();
  expect(state.recognize).toBe(1); expect(state.members[0].face_sample_count).toBe(2);
  page.on('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '清除人脸资料', exact: true }).click();
  await expect(page.getByText('0/10 张', { exact: true })).toBeVisible();
  await page.getByLabel('姓名', { exact: true }).fill('改名后家人');
  await expect(page.getByRole('button', { name: '选择照片录入', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '保存档案', exact: true }).click();
  await expect.poll(() => state.writes.at(-1)?.revision).toBe(4);
  await page.getByRole('button', { name: '删除成员', exact: true }).click();
  await expect(page.getByText('还没有成员，点击“添加成员”建立第一份档案。')).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('部分录入失败保留成功数量，非法文件不发送请求', async ({ page }) => {
  const state = await fixture(page, { existing: true, partial: true });
  await page.goto('/workspace?view=members');
  await page.getByRole('button', { name: /测试家人/ }).click();
  await page.getByRole('checkbox').check();
  await page.getByLabel('录入人脸照片', { exact: true }).setInputFiles([photo('front.png'), photo('bad.png')]);
  await expect(page.getByText('1/10 张', { exact: true })).toBeVisible();
  await expect(page.getByRole('alert').filter({ hasText: '照片需包含一张清晰人脸' })).toBeVisible();
  await page.getByLabel('录入人脸照片', { exact: true }).setInputFiles({ name: 'wrong.gif', mimeType: 'image/gif', buffer: png });
  await expect(page.getByText('请选择 JPEG 或 PNG 图片。', { exact: true })).toBeVisible();
  expect(state.enroll).toBe(2); expect(state.errors).toEqual([]);
});

test('档案冲突保留编辑，模型缺失允许建档并禁用录入', async ({ page }) => {
  const state = await fixture(page, { existing: true, missing: true, conflict: true });
  await page.goto('/workspace?view=members');
  await expect(page.getByText('缺少人脸模型，请先准备模型并刷新')).toBeVisible();
  await page.getByRole('button', { name: /测试家人/ }).click();
  await expect(page.getByRole('button', { name: '选择照片试识别', exact: true })).toBeDisabled();
  await page.getByLabel('姓名', { exact: true }).fill('保留未保存编辑');
  await page.getByRole('button', { name: '保存档案', exact: true }).click();
  await expect(page.getByRole('button', { name: '载入最新档案' })).toBeVisible();
  await expect(page.getByLabel('姓名', { exact: true })).toHaveValue('保留未保存编辑');
  expect(state.enroll).toBe(0); expect(state.errors).toEqual([]);
});

test('移动尺寸可使用成员页面，路由刷新保留入口', async ({ page }) => {
  const state = await fixture(page, { existing: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/workspace?view=members');
  await page.getByRole('button', { name: /测试家人/ }).click();
  await expect(page.getByRole('heading', { name: '人脸资料' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '../runtime/member-mobile.png', fullPage: true });
  await page.reload(); await expect(page.getByRole('heading', { name: '家庭成员', exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('桌面成员页面显示录入状态，云端页面不读取家庭资料', async ({ page }) => {
  await fixture(page, { existing: true });
  await page.goto('/workspace?view=members');
  await page.getByRole('button', { name: /测试家人/ }).click();
  await expect(page.getByRole('heading', { name: '人脸资料' })).toBeVisible();
  await page.screenshot({ path: '../runtime/member-desktop.png', fullPage: true });
  await page.unroute('**/api/**');
  const state = await fixture(page, { cloud: true });
  await page.reload();
  await expect(page.getByText('请在电脑本地启动服务后管理家庭档案和人脸资料。')).toBeVisible();
  await expect(page.getByRole('button', { name: '添加成员', exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('摄像头告警显示身份候选并随撤销更新，明火不挂姓名', async ({ page }) => {
  const state = await fixture(page);
  let matched = true;
  await page.route('**/api/camera/status', route => route.fulfill({ json: {
    state: 'stopped', frames: 0, recording: false, review_enabled: true,
    events: ['suspected_fall', 'fire_detected'].map(event_type => ({
      candidate_id: event_type, event_type, occurred_at: '2026-11-06T05:00:00Z',
      review_status: 'confirmed', review_model: 'synthetic-review',
      face_identity: matched ? { status: 'matched', member_id: 'test-person', name: '告警测试家人' } : { status: 'unknown', member_id: null },
    })),
  } }));
  await page.goto('/workspace?view=camera');
  await expect(page.getByRole('button', { name: '扫码连接手机', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '搜索摄像头', exact: true })).toBeVisible();
  await expect(page.locator('.privacy').getByRole('link', { name: '家庭成员', exact: true })).toBeVisible();
  await page.getByText('本次会话事件', { exact: true }).click();
  await expect(page.getByText('成员：告警测试家人（人脸识别候选，请核对）', { exact: true })).toHaveCount(1);
  const fire = page.locator('.live-camera-events li').filter({ hasText: '疑似明火' });
  await expect(fire).not.toContainText('成员：');
  matched = false;
  await expect(page.getByText('成员：身份未确认', { exact: true })).toBeVisible();
  await expect(page.getByText('成员：告警测试家人（人脸识别候选，请核对）', { exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('录像告警显示后端人脸身份，不把环境事件归给家人', async ({ page }) => {
  await installWorkspaceFixture(page);
  await page.route('**/api/videos/*/alerts', route => route.fulfill({ json: {
    alerts: ['fall', 'flame'].map((action, i) => ({ id: `synthetic-alert-${i}`, action,
      title: i ? '环境测试明火' : '人物测试跌倒', person_label: i ? '明火位置' : '匿名人物',
      severity: 'urgent', start_ms: 2300, end_ms: 6300, acknowledged: false, evidence_ids: [], is_mock: true,
      face_identity: { status: 'matched', member_id: 'recorded-person', name: '录像测试家人' },
    })), evidence: [], frames: [], absence_alert_seconds: 300,
  } }));
  await page.goto('/workspace?view=videos&video=v_inspector_fixture');
  await page.getByText('预警与通知设置', { exact: true }).click();
  await expect(page.getByText('成员：录像测试家人（人脸识别候选，请核对）', { exact: true })).toHaveCount(1);
  await expect(page.locator('.safety-alert').filter({ hasText: '环境测试明火' })).not.toContainText('成员：');
});
