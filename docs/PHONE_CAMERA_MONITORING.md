# 手机开启后端摄像头监护（2026-11-05）

适用于更新后的 website 与 App 源码。电脑运行共享后端，手机通过摄像头配置启动同一个后端监护进程。电脑不需要打开网页，手机不需要一直显示预览。

## 启动与使用

1. 在 website 按首次运行指南安装环境、生成手机令牌，并配置根 .env 的检测环境、模型配置及 Qwen 复核。保留 elder-safety/.env 中的 SAFETY_DEVICE_ID、SAFETY_DEVICE_TOKEN 和内部服务配置。
2. 启动共享服务：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\start-phone.ps1 -Address '电脑当前局域网IPv4'
   ```

3. 使用更新后的 App 源码构建并安装。在 App 设置中填写电脑的 8000 地址和统一访问令牌。
4. 进入“局域网摄像头”，填写摄像头私有 IPv4、RTSP 端口、本地设备账号、密码与视频路径，点击“连接摄像头并开启后端监护”。
5. 等待后端状态从“正在启动模型”变为“正在识别”。启动响应只证明进程已启动；摄像头鉴权、模型加载和取帧结果以后续状态为准。首次模型加载可能需要等待。
6. 小模型产生候选后交给现有 Qwen 复核，确认事件进入统一告警中心。前台 App 按现有刷新周期获取告警；后台与锁屏系统通知仍需双方配置 FCM 并实机验收。

电脑必须能访问摄像头，手机必须能访问电脑。手机直接预览还需要手机能访问摄像头。手机预览失败不自动停止后端，检查下方后端状态。

## 生命周期与共享

- 一台后端同时管理一个实时摄像头，多个手机共享它；同一配置重复连接不重启。
- “断开画面”、切换页面、App 进入后台或关闭电脑网页，只关闭客户端预览，后端监护继续。
- “停止后端监护”会影响所有连接这台后端的手机，App 会先提示确认。电脑网页的停止按钮也会停止同一进程。
- 后端退出时停止摄像头进程；后端重启后不会自动恢复监护。App 点击“启动已保存的后端监护”恢复。
- 新提交的连接优先于 elder-safety/.env 的 RTSP_URL；手机未提交过配置时仍使用原 .env。新配置存于本地 runtime/camera-connection.json，包含设备凭据，已被 Git 忽略。不要上传或分享该文件。
- 手机端连接接口先检查电脑到摄像头 TCP 端口是否可达；端口不通或输入无效时保留现有监护。端口可达不保证设备密码、路径或视频编码正确。

## 接口契约

原 /api/camera/* 仍只供电脑本机预览与操作，手机使用下列接口，携带同一 Authorization: Bearer 令牌。

| 方法与路径 | 用途 |
| --- | --- |
| GET /api/monitoring/camera | 当前状态，无设备密码或完整 RTSP |
| POST /api/monitoring/camera | 提交连接并启动，JSON 字段 host、port、username、password、path |
| POST /api/monitoring/camera/start | 启动已保存的连接或原本机 .env |
| POST /api/monitoring/camera/stop | 停止共享后端监护，保留配置 |

状态沿用 state、frames、fps、events、review_enabled、review_error，并增加 lifetime=backend、monitoring（进程存活）、configured（已保存手机配置）、camera（仅 host/port）。monitoring=true 不代表已经取到实时画面；应同时检查 state=running 和 frame_age_seconds。/api/connection 的 camera_control 为 authenticated_shared_backend。

只接受 RFC1918 私有 IPv4，端口 1–65535；手机密码在 POST 请求体内传输，不进入 URL 或命令行。局域网 HTTP 应在可信网络使用，跨公网部署需另行配置 HTTPS。

常见错误：CAMERA_UNREACHABLE 检查电脑到摄像头的网络与端口；CAMERA_ALERT_RELAY_MISSING 检查设备身份；CAMERA_RUNTIME_MISSING 检查 GPU Python 与摄像头 .env；SAFETY_REVIEW_DISABLED 等错误检查根 .env 的复核配置。网页预览仍为本机接口。

## 验证范围

自动化覆盖鉴权、输入校验、配置保存与回滚、共享启停、独立保活、App 接口与页面生命周期，并回归统一告警。真实 RTSP、云复核、两部手机与锁屏通知需现场验收；仓库旧版 APK 不代表本次源码，必须重新构建安装。

## 本次源码验证

2026-11-05：监护接口、摄像头进程、手机鉴权、统一告警和推送模块相关后端测试共 94 项通过；Python 修改文件语法检查通过。前端生产构建通过，38 项前端测试通过。App 全套测试 98 项通过、4 项按外部服务前提跳过，静态检查退出码 0（两条既有弃用提示）。本次不构建 APK，由接手同学构建；真实设备与通知送达验收范围见上文。

## 2026-11-05 本地认证检查修复

手机提交连接及恢复尚未正常取流的监护前，先以 FFprobe 检查 RTSP 认证和有效视频轨道。原有正常监护的重复启动保持幂等。错误码 CAMERA_AUTH_FAILED 表示摄像头认证或权限未通过（HTTP 422），不是手机访问后端的 Bearer 令牌失败；CAMERA_STREAM_NOT_FOUND 表示视频路径不存在；CAMERA_STREAM_TIMEOUT 表示取流超时；CAMERA_STREAM_UNAVAILABLE 表示未取得有效视频；CAMERA_PROBE_MISSING 表示后端 FFprobe 工具不可用。检查失败不会覆盖原配置或停止原监护，工具原始输出不进入 HTTP 响应。现有视频画面和录像状态仍需独立检查。
