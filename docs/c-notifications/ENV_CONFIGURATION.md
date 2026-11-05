# 手机与网页共用后端：本机环境配置说明

适用目录：G:/比赛/AI黑客松/GitHub同步/website。
编写日期：2026-11-04；同步整理日期：2026-11-05。配置说明以源码为准，本机环境记录见下方日期说明。

更新：2026-11-05 的本地 GPU 完整视频扫描已成功运行，早先缺失依赖的记录已过时。结果见 [针对性修复记录](TARGETED_DETECTION_FIX.md)。本文中的本机路径是示例，其它电脑应使用自己的解释器路径。
本文位于 C 的 docs/c-notifications 范围；不修改 A 的主配置模板、后端业务或 B 的手机代码。

## 1. 先解决本次两个配置项

这两个变量都填写在 website 根目录的 .env 中。变量名必须大写。
SAFETY_PYTHON 是执行本地检测的 Python 可执行文件，不是模型文件。
SAFETY_MODEL_CONFIG 是检测配置 JSON，不是 API Key，也不是权重目录。

本机已检查到以下可用 GPU 环境：

~~~dotenv
SAFETY_PYTHON='D:/Anaconda/envs/gear_test/python.exe'
SAFETY_MODEL_CONFIG='runtime/team-camera.json'
~~~

该环境当前为 Python 3.11.15、torch 2.13.0+cu126，CUDA 可用，识别到 RTX 4060 Laptop GPU。
网页服务继续用 website/.venv 的 Python 3.13.7；检测子进程用上述 Python 3.11。两个环境可以分开。
不要把 Python 3.11 的 torch 文件复制进 Python 3.13 环境。

此轮已执行下列命令并成功校验资源、生成 GPU 设备 0 的配置，不必马上重复：

~~~powershell
Set-Location 'G:\比赛\AI黑客松\GitHub同步\website'
.\.venv\Scripts\python.exe scripts/prepare_team.py --device 0
~~~

生成文件是 runtime/team-camera.json。脚本按新仓库位置修正权重和 ffmpeg 路径。
其中 models.device 为 "0"；不使用 GPU 时可以用 --device cpu 重新生成。
新电脑上的 Python 路径需要按那台电脑的实际环境填写，不能直接照抄 D 盘路径。

2026-11-04 最初检查时 GPU 环境缺少 httpx、python-dotenv、dill；后续完整 GPU 扫描已成功，不需要为同步重复安装。仅当新环境确实缺少这些依赖时执行：

~~~powershell
& 'D:\Anaconda\envs\gear_test\python.exe' -m pip install --cache-dir 'G:\比赛\AI黑客松\GitHub同步\website\runtime\setup\pip-cache' 'httpx==0.28.1' 'python-dotenv==1.2.4' 'dill==0.4.1'
~~~

以上只安装三个小依赖，没有要求重新安装 torch。版本取自 requirements-detector.txt。
新环境应在依赖齐全、.env 路径填写正确后运行第 7 节的完整检测诊断。

注意：scripts/configure_phone.py 每次都会把根 .env 的 SAFETY_PYTHON 重写为
elder-safety/.venv-gpu/Scripts/python.exe。采用 gear_test 时，重新执行该脚本后必须再次改回实际 Python 路径。
日常启动不需要重新运行 setup 或 configure_phone。

## 2. 配置文件各自放什么

| 文件 | 作用 | 提交 GitHub？ |
| --- | --- | --- |
| website/.env | 网页、手机共用服务的模型 API、本地检测、预算与采样参数 | 否 |
| website/.env.phone | APP_LAN_TOKEN，手机访问电脑服务的令牌 | 否 |
| website/elder-safety/.env | 内部安全服务身份、摄像头身份、RTSP 等 | 否 |
| website/runtime/team-camera.json | 本机生成的检测权重路径、GPU/CPU、检测规则 | 否；提交其来源模板即可 |
| website/.env.example | A 维护的无真实凭据模板 | 是 |
| docs/c-notifications/phone-local.env.example | C 提供的完整无密钥参考模板 | 是 |
| docs/c-notifications/ENV_CONFIGURATION.md | 本说明 | 是 |

