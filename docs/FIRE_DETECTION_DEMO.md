# 第二阶段：明火异常识别动态演示

开发及验收日期：2026-11-06。修改目录：`D:\anju-vision-website`。本阶段在已完成的跌倒演示基础上开发第三张明火卡片。

按用户追加要求，当前官网视频已移除附件的前 9 秒，并在此基础上裁掉末尾 2.6 秒，时长约 **6.62 秒**（视频帧边界带来约 24 毫秒误差）；火焰证据区间保持为 **00:03.03–00:05.76**，80 个已核对框完整保留。附件原文件保持不变。末尾剪辑记录位于 `data/run/fire-trim-end-check-20261106/`，整合版本测试记录位于 `data/run/github-sync-20261106/`。

## 1. 输入与压缩包实际内容

用户提供：

- `C:\Users\zll\Desktop\着火 - Trim.mp4`
- `C:\Users\zll\Desktop\fire_check.rar`

归档已检查路径并完整解压到 `data/run/fire-overlay-check-20261106/source`，未要求用户重复移动附件。归档内共 **31 个文件**：

| 文件 | 内容 |
| --- | --- |
| `checkpoint.json` | 1 条明火事件、1 组证据、12 张引用帧、1 个本地候选及其已保存视觉复核 |
| `safety-reviews.json` | 复核、统计及历史调用核算 |
| `response-ad908227fedd441e8d5fa1707b17e06b.json` | 已保存视觉模型响应及请求元数据 |
| `safety/scan.json` | 原视频扫描统计、来源指纹、采样策略和候选 |
| `safety/resume.json` | 扫描配置、权重与代码指纹 |
| `safety/pipeline.json` | 流水线指纹 |
| `safety/sc_7133ef5d63d616f62ffcb688f6bdcd7c/manifest.json` | 候选详情、候选区域提示框及帧引用 |
| `frames/*.jpg` | 12 张原始关键帧 |
| `safety/sc_7133ef5d63d616f62ffcb688f6bdcd7c/*.jpg` | 同一组 12 张证据帧的候选目录副本 |

归档**不含原始视频、分析后视频、逐帧火焰检测记录、TXT/CSV、分割 Mask 或多边形轮廓**。JSON 是本项目的分析 checkpoint/候选结构，不是 YOLO 标签文本或 COCO 标注。帧上的 `person_bboxes` 是人物区域，未将其当作火焰框。

`manifest.json` 的 `subject_bbox_hint` 是候选触发时刻的一次区域提示，不能铺满整个视频冒充动态结果。`details.frames: 16` 是候选累计检测统计，不是随包提供的 16 个逐帧坐标。

## 2. 视频核实及剪辑时间映射

| 项目 | 当前官网使用的新视频 |
| --- | --- |
| 用户原始文件名 | `着火 - Trim.mp4` |
| 官网资产 | `frontend/public/demo/videos/fire.mp4`，从附件移除前 9 秒并重新编码，不裁切或缩放画面，不烧录标注；URL 带文件哈希版本参数 |
| 时长 | **6.620690 秒**，UI 显示为 00:06.62；附件原文件为 18.215172 秒 |
| 分辨率 / 比例 | **854 × 480**，约 16:9 |
| 帧率 | 标称 **29 fps**；保留 192 个解码帧，逐帧核对实际 PTS，不把帧号简单除以 29 |
| 编码 / 像素 | H.264 / yuv420p |
| 方向 | 横向，无旋转元数据 |
| 当前视频 SHA256 | `3105463a9a3785c1ca38a6d42c7c4e7bed6bb5df59a9d1cf14efc007823d9444` |
| 附件 SHA256 | `21ea40da201d5c214a7b885e143d8e0cd6ddd41c74b4217455077734ca58cc82`，原文件未改动 |

最新归档 SHA256：`68afaaafba8bd87af73c384bcc3ace4edd658e10472e93f7367dafef31e77d3e`。

checkpoint SHA256：`4e8f7a1abe220770f18a09fd4f28481f0aa184a5f0ddea2aa3d5f351e0b8ac9b`。

归档对应的分析源视频 SHA256 为 `0c12ad8ababe1aef3291ed18f4a73c69b90728ac867e269dd5d145f81fc79011`，与新剪辑不同。`scan.json` 报告原分析视频约 29.9967 秒、29.97 fps、899 帧，时间基准为 `decoded_frame_index/source_fps; CFR assumption, not decoded source PTS`；实际推理抽样 450 帧。

