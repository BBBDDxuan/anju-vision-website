# 跌倒识别动态演示交付说明

验收日期：2026-11-06。工作目录：`D:\anju-vision-website`。代码基线：`4b7ae59a5f9f626576dddd632ba0c33c75ed4573`。本次修改保留在本地，未提交或推送。

## 1. 本阶段范围与现有功能检查

官网入口在 `frontend/src/LandingPage.tsx`，核心能力及工作流程演示在 `frontend/src/VideoDemos.tsx`。原始跌倒视频为 `frontend/public/demo/videos/fall.mp4`，播放器沿用 `useDemoVideo.ts`。该 hook 的播放命令取消、视频互斥、离屏暂停和页面隐藏暂停逻辑保持不变。

开发前检查了现有 AI 视频分析工作台，已经具备以下功能：

| 已有功能 | 现有实现 |
| --- | --- |
| 视频导入、上传进度、模型分析、暂停和继续 | `frontend/src/main.tsx`、`VideoAnalysis.tsx`；`backend/main.py` 的上传、analyze、pause、resume 接口；`backend/jobs.py` |
| 风险事件列表、点击事件定位、原始视频回放 | `main.tsx`、`seekVideo.ts`、`EvidenceTimebar.tsx`；events、replay 接口 |
| 关键帧证据、补充取帧、AI 复核 | `main.tsx`、`ReviewPanel.tsx`；frames、review、sample、verify 接口 |
| 人工确认、否定、纠正、未知标记及撤销 | `ReviewPanel.tsx`、`backend/review.py` |
| 复核历史及原始结果保留 | `ReviewPanel.tsx` 的复核历史；`backend/db.py` 的 `review_log` |
| AI 问答、引用证据并定位回放 | `main.tsx`；query 接口及已有录像问答实现 |
| 工作台事件、问答、追踪与复核标签及路由 | `VideoAnalysisWorkspace.tsx`、`navigation.ts` |

因此本次没有重建以上业务模块。核心能力移除了“查看 AI 分析依据”按钮及展开面板，仅保留识别效果与简短案例结果。完整证据、复核历史和问答继续由已有工作流程/工作台负责。

本次只给跌倒卡片接入覆盖层。举手、明火继续使用原有视频、文案、播放逻辑与待接入状态。导航、路由和其他业务页面沿用原实现。官网四阶段工作流程演示与跌倒卡片共用的证据数据更新为最新导出；没有向工作流程接入新的检测覆盖层。

## 2. 最新分析数据与原片核实

以用户最新提供的 `摔倒(1).rar` 为权威分析结果。旧附件中的证据区间不再作为本次公开演示结果。

| 项目 | 核实结果 |
| --- | --- |
| 原片 | 720 × 480，3:2，20 fps，171 帧，8.55 秒，H.264 / yuv420p |
| 原片 SHA256 | `24bc42907926bf3981bdedc2a4f54bc32454fa06a1228b7cf02ac181ba584938` |
| 最新归档 SHA256 | `3e242f71cd0a2737776c7d9dc6e723fe9428dc40ace4ac3e38a2f644e911cc84` |
| 最新 checkpoint SHA256 | `a0e015595fad617d54b5d55c5e396988c4317aa6851515a0d7b8b3bd0bfaaeeb` |
| 导出结果目录 | `data/run/fall-overlay-check-20261106/latest-source/j_57421a00ffa145f09b3ff4404769a6a8` |
| 视觉复核 | 已保存的 `qwen3.8-max` 复核，2026-11-06 10:53:54 UTC，模型支持疑似跌倒 |
| 最新证据区间 | **00:04.50–00:06.00** |
| 实际引用关键帧 | 4.50、5.00、5.50、6.00 秒，共 4 张 |
| 本地候选触发时间 | 6.30 秒；它与模型引用证据区间含义不同 |
| 人工事件确认 | 尚未确认；坐标视觉检查不代表人工事件确认 |

4 张公开关键帧直接从最新归档复制，已核对源文件、`public` 与 `dist` 哈希一致。导出结果没有经校准的事件概率，因此页面未把检测置信度转成“跌倒概率”。

## 3. 人体框、骨架和风险状态的数据来源

这是**真实视频上的离线演示**，包含明确标识的核对标注；网页播放时不发起实时模型推理。

