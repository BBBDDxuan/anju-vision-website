# 安居视界：魔搭 Docker 部署

部署分支：`model/modelscope-deploy-20261107`，基于最新 main 建立，不会自动合并到 main。保留上传录像的小模型检测 → Qwen 视觉复核 → 证据回放流程。

## 1. 取得代码与部署目录

```powershell
git clone --branch model/modelscope-deploy-20261107 --single-branch https://github.com/BBBDDxuan/anju-vision-website.git anju-vision-modelscope
cd anju-vision-modelscope
```

根 Dockerfile 构建最新前端，并分别安装网页和检测依赖。不要使用旧 `scripts/export_modelscope.py`、`deploy_modelscope.py`、`scripts/cloud_entrypoint.py`：它们依赖当前仓库未包含的历史演示文件。本部署入口为 `deployment/start_modelscope.py`，不导入本机数据库和旧演示包。首页静态案例保留，服务器 `/api/demos` 初始没有历史分析记录。

创建页面若支持 Git 导入，请明确选择本分支；只填仓库地址可能导入 main。若不能选择分支，使用有 Git 的终端运行以下命令，导出可上传目录和 ZIP：

```powershell
python scripts/export_modelscope_source.py --output ..\anju-modelscope-source
```

输出必须是尚不存在的新目录。工具只导出已提交的源码白名单，附提交号和文件哈希，不带 `.git`、`.env`、账号数据库、运行录像、虚拟环境和 Windows 工具。上传后确保 Dockerfile 位于创空间根目录，不能多套一层文件夹。

也可克隆魔搭页面提供的创空间 Git 地址，在其独立 checkout 先拉取更新，再复制导出目录，检查 diff 后正常提交和 push。以平台实际默认分支为准，不 force push。

## 2. 创建创空间

打开 https://modelscope.cn/studios，创建程序化创空间，选择 **Docker**。按平台要求完成阿里云绑定和实名认证。首次部署可选择账号当前可用的免费 CPU 资源；使用收费资源前先确认费用。

本分支：https://github.com/BBBDDxuan/anju-vision-website/tree/model/modelscope-deploy-20261107

## 3. 环境变量和 Secrets

将 `deployment/modelscope.env.example` 中配置添加到平台变量。模型密钥只添加到平台 **Secrets**，不要上传本机 `.env`。

| 配置 | 说明 |
| --- | --- |
| `CLOUD_MODE=true` | 开启云端访客隔离 |
| `DATA_DIR=/mnt/workspace/anju-data` | 独立运行目录，不能使用家庭账号数据目录 |
| `MAX_CONCURRENCY=1` | 单进程、单并发 |
| `MAX_MODEL_CALLS_PER_DAY=100` | 可调低，入口拒绝超过 100；失败请求也受已有账本约束 |
| `VLM_BILLING_MODE=request_limited` | 按请求限额控制；免费创空间不代表 API 免费 |
| `VLM_API_KEY` | 放 Secrets，使用有权限、有额度的服务密钥 |
| `VLM_BASE_URL` | 与 Key 对应的 OpenAI 兼容服务基址，不是魔搭创空间管理 API |
| `VLM_MODEL`、`VLM_REVIEW_MODEL` | 服务实际支持的视觉模型。默认沿用仓库 `qwen3.8-max`，需要确认账号可用 |
| `VLM_ENABLE_NETWORK=true` | 配置好模型后开启。默认 false 不请求 Qwen，网页仍能启动 |
| `APP_ALLOWED_HOSTS` | 推荐填写实际应用域名，不是创空间仓库页面 URL。默认允许 `*.ms.show`，自动保留本地健康检查域名；不能填 `*` |
| `SAFETY_DEVICE=cpu` | 默认 CPU；CUDA 镜像和 GPU 就绪后改为 `0` |
| `FACE_RECOGNITION_ENABLED=false` | 保持现有云模式的人脸接口限制 |

