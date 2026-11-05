# A → B/C 手机联调接口契约

版本：`2026-11-04.v1`。实现入口：`backend.phone_app:create_app`。单家庭 `default`，同一令牌的手机共享录像和处理状态；本次不提供多家庭账号隔离。

## 地址、版本和权限

使用 `start-phone.bat`，手机只配置 `http://电脑局域网IP:8000` 和 `.env.phone` 中的 `APP_LAN_TOKEN`。所有远程 API、证据和播放请求携带：

```http
Authorization: Bearer <在本机填写，样例不提供真实值>
```

内部 8010 及其设备、用户令牌不向手机公开。旧 App 两组设置均填相同的 8000 地址和统一令牌。旧 APK 只获得原接口兼容，不自动获得新的统一列表和通知。

后端唯一开发仓库为 `BBBDDxuan/anju-vision-website`；App 仓库只维护客户端。此次 A 分支为 `codex/a-unified-backend`，基于网站提交 `5127d7b`，保留其摄像头与可移植路径修复。发布版本用 `git rev-parse HEAD` 记录。本文件是唯一联调契约，B/C 的确认和真机验收另行记录。

## 接口清单

| 方法与路径 | 作用 | 实现状态 |
| --- | --- | --- |
| GET `/api/connection` | 契约版本、模型配置就绪、来源同步、推送配置 | A 新增 |
| GET `/api/alerts?limit=100&offset=0` | 两类来源的统一列表，可用 `source=live_camera` 或 `uploaded_recording` | A 新增，B 待接 |
| GET `/api/alerts/changes?after=0&limit=100` | 按 revision 返回变更快照 | A 新增，B/C 共用一条数据流 |
| GET `/api/alerts/{id}` | 按全局 ID 获取当前详情 | A 新增 |
| POST `/api/alerts/{id}/ack` | 幂等确认，无请求体 | A 新增 |
| POST `/api/alerts/{id}/resolve` | 幂等标记处理完毕，无请求体 | A 新增 |
| GET `/api/alerts/{id}/evidence/{number}` | JPEG 快照，编号从 0 开始 | A 新增，需认证 |
| GET `/api/videos`、`/api/videos/{video_id}` | 录像库、录像及最近任务 | 复用 |
| POST `/api/videos` | multipart 字段 `file` 上传，不自动分析 | 复用 |
| POST `/api/videos/upload` | 原始流上传，文件名用 `X-Video-Name` | 复用 |
| POST `/api/uploads` | JSON `filename,size` 创建分片上传 | 复用 |
| POST `/api/uploads/{id}/chunks?offset=字节位置` | 原始分片 | 复用 |
| POST `/api/uploads/{id}/complete`、`/cancel` | 完成/取消上传 | 复用 |
| POST `/api/videos/{id}/analyze?mode=safety` | 安全扫描；`all` 同时处理物品，`objects` 只处理物品 | 复用，返回 202 和任务 |
| POST `/api/videos/{id}/pause`、`/resume` | 暂停/继续现有任务 | 复用 |
| GET `/api/jobs/{job_id}` | 任务进度、状态、错误、预算信息 | 复用 |
| GET `/media/videos/{video_id}` | 录像播放，支持原有媒体行为 | 复用，需认证 |
| GET `/api/v1/devices`、`/api/v1/devices/{id}/status` | 设备旧接口 | A 的白名单兼容代理 |
| GET `/api/v1/events`、`/api/v1/events/{id}` | 实摄来源旧数据，可能含未复核候选 | 仅兼容，不能用于正式危险通知 |
| POST `/api/v1/push-tokens`、DELETE `/api/v1/push-tokens/{id}` | 手机登记/解绑自己的推送 token | 复用，C 负责客户端接入 |
| GET/PUT `/api/v1/preferences` | 通知总开关和联系人设置 | 复用，C 模块需读取并遵守 |

设备注册、心跳、事件写入、证据上传不经手机代理开放。手机 `/api/camera/*` 的控制限制保留；2026-11-05 新增受统一令牌保护的 `/api/monitoring/camera`，用于共享后端监护的连接、状态、启动和停止，详见 [监护契约](PHONE_CAMERA_MONITORING.md)；不得通过 `X-Forwarded-For` 等头绕过。原录像逐条提醒接口保留，但正式手机告警以统一接口为准。

