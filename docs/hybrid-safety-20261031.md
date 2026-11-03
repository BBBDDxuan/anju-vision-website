# 视频与实时摄像头统一安全检测（2026-10-31）

## 实现范围

安全检测限定为 **疑似跌倒、举手求助、明火**。没有修改三个小模型/时序规则的阈值，没有训练新权重。

- 上传视频：原片完整逐帧小模型扫描 → 为候选收集前后证据 → Qwen 逐项复核 → 确认的视觉候选进入事件/提醒；物品寻迹仍只走 Qwen。
- 实时摄像头：相同的 `SafetyCandidateCollector` / `DetectionEngine` / 配置 → 本地候选证据 → 网页后端中的相同 `HybridSafety.review` → 复核确认后才进入通知 outbox。
- 两条链路共用 `backend/hybrid_safety.py` 的复核提示词、输出校验、模型设置和费用/每日请求账本。
- 物品链路同时有提示词约束和归一化前的代码过滤，不能绕过小模型重新生成安全报警。

## 关键实现

| 文件（相对本项目根目录） | 职责 |
| --- | --- |
| `elder-safety/ai-service/ai_service/safety_candidates.py` | 小模型与时序规则、4fps证据采样、前2秒/后1秒窗口，最多12张真实帧 |
| `elder-safety/scripts/scan-safety-video.py` | 在独立AI环境逐帧扫描视频，不联网、不通知、不启动循环录像 |
| `backend/hybrid_safety.py` | 共用Qwen复核、证据引用校验、扫描进程及完整性检查 |
| `backend/jobs.py` | 视频分阶段运行、预算、暂停恢复、已付款复核缓存、物品事件过滤 |
| `backend/live_camera.py` | 有界后台复核队列、父子进程通信，网络等待不阻塞预览 |
| `elder-safety/scripts/web-camera-worker.py` | 小模型候选生成及确认后报警；不持有Qwen凭据 |
| `frontend/src/LiveCameraPanel.tsx` | 显示待复核、确认、排除、证据不足、复核不可用 |

## 配置与启动

根目录 `.env` 中继续使用已有的 `VLM_API_KEY`、`VLM_BASE_URL`、`VLM_ENABLE_NETWORK`、计费模式与预算；不要向组员传真实密钥。

新增公开配置示例：

```dotenv
HYBRID_SAFETY_ENABLED=true
SAFETY_PYTHON="G:/比赛/AI黑客松/elder-safety/.venv-gpu/Scripts/python.exe"
SAFETY_MODEL_CONFIG="G:/比赛/AI黑客松/elder-safety/ai-service/config.camera-gpu.json"
VLM_REVIEW_MODEL="qwen3.8-max"
```

- 默认开启统一链路；默认安全模型配置与此前实时摄像头相同。视频不把PyTorch装进网页环境，避免NumPy/OpenCV依赖冲突。
- `VLM_REVIEW_MODEL` 为真实调用的模型标识；保留项目当前的 `qwen3.8-max`。具体API供应方是否提供该型号、是否支持多图，尚未通过本次真实付费调用验证。若供应方返回不存在/不支持，明确显示复核不可用，不伪造成功。
- 如复核模型与物品模型不同且采用 `priced` 计费模式，还须配置复核模型价格与 `VLM_REVIEW_COST_CONFIRMED`；未确认价格则不调用。
- 实时复核最多1个并发，队列有界；日请求和费用与上传分析共用。队列满、预算耗尽、网络失败不会自动回退为小模型确认报警。
- 实时单次会话还按每请求最多12帧、1024输出token保守限制请求数。默认输出预算8192时最多8个复核请求；用完需检查预算和状态，不能以重启绕过每日费用/请求上限。
- 重启网页后端、重建前端，并停止/重新开启旧摄像头worker，才能加载新代码。原视频和已有分析结果不会被自动改写；旧结果需要重新分析。

## 状态与证据边界

- `confirmed`：Qwen支持此视觉候选，不代表受伤、真实求救意图或现实危险已经确诊。
- `rejected`：未采用候选，不产生确认报警。
- `uncertain`：证据不足，保留记录但不发确认报警。
- `unavailable`：接口、配置、预算、队列或响应校验失败；不是“没有危险”。
- 只有两个以上不同时刻、来自本地真实帧且引用合法的证据，才可能确认。单帧不联网复核、不补造帧。
- 原片逐帧扫描不依赖物品寻迹的抽帧/静止过滤；实时仍保留最新帧策略和异步明火推理，负载与断流可能导致丢帧。因此是同一套判断规则，不承诺逐条报警时间与离线完全一致。
- 视频复核结果及候选保存在该任务的 `data/runs/<job>/safety*`；实时候选证据在 `elder-safety/runtime/safety-candidates/<session>`。云端只发送候选关键帧，不上传整段录像。普通物品寻迹仍按原功能向API发送其采样帧。
- 实时确认报警使用新的 `elder-safety/runtime/web-reviewed-alerts-v1` outbox；旧 `web-alerts` 队列保留原样，不会在新链路启动时把历史未复核候选当成新确认报警自动补发。
- 通知后端/手机FCM配置仍需单独就绪；后台接收成功不等于手机已经收到。
- 独立的旧版 `ai_service` CLI 和交接包 `team-tools/run_demo.py` 仍是原有的小模型诊断/离线工具，不宣称经过Qwen复核。正式统一链路指网页上传与网页实时摄像头。

## 本次验证边界

实际执行了模拟API/离线测试、前端检查及真实GPU本地模型短扫描，测试结果见本次运行日志。没有开启真实摄像头，没有向外部API上传家庭画面，没有执行真实付费Qwen调用，也没有验证手机实际收件或完整demo的识别准确率。


### 可复查的本次结果

- 安全服务完整测试集：438 passed（`elder-safety/runtime/pytest-safety-hybrid-verified-20261031.log`）。
- 上传任务统一链路新增测试：27 passed；见 `runtime/pytest-hybrid-focused-final-20261031-f.log` 及对应 JUnit 报告（包含其他适配器测试共43 passed）。
- 统一复核/摄像头控制器/Qwen适配器专项：43 passed（`elder-safety/runtime/pytest-hybrid-backend-final-20261031.log`）。
- 前端离线渲染：12 passed；`npm --prefix frontend run build` 成功，现有 `frontend/dist` 已更新。
- 实际CUDA权重扫描：`video/10月30日.mp4` 前60帧完成，保存在 `elder-safety/runtime/hybrid-real-model-smoke-20261031/scan.json`；`completed=false` 是限帧smoke的正确状态，不是完整视频/识别质量验收。
- 网页全量测试尚未验收通过；当前执行环境找不到FFprobe，已复现上传返回 `MEDIA_PROBE_FAILED`。未以修改媒体验证逻辑或伪造时间戳绕过。旧全量日志保留在 `elder-safety/runtime/pytest-web-hybrid-full-20261031.log`，环境补齐后须重跑。

运行上传视频前，请保证FFmpeg和FFprobe均可用，或在根目录 `.env` 正确设置 `FFMPEG_PATH` / `FFPROBE_PATH`。本次未下载新依赖、未修改真实凭据。若原启动脚本选择的旧Python环境失效，可以显式选用当前已验证的网页测试环境：

```powershell
& 'G:\比赛\AI黑客松\start.ps1' -Python 'G:\比赛\AI黑客松\elder-safety\.venv\Scripts\python.exe'
```

此启动命令不是FFprobe缺失的修复；媒体工具仍需就绪。Qwen真实调用和手机通知由后续真实联调分别验收。
