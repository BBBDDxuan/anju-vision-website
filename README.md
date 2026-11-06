# 安居视界 · 团队可复现运行包

## 使用说明入口

- [手机直接开启后端摄像头监护](docs/PHONE_CAMERA_MONITORING.md)：无需保持电脑网页或手机预览打开。

- **第一次运行或配置 `.env`：[首次运行、环境配置与手机联调](docs/GETTING_STARTED.md)**。按安装、生成令牌、编辑配置、自检、启动的顺序操作。
- [完整环境变量表](docs/c-notifications/ENV_CONFIGURATION.md)：根 `.env`、`elder-safety/.env`、`.env.phone`、GPU 路径、RTSP、预算和 Firebase。
- [手机连接与网络排错](docs/a-backend/PHONE_LOCAL_SETUP.md)；[统一 API 契约](docs/api-contract-abc.md)。

网页与手机联调使用 `start-phone.ps1`，已构建的前端随服务一起提供；`start.ps1` 仅用于电脑单机模式。真实配置在每台运行后端的电脑本机填写，队友拉取仓库后需要各自配置。

本仓库包含网页前端、网页后端、实时摄像头、本地安全检测、模型权重、FFmpeg/FFprobe、团队运行配置和自检工具。按所有者要求，不上传视频、视频分片、录像或真实凭据。

> **凭据仅在本机保存，不提交 Git。** 两份 `.env.example` 保留全部运行参数及可移植路径，
> 安装脚本会在缺少 `.env` 时生成本地配置。API Key、摄像头账号及通知令牌请通过团队安全渠道另行提供。
> 新生成配置默认关闭云请求；填好密钥后将根 `.env` 的 `VLM_ENABLE_NETWORK` 改为 `true` 才能进行真实云复核。

## 网页与 App 共用后端（2026-11-04）

团队约定后端只在本 website 仓库维护，App 仓库维护手机端。原有 `backend/`、`elder-safety/` 和 `frontend/` 目录不移动；手机和网页共用这一个运行中的电脑后端。

完成下面的环境安装后，首次在仓库根目录执行初始化，再编辑配置，最后启动：

```powershell
.\.venv\Scripts\python.exe scripts/configure_phone.py
# 按首次运行指南编辑 .env 和 elder-safety/.env，再启动。
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-phone.ps1 -Address '电脑当前局域网IPv4'
```

**自定义 GPU 环境注意：** `configure_phone.py` 会重写 `SAFETY_PYTHON` 和 `SAFETY_MODEL_CONFIG`；执行后恢复自己的路径。已有配置日常启动无需重新初始化。

电脑网页仍为 `http://127.0.0.1:8000/`；手机使用 `http://电脑局域网IP:8000` 和本机 `runtime/手机连接参数.txt` 的统一令牌。内部 8010 只监听电脑 loopback，手机不填第二个端口。旧 App 的两组服务字段填写同一地址和同一令牌。

统一告警新增 `/api/alerts`、详情、证据、幂等确认和 changes 游标。实摄及上传录像只在现有视觉复核确认后进入正式中心。仓库已包含独立通知/推送模块，需配置 Firebase、启用插件并完成 App 接线后分别验收实际送达。新启动器关闭旧安全 worker，避免候选或模拟通知另行发送；原安全数据和接口保留。

唯一接口契约见 [docs/api-contract-abc.md](docs/api-contract-abc.md)，启动和网络见 [手机连接说明](docs/a-backend/PHONE_LOCAL_SETUP.md)，A 的改动、来源接点登记和待验收项见 [A 交接](docs/a-backend/ROLE_A_HANDOFF_20261104.md)。正式 B/C 联调应先使用 `start-phone.ps1`；`start.bat` 和前端开发脚本保留原电脑用途。

后端回归用 `powershell -ExecutionPolicy Bypass -File .\test.ps1`。真实摄像头、真实录像、两部手机及后台/锁屏通知分别验收，自动化测试不替代这些结果。

## 一、队友首次运行（Windows x64）

