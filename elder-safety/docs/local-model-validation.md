# 第一版本地模型验证（2026-10-28）

正式项目位置：`G:\比赛\AI黑客松\elder-safety`。本次下载、依赖增量、缓存、临时文件和推理结果均在 G 盘项目中；复用已在 D 盘的 Python/PyTorch。

本版已完成真实模型推理、连续帧候选报警、截图/短视频记录和隔离后端回放。尚未连接新摄像头、验证手机真推送或调用真实 Qwen API。

本轮回归实际执行：230 项 Python 整套回归测试通过，另新增的中文路径读图专项也通过，1 条已有 Starlette/httpx 弃用警告。7 个标注视频、共 1007 帧及 4 对事件截图/短视频均检查可解码；跌倒和第二次求助手势靠近文件结尾，证据短片尾部截断并有明确标记。

独立进程 HTTP 模拟链路也在本轮重跑通过：`runtime/latest-smoke.json`，AI 队列 pending=0、blocked=0；该结果仍明确属于合成 AI，不与真实素材验证混淆。

## 本次实际结果

最终运行目录：`runtime/v1-local-validation-20261028-181635/`，原始 JSON 为 `summary.json`，各输入目录有 `trace.jsonl`、`report.json`、`annotated.mp4`、事件帧 JPG 及 `spool/evidence/` 截图与短视频。

|输入|实际推理帧数|结果|说明|
|---|---:|---|---|
|URFD fall-06，前视原始 RGB|100|3.067 秒触发疑似跌倒 1 次|真实姿态推理；原始 PNG 排序转 30 fps MP4，无复制帧或拉伸时间|
|KTH person01 handwaving d1|468|0.96、16.72 秒触发求助手势候选|真人约定动作；15 秒冷却后可再次触发|
|公开火焰 GIF demo1|109|3.80 秒触发明火 1 次|原素材已有他人检测框，属于有偏功能样本，不是干净独立测试集|
|URFD adl-01 的右侧 RGB 画面|150|无报警|正常行走/下蹲；裁掉左侧深度图，保留原时间顺序|
|公开坐姿片段 sample1|100|无报警|原片带骨架/文字，作为辅助负例|
|公开坐姿片段 sample3|50|无报警|原片带骨架/文字，作为辅助负例|
|Ultralytics bus 图片|30|无报警|同一图重复供推理，只有静态负例意义|

上述素材参与了开发和参数选择。结果是“小样本功能验证”，没有独立测试准确率、召回率或误报率结论。跌倒需要先观察到直立、快速下降、持续低位水平姿态；起始就躺在地上不会被当作已观察到的跌倒。多人人体互相遮挡、背身、夜间、俯视、镜头移动、主动快速躺下及真实居家环境尚未验收。

后端验证：`backend-replay.json` 记录 4 次真实模型报警提交，服务端去重后为 3 类事件；上传 8 个截图/短视频，确认可鉴权查看、收到 mock 通知、完成 mock 复核、确认和解决。使用 FastAPI TestClient 在进程内运行，不是实际手机推送；服务端去重使同一人的两次手势合并为一个后端事件。

## 模型与能力边界

- 姿态：Ultralytics `yolo11n-pose.pt`，6,255,593 bytes，COCO 17 点。模型输出人体框/关键点，跌倒与求助由项目时序规则生成。
- 明火：`luminous0219/fire-and-smoke-detection-yolov8` 的 `weights/best.pt`，6,262,051 bytes。实际加载标签 `{0: fire, 1: smoke}`，本版只保留 `fire`。仓库 LICENSE 为 AGPL-3.0，训练数据及素材权利单独核查。
- 备选保留：`rabahdev/fire-smoke-yolov8n`，实际标签 `{0: smoke, 1: fire}`。首轮在火焰 demo1 未形成报警，因此没有选作本版默认。权重保留在本地，不覆盖/删除。
- 选中权重的来源、固定版本 URL、字节数、SHA256 见 `weights/manifest.json`。姿态发布资产未提供发布者 SHA256，只记录本机哈希，不将它说成发布者签名。
- 火花不等于明火：本版没有电火花类别，不能承诺识别短促电弧或焊接火星。
- 求助定义：同一 track 双腕高于鼻点/双肩最高位置，并有 bbox 高度 5% 余量、有效置信度，持续至少 0.5 秒且至少 5 帧。只识别约定动作，不能理解求助意图，也不识别“救命”等声音。伸懒腰、体操可能触发。
- 烟雾按用户要求先不验收且默认过滤。电视中的火焰、灯光与真实火源仍可能混淆。

## 参数与失败披露

通用规则默认仍为：求助 1 秒、跌倒髋部下降 0.18 个站立框高度、观察窗 1.5 秒。本机 `config.local-models.json` 和下载器生成的新配置采用开发参数：求助 0.5 秒、跌倒下降 0.12、观察窗 2.5 秒；跌倒保持 0.8 秒、明火保持 1 秒、阈值 0.6、冷却 15 秒。

KTH 挥双手样本在原始 1 秒要求下 0 次报警，最长连续候选约 0.56 秒。调整到 0.5 秒后才触发，属于样本上调参，不能表述为默认 1 秒规则通过。

修复了跌倒规则问题：站立阶段每帧随倾斜/下降刷新髋部参考，会降低测得的跌落幅度。现从该段直立的起点保留参考，在阶段变化/缺失/重连时重建，并新增回归测试。后续仍需新拍的正负视频验证。