## 告警字段及样例

以下是契约样例，不是真实识别记录。`id` 为跨来源稳定哈希 ID；录像 key 包含 `video_id + run_id + 原提醒ID`，重分析的新 run 是独立告警。不要使用标题、数组下标、片内时间或原始 source ID 去重。

```json
{
  "id": "a_1111111111111111111111111111111111111111",
  "source": "live_camera",
  "source_key": "example-event",
  "source_event_id": "example-event",
  "camera_id": "camera_001",
  "video_id": null,
  "event_type": "suspected_fall",
  "title": "疑似跌倒",
  "severity": "critical",
  "occurred_at": "2026-11-04T03:00:00+00:00",
  "video_offset_ms": null,
  "review_status": "confirmed",
  "review_model": "configured-review-model",
  "review_reason": "示例视觉依据，非真实验收结果",
  "handling_status": "unacknowledged",
  "notification_status": "pending",
  "evidence": [{"url": "/api/alerts/a_1111111111111111111111111111111111111111/evidence/0", "media_type": "image/jpeg"}],
  "evidence_status": "available",
  "playback_url": null,
  "created_at": "2026-11-04T03:00:01+00:00",
  "updated_at": "2026-11-04T03:00:01+00:00",
  "revision": 12
}
```

录像记录使用同一字段结构，关键差异如下；另有自己的 `id/source_key/source_event_id`：

```json
{
  "source": "uploaded_recording",
  "camera_id": null,
  "video_id": "v_example",
  "run_id": "j_example",
  "occurred_at": null,
  "video_offset_ms": 1200,
  "video_end_ms": 2200,
  "evidence": [{"url": "/api/alerts/a_2222222222222222222222222222222222222222/evidence/0", "media_type": "image/jpeg", "video_offset_ms": 1200}],
  "playback_url": "/media/videos/v_example"
}
```

墙上时刻用带时区 ISO 8601，服务端统一 UTC，手机可按本地时区显示。录像拍摄墙上时刻未知，不能拿上传时间冒充；使用片内偏移跳转播放。当前事件类型为 `suspected_fall / fire_detected / help_requested`，视觉确认不等于诊断了实际受伤、真实火灾或求救意图。

| 字段 | 取值及客户端行为 |
| --- | --- |
| `review_status` | `confirmed` 可进入正式流程；`withdrawn` 表示来源删除、重分析切换或有效证据记录被移除，保留历史，不再提醒 |
| `handling_status` | `unacknowledged / acknowledged / resolved`；resolved 不被后续 ack 降级 |
| `notification_status` | `skipped_history / pending / retrying / cancelled / accepted_by_provider`，不是手机送达回执 |
| `evidence_status` | `available / pending_or_unavailable`；事件先到、截图稍后关联是允许的 |
| `revision` | 中心全局递增修订号，同一 ID 只采用较大的 revision |

候选、rejected、uncertain、unavailable 和 mock 记录不会进入中心。来源读取失败时只报告未就绪，不撤销已有记录；只有成功完整扫描后才核对撤销。撤销与用户确认独立。已经开始的外部发送无法撤回，C/客户端仍须在详情刷新状态。

图片和播放 URL 相对统一根地址解析，均带令牌；不返回电脑磁盘路径。中心复制快照，使来源过期/新任务不会悄悄换掉旧图片。快照跟随中心历史保留，不沿用临时来源图的过期删除；当前无自动历史清理策略。

## 分页、增量及多手机同步

列表响应为 `{alerts,total,offset,has_more,snapshot_cursor}`，按首次入库顺序从新到旧排列。limit 为 1～200，不按客户端认为的数组位置做身份识别。

1. 首次同步请求列表并记住**第一页**的 `snapshot_cursor=S`，逐页加载，用 ID 合并。
2. 从 `/api/alerts/changes?after=S` 补齐列表加载期间的变化；按 `next_cursor` 继续至 `has_more=false`。整个初始化阶段仅建立缓存，不批量响铃。
3. 持久化最后的 `next_cursor`。进入正常同步后从这个游标继续；同一 ID 的处理状态、证据、推送状态变化是更新，不重复提醒。
4. App 暂停/重连时从已保存游标继续；服务器中心重建或游标失效时重新初始化。HTTP 成功后再前移游标，不能因失败跳过。
5. POST ack/resolve 后立即应用返回对象，再继续同步；另一部手机通过 changes 获得相同状态。旧缓存只能在 revision 较大时覆盖。

