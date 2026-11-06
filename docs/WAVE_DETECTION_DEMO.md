# 第三阶段：挥手动作识别动态演示

开发日期：2026-11-06。直接修改 `D:\anju-vision-website`；没有提交或推送代码。第二张卡片使用用户新提供的 `C:\Users\zll\Desktop\挥手视频.mp4`，原样复制为 `frontend/public/demo/videos/help.mp4`，保留原有资源路径与卡片 ID。URL 增加文件哈希版本参数，封面从新视频第一帧重新生成。

## 模型检查和实际运行

项目原有 `yolo11n-pose.pt` 人体姿态模型，以及 `indoor-dfire-yolov8n.pt`、`luminous-fire-yolov8n.pt`、`fire-smoke-yolov8n.pt` 火焰相关权重。没有找到独立手部关键点模型或经过训练的挥手动作分类模型。

现有 `HelpGestureDetector` 主要判断持续双手举起；`ReviewHelpDetector` 已有肩部相对运动的复核候选逻辑，但要求双手同时举起且输出候选信号，不直接提供本次所需的逐手动作区间。此次复用 `LocalModels.infer_pose()`、`Config` 和已有 `Tracker`，在独立离线脚本中提取更保守的往复运动区间，不改动实时后端的动作规则。

实际使用 `elder-safety/.venv-gpu/Scripts/python.exe`：Python 3.13.2、Torch 2.13.0+cu126、Ultralytics 8.4.118、OpenCV 5.0.0，GPU 为 NVIDIA GeForce RTX 4060 Laptop GPU。没有安装依赖、下载权重或调用付费 API。权重 SHA256：`869e83fcdffdc7371fa4e34cd8e51c838cc729571d1635e5141e3075e9319dc0`。

完整解码并推理全部 **116 帧**，使用 ffprobe 解码帧实际 PTS，而不是简单用帧号除以 FPS。每帧均为一人，稳定 ID 为 `person-1`。完整身体、上举手臂及双手腕可见；源文件没有编码黑边或裁切，网页采用 contain 时产生的两侧留白由公共坐标映射处理。已检查模型接触表，未用人工坐标替代模型结果。

| 项目 | 结果 |
| --- | --- |
| 源视频 SHA256 | `f7f45b919cb03fb59fec122f92040cdc9a13e94d05f4c152bb60f092eb3c15a3` |
| 网页文件 | 与附件逐字节相同，未裁剪、缩放或烧录标注 |
| 分辨率 | 720 × 1280，竖屏，无旋转 |
| 时长 | 容器 3.882854 秒；视频帧覆盖至 3.866333 秒 |
| FPS / 编码 | 标称 30 fps，平均 348000/11599；H.264 视频、AAC 音频 |
| 人体模型 | YOLO11n Pose，COCO 17 点，输入 imgsz 640；本次关闭无关的跌倒旋转补充推理 |
| 跟踪 | 复用现有 IoU/中心点一对一关联 Tracker；按人物 ID 分离手腕历史 |
| 动作方法 | **pose-based heuristic**，`shoulder-relative-reciprocal-v1` |

## 举手、挥手和求助的区分

每侧独立检查双肩、该侧肘腕的真实置信度（至少 0.65）；肩宽至少 8 像素，手腕高于肩部至少 0.1 个肩宽，手肘不能明显低垂。使用“手腕横坐标减同侧肩部横坐标，再除以肩宽”，抵消整体平移和尺寸变化。使用因果三点中值降低抖动；连续的三个交替方向摆动，每段至少 0.35 个肩宽，总计至少 650 ms，单段不超过 900 ms，才支持挥手。

低置信度、手臂放下、人物缺失或超过 100 ms 的数据间隔立即切断连续性；不同人物和左右手不共享历史。静止举手、单次抬臂、细小抖动、单纯平移和低位行走摆臂不满足这组规则。规则参数针对当前短片验证，不声称通用识别准确率。

| 事件 | 实际离线规则结果 |
| --- | --- |
| 动作数量 | 1 条，`wave-1`，人物 `person-1` |
| 合并动作区间 | **0.733333–3.866333 秒**，网页显示 00:00.73–00:03.87 |
| 右腕支持区间 | 0.733333–3.733333 秒 |
| 左腕支持区间 | 0.800000–3.866333 秒 |
| 足够证据的确认时间 | 1.900000 秒 |

区间是完整离线分析后的回溯边界；开始时间早于积累足够往复运动的确认时间。网页播放保存的离线结果，不能用其反推实时判定延迟。视频在动作仍进行时结束，事件末尾是可用帧的边界，**没有观察到人物停止挥手**。这些时间由规则从真实关键点计算，没有人工指定事件窗口。