已经有配置时，请编辑原 .env，保留真实 API Key、原令牌与现有参数，不要直接用模板覆盖。
只有全新安装且没有 .env 时，才复制模板为根 .env，再单独填写真实信息。
路径建议使用正斜杠并以单引号括起来，避免双引号中的反斜杠被当作转义。
根 .env 的相对路径以 website 为基准；JSON 内的相对权重路径以 JSON 所在目录为基准。
配置读取使用 override=False：若 PowerShell/Conda 已有同名环境变量，它会优先于 .env。
改完配置后必须停止旧后端并重新启动，正在运行的进程不会自动采用新值。

## 3. 根 .env 的全部现有项目（35 项）

下表值来自当前 .env.example；不是读取并公布你的真实 .env 值。
你当前实际 .env 的键名与这 35 项一致，另有补充可选项见第 4 节。

### 3.1 模型、联网与计费

| 变量 | 模板值 | 配置方法 |
| --- | --- | --- |
| VLM_PROVIDER | qwen | 当前实现使用 qwen，保留。 |
| VLM_API_KEY | 空 | 必填：填写你所用视觉模型服务的真实 API Key，只在本机保存。 |
| VLM_BASE_URL | https://maas.qianwenaiapi.com/compatible-mode/v1 | 填与 API Key 对应的 HTTPS 兼容接口根地址，不追加 /chat/completions。当前模板地址不代表本轮已验证其账号/模型可用性。 |
| VLM_MODEL | qwen3.8-max | 填该接口、账号实际可调用且满足当前图片输入与 JSON 输出需求的模型 ID。本轮保留模板文字，没有验证云端模型是否存在或可用。 |
| VLM_REVIEW_MODEL | qwen3.8-max | 本地检测候选的云端复核模型。先使用你已核实可用的模型；不能用不存在或不支持当前请求格式的模型 ID。 |
| VLM_ENABLE_NETWORK | false | 离线检查保持 false；正式使用云端分析时手动改 true。仅填写 Key 不会自动打开联网。 |
| VLM_BILLING_MODE | request_limited | 两种模式：request_limited 按请求数限制；priced 按填写的 token 单价预留金额。 |
| VLM_COST_CONFIRMED | false | request_limited 可保持 false；priced 必须核实输入、输出单价后改 true。 |
| VLM_INPUT_RMB_PER_MILLION | 0 | 输入每百万 token 的人民币单价。priced 必须填写真实正数，不能凭空写一个价格。 |
| VLM_OUTPUT_RMB_PER_MILLION | 0 | 输出每百万 token 的人民币单价，要求同上。 |
| MAX_COST_RMB_PER_VIDEO | 2 | priced 模式下单视频金额预算；request_limited 不按此值保证金额上限。 |
| MAX_COST_RMB_PER_DAY | 5 | priced 模式下每日金额预算；request_limited 不按此值保证金额上限。 |
| MAX_MODEL_CALLS_PER_VIDEO | 16 | 单视频模型请求配额，必须大于 0。更低配额可能使长视频分析不能完整完成。 |
| MAX_MODEL_CALLS_PER_DAY | 100 | 每日模型请求配额，必须大于 0；不是免费额度。 |
| VLM_MAX_RETRIES | 1 | 最大重试数，只允许 0 或 1。 |
| REQUEST_TIMEOUT_SECONDS | 90 | 单次模型 HTTP 请求超时秒数。 |

如同时填写 DASHSCOPE_API_KEY，根配置会优先使用非空 VLM_API_KEY；只选一种来源更容易排查。
request_limited 仍可能产生真实费用。开启联网前，请自己确认模型服务、价格和账号额度。
priced 的金额控制基于源码中的 token 用量预估与填写单价，不等同于服务商账户级消费上限。

### 3.2 本地检测与媒体工具

