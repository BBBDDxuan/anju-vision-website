"""Prepare a source-only GitHub snapshot; never publish runtime data or credentials."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil

from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]


def main():
    target=ROOT/'deliverables'/('GitHub_AnjuVision_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    target.mkdir(parents=True,exist_ok=False)
    for name in ['backend','frontend/src','schemas','prompts','tests','examples']:
        shutil.copytree(ROOT/name,target/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.log','*.tsbuildinfo'))
    for name in ['requirements.txt','requirements-dev.txt','pyproject.toml','start.ps1','start.bat','.env.example','.gitignore','.dockerignore']:
        shutil.copy2(ROOT/name,target/name)
    for name in ['package.json','package-lock.json','index.html','tsconfig.json','vite.config.ts']:
        shutil.copy2(ROOT/'frontend'/name,target/'frontend'/name)
    (target/'scripts').mkdir()
    for name in ['cloud_entrypoint.py','import_demo.py','export_github.py']:
        shutil.copy2(ROOT/'scripts'/name,target/'scripts'/name)
    (target/'docs').mkdir()
    for name in ['ACTIVITY_FILTER.md','CONFIDENCE_DISPLAY.md','CONTINUOUS_ANALYSIS.md','ENVIRONMENT_ALERTS.md','RECORD_QA.md','MIXED_FALL_DEDUP.md']:
        shutil.copy2(ROOT/'docs'/name,target/'docs'/name)
    # The public source build starts with an empty library. Cloud demonstration
    # assets and their importer entrypoint are deliberately not Docker inputs.
    docker=(ROOT/'Dockerfile').read_text(encoding='utf8')
    docker=docker.replace('CLOUD_MODE=true CLOUD_COOKIE_SECURE=true','CLOUD_MODE=false CLOUD_COOKIE_SECURE=true')
    docker=docker.replace('DATA_DIR=/mnt/workspace/anju-data','DATA_DIR=/app/data')
    for line in ['COPY scripts/cloud_entrypoint.py scripts/import_demo.py ./scripts/','COPY demo-recording ./demo-recording','COPY demo-recordings ./demo-recordings','COPY demo-catalog.json ./demo-catalog.json']:
        docker=docker.replace(line+'\n','')
    docker=docker.replace('CMD ["python", "scripts/cloud_entrypoint.py"]','CMD ["python", "-m", "uvicorn", "backend.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "7860", "--no-proxy-headers"]')
    (target/'Dockerfile').write_text(docker,encoding='utf8')
    env=(target/'.env.example').read_text(encoding='utf8')
    env=re.sub(r'^VLM_BASE_URL=.*$', 'VLM_BASE_URL="https://your-provider.example/compatible-mode/v1"',env,flags=re.M)
    env=re.sub(r'^VLM_MODEL=.*$', 'VLM_MODEL="your-vision-model"',env,flags=re.M)
    env=re.sub(r'^VLM_REVIEW_MODEL=.*$', 'VLM_REVIEW_MODEL="your-vision-model"',env,flags=re.M)
    env=re.sub(r'^MAX_MODEL_CALLS_PER_DAY=.*$', 'MAX_MODEL_CALLS_PER_DAY=100',env,flags=re.M)
    (target/'.env.example').write_text(env,encoding='utf8')
    (target/'.gitattributes').write_text('* text=auto eol=lf\n*.bat text eol=crlf\n',encoding='utf8')
    (target/'README.md').write_text(README,encoding='utf8')
    (target/'docs/ARCHITECTURE.md').write_text(ARCHITECTURE,encoding='utf8')
    (target/'docs/DATA_SOURCES.md').write_text(DATA_SOURCES,encoding='utf8')
    (target/'docs/DEPLOYMENT.md').write_text(DEPLOYMENT,encoding='utf8')
    known=[v.encode() for k,v in dotenv_values(ROOT/'.env').items() if v and len(v)>=8 and any(word in k.upper() for word in ['KEY','TOKEN','PASSWORD','SECRET'])]
    credential=re.compile(rb'(?:github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_-]{25,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
    files=sorted(p for p in target.rglob('*') if p.is_file())
    hashes={}
    for path in files:
        relative=path.relative_to(target).as_posix()
        data=path.read_bytes()
        if path.name=='.env' or path.suffix.lower() in {'.mp4','.avi','.zip','.sqlite3','.pdf','.jpg','.png'}:
            raise RuntimeError('Unexpected non-source file: '+relative)
        if any(v in data for v in known) or credential.search(data):
            raise RuntimeError('Possible credential in source: '+relative)
        if len(data)>2*1024**2:
            raise RuntimeError('Unexpected large source: '+relative)
        hashes[relative]=hashlib.sha256(data).hexdigest()
    report={'directory':str(target),'files':len(files),'bytes':sum(p.stat().st_size for p in files),
            'credentials_scan_passed':True,'runtime_data_included':False,'videos_included':False,'sha256':hashes}
    (ROOT/'data/github-export.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='sha256'},ensure_ascii=False))


README='''# 安居视界 · Anju Vision

面向独居老人关怀与家庭安全的 AI 视频分析系统，支持人物行为与危险事件识别、物品踪迹追溯，以及带视频证据的自然语言问答。

**[在线体验 · 魔搭创空间](https://modelscope.cn/studios/BBBDDxuan/anju-vision)**

## 当前能力

- 上传单机位 MP4，校验媒体并按真实时间戳抽帧，调用兼容 Qwen 的视觉服务提取结构化事件。
- 人物、环境危险、物体事件分别展示；疑似跌倒、求助、长时间未出现、烟雾、明火和火花线索优先提醒。
- SQLite 保存事件、证据与任务进度，合并同一事件的重叠候选，区分直接观察、条件推断和未知。
- 问答支持人物候选数、录像概括、重点事件和物品线索；结论关联实际事件与视频证据。
- 长录像按窗口处理，支持本地画面变化筛查、暂停、恢复和批次自动接续。
- 云端大文件采用分片上传；内置案例可创建访客专属副本重新分析，原展示结果保留。

当前是**上传录像分析原型**，尚未接入实时摄像头、手机推送或具体身份识别。风险线索可能漏检或误判，不能替代消防探测器、实时照护或紧急呼救。

## 源码与在线演示

本仓库包含前后端源码、提示词、JSON Schema、模拟测试及配置模板。首次启动录像库为空，请上传自己的视频；不会将模拟测试数据作为实际识别结果。

在线创空间另行提供四个已保存的真实分析案例：火光/明火、白天模拟跌倒、低照度模拟跌倒及约6分34秒桌面录像。录像、模型响应、数据库、API Key和交付压缩包不进入GitHub；数据说明见[数据来源](docs/DATA_SOURCES.md)。

## 本地启动

建议使用 Python 3.13、Node.js 24，安装可用的 FFmpeg 和 FFprobe 并放入 PATH。模型通过远程API调用，无需本地GPU。

```powershell
git clone https://github.com/BBBDDxuan/anju-vision.git
cd anju-vision
py -3.13 -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
cd frontend
npm ci
npm run build
cd ..
.\\start.ps1
```

打开 http://127.0.0.1:8000 。Windows也可双击start.bat；Linux/macOS可用：

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m uvicorn backend.main:create_app --factory --host 127.0.0.1 --port 8000
```

## 模型配置

编辑本地.env，填写自己服务商的兼容端点、密钥与实际支持的视觉模型名称：

```dotenv
VLM_PROVIDER=qwen
VLM_BASE_URL=https://your-provider.example/compatible-mode/v1
VLM_API_KEY=your_key_here
VLM_MODEL=your-vision-model
VLM_REVIEW_MODEL=your-vision-model
VLM_ENABLE_NETWORK=true
VLM_BILLING_MODE=request_limited
MAX_MODEL_CALLS_PER_DAY=100
```

模板默认关闭模型网络访问。未配置有效模型时可打开界面，但不能进行真实AI分析。部署实例的服务商模型标识为qwen3.8-max；这不表示该标识可用于所有Qwen服务商，请按自己的账户配置。没有训练或微调模型权重。

MAX_UPLOAD_MB=0、MAX_VIDEO_SECONDS=0表示不设应用层大小与时长上限；实际仍受存储、媒体处理与平台资源限制，磁盘检查保留。CLOUD_STORAGE_MB=0取消固定演示存储配额。模型调用仍受每日上限约束，失败及重试也计数。完整24小时录像未做端到端实测。

开启“使用大模型解读已有记录”时，每个问题最多新增一次文本模型请求；人数统计、重点事件规则检索和缓存命中不新增调用。详见[记录问答](docs/RECORD_QA.md)。

## 验证

```powershell
.\\.venv\\Scripts\\python.exe -m pip install -r requirements-dev.txt
.\\.venv\\Scripts\\python.exe -m pytest tests/test_unlimited_upload.py tests/test_cloud.py tests/test_media.py -q
node --experimental-strip-types --test tests/confidence.test.mjs tests/event_presentation.test.mjs
npm --prefix frontend run build
```

自动化测试使用明确标识的合成视频和Mock适配器，不会调用付费模型，不代表真实识别准确率。真实样例验证和测试报告不是跨场景准确率评测。

## 项目结构

| 目录 | 内容 |
| --- | --- |
| backend | FastAPI、媒体处理、模型适配、SQLite、任务与问答 |
| frontend | React / TypeScript / Vite界面 |
| prompts | 事件提取、恢复和居家场景提示词 |
| schemas | 结构化记忆JSON Schema |
| tests | Mock与合成媒体测试 |
| docs | 当前架构、使用说明及部署边界 |

更多：[架构](docs/ARCHITECTURE.md) · [部署](docs/DEPLOYMENT.md) · [静止过滤](docs/ACTIVITY_FILTER.md) · [置信度展示](docs/CONFIDENCE_DISPLAY.md)

请勿提交.env、访问令牌、数据库、真实私人录像或含密钥压缩包。项目尚未指定开源许可证；第三方数据集许可不适用于本项目全部代码或用户录像。
'''

ARCHITECTURE='''# 当前架构

React/TypeScript前端通过FastAPI提交上传、分析、查询及证据请求；Python服务使用SQLite存储时序记忆和任务状态，远程视觉API承担事件理解。没有向量数据库、图数据库或重型Agent框架。

| 模块 | 实现 |
| --- | --- |
| API与会话 | backend/main.py、backend/cloud.py |
| 媒体与时间戳 | backend/media.py |
| 分片上传 | backend/uploads.py；云端超过16MiB时每片4MiB，按偏移合并校验 |
| 模型接口 | backend/providers/base.py、backend/providers/qwen.py |
| 提示词与结构校验 | backend/prompting.py、backend/events.py、schemas/memory.schema.json |
| 分窗与恢复 | backend/jobs.py |
| 静止片段筛查 | backend/activity.py |
| SQLite记忆与账本 | backend/db.py |
| 合并与问答 | backend/grouping.py、backend/answer.py、backend/record_qa.py |

上传视频→校验/准备回放→PTS抽帧→视觉模型分窗提取→Schema与证据校验→SQLite保存→事件展示/带引用问答。

推测不会升级成直接观察，人物ID是外观候选而非可靠身份。没有采样或没有事件不等于没有风险。云端要求单进程运行，访客上传、分析副本和分片会话按浏览器Cookie隔离；全站模型调用共用每日账本。公开示例原记录只读，新分析在独立副本中执行。
'''

DATA_SOURCES='''# 数据来源与仓库边界

仓库仅包含源码和明确标识的设计/测试样例；examples/design_fixture.json是设计夹具，tests生成合成媒体并使用Mock适配器，不是实际模型观察。

项目曾使用CAUCAFall v5的成年人模拟跌倒短片进行验证。作者：Jose Camilo Eraso、Elena Muñoz、Mariela Muñoz、Jesus Pinto。来源：https://data.mendeley.com/datasets/7w7fccy7ky/5 ，DOI：10.17632/7w7fccy7ky.5，许可CC BY 4.0。它们不是独居老人真实事故。视频文件未随本仓库发布，需要时请从原始来源按许可获取并保留署名。

创空间中的火光与桌面录像由作者指定并授权用于公开演示，未随GitHub源码分发。火光案例实际为受控打火机明火，不等同于已经验证电弧火花或真实火灾检测。

Toyota Smarthome/TSU受限样本、授权邮件、临时下载凭据、原始录像及分析记录均不包含在仓库中，不得把科研访问授权理解为公开再分发授权。
'''

DEPLOYMENT='''# 部署说明

仓库Dockerfile是无内置录像的源码构建，启动空录像库；它不依赖本机的演示包、数据库或密钥。公开创空间含独立准备的真实演示资源，直接克隆源码不会自动获得这些文件。

```bash
docker build -t anju-vision .
docker run --rm -p 127.0.0.1:7860:7860 --env-file .env -e DATA_DIR=/app/data -v anju-data:/app/data anju-vision
```

打开 http://127.0.0.1:7860 。构建需要外网下载基础镜像、Python和Node依赖；本次GitHub同步未另行验证Docker镜像构建。API Key只放运行环境或平台Secrets，不进入镜像和仓库。

公网托管时设置CLOUD_MODE=true、MAX_CONCURRENCY=1、MAX_MODEL_CALLS_PER_DAY=100，并将APP_ALLOWED_HOSTS与APP_ALLOWED_ORIGINS配置为实际的精确域名。HTTPS应保留CLOUD_COOKIE_SECURE=true；只有确认平台代理地址后才配置精确APP_TRUSTED_PROXY_CIDRS，不使用宽网段。

MAX_UPLOAD_MB=0、MAX_VIDEO_SECONDS=0取消应用层上限，CLOUD_STORAGE_MB=0取消固定配额，磁盘空间检查仍启用。魔搭单次请求实测限制32MiB；网页自动将超过16MiB的视频以4MiB分片发送，完成后统一校验和计算哈希。访客最多3条、全站最多20条录像的默认数量限制独立保留。

如需使用scripts/cloud_entrypoint.py的演示导入启动流程，需另外提供合法授权且经哈希验证的demo-recording资源；本仓库Dockerfile使用FastAPI直接启动，不会调用这一导入流程。部署到付费资源前自行确认费用；免费托管不代表模型API免费。
'''


if __name__=='__main__':
    main()