初版将 12 张 854×480 导出图片与附件的全部 529 个解码帧匹配并视觉核对，缩略图均方差约 5.72–8.61，仅作为图像匹配指标。此次再移除附件前 9 秒，首个保留帧是附件第 262 帧（0 起始），实际 PTS 为 9.008345 秒；该帧在新片中从 0 开始。267 个保留帧的时间对应关系全部逐帧验证，80 个演示框只调整时间和帧号，空间坐标保持不变。没有套用未经核对的固定模型偏移。

| 原模型时间（秒） | 新片实际 PTS（秒） | 新片帧序号（0 起始） |
| --- | --- | --- |
| 16.283 | 3.034483 | 88 |
| 16.550 | 3.310345 | 96 |
| 16.750 | 3.482759 | 101 |
| 17.017 | 3.758621 | 109 |
| 17.284 | 4.034483 | 117 |
| 17.551 | 4.310345 | 125 |
| 17.751 | 4.482759 | 130 |
| 18.018 | 4.758621 | 138 |
| 18.285 | 5.034483 | 146 |
| 18.552 | 5.310345 | 154 |
| 18.752 | 5.482759 | 159 |
| 19.019 | 5.758621 | 167 |

网页证据区间为 **00:03.03–00:05.76**；原模型证据仍为 **16.283–19.019 秒**。最后一个已核对帧从 5.758621 秒保持至下一解码帧 5.793103 秒，在该帧显示期间淡出，之后清空。新片已将媒体时间重置为 0，而非仅跳过前 9 秒播放。

## 3. 真实模型结果与演示坐标的界限

实际使用的模型输出为：

- 类别 `fire`，事件类型 `fire_detected`，业务事件 action 为 `flame`。
- 本地模型标识 `indoor-dfire-yolov8n.pt`，导出权重 SHA256 为 `517ded23621666c842516212f4cace9680149efb1f17bc4a8db80938b0d94180`。
- 本地候选 `fire-9`；保存的 `qwen3.8-max` 复核结论为 `confirmed`，支持左侧木制模型屋顶的可见明火线索。
- checkpoint 中 1 条事件、1 组证据、12 张实际引用帧。页面的“1 条明火线索”来自这一条保存事件，不是火焰数量或累计框数。
- 事件 `model_score` 为 null。候选检测分数为 0.780257，但导出明确声明不是经校准的事件概率；UI 不显示百分比。
- 原始状态为 `model_unreviewed`，没有人工确认记录；页面使用“疑似明火”“模型复核支持”“尚未确认”。

由于没有逐帧模型框，覆盖层采用**独立的预置演示坐标**：附件第 350–429 帧共 **80 帧**已连同矩形逐帧视觉检查，此次对应新片第 **88–167 帧**。检查人标识为 `Codex visual inspection`，不冒称有人工作者确认事件。

这些框会随实际火焰高度、位置和宽度变化，逐解码帧读取，不跨未知窗口插值或延长。仅描出屋顶主要火焰区域，不单独追踪离散火星和手持点火源。未生成或展示 Mask、多边形、热成像、温度或虚假置信度。橙色轻填充只在矩形内部，并非精确火焰分割。

来源说明在卡片中常驻可见：“结论来自已保存的模型复核；火焰框为逐帧核对标注，无分割数据。非实时推理。”

仅证据窗口有覆盖层。片段其他时刻也可能存在可见火焰，当前不显示框代表没有整理对应检测数据，**不代表模型已确认没有火焰**。没有为扩大展示时段新增模型推理或付费调用。

## 4. 复用架构与页面范围

明火卡片直接复用既有 `DetectionOverlay`，没有第二套播放同步或坐标映射逻辑：

- `useDemoVideo.ts`：原播放器命令、暂停、互斥、离屏与隐藏暂停机制不变。
- `useVideoFrame.ts`：沿用 rVFC 媒体时间；不支持时沿用 rAF + `currentTime`；卸载清理逻辑不变。
- `sampleDetection.ts`：向后兼容增加 `sampleMode: 'frame'` 和逐帧 `validUntilMs`，防止未知区间保留上一框；跌倒仍使用原插值方式。
- `DetectionOverlay.tsx`：沿用 SVG 节点复用、ResizeObserver、源像素映射、contain/cover/fill 与 object-position 处理。火焰只分支渲染框、火焰图标和提示，不创建骨架。
- `types.ts`：在原结构上增加可选目标类型、通用 objects、className、frameIndex、有效期、可选 polygon/confidence；原跌倒数据无需改写。没有提供分割数据时不创建多边形节点。
- `FireDetectionOverlay` 是共享 renderer 的薄包装；`FireBoundingBox`、`FireRiskLabel` 负责火焰视觉。`DetectionHint` 复用现有组件。