| 变量 | 模板值 | 本机配置方法 |
| --- | --- | --- |
| SAFETY_PYTHON | elder-safety/.venv-gpu/Scripts/python.exe | 本机改为 D:/Anaconda/envs/gear_test/python.exe，先补齐第 1 节三个依赖。 |
| SAFETY_MODEL_CONFIG | runtime/team-camera.json | 保留。本轮已生成此文件，模型路径由 prepare_team.py 修正。 |
| FFMPEG_PATH | elder-safety/tools/ffmpeg/ffmpeg.exe | 保留；本轮 -version 检查通过。 |
| FFPROBE_PATH | elder-safety/tools/ffmpeg/ffprobe.exe | 保留；本轮 -version 检查通过。 |
| DATA_DIR | data | 录像分析与数据库数据目录，根目录相对路径，保留即可。 |
| CLOUD_STORAGE_MB | 0 | 云模式存储配额，MB；0 表示取消固定配额。不是硬盘无限大。 |

### 3.3 视频大小、抽帧与并发

| 变量 | 模板值 | 含义及约束 |
| --- | --- | --- |
| MAX_VIDEO_SECONDS | 0 | 视频时长上限，0 表示不限制时长；可填正整数限制。 |
| MAX_UPLOAD_MB | 0 | 上传大小上限，0 表示不设置固定大小上限；可填正整数限制。 |
| MAX_FRAME_REFERENCES | 256 | 帧引用预算，必须大于 0。 |
| MAX_CONCURRENCY | 2 | 任务并发，只允许 1–2。 |
| MEDIA_TIMEOUT_SECONDS | 1800 | 媒体处理超时秒数，必须大于 0。 |
| MAX_SAMPLED_FRAMES | 100000 | 采样帧数量预算，必须大于 0。 |
| VLM_OUTPUT_TOKENS_PER_WINDOW | 4096 | 单窗口输出预算，允许 512–4096；还需满足服务商模型能力。 |
| LONG_VIDEO_THRESHOLD_SECONDS | 120 | 长视频策略的时长阈值，必须至少 1 秒。 |
| LONG_VIDEO_SAMPLING_FPS | .5 | 长视频采样帧率，允许 0.2–4 FPS。 |
| LONG_VIDEO_WINDOW_SECONDS | 30 | 长视频窗口长度，允许 2–30 秒。 |
| LONG_VIDEO_WINDOW_STRIDE_SECONDS | 28 | 窗口移动步长，至少 1 秒，且不超过长视频窗口长度。 |
| MAX_OUTPUT_TOKENS_PER_VIDEO | 32768 | 单视频累计输出预算，必须大于 0。 |

这些是预算、采样和资源参数，当前任务不要求调优，先保留模板值。
视频时长/大小不设上限，也仍会受到时间、帧数、请求和输出预算约束。

### 3.4 推送入口

| 变量 | 模板值 | 配置方法 |
| --- | --- | --- |
| APP_ALERT_PUSH_HANDLER | 空 | 普通分析可保持空。完成真实 FCM 配置时才填 backend.push_notifications:publish_alert，详见第 6 节。 |

## 4. 主服务额外支持的可选项目

这些项并未出现在原 35 项模板中；不需要全手动补齐，省略时使用源码默认值。
为方便团队查阅，C 的参考模板提供了这些非敏感默认值或注释。

