# A 交接：统一后端与告警中心

日期：2026-11-04。对应 [三人联调分工](../三人联调分工_20261104.md) 的 A 范围。统一后端只在 website 仓库维护，网页与 App 使用同一个电脑服务；两类真实告警、两部真机和 C 的真实推送仍未验收。

## 启动与基线

在 `anju-vision-website/` 执行：

```powershell
.\start-phone.ps1 -Address '10.181.205.172'
```

该地址是当日本机 WLAN 检查值；换网络后以当前 IPv4 为准。手机访问 `http://10.181.205.172:8000`，令牌在启动后生成的 `runtime/手机连接参数.txt`；旧 App 的两组设置填相同地址、相同统一令牌。电脑网页仍为 `http://127.0.0.1:8000/`。内部安全服务只监听 `127.0.0.1:8010`，手机不再需要第二个端口或内部令牌。

本次网站基线为 `5127d7b`，A 分支为 `codex/a-unified-backend`，保留基线的摄像头兼容、可移植配置和录像修复。App 仓库误放后端的 `32d4c60` 已通过回退提交 `e85049d` 撤销，原 APK 和发布文件保留，未改写历史。当前实际网站版本以 `git rev-parse HEAD` 为准。

Python、Flutter、Gradle 业务目录保持原样，没有引入 `server/` 副本。唯一接口主文档为 `docs/api-contract-abc.md`；A 文档为 `docs/a-backend/`，C 独立模块与 B/C App 文件没有被修改。

## 已完成的 A 工作

| 工作 | 结果 |
| --- | --- |
| 统一根地址/令牌 | 8000 对手机提供原录像能力、新告警接口与安全兼容代理 |
| 内部权限隔离 | 内部 8010 只监听 loopback；手机无设备上报权限；原摄像头本机控制限制保留 |
| 两类来源 | 实摄确认事件，以及现有录像混合任务确认结果，统一投影入库 |
| 来源有效性 | 匹配视觉复核、事件类型、真实分析分支；过滤 mock、候选及非确认结果 |
| 持久化 | `data/alert-center/alerts.sqlite3` 保留稳定 ID、来源、时间、状态、修订与发送队列 |
| 证据 | 复核选定的实摄截图经原 durable relay 上传；中心保留 JPEG 快照和录像偏移 |
| 多手机状态 | ack/resolve 幂等；resolved 不降级；中心状态不被旧来源刷新覆盖，并重试来源写回 |
| 重分析一致性 | 录像 proof、原提醒、帧路径及 ID 绑定同一个 run；旧详情确认不会误操作新 run |
| 来源撤销 | 成功扫描发现记录失效后标记 withdrawn，保留历史并取消待发送通知；不冒充人工确认 |
| 增量同步 | 全局 revision、分页列表和 changes 游标，供 B/C 共用数据流 |
| A→C 接点 | 独立异步插件、持久化 outbox、超时和失败重试；未配置插件不发送推送 |
| 联调说明 | 接口样例、认证、相对媒体 URL、首次同步规则及错误分类已写入契约 |

新启动器关闭旧安全 worker 的复核/通知/升级/离线监测任务，防止它绕过正式视觉确认流程。旧 API 和数据仍存在；正式手机通知必须接统一中心。当前推送配置为可插拔接口，不是已完成的 FCM 服务。

## 修改文件

- `backend/phone_app.py`：统一应用、接口、内部白名单代理、生命周期、通知分发接点。
- `backend/main.py`、`backend/config.py`、`backend/lan_access.py`：公共请求的可选手机令牌和鉴权；原电脑与云模式仍分开。
- `backend/alert_center.py`：SQLite 投影、变化日志、处理状态、快照、outbox。
- `backend/alert_sources.py`：现有实摄与录像来源适配，不启动模型或摄像头。
- `backend/db.py`：原提醒读取/确认可显式绑定 run，保留旧调用兼容。
- `scripts/phone_server.py`：公共/内部监听分离、统一连接参数及 `--check` 启动检查。
- `elder-safety/ai-service/ai_service/preview_alerts.py`、`elder-safety/scripts/web-camera-worker.py`：传递复核选定截图，复用原上传和关联队列。
- `tests/test_alert_center.py`、`elder-safety/ai-service/tests/test_reviewed_preview_evidence.py`：来源、状态、认证、证据及异步模块契约测试。
- `tests/test_activity.py`、`tests/test_continuous_analysis.py`：静态筛选和计费测试关闭无关混合检测，避免假 provider 依赖真实 GPU 或本机模型路径；生产检测流程未变。
- 配置模板、README、手机连接说明、接口契约及根架构说明同步更新。