页面保留整体米白和森林绿风格，火焰框为琥珀细线与轻填充，标签放在火焰旁边，暂停播放控件缩在角落避免盖住火焰；覆盖层 `pointer-events: none`。新增的进度条只接入第三张卡片，保留“播放场景”和重播。没有恢复 AI 证据展开面板，没有增加视频播放器，也没有接入工作流程覆盖层。

现有官网问答本来就读取 `demoRegistry`。更新共享火焰证据后，它可引用这 12 张保存证据；未新增或重写问答模块，`LandingPage.tsx` 没有变化。四阶段工作流程仍播放原有跌倒案例。

## 5. 本阶段文件清单

相对于 `D:\anju-vision-website`：

| 操作 | 文件 / 内容 |
| --- | --- |
| 修改 | `frontend/src/VideoDemos.tsx`：第三张卡片、火焰覆盖层、进度与循环、真实简要结果及来源说明 |
| 兼容扩展 | `frontend/src/detection/types.ts`、`sampleDetection.ts`、`DetectionOverlay.tsx` |
| 新增 | `frontend/src/detection/fireDemoData.ts`：80 帧预置坐标、来源和时间窗口 |
| 新增 | `frontend/src/detection/fire-detection-overlay.css`：仅火焰覆盖层和第三张卡片样式 |
| 更新 | `frontend/public/demo/videos/fire.mp4`、`posters/fire.jpg`、`demo-public-fixture.json` 中 fire 条目 |
| 新增 | `frontend/public/demo/keyframes/` 下归档中实际引用的 12 张 `sf_*.jpg`；完整 ID 见 fixture |
| 新增 | `scripts/prepare_fire_overlay.py`：核对检查记录并发布数据、原视频、封面和引用帧 |
| 新增 | `frontend/tests/fire-detection.test.mjs`、`fire-overlay.spec.ts`、`frontend/playwright.fire-overlay.config.ts` |
| 更新 | `frontend/tests/demo-registry.test.mjs`、`real-video-cases.ts`，适配第三张卡片已取得真实证据的状态 |
| 更新构建 | `frontend/dist/index.html`、JS/CSS 哈希文件、fire 视频/封面/fixture/12 张关键帧 |
| 新增说明 | 本文档 |

本阶段未修改 `fallDemoData.ts`、原跌倒 CSS、`useVideoFrame.ts`、`useDemoVideo.ts`、`LandingPage.tsx`、跌倒和举手视频、package 清单或后端业务代码。第一个阶段尚未提交的修改仍保留；Git 全量修改清单会同时包含两阶段内容。

## 6. 实际验收结果

| 检查 | 实际结果 |
| --- | --- |
| 单元测试 `npm.cmd test` | **51 / 51 通过**，0 失败，0 跳过 |
| 生产构建 `npm.cmd run build` | **通过**，TypeScript + Vite，125 模块 |
| 真实 Chrome 156.0.8078.80 | **43 / 43 通过**，0 失败、0 跳过、0 flaky；各次日志单独留存 |
| 素材一致性 | 截去前 9 秒后逐帧对应附件剩余画面；public/dist 视频、封面、fixture 与 12 张源关键帧核对一致 |
| 识别交互 | 播放、暂停、反向/快速拖动、循环、从头重播、自然结束、边界淡出及未知区间清空通过 |
| 共享机制 | rAF 强制回退、鼠标移出、离屏暂停、页面隐藏暂停、减少动态效果、SVG 元素复用、卸载无异常通过 |
| 响应式映射 | 1440 / 768 / 390 px 下 contain、cover、fill 和非居中位置，误差断言小于 1 CSS px；无横向溢出 |
| 原功能 | 包含原阶段 34 项浏览器回归：跌倒、举手、导航、工作流程、证据定位、工作台问答和复核等 |
| 跌倒视觉保持 | 同时间、同视口的 1440 / 768 / 390 px 卡片截图，与第一阶段截图 **PNG 文件哈希完全相同** |
| 配置保护 | 7 个保护文件哈希不变；后端 33 包、GPU 环境 43 包及版本清单不变 |
| 新增推理调用 | 云模型 **0 次**，本地模型 **0 次**；使用已保存结论与演示坐标 |
| Git 空白检查 | `git diff --check` 通过 |

