# 安居视界 · 居家视频监测系统

本仓库包含当前网页系统的前端、Python 后端、本地检测代码、启动脚本与前端构建产物。
支持产品介绍、RTSP 实时摄像头预览、录像导入、安全分析、物品追踪和视频证据回溯。

原仓库根目录中的前端已迁移至 **frontend/**，既有 Git 历史保留。
仓库不包含私人录像、历史分析数据库、真实密钥、本地虚拟环境、FFmpeg 二进制和模型权重。
独立手机 App 与其通知后端不在本仓库中；网页中的可选报警转发需要另行部署该服务。

## Windows 首次运行

1. 安装 **Python 3.12**（本次验证版本），下载或克隆本仓库。
2. 双击根目录 **setup.bat**，创建 .venv、安装后端依赖，并在不存在时复制 .env.example 为 .env。
3. 双击根目录 **start.bat**，保持终端运行，打开 **http://127.0.0.1:8000/**。

前端 dist 已提交，查看页面和启动服务不需要 Node.js 或重新编译。
首次依赖安装需要联网；安装脚本不会覆盖已有 .env，不会下载模型或调用视觉 API。
关闭服务使用 Ctrl+C。监听地址为本机 127.0.0.1，不会自动发布到互联网。

也可以在仓库根目录使用 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

若有多个 Python 环境，可指定解释器：

```powershell
.\setup.ps1 -Python 'C:\Python312\python.exe'
.\start.ps1 -Python '.\.venv\Scripts\python.exe' -Port 8000
```

自定义端口用于本机启动；前端开发服务器的 API 代理仍默认指向 8000，需对应修改。

## 启用录像和模型分析

安装包含 **ffmpeg.exe 和 ffprobe.exe** 的 FFmpeg 5+ 发行版，例如通过
[FFmpeg 下载页](https://ffmpeg.org/download.html) 选择 Windows 构建。
本次媒体验证使用 FFmpeg 9.0.2，已使用新版保留原帧时间戳的参数。
将二者加入 PATH，或在根目录 .env 中填写绝对路径：

```dotenv
FFMPEG_PATH=C:/tools/ffmpeg/bin/ffmpeg.exe
FFPROBE_PATH=C:/tools/ffmpeg/bin/ffprobe.exe
```

根目录 .env 中填写自己的模型服务配置。默认密钥为空、网络调用关闭；页面可以打开，
但实际分析会提示未配置，不能将其理解为已经执行识别。

```dotenv
VLM_PROVIDER=qwen
VLM_API_KEY=在本地填写自己的密钥
VLM_BASE_URL=https://maas.qianwenaiapi.com/compatible-mode/v1
VLM_MODEL=qwen3.8-max
VLM_REVIEW_MODEL=qwen3.8-max
VLM_ENABLE_NETWORK=true
VLM_BILLING_MODE=request_limited
```

上述服务地址和模型名须以自己账号实际可用的配置为准。模型请求可能产生费用，
并会将采样图像发送至所配置的服务；每日和每批调用预算见 .env.example。
修改配置后重启后端。录像与结果默认保存于本机 data/，不进入 Git。

- **物品追踪**：需要 FFmpeg 和可用的视觉模型服务。
- **安全分析**：还需要下一节的本地检测环境和模型，候选由本地检测生成，再由配置的视觉服务复核。
- **实时摄像头**：需要支持 RTSP 的网络摄像头及下一节的环境；当前不是浏览器直接调用 USB 摄像头的实现。

## 配置本地检测和实时摄像头

检测进程使用独立 Python 环境，避免与网页后端的 NumPy/OpenCV 依赖冲突。
以下命令在仓库根目录执行：

```powershell
py -3.12 -m venv elder-safety/.venv-gpu
.\elder-safety\.venv-gpu\Scripts\python.exe -m pip install -e './elder-safety/ai-service[pose]'
```

安装 Ultralytics 会安装其检测依赖。使用 GPU 时，还需按硬件和驱动从
[PyTorch 官网](https://pytorch.org/get-started/locally/) 安装兼容的 CUDA 版本；
仅安装 Python 包不能保证 GPU 已可用。配置 device=cpu 可使用 CPU，速度取决于机器。

从 [模型来源清单](elder-safety/weights/manifest.json) 下载这两个明确版本的权重，
建议放到 elder-safety/weights/：

- yolo11n-pose.pt：Ultralytics YOLO11n Pose。
- luminous-fire-yolov8n.pt：清单中固定提交版本的明火模型。

清单包含下载链接和已记录的 SHA256，权重不上传到本仓库。
查看所选火焰模型的确切类别名称后生成本地配置，以下以该模型的 fire 类为例：

```powershell
.\.venv\Scripts\python.exe scripts/configure_safety.py --pose elder-safety/weights/yolo11n-pose.pt --flame elder-safety/weights/luminous-fire-yolov8n.pt --flame-class fire --device cpu
```

脚本校验权重哈希，写入本机绝对路径，并创建被 Git 忽略的
elder-safety/ai-service/config.camera-gpu.json；已有配置会保留，不会被覆盖。
GPU 配置可使用 --device 0，必须先确认该环境能使用 CUDA。
默认关闭摄像头录像；需要录像时还须配置 recording 选项和 FFmpeg。

要连接实时摄像头，另复制配置并填写自己的设备地址：

```powershell
# 仅在该配置尚不存在时执行，保留已有摄像头设置
if (-not (Test-Path 'elder-safety/.env')) {
    Copy-Item elder-safety/.env.example elder-safety/.env
}
```

在 elder-safety/.env 设置包含设备用户名、密码和正确路径的 RTSP_URL。
路径以摄像头厂家与设备实际配置为准，不在仓库中保存真实地址或密码。
根目录 .env 配置云模型；elder-safety/.env 配置摄像头，两者用途不同。
完成后重启网页服务，在“实时摄像头”点击“开启摄像头”。
可选手机报警转发服务未配置时，本机预览仍可运行，不代表手机通知已打通。

## 开发和测试

修改前端才需要 Node.js LTS：

```powershell
cd frontend
npm ci
npm run dev
# 开发地址通常为 http://127.0.0.1:5173/，API 代理到 8000
npm run build
node --test tests/*.test.mjs
```

后端测试在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

媒体测试需要 ffmpeg 和 ffprobe 在 PATH 上。测试使用临时合成视频和模拟服务，
不连接真实摄像头、不调用付费模型。真实设备和识别效果需要在自己的环境另行验证。

## 目录

```text
backend/                     网页 API、数据库、任务、摄像头桥接和模型适配
frontend/src/                React 页面及交互
frontend/public/             前端原始静态资源
frontend/dist/               已编译网页，由后端提供
frontend/tests/              前端测试
elder-safety/ai-service/      本地姿态、求助、明火与连续过程检测代码
elder-safety/scripts/         摄像头工作进程及录像扫描脚本
elder-safety/weights/manifest.json  模型来源和版本哈希
prompts/                     模型提示词及历史版本
schemas/                     事件和证据数据结构
examples/                    明确标识的合成样例
scripts/configure_safety.py   生成本地检测配置
setup.bat / setup.ps1         首次安装后端依赖
start.bat / start.ps1         启动完整网页服务
.env.example                 配置模板，无真实密钥
requirements*.txt            后端依赖及原交付环境记录
```

已有完整系统只想更新页面时，整体替换 frontend/dist/ 即可。
此前的前端专用仓库布局可在旧提交中查阅；新克隆按本说明启动。
第三方来源和许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