模型仅提供人体框和关键点分数。没有编造动作置信度、视觉大模型复核或人工确认。挥手可能是打招呼、练习动作或求助，无法根据这段视频确定求助意图。

## 独立数据与复用方式

`assets/analysis/wave/wave_analysis.json` 保存统一 `DetectionSequence`：视频、来源、规则版本、归一化逐帧框、COCO 17 点、动作与左右腕轨迹。`wave_summary.json` 保存模型、视频元数据、规则参数、事件区间及限制。双肩、双肘和双腕的 COCO ID 分别为 5/6、7/8、9/10，名称映射在 summary 内。原始源像素推理记录保留在检查目录的 `pose-recording.json`。

两份经检查的演示导出复制至 `frontend/public/assets/analysis/wave/`，构建后位于 `frontend/dist/assets/analysis/wave/`，供网站和后续工作流程复用。原始推理日志、环境快照和检查图不公开。

第二张卡片复用 `CapabilityVideoCard`、`useDemoVideo`、`DetectionOverlay`、`PersonBoundingBox`、`PoseSkeleton`、`sampleDetection`、`videoContentRect` 和 `useVideoFrame`。没有新增第二套播放器或独立动画时钟。帧时间同步仍用现有媒体时钟：rVFC 优先，rAF 回退，暂停/seek 时读取视频时间。

扩展的波形目标只显示可靠的头、肩、肘、腕；SVG 增加 `WristHighlight`、`WristMotionTrail`、`WaveActionLabel`。轨迹仅包含同一个人、对应手侧、最近 600 ms、目标时间之前的真实坐标；缺失时切断。暂停静止、反向拖动直接采样目标帧、循环清除上一轮轨迹。动作标签按媒体时间淡入淡出，竖屏情况下保持可读大小，CSS 不运行独立动画。

来源以 `kind: demo`、`basis: offline_pose_with_temporal_heuristic` 与实际在线 `inference` 区分，保留源视频、模型、原始姿态导出的哈希。首页问答的求助示例同步说明“挥手规则结果无法确认求助意图”，未新增证据展开面板。工作流程的现有跌倒示例和后端业务保持原有实现。

## 修改文件

| 范围 | 文件 |
| --- | --- |
| 离线分析与规则 | `scripts/analyze_wave_demo.py`、`scripts/wave_motion.py`、`scripts/test_wave_motion.py` |
| 独立导出 | `assets/analysis/wave/wave_analysis.json`、`assets/analysis/wave/wave_summary.json`，及 public/dist 的同名副本 |
| 公共覆盖层扩展 | `frontend/src/detection/types.ts`、`frontend/src/detection/DetectionOverlay.tsx` |
| 挥手接入与样式 | `frontend/src/detection/waveDemoData.ts`、`frontend/src/detection/wave-detection-overlay.css` |
| 卡片和来源说明 | `frontend/src/VideoDemos.tsx`、`frontend/src/demoRegistry.ts`、`frontend/src/LandingPage.tsx` |
| 新视频与封面 | `frontend/public/demo/videos/help.mp4`、`frontend/public/demo/posters/help.jpg`、`frontend/public/demo/demo-public-fixture.json`，及 dist 副本 |
| 单元/浏览器测试 | `frontend/tests/wave-detection.test.mjs`、`frontend/tests/wave-overlay.spec.ts`、`frontend/playwright.wave-overlay.config.ts` |
| 已有断言适配 | `frontend/tests/demo-registry.test.mjs`、`frontend/tests/real-video-cases.ts`、`frontend/tests/fire-overlay.spec.ts`，仅更新本次主动变更的挥手预期 |
| 可运行产物/交付说明 | `frontend/dist/index.html`、dist 的新 JS/CSS 产物、本文件 |

## 复现和实际检查记录

```powershell
Set-Location D:\anju-vision-website
& .\elder-safety\.venv-gpu\Scripts\python.exe .\scripts\analyze_wave_demo.py --video 'C:\Users\zll\Desktop\挥手视频.mp4'
& .\elder-safety\.venv-gpu\Scripts\python.exe .\scripts\test_wave_motion.py
Set-Location .\frontend
npm.cmd test
npm.cmd run build
npx.cmd playwright test --config playwright.wave-overlay.config.ts
```

脚本默认复用同源哈希的已保存姿态输出，避免无意义的重复推理；需重新推理时指定新的 `--run-dir`。这会重新发布当前演示资源，应保留原始输入。

检查记录统一存放于 **`data/run/wave-overlay-check-20261106/`**。实际结果、日志、截图、保护配置哈希核对和文件清单见该目录的 `CHECK.md`、`summary.json`，以其最终内容为准。
