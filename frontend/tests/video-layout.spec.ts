import { test, expect, type Page } from '@playwright/test';
import path from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';
import { installWorkspaceFixture } from './workspace-fixture.mjs';
import './product-round2.spec';

const run = process.env.ANJU_CHECK_DIR || path.resolve('../data/run/video-workspace-layout-check-20261107');
const anchors = ['.recording-player', '.recording-controls', '.evidence-timebar', '.analysis-primary-row', '.analysis-inspector'];
async function geometry(page: Page) {
  return Promise.all(anchors.map(async selector => ({ selector, box: (await page.locator(selector).boundingBox())! })));
}
async function stable(page: Page, before: Awaited<ReturnType<typeof geometry>>) {
  const after = await geometry(page);
  for (let i = 0; i < before.length; i++) for (const key of ['x','y','width'] as const) {
    expect(Math.abs(after[i].box[key] - before[i].box[key]), `${before[i].selector} ${key}`).toBeLessThan(1);
  }
  expect(after[0].box.height).toBe(before[0].box.height);
  expect(await page.evaluate(() => scrollY)).toBe(0);
}

for (const [width, height] of [[1366,768],[1440,900],[1920,1080]]) {
  test(`${width}×${height} 四状态布局、65/35比例、首屏操作及播放器保持稳定`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    const fixture = await installWorkspaceFixture(page, { empty:true, analyzed:false, autoComplete:false });
    await page.goto('/workspace?view=videos');
    await expect(page.locator('.video-page-heading')).toHaveCount(0);
    await expect(page.locator('.topbar')).toContainText('我的工作台');
    await expect(page.locator('.workspace-capability-card h2')).toHaveText(['01跌倒识别','02挥手动作识别','03明火识别']);
    await expect(page.locator('.workspace-workflow-steps li')).toHaveCount(3);
    await page.screenshot({ path:path.join(run,`layout-empty-${width}.png`), fullPage:true });
    await page.locator('#recording-upload').setInputFiles(path.resolve('public/demo/videos/fall.mp4'));
    const video = page.locator('#watch video');
    await expect.poll(() => video.evaluate((v:HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(1);
    await expect(page.locator('#timeline')).toContainText('完成视频分析后，识别到的安全事件将在这里展示。');
    const handle = await video.elementHandle();
    const start = page.getByRole('button',{name:'开始安全分析',exact:true});
    const box = (await start.boundingBox())!;
    expect(box.y + box.height).toBeLessThan(height);
    const before = await geometry(page);
    const left = (await page.locator('.analysis-player-column').boundingBox())!.width, right = before.at(-1)!.box.width;
    expect(left/(left+right)).toBeGreaterThan(.64);
    expect(left/(left+right)).toBeLessThan(.66);
    expect(before[0].box.height).toBeGreaterThanOrEqual(360);
    expect(before[0].box.height).toBeLessThanOrEqual(480);
    await page.screenshot({ path:path.join(run,`layout-ready-${width}.png`) });
    await start.click();
    await expect(page.getByRole('progressbar',{name:'视频分析进度'})).toHaveAttribute('value','30');
    await expect(page.locator('.inspector-analysis-state')).toBeVisible();
    await stable(page,before);
    expect(await video.evaluate((v, previous) => v === previous, handle)).toBe(true);
    expect(fixture.calls.filter(call => /\/analyze\?/.test(call.path))).toHaveLength(1);
    await page.screenshot({ path:path.join(run,`layout-running-${width}.png`) });
    fixture.state.status = 'completed';
    await expect(page.locator('.analysis-result-summary')).toContainText('分析完成');
    await expect(page.locator('#timeline .event')).toHaveCount(2);
    await stable(page,before);
    expect(await video.evaluate((v, previous) => v === previous, handle)).toBe(true);
    await page.locator('.timebar-markers button').last().click();
    await expect.poll(() => video.evaluate((v:HTMLVideoElement) => v.currentTime)).toBeCloseTo(4.5,1);
    await page.evaluate(() => scrollTo(0,0));
    await page.screenshot({ path:path.join(run,`layout-completed-${width}.png`) });
    await writeFile(path.join(run,`layout-metrics-${width}.json`),JSON.stringify({viewport:{width,height},anchors:before,start:box,leftRatio:left/(left+right)},null,2));
  });
}

for (const [name,file,portrait] of [['landscape','fall.mp4',false],['portrait','help.mp4',true]] as const) {
  test(`${name} 原片完整显示，使用 contain 且横竖视频容器尺寸相同`, async ({ page }) => {
    await page.setViewportSize({width:1440,height:900});
    await installWorkspaceFixture(page,{analyzed:false,mediaFile:file,durationMs:portrait?3883:8550});
    await page.goto('/workspace?view=videos&video=v_inspector_fixture');
    const video=page.locator('#watch video');
    await expect.poll(() => video.evaluate((v:HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2);
    const media=await video.evaluate((v:HTMLVideoElement) => ({width:v.videoWidth,height:v.videoHeight,fit:getComputedStyle(v).objectFit,controls:v.controls}));
    expect(media.width < media.height).toBe(portrait);
    expect(media.fit).toBe('contain'); expect(media.controls).toBe(true);
    const bounds=(await page.locator('.recording-player').boundingBox())!;
    expect(bounds.width/bounds.height).toBeCloseTo(16/9,1);
    await video.evaluate((v:HTMLVideoElement) => { v.currentTime=1; });
    await expect.poll(() => video.evaluate((v:HTMLVideoElement) => v.currentTime)).toBeCloseTo(1,1);
    await video.evaluate((v:HTMLVideoElement) => {v.muted=true;return v.play();});
    await expect.poll(() => video.evaluate((v:HTMLVideoElement) => v.currentTime)).toBeGreaterThan(1.12);
    await video.evaluate((v:HTMLVideoElement) => v.pause());
    await expect.poll(() => video.evaluate((v:HTMLVideoElement) => !v.seeking && v.readyState>=2)).toBe(true);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.screenshot({path:path.join(run,`layout-media-${name}.png`)});
  });
}

test('大量事件不会推移时间轴和主操作，完整事件仍在右侧可查',async ({page}) => {
  await page.setViewportSize({width:1366,height:768});
  const fixture=await installWorkspaceFixture(page,{analyzed:false,autoComplete:false});
  await page.goto('/workspace?view=videos&video=v_inspector_fixture');
  await expect(page.getByRole('button',{name:'开始安全分析',exact:true})).toBeEnabled();
  const before=await geometry(page);
  // Keep source frame times inside each synthetic event, as production gating requires.
  for(let i=0;i<10;i++) fixture.events.push({...fixture.events[0],id:`extra_${i}`});
  await page.getByRole('button',{name:'开始安全分析',exact:true}).click();
  fixture.state.status='completed';
  await expect(page.locator('#timeline .event')).toHaveCount(12);
  await expect(page.locator('.timebar-markers button')).toHaveCount(3);
  await expect(page.locator('.timebar-more')).toContainText('其余 9 项见事件列表');
  await stable(page,before);
});

for(const width of [1100,1024,900,768,390,320]) test(`${width}px 窄屏与手机操作顺序和无横向溢出`,async ({page}) => {
  await page.setViewportSize({width,height:844});
  await installWorkspaceFixture(page);
  await page.goto('/workspace?view=videos&video=v_inspector_fixture');
  await expect(page.locator('#watch video')).toBeVisible();
  const left=(await page.locator('.analysis-player-column').boundingBox())!;
  const right=(await page.locator('.analysis-inspector').boundingBox())!;
  if(width<=900 || width===1100) expect(right.y).toBeGreaterThan(left.y+left.height);
  else expect(right.x).toBeGreaterThan(left.x+left.width);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('tab',{name:'AI 问答',exact:true}).click();
  await expect(page.locator('#inspector-panel-qa')).toBeVisible();
  if(width===390) {
    // Capture from the document start so sticky navigation is not stitched mid-page.
    await page.evaluate(() => scrollTo(0,0));
    await expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
    await page.screenshot({path:path.join(run,'layout-mobile-390.png'),fullPage:true});
  }
});

test('真实隔离服务：拖放导入、原片播放暂停、手动跳时、重新导入与删除',async ({page}) => {
  await page.goto('/workspace?view=videos');
  const bytes=await readFile(path.resolve('public/demo/videos/help.mp4'));
  const transfer=await page.evaluateHandle(data => {
    const dt=new DataTransfer();dt.items.add(new File([new Uint8Array(data)],'布局验收竖屏录像.mp4',{type:'video/mp4'}));return dt;
  },Array.from(bytes));
  await page.locator('.upload-dropzone').dispatchEvent('drop',{dataTransfer:transfer});
  const video=page.locator('#watch video');
  await expect.poll(() => video.evaluate((v:HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(1);
  await video.evaluate((v:HTMLVideoElement) => {v.muted=true;return v.play();});
  await expect.poll(() => video.evaluate((v:HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0);
  await video.evaluate((v:HTMLVideoElement) => v.pause());
  await page.getByLabel('跳转录像时间').fill('00:00:02');
  await page.getByRole('button',{name:'跳转',exact:true}).click();
  await expect.poll(() => video.evaluate((v:HTMLVideoElement) => v.currentTime)).toBeCloseTo(2,1);
  await page.getByRole('button',{name:'删除视频',exact:true}).click();
  await page.getByRole('button',{name:'取消',exact:true}).click();
  await expect(video).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('当前视频',{exact:true})).toContainText('布局验收竖屏录像.mp4');
  await page.getByRole('button',{name:'删除视频',exact:true}).click();
  await page.getByRole('button',{name:'确认删除',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByLabel('当前视频',{exact:true})).not.toContainText('布局验收竖屏录像.mp4');
  const chooser=page.waitForEvent('filechooser');
  await page.getByRole('button',{name:'导入新录像',exact:true}).click();
  await (await chooser).setFiles(path.resolve('public/demo/videos/fall.mp4'));
  await expect(page.getByLabel('当前视频',{exact:true})).toContainText('fall.mp4');
  await expect.poll(() => video.evaluate((v:HTMLVideoElement) => v.videoWidth > v.videoHeight)).toBe(true);
});
