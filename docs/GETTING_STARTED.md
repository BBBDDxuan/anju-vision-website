# 安居视界：首次运行、环境配置与手机联调

更新日期：2026-11-05。适用于本 website 仓库的 Windows 本地运行。所有命令均在克隆后的仓库根目录执行；下文 `G:\比赛\AI黑客松\网页美化\Github` 是路径示例，请替换为自己的位置。

建议顺序：安装环境 → 初始化本机令牌 → 编辑两份运行配置 → 自检 → 启动共享服务 → 电脑开启摄像头 → 手机连接 → 验收告警。已有环境日常运行从第 5 节开始。

完整变量表见 [环境变量详解](c-notifications/ENV_CONFIGURATION.md)，接口见 [统一 API 契约](api-contract-abc.md)。本指南不包含任何真实账号、密钥或令牌。

## 1. 首次安装

准备 Windows x64、64 位 Python 3.12 或更新版本。使用 NVIDIA GPU 时执行 CUDA 安装。Node.js 仅在修改或重新构建前端时需要；仓库已经提供 `frontend/dist`，直接使用网页不需要 Node.js。

```powershell
Set-Location -LiteralPath 'G:\比赛\AI黑客松\网页美化\Github'
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-team.ps1 -Device cuda
```

脚本分别创建网页环境 `.venv` 和检测环境 `elder-safety/.venv-gpu`，安装依赖、校验模型与媒体工具，并生成 `runtime/team-camera.json`。缓存和临时下载放在仓库的 `runtime/setup`；把仓库放在 G 盘即可让这些文件保存在 G 盘。脚本遇到 CUDA 不可用会报错，不会静默改成 CPU。