| 变量 | 源码默认值/当前建议 | 作用 |
| --- | --- | --- |
| DASHSCOPE_API_KEY | 空 | VLM_API_KEY 的备用来源。不要同时配置不同账号的两个 Key。 |
| HYBRID_SAFETY_ENABLED | true | 启用本地检测与云端复核链路，保留 true；不要用关闭它掩盖检测环境缺失。 |
| RECORD_QA_ENABLED | true | 录像记忆问答开关。 |
| VLM_REVIEW_INPUT_RMB_PER_MILLION | 0 | priced 且复核模型与主模型不同：填写复核模型真实输入单价。 |
| VLM_REVIEW_OUTPUT_RMB_PER_MILLION | 0 | 同上，复核输出单价。 |
| VLM_REVIEW_COST_CONFIRMED | false | 同上，核实复核单价后才设 true。 |
| VIDEO_SAMPLING_FPS | 1 | 普通视频采样帧率，0.2–4。 |
| VIDEO_WINDOW_SECONDS | 8 | 普通视频窗口秒数，2–30。 |
| VIDEO_WINDOW_STRIDE_SECONDS | 6 | 普通视频步长，1 到窗口长度。 |
| SAFETY_SAMPLING_ENABLED | true | 危险事件增强采样开关。 |
| SAFETY_SAMPLING_FPS | 4 | 增强采样帧率，0.2–4。 |
| SAFETY_WINDOW_SECONDS | 4 | 增强窗口长度，2–30 秒。 |
| SAFETY_WINDOW_STRIDE_SECONDS | 3 | 增强步长，1 到增强窗口长度。 |
| ACTIVITY_FILTER_ENABLED | true | 静止画面过滤。 |
| ACTIVITY_FILTER_FPS | 1 | 过滤采样帧率，0.5–4。 |
| ACTIVITY_FILTER_PIXEL_THRESHOLD | 18 | 像素变化阈值，1–255。 |
| ACTIVITY_FILTER_CHANGE_RATIO | .003 | 变化像素比例，大于 0 且不超过 1。 |
| ACTIVITY_FILTER_REVIEW_SECONDS | 300 | 静止抽查间隔，不超过 3600 秒，且不小于有人时抽查间隔。 |
| ACTIVITY_FILTER_PERSON_REVIEW_SECONDS | 60 | 有人时抽查间隔，至少 1 秒。 |
| ACTIVITY_FILTER_POSTROLL_SECONDS | 30 | 活动后保留秒数，0–300。 |
| PERSON_ABSENCE_ALERT_SECONDS | 300 | 长时间未出现提醒阈值，至少 60 秒。 |
| APP_ALLOWED_HOSTS | 127.0.0.1,localhost,[::1] | 允许访问的主机名。start-phone 启动时自动加入指定电脑局域网 IP，不要设为 *。 |
| APP_ALLOWED_ORIGINS | 本机 localhost/127.0.0.1 的 5173、8000 地址 | 浏览器跨域允许来源；原生手机 App 日常连接通常不必手工改。 |
| APP_LAN_TOKEN | 由 .env.phone 提供 | 手机令牌应放 .env.phone，启动器读取并传给服务。至少 32 位无空白 ASCII，不是模型 Key。 |
| APP_SAFETY_INTERNAL_URL | 启动器设置 http://127.0.0.1:8010 | 内部安全服务地址，由统一启动器设置，无须手填。 |
| CLOUD_MODE | false | 本机手机模式保持 false；云部署需单独规划，不能和手机令牌模式同时开启。 |
| APP_TRUSTED_PROXY_CIDRS | 空 | 可信反向代理网段；本机使用保持空。 |
| CLOUD_COOKIE_SECURE | true | 云模式安全 Cookie 开关，本机不要为解决连接问题随意改它。 |
| CLOUD_MAX_VIDEOS | 20 | 云模式视频总数量上限，至少 1。 |
| CLOUD_VIDEOS_PER_VISITOR | 3 | 云模式每访客视频数量上限，至少 1。 |

## 5. elder-safety/.env 与 .env.phone

你已经执行过 configure_phone.py；有效的随机令牌无需重新生成。
.env.phone 只需保留已有 APP_LAN_TOKEN。App 填写的是这一令牌，而不是 API Key 或内部摄像头令牌。
该文件的令牌与 runtime/手机连接参数.txt 内容都不能上传或公开。

内部安全服务模板各项如下。统一手机入口日常只需保留已生成的身份和默认设置。