本次没有修改 Flutter 页面、Android 原生配置、模型权重、训练、检测阈值和真实私有配置。

### 必要来源接点登记

| 文件 | 改动目的 | 归属与边界 |
| --- | --- | --- |
| `elder-safety/scripts/web-camera-worker.py` | 将复核已经引用的 JPEG 传给既有 relay | A 来源适配；保留队友的设备配置、可移植路径、候选及阈值逻辑 |
| `elder-safety/ai-service/ai_service/preview_alerts.py` | 将上述 JPEG 复制进既有 spool，沿用上传与事件关联 | A 来源适配；不改检测模型、算法、权重或阈值 |
| `elder-safety/ai-service/tests/test_reviewed_preview_evidence.py` | 合成图像验证截图选择、上传和关联 | A 接点回归，非真实摄像头验收 |

这是既有 A 实现接点的补充登记，尚不代表其他成员已确认。C 负责的 `backend/push_notifications/**`、`tests/push_notifications/**` 未被占用。

## 验证结果与界限

| 验证 | 结果 | 覆盖范围 |
| --- | --- | --- |
| website 完整 `test.ps1` | 1003 passed：后端 405、安全 22、detector 576 | 最新网站基线与 A 整合后的回归，使用测试夹具；不代表真实识别质量 |
| 前端回归 | 29 passed | 原页面和摄像头请求/状态回归，未修改前端源码或构建 |
| 隔离双进程启动 | 8 项检查通过，退出后子进程停止 | 临时数据和临时端口，HTTP 鉴权、代理、来源配置状态、内部 loopback、远程摄像头拒绝 |
| 两个模拟手机客户端 | 通过 | 同一录像可见、认证、证据与幂等确认共享；不是两部物理设备 |
| JPEG relay | 通过 | 合成 JPEG → 持久 spool → 实际 API 上传/关联 → 中心快照，非真实摄像头 |
| 插件重试 | 通过 | 本地测试回执，过滤 mock/缺字段/未接受；失败不丢记录，确认和撤销取消后续重试 |

website 首轮回归有两条旧计费测试因混合检测依赖本机模型路径而失败；将对应计费夹具与无关 GPU 检测隔离后，重新完整运行得到上述 1003 通过。前端首次运行缺少 worktree 的 Node 依赖，执行 `npm ci` 后 29 项通过。各 Python 测试集有现有 Starlette TestClient 弃用警告，不影响通过。

启动检查请求了电脑自己的 WLAN 地址，只证明监听/鉴权和本机网络栈行为；没有验证手机穿过 Windows 防火墙或校园网隔离。临时检查进程已停止，没有启动生产后端或接入真实摄像头，也没有调用付费模型或发送真实推送。公开验证摘要见 [verification.json](verification.json)。

## B/C 现在可以并行做什么

1. B 按 [api-contract-abc.md](../api-contract-abc.md) 合并连接设置，正式列表切至 `/api/alerts`，实现初次分页/changes 同步和按统一 ID 的详情入口，媒体附带认证。
2. C 提供独立 async 推送模块、逐设备回执及安装实例/token 轮换契约，A 接入公共路由与任务主调度；与 B 共用新告警数据流及详情路由。当前整体回执和旧 token 登记不代表这部分已完成。
3. A 按 [PHONE_LOCAL_SETUP.md](PHONE_LOCAL_SETUP.md) 用实际局域网地址启动；只有真机无法访问且确认需要时，配置限定 8000/同子网的防火墙规则。本次没有改防火墙。

## 必须补做的真实验收

- [ ] B/C 对本契约确认并记录三个实际代码版本，完成 App 构建。
- [ ] 两部真手机带同一统一令牌连接成功，正确区分认证和网络失败。
- [ ] 手机 A 上传真实测试录像、手动启动分析，手机 B 读取相同任务和授权结果。
- [ ] 本地小模型和现有 Qwen 完成真实录像确认事件，保留对应证据与片内位置。
- [ ] 电脑开启真实摄像头 AI，产生视觉确认记录与截图，两部手机看到同一条告警。
- [ ] 手机 A 确认，手机 B 同步；重开 App、断网重连与重复刷新不重复响铃。
- [ ] C 的真实推送模块接入，两台设备实际接收；前台、后台、锁屏分别记录结果。
- [ ] 点击通知在 App 打开和冷启动均跳正确详情，认证证据正常加载。
- [ ] 新机器从最终 GitHub 版本按安装文档重建，记录模型、媒体工具及 APK 对应版本。

这些项目未被模拟测试替代。当前没有两条真实验收告警，也没有真实手机送达记录；不能据此宣布三人联调已全部完成。