1. 克隆完整仓库。模型、工具及配置模板随 Git 提交；视频由队友自行导入，真实凭据通过团队安全渠道提供。
2. 安装 **64 位 Python 3.12 或更新版本**。网页和检测环境相互独立，但可以用同一个基础解释器创建；已验证版本详见 `runtime-versions.json`。
3. 在仓库根目录打开 PowerShell：

```powershell
# 默认 NVIDIA GPU / CUDA；安装脚本验证显卡可用，不会静默退回 CPU。
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-team.ps1 -Device cuda
# 安装和自检成功后启动网页。
.\start.bat
```

打开终端显示的 `http://127.0.0.1:8000/`。前端已编译，无需安装 Node.js 就能使用网页。
首次安装仍需联网获取 Python 依赖；缓存、模型库状态和临时文件都保存在当前仓库的 `runtime/`。
`.venv` 和 `.venv-gpu` 不能从另一台电脑复制。安装脚本会在本机重新创建环境；仓库运行配置使用相对路径，不依赖原电脑的盘符或项目绝对路径。

**识别复现提示：** CPU 模式通过安装、模型加载和完整扫描检查，但在 `11月3日 (1).mp4` 开发正例中未产生跌倒候选，不能认为它与原 CUDA 环境等价。
原 CUDA 环境在三段开发视频均复现到跌倒候选。需要复现识别结果时优先选择 NVIDIA/CUDA，并运行本地验证脚本；详细验收与限制见 `docs/TEAM_DELIVERY_20261103.md`。

没有 NVIDIA 显卡时必须显式选择 CPU；已有 CPU 安装也可用 CUDA 命令切换：

```powershell
# 无 NVIDIA 显卡（识别结果可能与 GPU 不同）：
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-team.ps1 -Device cpu
# 把已有 CPU 安装切换到 GPU：
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-team.ps1 -Device cuda
# 如果 Python 不在默认位置，明确指定两个基础解释器：
.\setup-team.ps1 -Python 'C:\Python313\python.exe' -DetectorPython 'C:\Python311\python.exe' -Device cuda
```

脚本为检测环境安装记录的 PyTorch 2.13.0 / torchvision 0.28.0，CUDA 模式使用 cu126。安装命令固定 `+cu126` / `+cpu` 构建后缀，避免把 CPU 包误认成已安装 CUDA 包。
当前组合已在 Python 3.12、PyTorch 2.13.0+cu126、Ultralytics 8.4.118 和 RTX 4060 上通过模型加载及 GPU 自检。
自检会让姿态和明火两个模型真正执行一次推理，并核对模型实际设备。安装后切换设备需要重启网页后端；已生成的历史分析结果不会自动重算。
安装失败时保留完整错误，先核对 Python 架构、版本、网络和显卡驱动；不要直接替换模型权重或改业务阈值。
只运行网页、不安装本地检测时可加 `-BackendOnly`，但该模式不能验证安全检测。

## 二、接入实时摄像头

1. 复制模板（`setup-team.ps1` 首次运行也会自动创建）：

```powershell
if (-not (Test-Path -LiteralPath .\elder-safety\.env)) {
    Copy-Item -LiteralPath .\elder-safety\.env.example -Destination .\elder-safety\.env
}
notepad .\elder-safety\.env
```

2. 在 `elder-safety/.env` 填写摄像头提供的 RTSP 地址：

```dotenv
RTSP_URL='rtsp://用户名:密码@摄像头IP:554/stream2'
```

不同品牌的 RTSP 路径可能不同，应以摄像头说明书或管理页面为准。主码流通常是 `stream1`，画质更高但解码和推理开销更大；支持子码流时优先用 `stream2` 进行实时分析。本项目实测的 TP-Link IPC45AW 子码流为 H.264、640×360、15 FPS。

3. 运行 `start.bat`，打开 `http://127.0.0.1:8000/`，再点击网页中的“开启摄像头”。摄像头和电脑必须处于可互相访问的局域网；Windows 移动热点下应使用摄像头当前获得的 IP，而不是写死其他电脑上的地址。

