# C 独立推送插件

基于 A 的 website d272b8737ddd59a33e66b79ef120e60bf6d2be6b，分支 codex/c-notifications-release。仅新增 backend/push_notifications/、tests/push_notifications/ 与本目录；未修改 A 的公共入口、路由、数据模型或依赖清单。

插件使用 A 的 APP_ALERT_PUSH_HANDLER 异步接点、现有 preferences 和联系人多 token 表。C 持久化逐 token 哈希的 provider 接受回执，部分失败只重试未接受设备；A 继续维护事件 outbox、重试和确认撤销。插件返回接受不代表真实手机送达。

配置见 [A_INTEGRATION.md](A_INTEGRATION.md)，离线结果见 [validation/RESULTS.md](validation/RESULTS.md)。真实 Firebase 服务账号、客户端配置、签名和手机测试未配置/未执行。旧 token API 仅支持本安装本地登记轮换清理，不宣称服务器已具有强制安装所有权隔离。

App 源码和 B/C 接线说明在 anju-vision-app 的同名 C 分支。本次不上传源码 ZIP 或凭据。