`SAFETY_PYTHON` 已在镜像中指定。`SAFETY_MODEL_CONFIG` 每次启动根据仓库 `config.team.json` 生成于数据目录，只调整权重绝对路径、设备和关闭摄像头录像写入，不改检测阈值。当前模板使用 YOLO11s Pose + indoor-dfire，启动核对权重 SHA256。不要复制 Windows `.exe`、本机盘符路径或设置 `APP_LAN_TOKEN`。

## 4. CPU / CUDA

默认镜像从 PyTorch 官方 CPU 索引安装固定版本的 PyTorch/torchvision。仅选择 GPU 硬件不会把 CPU wheel 变成 CUDA wheel。

要在 GPU 创空间启用 CUDA 12.6，先把 Dockerfile 的默认构建参数改为：

```dockerfile
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126
```

提交并重新构建，分配兼容 GPU 后设置 `SAFETY_DEVICE=0`。本地构建也可用 `--build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126`。显式选择 0 时 CUDA 不可用会报错，不静默降为 CPU；`auto` 可显式自动选择。

仓库已有 Windows CPU/CUDA 候选差异记录，Linux CPU 不能视作原 NVIDIA 环境的质量等价替代。上线后分别测试求助、跌倒、明火正反例。常驻 worker 和预热性能优化不属于本次部署修改，视频仍沿用现有扫描子进程。

## 5. 本地 Docker 验证

需要运行中的 Linux Docker 引擎：

```powershell
docker build -t anju-vision-modelscope .
docker run --rm --name anju-modelscope -p 7860:7860 -e CLOUD_COOKIE_SECURE=false -v anju-modelscope-data:/mnt/workspace anju-vision-modelscope
```

访问 http://127.0.0.1:7860/。只有本地 HTTP 测试使用 `CLOUD_COOKIE_SECURE=false`，魔搭 HTTPS 保持 true。未带 Key、网络默认关闭时不调用 Qwen。

前后端同源监听 `0.0.0.0:7860`、单 worker。上传与分析数据保存到 `/mnt/workspace/anju-data`；平台迁移或重命名空间可能影响数据，重要记录另行备份。密钥不写入 Dockerfile 或构建参数。

## 6. 发布验收与排错

1. 构建成功，启动检查输出 `assets_verified=true`、`cloud_calls=0`。
2. 首页及 `/api/health` 正常、`cloud_mode=true`。未配置密钥时 `analysis_ready=false` 是明确配置状态，容器仍健康。
3. 配好模型和 Key、启用网络后，确认 `analysis_ready=true`。
4. 上传短视频，核对本地扫描、候选证据和 Qwen 复核；没有候选不应强行调用 Qwen。
5. 两个独立浏览器会话检查录像隔离；内嵌页 Cookie 受限时使用平台提供的独立应用链接。
6. 重启后核对数据保留；公开前检查资源费用、API 额度与数据来源。

GitHub workflow `ModelScope Docker` 在部署分支 push 时构建 Linux 镜像、检查容器 HTTP、运行合成视频本地扫描，不读取 Secrets、不调用 Qwen。CI 不替代真实平台、真实 Qwen 与持久化验收。

- **400 Host**：按日志将实际应用域名加入 `APP_ALLOWED_HOSTS`，从同源网页操作；不要允许所有域名或信任全部反向代理。
- **模型未就绪**：检查网络开关、Key、服务地址、模型权限及请求上限。
- **CUDA 不可用**：检查 cu126 镜像、GPU 硬件和驱动；只改设备变量不够。
- **旧 demo 缺失**：不要用历史 cloud_entrypoint；镜像 CMD 已使用新的独立入口。
- **摄像头 403**：现有云模式禁止本机摄像头操作，云端也不能直接打开访问者 USB 摄像头或访问家庭内网 RTSP。本次以录像上传分析为部署范围。

平台参考：https://github.com/modelscope/modelscope-skills/blob/main/skills/ms-studio-deploy/SKILL.md

PyTorch 安装：https://pytorch.org/get-started/locally/