工作台 API 回归使用模拟 fixture，未声称重新运行真实上传视频分析。最初单元测试中的本机代理连接受沙箱限制，保留了原日志；允许本机测试连接后同一测试通过，没有更改或跳过断言。

已实际打开检查第三张卡片的 1440 px、390 px 截图与桌面三卡模块图，火焰框和视频对齐，标签未盖住主体，文字可读。完整核对及自动检查保存在独立目录：

**`data/run/fire-overlay-check-20261106/`**

- `CHECK.md`、`summary.json`：最终验收。
- `unit-tests.txt`、`build.txt`、`browser-tests.txt`、`browser-results.json`：真实执行日志。
- `source-integrity-check.json`、`protected-files-check.json`、`environment-packages-check.json`、`first-stage-preservation.json`：数据与环境保护核对。
- `video-frame-matches.json`、`export-trim-comparison.jpg`：12 张导出帧与剪辑匹配关系。
- `box-proposals.json`、`coordinate-review.json`、`fire-contact-01.jpg` 至 `04.jpg`：80 帧坐标和视觉检查。
- `published-provenance.json`：来源、时间区间、无逐帧模型框/无 Mask 等记录。
- `fire-card-*.png`、`fire-viewport-*.png`：三种宽度最终截图；`fall-card-*.png` 为原功能回归截图。
- `before-source.zip`、`before-files.json`：本阶段修改前源码、素材快照与保护文件哈希；不包含 .env 内容。

该目录被 Git 忽略，原始响应和本机路径未进入公开目录。正式后端分析仍使用原来的 `data/runs`。

## 7. 已知限制和复现

当前缺少连续火焰模型框与分割结果，因此页面明确为“保存模型结论 + 核对演示框”，不能称为模型逐帧分割。展示范围只覆盖经匹配核实的证据窗口；未用额外框填充片段其他时刻。实际物理浏览器验收为 Chrome，未声称已实测 Safari、Firefox 或 Edge。1366×768 工作台原有列内滚动行为保持不变。

双击项目根目录 `start.bat`，刷新官网并播放第三张卡片即可查看；当前 `frontend/dist` 已包含本阶段构建。

2026-11-06 补充核对：附件、public、dist 和正在运行的 8000 端口返回视频的 SHA256 一致。为避免同名媒体缓存，视频和封面 URL 使用各自内容哈希的前 16 位作为 `?v=` 参数；封面改为新视频首帧。复验材料单独位于 `data/run/fire-video-replacement-check-20261106/`。

该补充修改涉及 `frontend/src/demoRegistry.ts` 的严格版本参数支持、`scripts/prepare_fire_overlay.py` 的首帧封面与版本生成、fire 公开素材与 fixture、生产构建，以及现有 `demo-registry.test.mjs`、`fire-overlay.spec.ts`。已再次通过 51 项单元测试、43 项 Chrome 测试及构建；另在当前 8000 端口实际核对播放器地址、附件内容哈希和播放交互。

从项目根目录使用已有本地检查材料重新发布：

```powershell
& .\.venv\Scripts\python.exe .\scripts\prepare_fire_overlay.py --video 'C:\Users\zll\Desktop\着火 - Trim.mp4' --analysis-dir .\data\run\fire-overlay-check-20261106\source --trim-start-seconds 9 --trim-end-seconds 2.6 --run-dir .\data\run\fire-trim-end-check-20261106
```

该脚本要求检查记录与原片、checkpoint、匹配关系和坐标文件哈希一致。更换输入或修改坐标后应重新做图像与逐帧检查；不要直接复用旧检查记录。团队推送时公开 TS 数据及视频已足够运行，原始检查资料如需转交，应单独共享本地检查目录。

前端复验：

```powershell
Set-Location D:\anju-vision-website\frontend
npm.cmd test
npm.cmd run build
npx.cmd playwright test --config playwright.fire-overlay.config.ts
```

浏览器验收使用端口 8019 及独立 DATA_DIR，禁止新模型网络请求；测试结束会退出该测试服务。正常项目启动配置不变。
