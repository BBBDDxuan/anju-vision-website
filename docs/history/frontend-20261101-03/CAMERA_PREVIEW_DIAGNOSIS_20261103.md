# 摄像头预览帧率与断连排查（2026-11-03）

## 现场边界

本轮本机 8000、8010 没有监听服务，也没有运行中的 Python 摄像头进程。web-ui 中未找到真实 .env、摄像头 GPU 解释器与模型配置。因此没有启动实际摄像头、录像、模型推理或云端复核；本轮不能唯一归因用户之前的真实黑屏。

## 源码确认的问题

- 原 LiveCameraPanel 每 66 ms 轮询一次，理想预览上限约 15.15 FPS。状态 JSON 和 JPEG 在同一个 fetching 锁里串行请求，无单次超时。JPEG 请求如果一直未完成，后续状态轮询也会停止。
- backend/live_camera.py 的 status() 向 worker 发送 ping；elder-safety/scripts/web-camera-worker.py 的 watchdog 超过 30 秒未收到 ping 会停止。故上述请求阻塞存在导致 worker 停止的代码路径，但尚未证明是用户那次事故原因。
- 每次状态或取图失败会清除预览，避免把旧画面当作实时画面。
- worker 处理一次姿态推理才输出一张 JPEG，循环有 50 ms 预算，即约 20 FPS 上限。实际值还取决于输入帧率、采集解码、推理和传输。
- project/elder-safety/ai-service/config.camera-gpu.json 当前 models.device 为 cpu，不能因文件名含 gpu 就认为使用 GPU。这不是当前运行配置的证据，未擅自切换设备。

## 已实施（仅 web-ui 前端）

- 状态/保活独立每 1 秒读取；JPEG 按 50 ms 预算连续请求，两个通道互不等待，单通道不叠加请求。
- GET 请求连同响应体读取设置 5 秒超时，异常后自动重试。图片先解码再替换，解码设置 3 秒截止。
- 恢复窗口焦点或可见性时立即更新状态；保留原有关闭工作台约 30 秒停止的规则。浏览器长时间挂起或网络彻底中断仍可触发原有停机机制，本轮未扩大后台监测时限。
- 通过版本标记忽略停止/状态切换前发出的延迟响应；卸载时中止请求、清理定时器和 Object URL。
- 不改变检测阈值、录像、模型配置或后端。源位置为 frontend/src/LiveCameraPanel.tsx。

## 验证

- npm run build 通过；最新入口资源为 index-CtN7aXjQ.js。
- node --test frontend/tests/*.test.mjs：31/31 通过，其中新增 5 项虚拟时间测试，覆盖 45 秒卡帧时持续保活、20 FPS 请求预算、状态请求超时恢复、停止期间延迟 JPEG、解码失败与清理。
- Playwright 真实 Chromium + 明确 MOCK 图像/接口：4 秒测得约 17.75 次/秒预览请求；状态约 1 Hz；卡帧超时仍保活、恢复图像、断线清旧图、手机无横向溢出、停止不被旧响应覆盖等 10 项检查通过。脚本 review/camera-polling-check.js。
- 17.75 是本机模拟接口的前端刷新测量，不是实际摄像头输出帧率或模型 FPS。
- 新版本源码与 dist 已留在 web-ui；未同步或推送 GitHub。

## 实机复测时应区分

1. 网页提示“状态更新中断”：检查本机 HTTP 服务/状态请求，而非立即判定摄像头断线。
2. 状态为 running 但预览报超时：优先检查 JPEG 请求和浏览器链路。
3. 状态为 reconnecting 或 stale：查看采集/RTSP 链路；frame_age_seconds 反映后端收到预览的时间，不是摄像头曝光时间。
4. error / camera_worker_stopped：确认 worker 退出和保活中断；当前代码吞掉原生诊断以免泄露 RTSP 凭据，历史事故不能从缺失日志反推。
5. 姿态 FPS 很低：核实运行环境实际加载的 models.device、inference_ms、decoded_frame_age_ms，再决定 GPU/采集/编码优化。

没有连接真实设备，故不能宣称长期稳定性已验证或保证提高到 20/30 FPS。
