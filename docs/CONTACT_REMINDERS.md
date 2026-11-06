# 联系人提醒与主备升级

统一手机入口由 AlertCenter 维护提醒阶段，C 的异步 publisher 负责按联系人角色读取已登记 token 并发送。继续复用现有 /api/v1/preferences、get_preferences()、contact_tokens() 和 push-token 登记接口，不建立第二套联系人注册表。统一启动器内的 legacy safety worker 继续关闭。

## 用户行为与时间语义

1. 来源适配器新建的 confirmed、unacknowledged 正式告警创建 primary 任务，只提醒偏好中 primary 角色的有效 Android/iOS token。一个角色可以有多个安装；mock 平台不参与。
2. 只有 live_camera / uploaded_recording 中 severity 为 critical、high 或兼容值 urgent，且 event_type 为 suspected_fall、fire_detected、smoke_detected、help_requested 的重要事件，才有 backup 阶段。当前来源适配器实际投影跌倒、明火、求助；本规则不会把烟雾候选自动提升为正式告警。
3. 起点是**新告警第一次创建 primary 任务的持久化时间**，不是 provider 接受时间，也不是每次重试时间。首次启用的派发规划读取 preferences.escalation_seconds（默认 120 秒，合法范围 5–86400 秒），冻结 backup_due_at = primary_scheduled_at + escalation_seconds。此时偏好需要包含 backup 角色。首次派发晚于创建时间时，已经过去的时间仍计入期限。
4. 到期前不派发 backup；到期后即使 primary 持续失败，也可独立派发 backup。primary 的失败仅重试 primary token，不会退回双角色 fan-out。两个阶段分别沿用 2、4、8…256 秒退避；**重试不重新等待 escalation_seconds，也不修改原备用截止时间**。主联系人从未登记 token 或角色缺失时，不会立即绕过期限发给备用联系人。
5. 主要联系人确认使用现有 POST /api/alerts/{id}/ack。任意手机调用该 ack 或 resolve，或来源同步回传 acknowledged/resolved，都会持久化取消所有 pending/sending 阶段及后续重试。ack 幂等，resolved 不会降回 acknowledged。已交给 provider 的请求无法撤回；已经在途的请求可能仍被接受，但其晚到回执不会重新启动提醒。
6. 来源被撤回、review 失去 confirmed 时，两阶段待办都取消。重要性降级或事件类型离开上述范围时，尚未发送的 backup 取消。后续来源标题、证据、优先级等更新保留已冻结的阶段状态与时间，不重新入队 sent/cancelled 阶段。

notifications_enabled=false 时派发器不领取任务，publisher 也再次检查总开关。已有任务暂停，不伪造接受；重新启用时按原创建时间继续，已经到期的备用任务可在主联系人首次尝试之后立即领取。角色与 token 每次发送重新读取：删除角色或没有有效 token 会报告未接受并保留重试，恢复登记后可继续；规划时不存在 backup 角色的告警标记 not_required，之后新增角色不会补发这些告警。APP_FCM_ENABLED=false 或缺少凭据也不会产生成功回执；配置恢复不会重置原期限。

## 给 UI 和自定义 handler 的契约

本次不新增端点、不要求来源适配器增加字段；直接使用现有 source、severity、event_type、review_status、handling_status。缺少重要性字段时仅规划 primary，不猜测为 critical。critical 与 high 的到期规则相同，不增加第二种延迟。

| 现有端点 | 本次阶段行为 |
| --- | --- |
| GET /api/alerts | 每条新告警可附带 contact_escalation，分页和原字段不变 |
| GET /api/alerts/{id} | 返回同一份阶段快照；contact_stage 只在 handler 调用中出现 |
| GET /api/alerts/changes | 阶段规划、接受、失败、取消使用原 revision/cursor 机制同步 |
| POST /api/alerts/{id}/ack | 任意手机确认，取消两个角色全部待办/重试 |
| POST /api/alerts/{id}/resolve | 取消两个角色全部待办/重试，并标记 resolved |
| GET/PUT /api/v1/preferences | 复用 notifications_enabled、escalation_seconds、contacts；已规划期限不重置 |
| POST /api/v1/push-tokens、DELETE /api/v1/push-tokens/{token_id} | 原登记/解绑与 primary/backup 角色不变 |