首轮俯视深度/RGB 拼接跌倒视频未触发，原火焰权重也未触发；原始失败结果保留在 `runtime/real-model-validation-20261028-174941/`，开发阶段中间结果在 `runtime/v1-development-validation/`。最终结果不是独立测试。

## 环境与性能

- Python 3.13.9；项目解释器 `.venv/Scripts/python.exe`，基础解释器 `D:/Anaconda/python.exe`。
- PyTorch `2.13.0+cpu`、torchvision `0.28.0+cpu`、Ultralytics `8.4.118`、NumPy `2.3.5`，CPU 线程 4。机器虽有 RTX 4060 Laptop 8 GB，当前 PyTorch 不支持 CUDA，未做 GPU 验证。
- 原 Anaconda NumPy 使用 MKL，与 torch 的 OpenMP 冲突；只在 G 盘项目虚拟环境安装 PyPI NumPy 2.3.5 修复。未设置 `KMP_DUPLICATE_LIB_OK`，未修改系统 DLL。
- 虚拟环境继承 D 盘 site-packages，尚非完全隔离可重建环境；版本快照不是完整传递依赖锁。
- 最终双模型暖机后单帧推理中位数约 187–259 ms，纯推理约 4–5 fps；端到端还受解码/证据编码/磁盘影响。不能据此宣称 25/30 fps 实时处理。
- 模型库设置、Torch/HF/Matplotlib 缓存通过 `runtime_env.py` 固定到项目 `runtime/`；不自动下载模型。

## 直接使用

本机无需重新下载：

```powershell
Set-Location 'G:\比赛\AI黑客松\elder-safety'
# 明火；每次输出新目录，不覆盖之前结果
.\scripts\validate-local.ps1 -InputFile 'G:\比赛\AI黑客松\elder-safety\runtime\validation-media\fire_demo.gif'
# 跌倒
.\scripts\validate-local.ps1 -InputFile 'G:\比赛\AI黑客松\elder-safety\runtime\validation-media\urfd-fall-06-cam0.mp4'
# 双手求助候选
.\scripts\validate-local.ps1 -InputFile 'G:\比赛\AI黑客松\elder-safety\runtime\validation-media\kth_person01_handwaving_d1_uncomp.avi'
```

替换 `-InputFile` 即可测试本人授权的其他本地视频。`-Output` 只接受尚不存在的输出目录，`-MaxFrames` 限制帧数。命令只做本地推理和证据保存，不连接云端或手机；`ALARM` 表示候选事件，分数不是校准后的危险概率。

后端回放：

```powershell
.\.venv\Scripts\python.exe scripts/verify-model-events.py --validation runtime/v1-local-validation-20261028-181635
```

摄像头到货后按 `camera-evening-checklist.md` 核对型号和 RTSP，在本机 `.env` 配置 `RTSP_URL` 后先离线存证据：

```powershell
.\scripts\run-ai.ps1 -Source rtsp -Config 'G:\比赛\AI黑客松\elder-safety\ai-service\config.local-models.json' -Offline
```

要接后端先启动 `scripts/start-backend.ps1`，再去掉 `-Offline`。RTSP 实际耗时对时序 gap 的影响需要实机验收，不能以文件媒体时钟验证代替。

## 下载与素材权利

`python scripts/download-models.py` 仅下载两份选中权重到项目 `weights/`，校验已有文件、不覆盖已有配置。缓存和原素材均不进入源码 ZIP。

URFD 按官方 CC BY-NC-SA 4.0 限非商业学术使用；KTH 官方注明非商业使用。公开 GIF/演示片段的公开可下载性不等于任意再分发权。比赛公开交付前需另核实素材许可，最好用自摄且获得授权的样本。请使用安全模拟动作与现有火焰素材，不让老人实摔或为测试点火。

来源与本地快照：

- https://docs.ultralytics.com/tasks/pose/ （`runtime/model-research/pose_docs.txt`）
- https://github.com/ultralytics/assets/releases/tag/v8.3.0
- https://github.com/luminous0219/fire-and-smoke-detection-yolov8 （README、LICENSE、固定 commit 已记录）
- https://huggingface.co/rabahdev/fire-smoke-yolov8n （作者卡、LFS SHA256 已保存）
- https://fenix.ur.edu.pl/~mkepski/ds/uf.html （`runtime/model-research/urfall.txt`，Kwolek 与 Kepski）
- https://www.csc.kth.se/cvap/actions/ （官方页面、单文件 Range 下载及校验见 `runtime/model-research/help-gesture-kth-*`）
- 坐姿负例：https://github.com/AHHJZ/Action-Detection ，commit `20e8251e08b939e35857aa42b98baa5391663786`。

组员安装可选推理依赖时，将 pip 缓存和临时目录也指向 G 盘（PyTorch CPU/CUDA版本按目标机器选择；本机无需重装）：

```powershell
$env:PIP_CACHE_DIR = 'G:\比赛\AI黑客松\elder-safety\runtime\pip-cache'
$env:TEMP = 'G:\比赛\AI黑客松\elder-safety\runtime\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
.\.venv\Scripts\python.exe -m pip install -r ai-service/requirements-inference.txt
```

以上是可选推理依赖清单，不是完整环境锁；跨机器/新环境仍需要重新验证。