1. **事件与证据**：使用最新归档中的实际分析结果、复核原文、区间和 4 张引用帧。
2. **人体框**：检查了最新导出的 12 张帧及其人物框，其中 4.50 秒框过大、5.00 秒缺框、5.50 秒框不完整。为避免错误展示，动态覆盖层采用原片像素坐标标注，关键时刻之间插值，随后逐帧核对；不将其称为连续模型框。脚本 `BOX_ANCHORS` 保存了原始标注坐标。此前检查覆盖 2.30–6.50 秒的 85 帧，本次使用其中 2.30–6.20 秒的 79 帧。
3. **骨架**：最新归档没有连续关键点轨迹。复用本机现有 `yolo11n-pose.pt`、生产姿态预处理和旋转恢复实现，对同一原片补充导出的离线模型结果。原始轨迹含 171 帧，模型权重 SHA256 为 `869e83fcdffdc7371fa4e34cd8e51c838cc729571d1635e5141e3075e9319dc0`。人物置信度至少 0.55、关键点至少 0.65；遗漏和低置信度点不补画，也不跨缺失区间拼接骨架。阈值并不保证每个解剖点绝对准确。
4. **风险状态**：4.50 秒仍属事前姿态，证据起点不等于跌倒开始。根据原片和最新复核，标签从约 5.00 秒出现；6.00 秒后随媒体时间用 200 ms 淡出。坐标核对记录的 reviewer 为 `Codex visual inspection`，事件仍保持 `humanConfirmed: false`。

页面展示“离线演示 · 含核对标注”和“人体框经逐帧核对；骨架为补充离线模型结果，仅显示可靠点。非实时推理。”，避免混淆来源。

公开标注数据在 `frontend/src/detection/fallDemoData.ts`；公开事件摘要在 `frontend/public/demo/demo-public-fixture.json`；原始轨迹、完整归档、坐标核对记录、拼图和检查结果只放在被 Git 忽略的 `data/run/fall-overlay-check-20261106`。

## 4. 可复用组件和统一结构

| 文件 / 导出 | 职责 |
| --- | --- |
| `detection/types.ts` / `DetectionSequence` | 归一化框、关键点、毫秒时间戳、状态、透明度窗口和来源信息；`source.kind` 明确区分 `demo` 与 `inference` |
| `detection/DetectionOverlay.tsx` / `DetectionOverlay` | 将统一数据映射到原始视频像素坐标，复用 SVG 节点，处理尺寸变化 |
| `PersonBoundingBox` | 细线人物框及四角标记 |
| `PoseSkeleton` | 可靠关键点及两端均有效的连接线 |
| `RiskLabel` / `DetectionHint` | 暖橙色疑似跌倒标签和下方轻提示 |
| `detection/sampleDetection.ts` | 二分定位相邻帧、同人物插值、拒绝过长缺口；处理 object-fit 和百分比/关键词 object-position |
| `detection/useVideoFrame.ts` | 统一媒体时钟、定位更新和回调清理 |
| `VideoDemos.tsx` / `FallDetectionCard` | 将上述组件接入现有跌倒卡片及播放入口 |

同步优先使用 `requestVideoFrameCallback` 的媒体时间；回退使用 `requestAnimationFrame` 读取 `video.currentTime`。暂停、拖动、回放和循环都由媒体时间决定，没有独立 CSS 动画计时。SVG 设置 `pointer-events: none`。显示位置考虑实际视频尺寸、contain 黑边、cover 裁切、fill 拉伸和窗口缩放。

SVG 元素在挂载时创建，播放时只更新属性；暂停、隐藏、离屏和卸载时停止或清理高频回调。覆盖层没有持续 CSS 动画，减少动态效果设置下仍可定位。

后续接入工作流程时，应把真实每次分析结果适配为同一 `DetectionSequence`，使用 `source.kind: 'inference'`、真实 `runId`、模型与原片标识；框和关键点来源使用 `inference`。不得用演示标注填充真实运行缺失结果。组件接入及其他两种识别按本阶段验收后的要求进行。

## 5. 修改文件清单

所有路径均相对于 `D:\anju-vision-website`。

| 类别 | 文件 |
| --- | --- |
| 新增可复用实现及标注 | `frontend/src/detection/types.ts`、`sampleDetection.ts`、`useVideoFrame.ts`、`DetectionOverlay.tsx`、`detection-overlay.css`、`fallDemoData.ts` |
| 卡片与来源说明 | `frontend/src/VideoDemos.tsx` |
| 删除重复面板 | 删除 `frontend/src/EvidenceInspectorPanel.tsx`；清理 `evidence-inspector.css`、`swiss-site.css`、`video-demos.css` 中面板专用样式 |
| 保留共享时间轴样式 | `frontend/src/EvidenceTimebar.tsx` 显式引入共享 CSS；保留小屏和减少动态效果规则 |
| 最新公开结果 | `frontend/public/demo/demo-public-fixture.json` |
| 新增 4 张引用帧 | `frontend/public/demo/keyframes/sf_a755ddf03dbc7c5717922743dca2a315.jpg`、`sf_41d4618f64a1d72fb7d36593f826a794.jpg`、`sf_1c0a72e17ca9bc6113e11195c545b7d1.jpg`、`sf_2f52aa041e840e1384d884fd8384d842.jpg` |
| 数据准备脚本 | `scripts/prepare_fall_overlay.py` |
| 新增验收 | `frontend/tests/detection-overlay.test.mjs`、`fall-overlay.spec.ts`、`frontend/playwright.fall-overlay.config.ts` |
| 更新现有测试 | `frontend/tests/VideoAnalysis.test.mjs`、`demo-registry.test.mjs`、`real-video-cases.ts`、`workspace-fixture.mjs`、`workspace-v2-cases.ts` |
| 构建产物 | `frontend/dist/index.html`、新 JS/CSS 哈希文件、最新 demo fixture 及 4 张关键帧；替换旧 JS/CSS 哈希文件 |
| 交接说明 | 本文件 |