| 变量 | 模板值 | 配置方法 |
| --- | --- | --- |
| SAFETY_USER_TOKEN | 空 | 脚本生成的内部用户随机令牌，保留本机现有值。 |
| SAFETY_DEVICE_TOKENS | 空 | JSON：设备 ID 到令牌的映射，脚本已生成；用户与各设备令牌须互不相同，至少 24 字符。 |
| SAFETY_DEVICE_ID | camera_001 | 摄像头/AI 服务设备 ID，与映射中的键保持一致。 |
| SAFETY_DEVICE_TOKEN | 空 | 对应设备的令牌，与 SAFETY_DEVICE_TOKENS 中同 ID 的值一致。 |
| SAFETY_DATABASE_URL | sqlite:///./runtime/safety.db | 内部 SQLite 连接串，相对 ./ 按 elder-safety 目录解析。 |
| SAFETY_DATA_DIR | runtime | 内部数据目录，相对 elder-safety 目录。 |
| SAFETY_REVIEW_MODE | mock | 独立旧安全服务复核模式。统一入口关闭旧 worker，不要为上传视频另开 qwen worker。 |
| SAFETY_PUSH_MODE | mock | 独立旧推送模式。当前统一推送用根配置的 APP_* 插件，勿为它改此项为 fcm。 |
| SAFETY_QWEN_MODEL | qwen3-vl-plus | 旧安全服务独立复核模型；不是根 .env 的 VLM_REVIEW_MODEL。日常保留。 |
| SAFETY_QWEN_BASE_URL | https://dashscope.aliyuncs.com/compatible-mode/v1 | 旧安全服务模型接口；保留。 |
| DASHSCOPE_API_KEY | 空 | 旧独立复核的密钥。统一入口分析上传视频时应在根 .env 配 VLM_API_KEY，无须在这里重复。 |
| SAFETY_ALLOW_CLOUD_IMAGES | false | 旧独立复核上传图片开关，统一入口使用保留 false。 |
| SAFETY_FIREBASE_CREDENTIALS | 空 | 旧独立推送的凭据文件路径。统一入口使用保持空，不是 APP_FCM_CREDENTIALS。 |
| SAFETY_ESCALATION_SECONDS | 120 | 内部告警升级等待秒数，正数。 |
| SAFETY_OFFLINE_SECONDS | 60 | 设备离线判定秒数，正数。 |
| SAFETY_DEDUP_SECONDS | 30 | 去重时间秒数，非负。 |
| SAFETY_EVIDENCE_TTL_SECONDS | 86400 | 证据保留秒数，正数。 |
| SAFETY_RETRY_SECONDS | 5 | 旧 worker 重试等待秒数，非负。 |
| SAFETY_REVIEW_INTERVAL_SECONDS | 5 | 旧 worker 轮询间隔秒数，非负。 |
| SAFETY_REQUEST_TIMEOUT_SECONDS | 20 | 旧请求超时秒数，正数。 |
| SAFETY_MAX_ATTEMPTS | 3 | 旧任务最大尝试数，正数。 |
| RTSP_URL | 空 | 只在连接真实网络摄像头时填厂家确认的 RTSP 地址，可能含账号密码。上传手机视频不需要填。 |
| POSE_WEIGHTS | weights/yolo11n-pose.pt | 保留模板。当前混合检测实际按 SAFETY_MODEL_CONFIG 的 models.pose_weights 读取。 |
| FIRE_WEIGHTS | weights/luminous-fire-yolov8n.pt | 保留模板。当前混合检测实际按 JSON 的 models.flame_weights 读取。 |

不要自行猜测摄像头路径；RTSP 地址只在本地填写。上面的内部令牌不要填入手机 App。

## 6. 可选：真实后台推送（不是视频分析的前置条件）

~~~dotenv
APP_ALERT_PUSH_HANDLER=backend.push_notifications:publish_alert
APP_FCM_ENABLED=true
APP_FCM_CREDENTIALS='G:/team-private/firebase-service-account.json'
APP_FCM_ORIGIN='http://电脑当前局域网IP:8000'
~~~

最后两行是填写格式示意，不能直接使用占位文字。凭据 JSON 放在仓库外，且应与 App 的 Firebase 项目一致。
APP_FCM_ORIGIN 必须与手机配置的服务根地址一致，不能加 /api、凭据、查询串或路径。
需另外安装 backend/push_notifications/requirements-fcm.txt，完成 App 的原生 Firebase 配置、通知权限和真机收发验证。
只设置这四个变量不等于手机后台推送已经正常。没有配置时保持 handler 空、APP_FCM_ENABLED=false 即可。
完整接口约定另见同目录 A_INTEGRATION.md。

## 7. 配好后先做离线检查，再重启服务

