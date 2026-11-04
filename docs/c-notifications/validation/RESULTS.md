# C 上传前后端验证（2026-11-04）

基线：website d272b8737ddd59a33e66b79ef120e60bf6d2be6b。

实际执行：

```powershell
python -m pytest tests/push_notifications tests/test_alert_center.py -q --basetemp=<英文可写临时目录> --junitxml=docs/c-notifications/validation/backend-junit.xml
```

59 passed，1 个 Starlette TestClient 弃用警告，15.15 秒，退出码 0。测试包括 A 的真实告警中心/outbox 接点、双合成设备、确认取消、部分发送重试、OAuth 离线签名。XML 为本次原始测试记录。

插件从 App 临时后端布局迁至 website 后，独立模块中的 safety_backend 导入路径改为 elder-safety/backend；没有修改 A 的公共文件。

未连接真实 FCM、未发送真实通知、未启动摄像头/模型、未进行真机送达验收。