async handler(alert, *, idempotency_key) 的签名不变，idempotency_key 始终等于原 alert.id。每次调用额外带上 alert.contact_stage，值为 primary 或 backup；不能用它再同时枚举两个角色。旧签名的 handler 无需新增参数，但要遵守这个字段才能实现角色隔离。C publisher 未收到 contact_stage 的兼容调用默认为 primary。FCM data 也包含 contact_stage，alert_id/event_id 与原统一 ID 保持一致，通知 tag 仍使用 alert ID。

新告警的详情、列表及 changes 流包含可选的 contact_escalation 对象；迁移前和历史告警可能没有该对象，UI 需要允许缺省：

```json
{
  "contact_escalation": {
    "primary_scheduled_at": "2026-11-05T08:00:00+00:00",
    "primary_status": "accepted_by_provider",
    "backup_due_at": "2026-11-05T08:02:00+00:00",
    "backup_status": "pending"
  }
}
```

时间为 UTC ISO 8601。primary_status 为 pending / retrying / accepted_by_provider / cancelled；backup_status 另有 not_scheduled（尚未规划）和 not_required（无需升级或规划时无备用角色）。backup_due_at 未规划或无需升级时为 null；取消、重试或成功不会擦除已冻结的期限。

notification_status 保留既有枚举；任一阶段已被 provider 接受时，总体状态保留 accepted_by_provider，另一阶段仍可能 pending/retrying，请结合阶段状态展示。**provider 接受不等于手机已送达，不等于联系人已查看或确认**；确认和处理仍以 handling_status 为准。没有真实 token、通知关闭、mock 回执、账号失败、部分 provider 拒绝都不能返回接受成功。handler 的正常回执仍需 accepted=true 且 is_mock=false。

## 持久化、去重与保守迁移

- 既有 outbox 继续承载 primary，新增 scheduled_at（nullable）和 escalation_planned（默认 0）列。新增 contact_escalations 表承载独立 backup 的 first_due、due、attempts、state。first_due 是固定期限，due 是当前重试或租约到期时间。alert JSON 中的阶段快照随 revision 一起持久化。
- 使用 SQLite 写事务串行化阶段规划、领取、取消和状态更新，包括多个 AlertCenter 实例。sending 租约为 30 秒，进程退出后到期可重新领取；重启不重建计时或重新发送已完成阶段。派发周期和前一个在途请求可能使实际发送晚于截止时间，但不会提前发送。
- 迁移只增列、增表，不重建、删除或重新入队原有记录。既有行 scheduled_at 为 NULL，继续保留原 primary 状态和重试，不新增 backup。historical/skipped_history 记录从不创建提醒，即使之后来源内容更新也是如此。本次验证只在临时目录创建合成数据库，没有对真实运行数据执行迁移。
- provider 接受账本仍只存 token 的 SHA256 和时间。角色键为 alert_id:primary / alert_id:backup，避免一个阶段的完成吞掉不同设备的另一个阶段；同时保留原 alert ID 的逐 token 接受记录，兼容旧账本并防止同一 token 跨角色或变更角色后重复通知。相同设备出现在两个角色时，仅需要一次 provider 接受；另一个阶段复用这个事实，不再次请求 FCM。
- 队列租约和账本不能提供网络层 exactly-once：provider 接受后、写入本地回执前若进程退出，重试仍可能重复请求。沿用 alert ID 通知 tag 和客户端去重；不把这一边界描述为真实手机去重或送达验证。

## 离线验证

新增 tests/test_contact_escalation.py 使用可控时钟、临时 SQLite、合成 token 和离线 transport，覆盖期限、首次延迟派发、主联系人失败、独立重试、主/备用租约恢复、重启、ack/resolve、来源撤回/降级、历史回灌、旧 schema 迁移、来源更新、角色过滤、通知关闭、FCM 关闭、重复 token、部分接受和自定义 async handler 签名。

在仓库根目录以 PowerShell 执行：

```powershell
& '.venv/Scripts/python.exe' -m pytest tests/test_contact_escalation.py tests/test_alert_center.py tests/push_notifications tests/test_alert_evidence.py tests/test_phone_access.py tests/test_camera_monitoring.py -q --basetemp=C:/Users/Administrator/AppData/Local/Temp/codex-contact-reminders-final-20261105 --tb=short
```

使用独立 ASCII 临时目录是因为现有 OpenCV JPEG 测试在 Windows 中文路径下会失败；本轮 G 盘临时目录的 mkdir 还出现长时间阻塞，因此最终验证仅将合成测试数据放在系统临时目录。无需安装新依赖，不使用付费接口、真实 FCM、摄像头或真实运行数据库；真机前台、后台、锁屏送达不属于这些离线测试的验证范围。