~~~powershell
Set-Location 'G:\比赛\AI黑客松\GitHub同步\website'
.\.venv\Scripts\python.exe scripts/doctor_team.py
~~~

完整 doctor 使用 .env 中的 SAFETY_PYTHON 加载本地姿态、火焰权重并跑合成黑帧；不调用云模型、不连接摄像头。
检查 detector_import_and_weights=true、synthetic_forward_passed=true，且使用 GPU 时 pose_device/flame_device 为 cuda 设备。
通过后最终报告应有 passed=true。报告保存于 runtime/team-doctor.json。
--backend-only 跳过检测模型，本轮此检查已通过，但不能代替完整 GPU 检测验证。
cloud_configured/cloud_enabled 只表示 Key 存在和开关状态，不证明 API Key、接口、模型或额度有效。

实际启动前，在原服务窗口按 Ctrl+C，等待旧服务停止。然后：

~~~powershell
Set-Location 'G:\比赛\AI黑客松\GitHub同步\website'
.\start-phone.ps1 -Address '10.181.59.60'
~~~

10.181.59.60 是此前使用的电脑地址，必须仍属于当前电脑；换网络后先用 ipconfig 查当前 IPv4 再替换。
手机与电脑只需网络互通，可同 Wi-Fi 或电脑热点。手机连接统一 8000 端口，不填内部 8010。
地址和 APP_LAN_TOKEN 没变时，后端重启通常不需要重新填写手机连接信息。
用一个短视频做实际端到端试用需你自行确认联网与费用；本轮没有调用付费模型。

## 8. GitHub 上传边界

GitHub 用于共享代码、无密钥模板和说明，不用于同步真实 .env。
当前 .gitignore 已忽略 .env、.env.phone、elder-safety/.env、runtime/ 等本地状态。
不要使用 git add -f 绕过忽略规则，也不要把真实 .env 改个名称再上传。

可以上传本说明和 phone-local.env.example。C 未改 A 的 .env.example；若团队想合并主模板，应交由 A 对齐。
GitHub Actions/服务器需要的密钥，通过相应 Secret/部署环境变量分别配置；队友各自本地填写，不能靠拉取仓库获得你的真实密钥。
模型权重的共享情况按已有资源清单处理，本轮只生成本机路径配置，不新增源码 ZIP。

上传前在 website 目录检查：

~~~powershell
git check-ignore .env .env.phone elder-safety/.env runtime/team-camera.json
git ls-files -- .env .env.phone elder-safety/.env runtime/team-camera.json
git status --short
~~~

第一条应显示这四个路径被忽略，第二条应无输出。只选择确认无凭据的文档和代码提交。
本文无密钥模板与说明随同步提交；真实配置仅保存在本机。如果真实 Key 以前已进 Git 历史，仅删除当前文件不够，应先吊销/更换该 Key，再处理历史。

## 9. 初次配置检查记录（2026-11-04，历史记录）

- 已确认现有 GPU 环境的 Python、torch、CUDA、显卡信息及缺失的三个小依赖。
- 已运行 prepare_team.py --device 0：资源文件大小与 SHA256 校验通过，配置成功生成。
- 已运行 doctor_team.py --backend-only：全部后端检查通过，ffmpeg/ffprobe 可执行，本地健康接口、首页、摄像头状态接口均返回 200。
- 未修改你的真实 .env、.env.phone 或内部令牌；未安装三个 GPU 小依赖；未完成检测模型完整前向验证。
- 未连接摄像头、未进行真实模型 API 请求、未验证付费账号和模型 ID、未执行 GitHub 上传。

源码依据：backend/config.py、backend/providers/qwen.py、backend/hybrid_safety.py、scripts/prepare_team.py、scripts/doctor_team.py、scripts/configure_phone.py、scripts/phone_server.py、elder-safety/backend/safety_backend/config.py、backend/push_notifications/publisher.py。

2026-11-05 后续验证和源码修复已完成，历史段落中的“未安装依赖/未完成前向验证”不代表当前状态。详见 TARGETED_DETECTION_FIX.md 与 TARGETED_DETECTION_VALIDATION.json；本轮同步不调用真实模型 API。