没有可用 NVIDIA GPU 时可以显式执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-team.ps1 -Device cpu
```

CPU 与 CUDA 的识别结果尚不能视为等价；优先按团队已验证的 CUDA 配置复现。不要复制别人的虚拟环境目录，也不要仅凭 `.venv-gpu` 的名字判断实际设备。

安装成功后，为网页和手机共用后端生成本机随机令牌：

```powershell
.\.venv\Scripts\python.exe scripts/configure_phone.py
```

该脚本会保留已有的非空令牌，并在忽略的 `runtime/phone-setup-backup-*` 下备份原配置。**它每次都会将根 `.env` 的 `SAFETY_PYTHON` 改为 `elder-safety/.venv-gpu/Scripts/python.exe`，将 `SAFETY_MODEL_CONFIG` 改为 `runtime/team-camera.json`。** 使用自定义检测解释器或配置文件时，执行后需要恢复自己的这两个值。下面的配置步骤放在初始化之后，日常启动无需重复运行初始化。

## 2. 三份 .env 分别放什么

| 文件 | 用途 | 需要自己做什么 |
| --- | --- | --- |
| 根 `.env` | 网页/手机共享后端的模型 API、联网开关、检测解释器、媒体工具和预算 | 配置 API 三要素及本机路径 |
| `elder-safety/.env` | 摄像头 RTSP、设备身份和内部安全服务配置 | 填本机可访问的 RTSP；保留初始化生成的内部令牌 |
| 根 `.env.phone` | 手机访问共享后端的 `APP_LAN_TOKEN` | 由初始化脚本生成，供 App 填写 |

两份 `.env.example` 是可提交的模板；三份真实 `.env` 和 `runtime/手机连接参数.txt` 都只保存在本机。不要将模型 API Key、手机令牌、摄像头密码互相混用。

安装脚本仅在配置不存在时复制模板。已有配置请直接编辑，不要重新复制模板覆盖。Windows 记事本保存时检查文件确实叫 `.env`，没有变成 `.env.txt`。

配置规则：

- 每行一个 `变量名=值`，变量名区分大小写；布尔值使用 `true` / `false`。
- Windows 路径推荐写成 `G:/path/to/file` 并使用单引号；路径包含中文和空格时仍保留引号。
- 根 `.env` 内的相对文件路径按仓库根目录解析；检测 JSON 内的相对模型、录像路径按 JSON 所在目录解析。
- 已存在的进程环境变量优先于 `.env`，因为代码使用 `override=False`。遇到编辑后不生效，先检查启动终端是否设置过同名 `$env:变量名`。
- 修改后停止旧服务并重新启动，运行中的服务不会自动重新读取配置。

## 3. 根 .env：模型 API 与本地推理

```powershell
notepad .\.env
```

### 3.1 接口地址、密钥与模型必须配套

在模板原有项目上修改，下面是占位示例，不能原样用于真实调用：

```dotenv
VLM_PROVIDER=qwen
VLM_API_KEY='replace-with-your-private-key'
VLM_BASE_URL='https://your-provider.example/compatible-mode/v1'
VLM_MODEL='your-vision-model-id'
VLM_REVIEW_MODEL='your-vision-model-id'
VLM_ENABLE_NETWORK=true
HYBRID_SAFETY_ENABLED=true
VLM_BILLING_MODE=request_limited
MAX_MODEL_CALLS_PER_VIDEO=16
MAX_MODEL_CALLS_PER_DAY=100
```

- `VLM_BASE_URL` 填团队实际使用、与 Key 匹配的 HTTPS 兼容接口根地址，代码会追加 `/chat/completions`，不要重复添加。
- `VLM_MODEL` 用于录像物品分析等任务，`VLM_REVIEW_MODEL` 用于安全候选复核。填写该服务账号实际可调用、支持图片输入的模型 ID。模板中的地址和模型名是既有配置参考，不保证每个新账号都可用，也不应当直接当作通用官方配置。
- `VLM_API_KEY` 为空时，代码会尝试 `DASHSCOPE_API_KEY`；建议只维护一套明确有效的配置，避免两个不同账号混用。
- `VLM_ENABLE_NETWORK=false` 时不会进行真实云端复核；手机连接、录像上传和本地自检可先独立验证，但不能由此认定正式危险告警闭环已完成。
- `request_limited` 按请求数量限制，不代表免费，也不等同于按人民币预算限额。模型 API 会消耗服务账号配额或费用。

如改为 `VLM_BILLING_MODE=priced`，需填写并核实 `VLM_INPUT_RMB_PER_MILLION`、`VLM_OUTPUT_RMB_PER_MILLION`，并设 `VLM_COST_CONFIRMED=true`；另设 `MAX_COST_RMB_PER_VIDEO`、`MAX_COST_RMB_PER_DAY`。复核与主模型不同时还需核实 `VLM_REVIEW_*` 单价和确认项，详见完整变量表。普通使用先保留模板的采样、并发和输出预算。

### 3.2 检测与媒体工具路径

标准安装保留：

```dotenv
SAFETY_PYTHON='elder-safety/.venv-gpu/Scripts/python.exe'
SAFETY_MODEL_CONFIG='runtime/team-camera.json'
FFMPEG_PATH='elder-safety/tools/ffmpeg/ffmpeg.exe'
FFPROBE_PATH='elder-safety/tools/ffmpeg/ffprobe.exe'
DATA_DIR='data'
CLOUD_MODE=false
```

`SAFETY_PYTHON` 是运行本地模型的 Python 可执行文件，不是模型权重；网页 `.venv` 可以与检测环境不同。使用已有 CUDA 环境时，将它改为本机经过验证的 `python.exe` 路径，不要照抄文档中的其他电脑路径。

`SAFETY_MODEL_CONFIG` 指检测 JSON；模型权重和检测阈值在 JSON 里。移动仓库或需要重新生成标准 GPU 配置时执行：

```powershell
.\elder-safety\.venv-gpu\Scripts\python.exe scripts/prepare_team.py --device 0
```

使用自定义环境时用它的 Python 替换上述解释器。CPU 对应 `--device cpu`。此命令会从团队模板重新生成 JSON；如果你修改过生成文件中的录像目录或检测参数，先保存自己的修改并在生成后复核。

## 4. elder-safety/.env：摄像头与内部身份

```powershell
notepad .\elder-safety\.env
```

将 `RTSP_URL` 填为摄像头本地访问设置中核实、且电脑能访问的完整地址：

```dotenv
RTSP_URL='rtsp://camera_user:camera_password@192.168.137.39:554/replace-with-verified-stream-path'
```

这是格式示例，IP、账号、密码和流路径全部需要按现场实际配置替换。摄像头 App 登录账号不一定就是本地 RTSP 账号，不猜测型号对应的路径。账号或密码中的 `@`、`#`、`:`、`/` 等 URL 特殊字符需要对对应账号/密码部分做百分号编码，不能把整个 RTSP URL 编码。

