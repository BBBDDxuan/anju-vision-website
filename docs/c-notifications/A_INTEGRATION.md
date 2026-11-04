# 给 A：启用独立推送插件

正式后端基线为 website d272b8737ddd59a33e66b79ef120e60bf6d2be6b。独立插件在 website/backend/push_notifications，App 不保留后端副本。A 已有可插拔分发点，C 未修改 phone_app.py、alert_center.py、配置模板、数据库模型或 A 依赖文件。

从 website 仓库根目录使用原有 Python 环境安装可选依赖：

~~~powershell
python -m pip install -r backend/push_notifications/requirements-fcm.txt
~~~

仅真实启用时，在启动 A 后端的同一个进程环境设置：

~~~powershell
$env:APP_ALERT_PUSH_HANDLER = 'backend.push_notifications:publish_alert'
$env:APP_FCM_ENABLED = 'true'
$env:APP_FCM_CREDENTIALS = 'G:\team-private\firebase-service-account.json'
$env:APP_FCM_ORIGIN = 'http://192.168.1.2:8000'
~~~

地址替换为 B 在两手机配置的同一规范化根地址，不能加 /api、凭据或末尾斜杠。服务账号应属于客户端 google-services.json 对应项目，授予必要 FCM 发送权限。示例不是实际凭据；本次已在 G 盘环境安装可选 google-auth，并用临时生成密钥和模拟 HTTP 验证签名；未配置真实账号或发送实际推送。不要将 JSON 放进仓库，不记录/输出 token、私钥或完整 provider 错误。

保持 A 已有启动方式与认证，参考 website 的 docs/a-backend/PHONE_LOCAL_SETUP.md。不要为 C 启动第二个公网安全服务；8010 仍是内部服务。统一告警 ID 为 a_ 加 40 位小写十六进制，两类来源均使用 /api/alerts 的详情和确认。

插件签名为 async publish_alert(alert: dict, *, idempotency_key: str) -> dict。只有 confirmed + unacknowledged 且非 skipped_history/cancelled 允许发送。A 持有事件 outbox、退避、确认/撤销取消；C 持有每设备接受回执并返回 accepted/is_mock/recipient_count。插件总预算 8 秒，每个发送 4 秒，低于 A 的 10 秒上限；取消传播。

复用 /api/v1/push-tokens 和 preferences 的 primary、backup 联系人。每个联系人的多个安装均保留，mock 平台不参与。无真实设备/未启用/账号失败/部分拒绝均不会返回虚假成功。数据保存在 DATA_DIR/alert-center/push-acceptance.sqlite3，只存 token SHA256，不存原 token。已有 token 登记仍由 A 数据库持有。

FCM data 使用 anju_type=unified_alert（message_type 是保留键，不能使用）、alert_id、event_id、source、household、scope。通知不含画面、证据 URL、住户姓名或 bearer。后台消息有 300 秒 TTL，tag 为统一 ID。iOS 的 APNs payload 仅为兼容准备，不是已完成 iOS 发布或送达验证。

离线回归命令见 BUILD_AND_TEST.md。实际接收、网络可达及 provider 权限必须用真设备另验。
