# 安居视界 · 团队可复现运行包

本仓库现包含网页前端、网页后端、本地安全检测、模型权重、FFmpeg/FFprobe、团队运行配置和自检工具。按所有者要求，不上传视频或视频分片。
本次整理保留现有前端源码与构建产物，仅补齐后端和运行依赖。

> **凭据仅在本机保存，不提交 Git。** 两份 `.env.example` 保留全部运行参数及可移植路径，
> 安装脚本会在缺少 `.env` 时生成本地配置。API Key、摄像头账号及通知令牌请通过团队安全渠道另行提供。
> 新生成配置默认关闭云请求；填好密钥后将根 `.env` 的 `VLM_ENABLE_NETWORK` 改为 `true` 才能进行真实云复核。

## 一、队友首次运行（Windows x64）

1. 克隆完整仓库。模型、工具及配置模板随 Git 提交；视频由队友自行导入，真实凭据通过团队安全渠道提供。
2. 安装 **Python 3.12 或更新版本**用于网页后端。想尽量匹配本机已验证环境，可另装
   **Python 3.11**用于检测进程；已验证版本详见 `runtime-versions.json`。
3. 在仓库根目录打开 PowerShell：

```powershell
# 默认 NVIDIA GPU / CUDA；安装脚本验证显卡可用，不会静默退回 CPU。
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-team.ps1 -Device cuda
# 安装和自检成功后启动网页。
.\start.bat
```

打开终端显示的 `http://127.0.0.1:8000/`。前端已编译，无需安装 Node.js 就能使用网页。
首次安装仍需联网获取 Python 依赖；缓存、模型库状态和临时文件都保存在当前仓库的 `runtime/`。
`.venv` 不能直接从另一台电脑复制：安装脚本会在本机重新创建，不依赖原电脑的 G:/D: 目录。

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
自检会让姿态和明火两个模型真正执行一次推理，并核对模型实际设备。安装后切换设备需要重启网页后端；已生成的历史分析结果不会自动重算。
安装失败时保留完整错误，先核对 Python 架构、版本、网络和显卡驱动；不要直接替换模型权重或改业务阈值。
只运行网页、不安装本地检测时可加 `-BackendOnly`，但该模式不能验证安全检测。

## 二、先复现本地检测（不消耗 Qwen token）

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

## 三、运行配置与移到其他目录

- 根 `.env`：团队视觉服务、预算及网页配置；本机填写真实配置，不提交 API Key。
- `elder-safety/.env`：摄像头和可选手机通知服务；**相机地址只对能访问相机所在局域网的电脑有效**。
- `elder-safety/ai-service/config.team.json`：已调好的检测阈值与可移植相对路径。
- `runtime/team-camera.json`：安装时生成的本机绝对路径配置，属于运行产物，不提交到 Git。
- `repro-assets.json`：模型、工具的大小及 SHA256。

移动目录后重新执行：

```powershell
.\elder-safety\.venv-gpu\Scripts\python.exe scripts\prepare_team.py --device auto
.\.venv\Scripts\python.exe scripts\doctor_team.py
```

摄像头 IP、网络或密码变更时修改 `elder-safety/.env`，不要修改前端代码。
网页点击“开启视频分析”会使用团队的真实 API 配额；本地自检和复现脚本不产生这些请求。

## 四、前端开发

Node.js 仅在需要修改前端时安装。保留的前端实现、页面和 dist 未因本次整理而更改。

```powershell
.\start-dev.bat
# 或显式指定使用本仓库的后端，避免复用外层旧项目：
.\start-dev.ps1 -BackendRoot .
```

开发页默认 `http://127.0.0.1:5173/`。依赖锁在 `frontend/package-lock.json`。

## 五、包含哪些模型和工具

| 文件 | 作用 |
|---|---|
| `elder-safety/weights/yolo11n-pose.pt` | 姿态检测，供跌倒及求助规则使用 |
| `elder-safety/weights/luminous-fire-yolov8n.pt` | 当前明火检测 |
| `elder-safety/weights/fire-smoke-yolov8n.pt` | 保留的备选权重，默认不启用 |
| `elder-safety/tools/ffmpeg/ffmpeg.exe` | 视频转码、取帧、录像 |
| `elder-safety/tools/ffmpeg/ffprobe.exe` | 视频元数据 |

工具二进制沿用原电脑实际使用的文件，版本和 SHA256 见 `provenance.json` 与 `repro-assets.json`。
这两份 FFmpeg 工具来自不同年代的构建，本次不偷偷替换；媒体和扫描功能需按自检结果确认。
来源、许可文本及分发说明见 `THIRD_PARTY_NOTICES.md`。

## 六、可选手机通知后端

`elder-safety/backend/` 和 `elder-safety/scripts/` 也已附带。网页不是独立手机 App 源码包。
若要在本机额外启动通知中继，可在已安装的网页环境安装其依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r elder-safety/backend/requirements.txt
$env:PYTHONPATH = "$PWD\elder-safety\backend"
.\.venv\Scripts\python.exe -m uvicorn safety_backend.main:create_app --factory --host 127.0.0.1 --port 8010 --no-access-log
```

真实 Firebase 推送仍需要对应 Firebase 项目和服务凭据；本包不会伪造这些配置，也没有替队友验证手机通知。

## 七、提交规则

本仓库提交两份 `.env.example`、三个模型、两份媒体工具；真实 `.env` 保留在本机并被 Git 忽略。
仍忽略虚拟环境、下载缓存、数据库、录像、推理运行记录及所有视频/分片。
`setup-team.ps1` 创建这些可重建环境，避免把原电脑绝对路径与旧数据库误当成队友的运行前提。
