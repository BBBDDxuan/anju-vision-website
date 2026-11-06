# 跌倒模块交付（2026-11-06）

本次以主分支 `4b7ae59a5f9f626576dddd632ba0c33c75ed4573` 为底座，合入跌倒候选、时序补证、恢复站姿校验与同事件去重。保留最新明火/求助复核、人脸关联、监护控制、报警证据选帧和前端设计。

## 队友更新

在仓库根目录执行。已有安装先在后端窗口按 Ctrl+C 停止服务，然后更新；不需要复制本机的虚拟环境、实验目录或数据库。

```powershell
git pull --ff-only origin main
.\elder-safety\.venv-gpu\Scripts\python.exe scripts\prepare_team.py --device auto
.\.venv\Scripts\python.exe scripts\prepare_face_models.py
.\.venv\Scripts\python.exe scripts\doctor_team.py
.\start.bat
```

首次安装仍按 README 执行 `setup.bat`。`prepare_team.py` 会校验交付资产并重新生成 `runtime/team-camera.json`；有自定义配置时先备份。根 `.env` 的 `SAFETY_MODEL_CONFIG` 应指向 `runtime/team-camera.json`，令牌和 API Key 继续由各自本机配置，不随 Git 分发。未配置网络复核时不会将本地候选直接当作确认报警。

默认姿态模型为官方预训练 **YOLO11s Pose**，不是本次微调产物。权重 `elder-safety/weights/yolo11s-pose.pt` 为 20,373,161 字节，SHA256 为 `1060bda4a27012060eca246f9b2adeea22eabb045a1e58f8d229be29b7ebc2ba`；来源和许可记录在 `elder-safety/weights/manifest.json`，完整性校验记录在 `repro-assets.json`。n 权重保留。明火仍使用队友的 `indoor-dfire-yolov8n.pt`。

## 接口与行为

- 保持手机报警字段 `event_id/device_id/event_type/severity/confidence/occurred_at/evidence`，上传录像动作仍为 `fall`，安全事件类型仍为 `suspected_fall`。
- 保留上传扫描 `flame_scan=normal|small_flame`。检测引擎每帧只处理一次，火焰和求助分支继续工作。
- 本地候选覆盖站立倒下、坐姿滑落、起身失稳、缓慢下降及下肢遮挡时的肩髋下降。严格独立检测器默认不启用扩展复核候选。
- 坐姿等早期候选自适应补证，最多五秒后情、十二张真实图片；仅已获云端确认的同一跌倒事件跳过重复复核和报警。`duplicate` 为附加候选状态。
- Qwen 仅在请求内部使用 `F01`～`F12`。严格校验后恢复原始证据 ID，再进入业务接口；保存映射用于审计。复核协议更新为 `local-candidates-qwen-review-v3`，旧任务不能静默复用不同协议的结果。
- 恢复站姿要求至少一个可靠、在画面内且低于对应髋部的脚点，防止倒置姿态被误当作恢复后再次报警。

## 验证与边界

本轮集成验证包括后端与接口测试、检测器测试、前端测试和生产构建；CUDA 双模型加载/空白帧推理、健康接口、页面与摄像头状态接口自检通过。原 S05 前倾跌倒视频重新走正式 GPU 扫描和模拟复核，保留 2.6 秒初始候选与 3.6 秒补证，得到一次确认、一次去重、仅一次模拟请求。测试不连接摄像头，不新增真实云端调用。

最新版组合结果：后端 734 项通过、2 项可选 Google OAuth 测试因未安装对应依赖跳过；检测器 730 项通过；前端 42 项通过，TypeScript 与 Vite 生产构建通过。网页和手机接口相关检查包含在后端测试中，不替代真实手机通知验收。

此前 CAUCAFall 100 段开发集结果为视频级 50 TP、50 TN；数据已参与开发，不代表独立泛化准确率。本次未重跑付费全量复核，也没有将模拟响应计为真实识别结果。真实摄像头、手机通知送达和端到端报警延迟仍需现场联调。

本机交付证据和原工作区备份保留于忽略的 `runtime/integration/20261106-170548/`，原实验数据、视频与凭据未提交。早期接入和修复文档保留为历史记录，运行路径、版本和集成状态以本说明为准。