电脑必须能访问摄像头所在网络。摄像头连接电脑热点时，核对当前热点网段和摄像头 IP；更换电脑、网络或热点后重新确认。复制仓库不会自动建立摄像头连接。

初始化后检查以下项目是否存在并保持一致，**保留实际随机值，不填示例短令牌**：

- `SAFETY_USER_TOKEN`：内部用户身份。
- `SAFETY_DEVICE_ID`：例如模板中的 `camera_001`。
- `SAFETY_DEVICE_TOKENS`：JSON 格式的设备 ID 到随机令牌映射。
- `SAFETY_DEVICE_TOKEN`：必须等于上述映射内当前设备 ID 对应的值。
- 用户令牌与设备令牌需要互不相同，内部服务要求至少 24 字符；初始化脚本会生成合适的值。

没有设备身份时，网页可以只预览，但摄像头告警中继可能被禁用。需要修改 `SAFETY_BACKEND_URL` 时，它表示摄像头向电脑内部安全服务上报的地址，标准启动保留默认 `http://127.0.0.1:8010`。

统一手机启动器关闭旧安全 worker，保留模板的 `SAFETY_REVIEW_MODE=mock`、`SAFETY_PUSH_MODE=mock` 不代表正式统一告警走模拟复核。正式视觉复核使用根 `.env` 的 `VLM_*`；正式推送使用第 7 节的 `APP_FCM_*`。不要为开启手机告警额外启动一套旧 worker。

## 5. 自检与日常启动

环境或检测配置变更后先运行：

```powershell
.\.venv\Scripts\python.exe scripts/doctor_team.py
```

它检查资源、执行本地模型前向并核对实际设备，不连接摄像头、不调用付费云模型。通过不代表 RTSP 可达、API 账号可用或手机推送已送达。

### 网页与手机一起使用（团队联调推荐）

查询当前地址：

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
    Format-Table InterfaceAlias,IPAddress
```

选手机实际能访问的 WLAN/以太网或热点接口地址，跳过 VMware 等虚拟网卡。将下例 IP 替换为当前电脑地址：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-phone.ps1 -Address '192.168.1.10'
```

等待 `Both services are running`。电脑打开 `http://127.0.0.1:8000/`；手机访问 `http://电脑当前IP:8000`。8000 是网页与手机统一入口；8010 只供内部服务使用。保持终端运行，`Ctrl+C` 停止两个服务。

在电脑网页点击“开启摄像头”，等到“本地实时识别中”。手机没有远程开启摄像头控制权限。测试时保持电脑工作台打开；关闭工作台约 30 秒后会停止监测。

### 只在电脑上使用

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

该入口是电脑单机模式；手机统一 `/api/alerts` 等接口使用 `start-phone.ps1`。两个启动器不能同时占用 8000。

已构建的前端由后端直接提供。修改过前端源码后，在 `frontend/` 内执行 `npm ci`、`npm run build` 更新构建产物，再回根目录启动。需要开发热更新时参考 README 的 `start-dev.ps1`；手机仍连接 8000。

## 6. App 地址、认证与连接验证

共享服务启动后打开本机参数文件：

```powershell
notepad .\runtime\手机连接参数.txt
```

App 填“统一服务地址”和“统一访问令牌”。真手机不能填 `127.0.0.1`；旧 App 如果仍有“录像服务”和“安全后端”两组设置，两组都填相同的 8000 地址、相同的统一令牌，联系人角色按需要选 `primary`。不要填内部 8010 或模型 API Key。

手机与电脑需要网络互通，校园网和访客 Wi-Fi 可能隔离终端。手机浏览器访问 `http://电脑当前IP:8000/api/health`；收到响应或 401 说明请求已经到服务，超时则先查地址、监听、防火墙和网络隔离。需要放行时只处理当前 Python、8000 和合适子网，具体命令见 [手机网络说明](a-backend/PHONE_LOCAL_SETUP.md)。