changes 响应为 `{alerts,next_cursor,server_cursor,has_more}`；同一告警可能出现多次，每项是该次修改后的完整快照。来源采集约每秒一次，不承诺硬实时。B 持有一个数据流，将新告警回调交给 C；C 不另建第二套轮询。

## A → C 异步推送模块

C 的独立实现范围为 `backend/push_notifications/**` 与 `tests/push_notifications/**`，公共入口、认证和依赖模板仍由 A 整合。下述函数是当前可测试的接点，尚未等同于双方确认的逐设备最终接口。

C 在独立模块实现以下接口，通过根 `.env` 配置 `APP_ALERT_PUSH_HANDLER=包.模块:函数`，重启生效。A 不修改 Android 或编写 Firebase 发送模块。

```python
async def publish_alert(alert: dict, *, idempotency_key: str) -> dict:
    # C: preferences, recipients, credentials, per-device dedup and retries.
    # Return only after the real provider accepts all intended recipient sends.
    return {"accepted": True, "is_mock": False}
```

- 调用发生在 SQLite 告警/待发送任务提交之后，独立后台任务执行，不阻塞识别。首次导入来源历史不入发送队列。
- A 维护事件级持久化 outbox；失败、异常、超时或不合格回执会重试，退避最高 300 秒。单次插件超时 10 秒，故 C 的 HTTP/SDK 调用必须有自己的超时并支持取消，不能在 async 函数中阻塞事件循环。
- `idempotency_key` 恒为统一告警 ID。当前实现提供事件级队列与整体接受回执，A/C 后续需冻结逐设备成功、失败及可重试分类，由 A 保留任务主调度；C 的模块不得启动第二套业务轮询。当前接口不能宣称已满足逐设备调度约定。
- `accepted=true` 必须同时返回 `is_mock=false`。mock、无收件人或尚未接受的发送不得返回成功。A 的 `accepted_by_provider` 仅表示模块报告提供商接受；真机前台、后台、锁屏送达分别验收。
- 用户确认或来源撤销后取消待发送重试；已发出的网络请求无法撤销。初始历史永久 `skipped_history`；插件晚接入时，其余未确认的新告警仍会从持久队列发送。
- 现有兼容 `/api/v1/push-tokens` 按 token 登记并支持同一联系人多个 token，尚无完整安装实例所有权；不能据此宣称更新/解绑仅影响本安装实例的机制已完成。C 提供安装实例、token 轮换和逐设备结果契约，A 负责接入公共路由/数据层。
- 现有 repository 支持同一联系人多个 token。C 可在独立模块访问 `safety_backend.repository.Repository` 和 `contact_tokens`，遵守通知 preferences；不用重新搭一套公共入口。模块只返回无密钥的接收结果，凭据留在本机配置。
- C 给 B 的详情路由参数使用统一 `alert.id`，服务端详情再给出来源、录像 ID 和片内位置。通知点击在打开和冷启动均由 B/C 联调。

## 无敏感值联调命令

PowerShell 在本机读取令牌，不把值复制进文档：

```powershell
$serverBase = 'http://电脑当前局域网IPv4:8000'
$phoneToken = Read-Host '输入本机统一访问令牌' -MaskInput
$headers = @{ Authorization = "Bearer $phoneToken" }
Invoke-RestMethod "$serverBase/api/connection" -Headers $headers
$page = Invoke-RestMethod "$serverBase/api/alerts?limit=100" -Headers $headers
$page.alerts | Select-Object id,source,review_status,handling_status
# 确需确认某条真实告警时，先核对该 ID：
# Invoke-RestMethod "$serverBase/api/alerts/<id>/ack" -Method Post -Headers $headers
```

令牌输入方式适用于 PowerShell 7；Windows PowerShell 5.1 可从本机参数文件复制到临时变量，不要在共享屏幕或日志输出它。错误分类和网络步骤见 [PHONE_LOCAL_SETUP.md](a-backend/PHONE_LOCAL_SETUP.md)。