`frontend/index.html` 仅规范了换行，Git 无文本差异。未增加 npm 依赖；未修改 package 清单、后端业务代码、启动脚本或环境文件。

## 6. 实际测试结果

测试在本机真实 Chrome `156.0.8078.80` 执行，使用构建后的页面及独立测试数据目录，未覆盖正常运行的数据。

| 检查 | 结果 / 范围 |
| --- | --- |
| `npm.cmd test` | **46 / 46 通过**，0 失败、0 跳过 |
| `npm.cmd run build` | **通过**，TypeScript + Vite，123 个模块 |
| Playwright | **34 / 34 通过**，36.7 秒，0 失败、0 跳过、0 flaky |
| 原视频与最新帧 | 3 段视频可加载；4 张最新引用帧逐一定位；公开素材哈希核对通过 |
| 跌倒交互 | 初始封面无框；播放同步；暂停静止；反向及重复拖动更新；循环清除残留；从头重播；鼠标移出；媒体时间淡出 |
| 映射与响应式 | 1440 / 768 / 390 px；contain 黑边、cover 裁切、fill 与非居中位置；断言映射误差小于 1 CSS 像素 |
| 回退和清理 | 强制禁用 rVFC 后回退可用；减少动态效果；离屏暂停；卸载后无页面异常；SVG 节点保持复用 |
| 现有功能回归 | 举手/明火播放互斥与完整播放；官网四阶段；快速证据定位；错误反馈；工作台事件、问答、复核、路由、键盘及移动导航 |
| 配置和环境 | 7 个保护文件哈希不变；后端 33 个、GPU 环境 43 个已装包及版本与修改前一致 |
| 外部模型调用 | 本次新增云模型请求 **0 次**；采用队友已保存复核。工作台 API 回归使用明确的模拟 fixture，不代表重新跑过真实视频分析 |
| `git diff --check` | 通过 |

检查文件统一存放在 **`data/run/fall-overlay-check-20261106/`**：`CHECK.md`、`summary.json`、单元/构建/浏览器日志、`browser-results.json`、保护文件和环境核对、来源核对、截图、轨迹及坐标检查。完整测试数据目录由 `.gitignore` 排除，交接说明随代码保留。后端原有真实分析目录 `data/runs` 不迁移。

已打开检查 1440 px 与 390 px 卡片截图及桌面模块截图，识别图层对齐、文字可读、无横向溢出。

### 已知限制

- 单人固定片段演示；部分时刻骨架不完整是模型缺失或置信度筛选结果。
- 逐帧坐标检查覆盖已展示的解码帧，不是标注员提供的动作真值或人工事件确认。
- 实测浏览器为 Chrome；rAF 回退经过强制测试，未宣称已实测 Safari、Firefox 或 Edge。
- 1366 × 768 的原版工作台已需要在播放器列内滚动才能看到全部时间线标记。用 HEAD 构建与本次构建作了实际浏览器对比，几何布局一致；本次保留该行为，测试验证列内可达，没有把它称为全部同屏展示。
- 本阶段尚未向工作流程接入新覆盖层，也未扩展举手、明火。

## 7. 运行与复现

正常查看：双击项目根目录的 `start.bat`，打开官网核心能力第一张卡片。已有启动脚本保持不变；当前 `frontend/dist` 已包含本次构建。

从项目根目录复用已有轨迹并重新发布演示数据：

```powershell
& .\elder-safety\.venv-gpu\Scripts\python.exe .\scripts\prepare_fall_overlay.py --reuse --publish --analysis-dir .\data\run\fall-overlay-check-20261106\latest-source\j_57421a00ffa145f09b3ff4404769a6a8
```

此命令需要本机已有原始轨迹和匹配的视觉检查记录。去掉 `--publish` 可生成检查拼图；去掉 `--reuse` 会运行本地姿态模型。发布会检查原片哈希、最新结果对应关系、轨迹哈希和检查覆盖区间；更换视频、模型或标注后应重新检查，不得直接沿用旧检查结论。

前端测试与构建：

```powershell
Set-Location D:\anju-vision-website\frontend
npm.cmd test
npm.cmd run build
npx.cmd playwright test --config playwright.fall-overlay.config.ts
```

Playwright 使用项目现有后端 Python，绑定 `127.0.0.1:8018`，本次配置使用本机 Chrome 路径；可用 `ANJU_BROWSER` 指定其他已安装浏览器程序，但不因此视为已完成兼容验收。`ANJU_CHECK_DIR` 可指定独立输出目录。