App 的 API、截图和视频请求都需带 `Authorization: Bearer <统一令牌>`，客户端接线以统一契约为准。连接成功、能看画面、出现正式告警、系统通知送达分别验收。

## 7. 可选：真实 Firebase 通知

仅查看告警列表可以暂不配置推送。需要后台系统通知时，后端和 App 两侧均要配置并接通：

```powershell
.\.venv\Scripts\python.exe -m pip install --cache-dir .\runtime\setup\pip-cache -r backend/push_notifications/requirements-fcm.txt
```

在根 `.env` 增加：

```dotenv
APP_ALERT_PUSH_HANDLER='backend.push_notifications:publish_alert'
APP_FCM_ENABLED=true
APP_FCM_CREDENTIALS='G:/team-private/firebase-service-account.json'
APP_FCM_ORIGIN='http://192.168.1.10:8000'
```

`APP_FCM_ORIGIN` 必须与 App 当前使用的规范化后端根地址一致，替换 IP，不加 `/api`、路径或末尾斜杠。服务账号文件保存在仓库外，与 App 的 Firebase 客户端配置属于同一项目。修改后重启共享服务。

还需 App 配好 Firebase、上传真实设备 token、启用通知权限并完成统一告警/通知点击接线。`SAFETY_FIREBASE_CREDENTIALS` 是旧安全服务选项，不能代替此插件的 `APP_FCM_CREDENTIALS`。提供商接受请求不等于设备送达，前台、后台、锁屏及冷启动点击分别测试。详细接点见 [推送配置](c-notifications/A_INTEGRATION.md)。

## 8. 常见问题与完成标准

| 现象 | 优先检查 |
| --- | --- |
| `Run scripts/configure_phone.py first` | 缺少 `.env.phone` 或统一令牌；初始化后复核自定义 GPU 路径 |
| 端口被占用 | 停止自己此前启动的服务，再选统一入口；不重复启动两个 8000 |
| 手机连接超时 | IP 是否变化、手机网络是否可达、服务是否运行、防火墙/终端隔离 |
| `400 Invalid host header` | 用当前可达 IP 重新执行 `start-phone.ps1 -Address ...`，不要把白名单改为 `*` |
| `401 PHONE_AUTH_REQUIRED` | App 和媒体请求是否使用 `.env.phone` 的统一令牌 |
| 手机摄像头操作 `403` | 在电脑 `127.0.0.1:8000` 网页开启摄像头 |
| `503 SAFETY_SERVICE_UNAVAILABLE` | 内部服务日志、内部令牌 JSON 与端口是否正确 |
| `CAMERA_RUNTIME_MISSING` | 根 `.env` 中检测 Python、基础环境和本地摄像头配置是否存在 |
| 能显示画面但没有正式告警 | 本地候选、复核开关/Key/模型、复核结果、设备中继和统一中心逐层检查 |
| `MODEL_NOT_CONFIGURED` / `NETWORK_DISABLED` | 根 `.env` 的 Key 和 `VLM_ENABLE_NETWORK` |
| `COST_NOT_CONFIRMED` | 明确选请求限额模式，或核实 priced 模式所需单价与确认项 |
| 推送未收到 | 插件、Firebase 项目/凭据、设备登记、App 通知接线与系统权限 |
| 上传后 `TRANSCODE_FAILED` | 查看 FFmpeg 日志及视频编码；区分转码兼容问题和网络问题 |

共享服务日志：

```powershell
Get-Content -LiteralPath .\runtime\phone-server\video.log -Tail 60
Get-Content -LiteralPath .\runtime\phone-server\safety.log -Tail 60
```

共享日志前先检查并脱敏，不把完整 `.env`、RTSP URL、令牌文件或服务账户 JSON 发到公开渠道。

交付验收至少包含：电脑摄像头正常；两部手机连接；真实录像与实摄分别产生确认告警；证据可查看；一部手机确认后另一部同步；重启不重放历史通知；前台/后台/锁屏通知分别记录结果。模型正反例使用受控录像进行测试。

本指南核对的是启动与配置代码，不代表已替每台电脑或手机完成这些真实验收。
