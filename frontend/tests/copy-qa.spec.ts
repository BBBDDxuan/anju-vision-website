import { test, expect } from '@playwright/test';
import path from 'node:path';
import { installWorkspaceFixture } from './workspace-fixture.mjs';

test('本轮官网文案与流程说明', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.av-hero-description')).toContainText('不用守着监控，也不会错过需要注意的危险。');
  await expect(page.locator('.av-connection-card h3')).toContainText(['本地摄像头']);
  await expect(page.getByText('分析人物活动与环境情况，找到需要注意的片段。', { exact: true })).toBeAttached();
  await expect(page.getByText('一个真实案例 · 四个查看阶段', { exact: true })).toHaveCount(0);
});

test('本轮问答保留否定答复和有证据的人数统计', async ({ page }) => {
  await installWorkspaceFixture(page);
  await page.route('**/api/videos/*/query', route => {
    const question = route.request().postDataJSON().question;
    const count = question.includes('几个人');
    return route.fulfill({ json: { status: count ? 'partial' : 'unknown', question, is_mock: true,
      observations: count ? [{ text: '已有直接观察记录包含1个人物候选。同一采样时刻最多为1个人物候选。这不是整段录像准确的独立人数。',
        event_ids: ['fixture_event_second'], evidence_ids: ['fixture_evidence_second'], record_summary: true }] : [],
      inferences: [], candidates: [], evidence: [], coverage_note: '',
      unknowns: count ? [] : [`已分析记录中未找到${question.includes('明火') ? '明火' : '跌倒'}事件；这不等于确认视频中没有发生，请回看原片。`],
    } });
  });
  await page.goto('/workspace?view=videos&video=v_inspector_fixture');
  await page.getByRole('tab', { name: 'AI 问答', exact: true }).click();
  for (const question of ['视频中有没有人摔倒？', '视频中也没有出现明火', '视频中有几个人？']) {
    await page.getByLabel('你想了解什么情况？', { exact: true }).fill(question);
    await page.getByRole('button', { name: '提问', exact: true }).click();
    const result = page.locator('.answer-result');
    await expect(result).toContainText(question.includes('几个人') ? '1个人物候选' : `未找到${question.includes('明火') ? '明火' : '跌倒'}事件`);
    await expect(result).not.toContainText('检测到挥手动作');
    await expect(result).not.toContainText('没有识别到清晰的人物画面');
  }
  await page.screenshot({ path: path.resolve('../data/run/frontend-copy-qa-20261107/qa-answer.png'), fullPage: true });
});