实时状态中的 `device` 应显示 `cuda:0` 或 `cpu`。GPU 占用也可在另一 PowerShell 中查看：

```powershell
nvidia-smi -l 1
```

### 本地录像

录像与实时预览同启停，默认保存到项目根目录的 `recordings/`，格式为 MKV，每 5 分钟分段。系统默认最多保留 3 天、总量不超过 10 GiB，并至少保留 5 GiB 磁盘空间。录像目录、数据库和视频文件均被 Git 忽略。

录像路径在 `runtime/team-camera.json` 中保存为相对路径 `../recordings`；运行时才解析为当前电脑上的真实绝对路径。查看状态与文件：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/camera/status' |
    Select-Object -ExpandProperty recording_status
Get-ChildItem .\recordings -Filter '*.mkv'
```

未配置手机通知时，网页显示“报警传送：未启用”是正常状态，不影响本地检测、录像或 Qwen 候选复核。

## 三、先复现本地检测（不消耗 Qwen token）

```powershell
.\elder-safety\.venv-gpu\Scripts\python.exe scripts\verify_reproduction.py --assets-only
# 持有以下三个开发视频时，指定自己的本地文件夹进行回归：
.\elder-safety\.venv-gpu\Scripts\python.exe scripts\verify_reproduction.py --source-dir "你的本地视频目录" --device auto
```

仓库不含视频。资源自检无需录像；如果你已有这三个开发视频，可指定本地文件夹检查候选与多帧证据：

| 视频 | 用途 | 本次 GPU 开发回归期望 |
|---|---|---|
| `11月3日 (1).mp4` | 前扑后骨架间歇丢失 | 约 24.9 秒恢复一个待复核候选 |
| `11月3日 (2).mp4` | 已能识别的对照 | 约 25.5 秒，一个候选 |
| `10月30日.mp4` | 跌倒、求助及蹲下/抱头等混合动作 | 一个跌倒候选，并保留原有求助候选 |

结果和模型输出保存在 `runtime/reproduce-*/verification.json` 及对应扫描目录。
CPU/不同设备的数值与具体触发时间可能不同，脚本会据实际输出检查，不能把开发样例当独立准确率。
该脚本不调用云模型、不连接摄像头、不推送手机通知。网页的最终危险报警仍走“小模型候选 → Qwen 复核”。

视频和视频分片均不提交，安装与网页启动不依赖这些开发视频。队友可直接在网页导入自己的视频。

## 四、运行配置与移到其他目录

- 根 `.env`：团队视觉服务、预算及网页配置；本机填写真实配置，不提交 API Key。
- `elder-safety/.env`：摄像头 RTSP、设备身份与内部安全服务；**相机地址只对能访问相机所在局域网的电脑有效**。
- `elder-safety/ai-service/config.team.json`：已调好的检测阈值与可移植相对路径。
- `.env.phone`：由 `configure_phone.py` 生成的手机统一访问令牌，仅本机保存。
- `runtime/team-camera.json`：安装时生成的可移植相对路径配置；路径相对该 JSON 文件解析，属于运行产物，不提交到 Git。
- `repro-assets.json`：模型、工具的大小及 SHA256。

移动目录后重新执行：

```powershell
.\elder-safety\.venv-gpu\Scripts\python.exe scripts\prepare_team.py --device auto
.\.venv\Scripts\python.exe scripts\doctor_team.py
```

摄像头 IP、网络或密码变更时修改 `elder-safety/.env`，不要修改前端代码。
配置中的模型、FFmpeg 和录像目录均相对配置文件本身解析；加载后才在内存中转换为绝对路径进行安全校验。不要把虚拟环境从旧电脑复制过来。
网页分析只有在启用网络复核且产生候选事件后才可能使用真实 API 配额；本地自检和复现脚本不产生这些请求。

## 五、启动日志与前端开发

正式启动使用：

```powershell
.\start.bat
# 或
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

`start.bat` 会打开独立的服务终端。直接运行 `start.ps1` 时，日志显示在当前终端。两种方式都可以在服务终端按 Ctrl+C 停止。若要同时保存日志：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 *>&1 |
    Tee-Object -FilePath .\runtime\start.log
