# 手机连接共享后端

首次安装先看 [首次运行与配置指南](../GETTING_STARTED.md)，需要逐项填写时看 [完整 `.env` 变量表](../c-notifications/ENV_CONFIGURATION.md)。

以下命令在 website 仓库根目录执行。网页和 App 共用同一后端，App 仓库不再保留后端副本。唯一接口契约见 [api-contract-abc.md](../api-contract-abc.md)，版本：`2026-11-04.v1`。

## 启动与地址

首次拉取先运行 `setup.bat` 安装环境，再执行 `.\.venv\Scripts\python.exe scripts/configure_phone.py` 生成本机令牌。已有凭据保留；备份位于忽略的 `runtime/`。脚本不启用付费模型网络，不连接摄像头。它会重写根 `.env` 的 `SAFETY_PYTHON` 和 `SAFETY_MODEL_CONFIG`；使用自定义 GPU 解释器或检测 JSON 时，初始化后恢复自己的值。日常启动无需重复初始化。

日常双击 `start-phone.bat`，或者指定当前电脑的局域网 IPv4：

```powershell
.\start-phone.ps1 -Address '10.181.205.172'
```

`10.181.205.172` 是 2026-11-04 本机 WLAN 的检查值，换网络后要更新。电脑打开 `http://127.0.0.1:8000/`，手机使用 `http://电脑当前局域网IPv4:8000`。不要在真机填 `127.0.0.1`。Android 模拟器一般用 `10.0.2.2:8000`，需在服务端 `APP_ALLOWED_HOSTS` 中明确加入 `10.0.2.2`。

| 进程 | 监听 | 用途 |
| --- | --- | --- |
| `backend.phone_app:create_app` | `0.0.0.0:8000` | 手机统一入口、录像原接口、统一告警与安全兼容代理 |
| `backend.phone_app:create_internal_safety_app` | `127.0.0.1:8010` | 内部设备上报、证据、设备登记与旧安全数据 |

保持终端运行，Ctrl+C 会停止两个进程。原 `start.bat` 保留电脑单机用途，不提供新增统一接口；不要同时占用 8000。仅检查启动后退出可用：

```powershell
.\.venv\Scripts\python.exe scripts/phone_server.py --address '电脑当前局域网IPv4' --check
```

## App 配置与认证

打开本机生成的 `runtime/手机连接参数.txt`，将统一地址和统一令牌填入 App。B 接入新版设置后只需一组字段；旧 App 的“录像服务”和“安全后端”两组均填写同一个 8000 地址、同一个统一令牌，联系人角色选 `primary`。

远程 API 和媒体请求均携带 `Authorization: Bearer <APP_LAN_TOKEN>`。统一令牌位于 `.env.phone`，内部安全令牌位于 `elder-safety/.env`，不能混用；内部令牌不再提供给手机。播放器、图片加载器也要带请求头。

旧 App 的连接检查、录像和兼容安全接口可复用；这不表示旧 APK 已接入统一告警。B 必须将正式列表改接 `/api/alerts`，C 使用相同数据流，不能继续把旧 `/api/v1/events` 候选当危险通知。

白名单每次启动加入当前局域网 IP，禁止代理头改写客户端 IP。更新后的 App 使用受令牌保护的 `/api/monitoring/camera` 提交连接并开启后端监护，无需电脑网页保持打开。旧本机预览接口仍拒绝手机访问。详见 [监护使用说明](../PHONE_CAMERA_MONITORING.md)。

## 网络与防火墙

电脑与手机要网络互通；手机热点可用于联调。校园网、访客 Wi-Fi 可能隔离终端。启动成功或本机访问自己的 WLAN 地址不能证明两部手机已经连通。

手机带令牌检查 `/api/health` 和 `/api/connection`；不带令牌得到 401 也能说明请求已到服务器。若超时，核对 IP、网络隔离及 Windows 入站规则。确需放行时，在管理员 PowerShell 中为当前 Python、8000、同一子网建立规则；不开放内部 8010：

```powershell
$pythonPath = (Resolve-Path '.\.venv\Scripts\python.exe').Path
New-NetFirewallRule -DisplayName 'Anju phone API 8000' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Program $pythonPath -RemoteAddress LocalSubnet -Profile Private
```

此命令只作用于 Private 网络配置文件；先确认当前网络类型。撤销规则用 `Remove-NetFirewallRule -DisplayName 'Anju phone API 8000'`。本次开发没有改动 Windows 防火墙。

本入口用于局域网联调；离家访问另行配置受控 VPN 或 HTTPS，不做公网端口映射。

## 模型和告警

沿用根 `.env` 的提供商、模型、预算及网络开关。真实分析要求有效密钥和 `VLM_ENABLE_NETWORK=true`，由负责人在本机配置。连接、上传和播放可独立于模型使用；连接成功不等于模型就绪。`/api/connection.analysis` 检查模型配置，不进行付费请求或 GPU 健康推理。

统一中心约每秒采集一次来源。实摄由电脑开启现有 AI，候选经过 Qwen 视觉复核后经持久化 relay 上报；上传录像复用现有混合分析任务及其确认记录。候选、拒绝、复核不可用、mock 数据不进入正式中心。录像事件墙上时刻未知时为 `null`，片内偏移单列。

新启动器关闭旧安全 worker 的复核、通知、升级和离线监测任务，保留内部数据和设备 API。正式通知由统一 outbox 和 C 的模块承担，避免旧 worker 并发发送候选或模拟通知。若单独运行旧 safety 入口，行为仍是旧实现，不能同时用于此次正式验收。

推送插件未配置时 `/api/connection.notifications.configured=false`；告警照常持久化并可查询，新告警等待接入。首次导入的历史告警不会批量推送。仓库已提供独立推送模块及逐 token 接受记录，启用配置见 [推送接点](../c-notifications/A_INTEGRATION.md)。插件整体回执和逐 token 接受记录不等同于服务端完整安装实例所有权管理。提供商接收与手机、后台或锁屏送达分别验收。

GPU、模型文件和媒体工具可独立自检：

```powershell
.\.venv\Scripts\python.exe scripts/doctor_team.py
```

该命令会执行合成图像 GPU 前向测试，不连接摄像头。完整自动化回归用 `powershell -ExecutionPolicy Bypass -File .\test.ps1`。

## 连接失败分类

| 现象 | 分类及处理 |
| --- | --- |
| 超时、拒绝连接 | 地址、进程、监听、防火墙或终端隔离 |
| `400 Invalid host header` | 当前地址未加入主机白名单，更新地址后重启 |
| `401 PHONE_AUTH_REQUIRED` | 缺少或错误统一令牌，包括图片/播放器请求 |
| `403` 摄像头控制拒绝 | 更新 App 使用 `/api/monitoring/camera`，旧本机预览接口仍受限 |
| `503 SAFETY_SERVICE_UNAVAILABLE` | 内部 8010 不可用，检查内部服务日志 |
| `analysis.ready=false` | Key、网络开关或计费配置未就绪，读取 `analysis.error` |
| `sources.*.ready=false` | 来源同步失败，不撤销已有告警；检查来源库 |
| 任务 `paused` / `failed` | 读取 `/api/jobs/{id}`，区分预算、媒体、模型或检测环境 |

私有配置和历史备份仍保留在 `runtime/`。实际接口和多人验收顺序见 [api-contract-abc.md](../api-contract-abc.md) 与 [ROLE_A_HANDOFF_20261104.md](ROLE_A_HANDOFF_20261104.md)。