```

Node.js 仅在需要修改前端时安装。仓库已提交构建后的 `frontend/dist/`，正式运行不要求安装 Node.js。

```powershell
.\start-dev.bat
# 或显式指定使用本仓库的后端，避免复用外层旧项目：
.\start-dev.ps1 -BackendRoot .
```

开发页默认 `http://127.0.0.1:5173/`。依赖锁在 `frontend/package-lock.json`。

## 六、包含哪些模型和工具

| 文件 | 作用 |
|---|---|
| `elder-safety/weights/yolo11n-pose.pt` | 姿态检测，供跌倒及求助规则使用 |
| `elder-safety/weights/indoor-dfire-yolov8n.pt` | 当前明火检测，使用 Indoor Fire Smoke 和 D-Fire 微调 |
| `elder-safety/weights/luminous-fire-yolov8n.pt` | 微调前的原始权重，供对照和回退 |
| `elder-safety/weights/fire-smoke-yolov8n.pt` | 保留的备选权重，默认不启用 |
| `models/face-recognition/face_detection_yunet_2026may.onnx` | 本地家庭成员人脸检测 |
| `models/face-recognition/face_recognition_sface_2021dec.onnx` | 本地家庭成员特征提取与比对 |
| `elder-safety/tools/ffmpeg/ffmpeg.exe` | 视频转码、取帧、录像 |
| `elder-safety/tools/ffmpeg/ffprobe.exe` | 视频元数据 |

工具二进制沿用原电脑实际使用的文件，版本和 SHA256 见 `provenance.json` 与 `repro-assets.json`。
这两份 FFmpeg 工具来自不同年代的构建，本次不偷偷替换；媒体和扫描功能需按自检结果确认。
来源、许可文本及分发说明见 `THIRD_PARTY_NOTICES.md`。

website 后端已接入本地 YuNet + SFace：支持成员照片录入、身份比对和告警身份关联。云部署关闭人脸接口；API、配置、存储与验证边界见 [人脸识别接入说明](docs/FACE_RECOGNITION.md)。

## 七、手机告警与可选 Firebase 通知

手机联调统一使用 `start-phone.ps1`，它同时管理 8000 共享入口和仅本机监听的 8010 内部安全服务，不需要额外手动启动旧安全后端。摄像头识别由电脑网页点击“开启摄像头”，测试时保持工作台打开。

App 的地址和令牌见启动后生成的 `runtime/手机连接参数.txt`。正式通知插件位于 `backend/push_notifications/`，配置 `APP_ALERT_PUSH_HANDLER`、`APP_FCM_ENABLED`、`APP_FCM_CREDENTIALS`、`APP_FCM_ORIGIN` 前先阅读 [完整步骤](docs/GETTING_STARTED.md#7-可选真实-firebase-通知) 和 [推送接点](docs/c-notifications/A_INTEGRATION.md)。

真实 Firebase 推送还需要 App 对应项目的客户端配置、设备登记、通知权限和业务接线；提供商接受不等于手机、后台或锁屏已经送达。真实凭据保留在本机，不提交到 Git。

## 八、提交前检查

本仓库包含两份 `.env.example`、三个检测模型和两份媒体工具；两个人脸模型由 `scripts/prepare_face_models.py` 在本地准备，命令见人脸接入说明。真实 `.env` 保留在本机并被 Git 忽略。
忽略所有 `.venv*` 虚拟环境、下载缓存、数据库、录像、推理运行记录及所有视频/分片。
`setup-team.ps1` 创建这些可重建环境，避免把原电脑绝对路径与旧数据库误当成队友的运行前提。

推送前至少执行：

```powershell
git status --short
git diff --check
.\.venv\Scripts\python.exe .\scripts\doctor_team.py
```

确认输出中没有 `.env`、虚拟环境、录像、数据库、视频或摄像头凭据后再提交。`doctor_team.py` 不连接摄像头、不调用云模型。
