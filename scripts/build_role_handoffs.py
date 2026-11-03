"""Build two source-only teammate handoffs; never changes product source or runs builds."""
from __future__ import annotations
import ast
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
STAMP=datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
OUT=ROOT/'deliverables'/('role-handoffs-'+STAMP)
SKIP={'.git','.venv','.venv-gpu','node_modules','runtime','data','tools','weights','recordings','deliverables','dist','build','var','.tmp','tmp','__pycache__','.pytest_cache','.dart_tool','.gradle','.idea','.platform_scaffold','.cxx','captures','.agents','.codex','.aws'}
PRIVATE={'.env','google-services.json','GoogleService-Info.plist','firebase_options.dart','local.properties','key.properties','.flutter-plugins-dependencies','config.camera-gpu.json','config.local-models.json','tsconfig.tsbuildinfo','GeneratedPluginRegistrant.java'}
EXT={'.dart','.py','.md','.txt','.json','.toml','.yaml','.yml','.lock','.ps1','.bat','.sh','.js','.mjs','.ts','.tsx','.css','.html','.svg','.xml','.kt','.kts','.gradle','.properties','.java','.png','.jpg','.jpeg','.webp','.ico','.jar','.template'}
BINARY={'.png','.jpg','.jpeg','.webp','.ico','.jar'}
SECRETS=set()
def collect(value):
    if isinstance(value,dict):
        for v in value.values(): collect(v)
    elif isinstance(value,list):
        for v in value: collect(v)
    elif isinstance(value,str) and len(value)>=8 and not value.startswith('REPLACE_'): SECRETS.add(value)
for env in [ROOT/'.env',ROOT/'elder-safety/.env']:
    if env.is_file():
        for k,v in dotenv_values(env).items():
            if v and any(w in k.upper() for w in ('TOKEN','KEY','PASSWORD','CREDENTIAL','RTSP')):
                collect(v)
                try: collect(json.loads(v))
                except ValueError: pass

def sha(data): return hashlib.sha256(data).hexdigest()
def dump(path,obj): path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
def put(base,name,text):
    p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text.strip()+'\n',encoding='utf8')
def security(path,data):
    p=Path(path)
    if p.name in PRIVATE or (p.name.startswith('.env') and p.name!='.env.example') or p.suffix.lower() in {'.pem','.key','.pfx','.jks','.keystore','.sqlite','.sqlite3','.db','.mp4','.avi','.mkv','.pt','.apk','.zip','.rar','.log','.pyc'}:
        raise RuntimeError('Forbidden package entry: '+str(path))
    if any(v.encode('utf8') in data for v in SECRETS): raise RuntimeError('Configured credential detected in '+str(path))
    if p.suffix.lower() not in BINARY:
        t=data.decode('utf-8-sig')
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|sk-[A-Za-z0-9_-]{24,}|AIza[0-9A-Za-z_-]{30,}|AKIA[A-Z0-9]{16}|rtsp://[^\s/]+:[^\s/]+@',t):
            raise RuntimeError('Credential pattern detected in '+str(path))

def copy_file(base,rel,provenance,target=None):
    src=ROOT/rel
    if not src.is_file(): raise FileNotFoundError(rel)
    dst=base/(target or ('project/'+rel));data=src.read_bytes();security(dst.relative_to(base),data)
    dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(data)
    provenance[dst.relative_to(base).as_posix()]={'source':src.relative_to(ROOT).as_posix(),'sha256':sha(data)}

def copy_tree(base,rel,provenance,dist=False):
    for d,ds,fs in os.walk(ROOT/rel):
        ds[:]=sorted(x for x in ds if x not in (SKIP-{'dist'} if dist else SKIP) and not x.startswith('.venv'))
        for f in sorted(fs):
            p=Path(d)/f
            if p.is_symlink() or f in PRIVATE or f.endswith(('.tsbuildinfo','.iml')):continue
            if f.startswith('.env') and f!='.env.example':continue
            if p.suffix.lower() not in EXT and f not in {'.metadata','.gitignore','.dockerignore','Dockerfile','gradlew','gradlew.bat','.env.example'}:continue
            copy_file(base,p.relative_to(ROOT).as_posix(),provenance)

def routes(rel,prefix=''):
    result=[]
    for node in ast.walk(ast.parse((ROOT/rel).read_text('utf8'))):
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                if isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.func.attr in {'get','post','put','delete','patch'} and d.args and isinstance(d.args[0],ast.Constant) and isinstance(d.args[0].value,str):
                    result.append({'method':d.func.attr.upper(),'path':prefix+d.args[0].value,'source':rel,'line':node.lineno})
    return sorted(result,key=lambda x:(x['path'],x['method']))

COMMON='''# 当前成果与协作约定（2026-11-01，北京时间）

本次仅制作交接材料，没有替接手同学开发新功能、修复移动构建、创建 Firebase、发送推送或部署。

## 两套服务

| 服务 | 入口与端口 | 用途 |
| --- | --- | --- |
| 网页工作台 | 项目根目录 `backend/main.py`，8000；Vite 开发页5173 | 录像分析、物品追踪、实时摄像头预览与本地推理调度 |
| 手机通知后端 | `elder-safety/backend/safety_backend/main.py`，8010 | Bearer鉴权、设备/事件、证据、确认/解决、FCM、通知偏好 |

网页的 `/api/videos/...` 与手机的 `/api/v1/events/...` 是不同契约。手机不直接访问本机限定的摄像头接口。网页实时摄像头将复核通过事件送入通知后端，手机读取8010事件。录像安全结果不意味着自动接通手机通知。

当前视觉范围为跌倒、举手求助、可见明火；物品追踪直接使用Qwen。安全流程使用本地小模型和时序规则，随后用配置的Qwen模型逐项复核。`qwen3.8-max` 是当前项目配置值，11月1日已实际调用2次并成功返回；不推定接手人的账号必有相同模型权限。

87.233秒演示片：完整小模型扫描79.428秒；扫描加流式Qwen复核77.126秒，2次请求、15,012 tokens。检测位置是19.9秒求助、31.4秒跌倒。该次计时不含上传、物品追踪和完整网页任务持久化。证据见附件报告；本次打包未重跑测试/模型/API。

网页最近一次前端测试24/24通过，TypeScript/Vite构建成功，桌面浏览器检查无横向溢出；摄像头面板在1280×720窗口中从约1389px缩至661px，检测/存储折叠可用。以上是11月1日前序实测，不代表打包后在新电脑已经运行通过。

## 传递材料与合并

- 两包均是当前工作区文件快照，不以旧Git提交代表当前成果。不同角色的主编辑范围在各自交接文档说明。
- 先在各自副本开发，回交修改文件清单、启动/测试命令、截图或录屏、测试结果与未完成项。公共接口变动先与项目负责人协调。
- 本包未附真实`.env`、API Key、访问token、摄像头密码、Firebase配置/私钥、签名密钥、数据库、家庭录像、运行证据、模型权重、SDK、虚拟环境或依赖缓存。
- 手机端开发和网页美化可先用现有模拟数据或本地空数据页面。演示视频`10月30日.mp4`需项目负责人另行私下提供，不在这两包中复制。
- 解压后优先阅读包根目录新交接文档；`project/`里的原有README与旧日期文档可能记录历史状态，不能据此认定当前APK/推送已通过。
- 代码为项目组内部协作快照，不新增项目开源授权；原有依赖版本与声明保留。本包无模型权重。不要把内部配置或家庭录像提交公共仓库。

`manifest-sha256.json`覆盖包内所有其他文件；`source-provenance.json`记录源码来源与哈希。校验方法见`校验与范围.md`。
'''

MOBILE='''# 手机端 APP 开发交接 — 戈宇晗

日期：2026-11-01（北京时间）。原工作区：`G:\\比赛\\AI黑客松`。

## 目标、当前状态与边界

继续已有Flutter/Dart应用“安心相伴”：接收居家危险通知，查看设备/事件与证据，确认收到、标记解决。你负责手机端工程；本包没有代做你的开发任务。

已有`lib/`、`test/`、`android/`、`tool/`和锁文件。`pubspec.yaml`要求Dart>=3.8、Flutter>=3.32；Android applicationId为`com.eldersafety.elder_safety_mobile`。有首页/设备/历史/详情/设置、模拟数据、认证HTTP接口、前台轮询、FCM token生命周期、通知点击和Android通知桥接源码。

当前构建状态文件复核仍为`failed`（2026-10-30 02:25:28 +08:00），Firebase关闭、未真机测试、APK路径为空；交付目录未找到APK。本包没有APK。旧交接记录称失败在NDK安装，需接手后查看当前构建输出复现，不能把该旧原因视作新运行结果。旧记录有19项Flutter测试通过；本轮只做文件打包与完整性校验，没有重新运行Flutter测试或构建。

FCM项目、Google Services Gradle配置、真实服务端凭据、安装到手机、前台/后台/锁屏通知都仍需接手联调。当前Google Play服务可用是用户提供的信息，真实送达未验证。Android release仍使用debug签名，正式发布签名另行处理。

## 包内路径

- `project/elder-safety/mobile-app/`：主编辑范围，Flutter/Android源码与测试；有现成平台目录，不必重新`flutter create .`。
- `project/elder-safety/backend/`：8010通知后端源码、测试、依赖文件，供独立联调。
- `project/elder-safety/scripts/configure.py`、`start-backend.ps1`：现有配置生成/启动脚本。
- `project/elder-safety/docs/api-contract.md`与`openapi.json`：接口资料。静态OpenAPI是已有快照；运行中的8010 `/openapi.json`与当前源码优先。
- `integration-reference/`：网页实时复核/通知桥接的当前源文件，仅阅读理解上游，不是完整可运行网页工程，也不是你的主编辑范围。
- `资料/`：11月1日性能报告、两服务边界、静态接口路由清单、已复核构建状态。

## 首次运行与下一条命令

建议解压到G盘ASCII路径，如`G:\\AnjuHandoff\\AnjuVision_Mobile_20261101`。以下命令在包根目录运行；需自己已安装Python、Node、Flutter，并将工具/缓存保存在G/D盘。

```powershell
Set-Location .\\project\\elder-safety\\mobile-app
node .\\tool\\source_check.mjs
flutter --version
flutter pub get
flutter analyze --no-fatal-infos
flutter test
flutter run --dart-define=USE_FIREBASE=false
```

第一条`node`命令只做源码一致性检查，不下载SDK、不运行Dart测试。`flutter run`选择自己的已连接设备；默认模拟模式可先开发界面。

现有`tool/android-env.ps1`依赖G盘目录、`elder-safety/tools/android-build`工具树与R盘临时映射；本包不附这些工具。不要直接照搬原机R盘映射或`local.properties`。已有本机Flutter/Android SDK者可用标准Flutter命令，并自行设置`PUB_CACHE`、`GRADLE_USER_HOME`和SDK位置。旧环境与安装脚本作为参考保留，但本次未下载或执行。

需要本地通知后端时，从包根另开PowerShell：

```powershell
Set-Location .\\project\\elder-safety
py -3 -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install --cache-dir .\\runtime\\pip-cache -r .\\backend\\requirements-dev.txt
.\\.venv\\Scripts\\python.exe .\\scripts\\configure.py
.\\scripts\\start-backend.ps1
```

`configure.py`只在`.env`不存在时生成随机用户/设备token，默认复核和推送均mock，不打印token。手机设置中填用户token；设备token仅供设备上报，不放手机应用。不要将两个token混用。

默认后端仅监听127.0.0.1。真机局域网联调需在确认网络可达后由接手人选择`start-backend.ps1 -BindAddress 0.0.0.0`，手机填写`http://电脑局域网IP:8010`；手机里的127.0.0.1指手机自己。对外访问方案与认证由负责人协调，本次没有开放端口或修改防火墙。

## 关键接口

均来自8010；除GET `/health`外需`Authorization: Bearer <用户token>`。详见`资料/接口与数据约定.md`和原API文档。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/devices` | 设备列表/在线状态 |
| GET | `/api/v1/events?limit=100&offset=0` | 事件列表；支持类型、状态、时间过滤 |
| GET | `/api/v1/events/{event_id}` | 事件详情 |
| GET | `/api/v1/evidence/{evidence_id}` | 鉴权证据读取 |
| POST | `/api/v1/events/{event_id}/ack` | 确认收到 |
| POST | `/api/v1/events/{event_id}/resolve` | 标记解决 |
| POST / DELETE | `/api/v1/push-tokens` / `/api/v1/push-tokens/{token_id}` | 注册/撤销手机token |
| GET / PUT | `/api/v1/preferences` | 通知偏好与联系人角色 |

`review_status`有pending/completed/failed/unavailable；`notification_status`有pending/sent/failed/skipped；`state`有detected/notified/acknowledged/resolved。这些是通知后端字段，不与网页安全复核confirmed/rejected等枚举混用。当前客户端证据读取以图片为主，实时视频和短视频播放尚未验收。

## 建议接手顺序与回交

1. 先运行模拟模式，确认现有页面、筛选、详情、ack/resolve可继续开发。
2. 复现并解决Android构建失败，交付可安装debug APK及SHA256。
3. 对接8010真实鉴权服务，再配置Firebase；用户自己控制的客户端配置与服务端私钥分别存放。默认`USE_FIREBASE=false`；加入配置文件并不代表推送接通。
4. 验证通知权限、前台/后台/锁屏、点击正确事件、token刷新/注销、断网恢复与回执。若适配联想平板，另测横屏、分辨率与平板实际推送服务，不直接声称手机验收等于平板验收。

回交：源码修改清单、APK+哈希、构建/测试命令及输出、设备与Android版本、各场景结果、接口变更与未完成项。共享API/事件类型变更先协调；不需要改YOLO权重或接RTSP。
'''

FRONTEND='''# 网页前端美化交接 — 罗浩冬

日期：2026-11-01（北京时间）。原工作区：`G:\\比赛\\AI黑客松`。

## 目标与现状

在当前React/TypeScript/Vite工作台上继续视觉美化与布局适配，保留现有接口和交互。你负责前端美化，本包没有代做新的设计或功能。

当前已有首页、录像库/导入、原片回放、时间线、证据问答、人物/环境事件、安全分析与物品追踪独立入口，以及本机实时摄像头。

11月1日刚完成：相机16:9画面、精简状态栏、画面下方会话事件、折叠检测/录像详情；删除固定冗长免责声明，保留简短复核状态与实际错误；中间可信度标签统一为“中等可信”。请以此版为基线，避免合并时覆盖这些修改。

最新现有构建包含在`project/frontend/dist`。前序验证为24项node测试通过、TypeScript/Vite构建通过；1280×720桌面相机卡片约661px高、无横向溢出，两个折叠操作通过。未据此完成手机/平板全量适配。本轮打包不重做界面、不重跑应用测试或构建。

## 包内与编辑范围

- `project/frontend/src/`：主编辑范围；`frontend/tests/`是现有交互与状态测试。
- `project/frontend/package.json`、`package-lock.json`：依赖锁定；有React19、TypeScript、Vite，不需要迁移框架。
- `project/backend/`、`prompts/`、`schemas/`与根启动文件：供本地网页后端启动/接口联调；后端业务不是本轮主编辑范围。
- `project/elder-safety/ai-service/ai_service/`、示例配置、worker脚本：原有相机/小模型调度支持源码；没有GPU环境、权重或真实配置。
- `资料/`：当前性能报告、两套服务说明、静态路由清单和功能边界。

不含真实API Key、摄像头凭据、视频、分析数据库、证据图或SDK；解压后录像库为空、摄像头未配置属预期，不要写死已有演示事件作为“真实检测结果”。需要实拍页面数据时向项目负责人要脱敏素材或连接其联调实例。

## 首次启动与下一条命令

建议解压到G/D盘，从包根执行。前端预览需要配套8000后端以提供API。

后端终端：

```powershell
Set-Location .\\project
py -3 -m venv .\\elder-safety\\.venv
.\\elder-safety\\.venv\\Scripts\\python.exe -m pip install --cache-dir .\\runtime\\pip-cache -r .\\requirements.txt
Copy-Item .\\.env.example .\\.env
.\\start.ps1
```

`.env.example`中的Key留空可以查看空数据UI，不要为美化任务开启付费分析。初次已附dist，因此`start.ps1`可以提供预构建页面。未提供FFmpeg时上传/转码功能需另配置；相机/安全扫描另需GPU环境与权重，由项目负责人联调。

前端终端：

```powershell
Set-Location .\\project\\frontend
npm ci --cache ..\\runtime\\npm-cache
npm run dev
```

第一条下一步命令是`npm ci`，会按锁文件安装依赖；开发页由Vite输出本地地址（通常5173），`/api`和`/media`代理到8000。

实时摄像头接口检查同源。要验收摄像头开/停，请构建后从8000工作台访问，避免在5173页面直接操作摄像头导致同源拒绝：

```powershell
npm run build
# 浏览器访问 http://127.0.0.1:8000/workspace 并刷新
```

常规回交前检查（项目根目录执行）：

```powershell
node --test frontend/tests/*.test.mjs
npm run build --prefix frontend
```

无需为美化重新下载权重、重训模型、创建Firebase或改通知后端。真实视频/摄像头联调由项目负责人提供素材和私下配置，不把Key写进Vite环境变量或前端代码。

## 关键文件

| 文件 | 负责内容 |
| --- | --- |
| `frontend/src/main.tsx` | 工作台状态、录像选择、分析与结果布局 |
| `frontend/src/Shell.tsx` / `LandingPage.tsx` | 导航与介绍页 |
| `frontend/src/LiveCameraPanel.tsx` / `live-camera.css` | 实时预览、状态、会话事件、折叠详情 |
| `frontend/src/VideoAnalysis.tsx` | 安全/物品分析选择、开始/暂停/继续、进度 |
| `frontend/src/SafetyPanel.tsx` / `safety.css` | 人物/环境提醒、证据、标记知悉 |
| `frontend/src/EventTimeline.tsx` / `confidence.ts` | 时间线分组与可信度展示 |
| `frontend/src/ui.tsx`、其他CSS | 共用图标、时间格式与样式 |

## 必须保留的交互与状态

- 同一录像一次一个任务；安全与物品任务分开选择；继续任务保持原模式，不因切tab改变。
- 安全流程仍为本地小模型→候选片段Qwen复核，物品追踪直接Qwen；不能为美化把待复核改为已确认。
- 进度、暂停恢复、任务失败、识别缺口、API不可用、空数据/加载/错误状态需能分辨；真实错误信息不隐藏。
- 回看时间与关键帧、事件筛选、证据问答、已知悉操作、相机开/停、录像状态与存储参数不能丢。
- 不重加已删除的固定免责声明；不显示API Key、摄像头地址/密码或用户token。
- 关注小屏/平板横竖屏、长文件名、长事件说明、无事件与错误状态。具体视觉风格由你决定，本包不预设新设计。

接口路径与8000/8010区分见`资料/接口与数据约定.md`。UI主用8000；实时接口`/api/camera/status|start|stop|frame.jpg`限定本机同源。不要为手机访问而取消该限制。

回交：前端源码修改、截图/录屏（桌面及手机/平板尺寸）、启动与测试输出、接口改动说明和未完成项。后端枚举、相机轮询、模型规则若需改动先与负责人协调。
'''

API='''# 接口与数据约定

以下为交接用索引，具体参数以包内源码和运行中的`/openapi.json`为准。

## 网页8000

入口：`backend/main.py`与`backend/live_camera.py`。常用：`GET /api/health`、`GET /api/videos`；录像分析、analysis-plan、暂停、继续、memory、alerts、query等完整方法/路径见`web-routes.json`。摄像头路由有本机/同源校验，不能当作手机服务。

视频任务mode为`safety|objects|all`，页面主要独立启动safety或objects。前端`analysisPath`处理analyze/reanalyze/resume差异。视频帧数/扫描进度与复核计数分别显示。实时事件review_status可有pending/confirmed/rejected/uncertain/unavailable，保留这些实际语义。

## 手机8010

入口：`elder-safety/backend/safety_backend/main.py`。除`/health`外使用Bearer认证。家庭用户token读事件、证据、ack/resolve和手机token管理；设备token做注册、心跳、事件/证据上报，不能互换。

当前EventType：`suspected_fall|fire_detected|smoke_detected|help_requested|device_offline`。枚举保留smoke不表示当前视觉模型已启用烟雾。离线事件由服务端心跳监视器产生，不由手机伪造。severity为info/warning/critical；occurred_at要求时区。

手机EventOut还包含review_status（pending/completed/failed/unavailable）、notification_status（pending/sent/failed/skipped）、acknowledgement_status（unacknowledged/acknowledged）、state（detected/notified/acknowledged/resolved）。注意手机review_status与网页视觉复核枚举不同。

FCM消息数据的event_id或`elder-safety://events/{event_id}`用于通知点击定位；实现见mobile-app/lib/services/push_service.dart。服务端服务账户私钥只放后端；手机JSON客户端配置另由Firebase项目产生，二者不能混用。

完整静态路由清单`mobile-routes.json`由本轮当前Python源码AST读取生成，不运行服务、不调用模型。原`openapi.json`是已有文档快照，实际联调仍查看运行服务。
'''

CHECK='''# 校验与范围

本包是源码/文档交接，不包含可直接复用的开发环境。`manifest-sha256.json`列出包中其他每个文件的SHA256和字节数；文件本身不自哈希。`source-provenance.json`记录原工作区相对路径与源文件SHA256。

Windows可用`Get-FileHash -Algorithm SHA256 <zip文件>`与ZIP旁的`.sha256`核对。解压后可读取manifest逐文件核对；ZIP使用UTF-8文件名。

本轮执行：ZIP CRC完整性读取；所有包内文件与manifest哈希/长度比对；源码副本与原文件哈希核对；关键工程入口、锁文件、Android wrapper/平台源码、前端src/dist存在性检查；禁止目录/扩展名及已配置凭据扫描。没有为两位接手同学执行开发、依赖安装、编译、模型测试、Firebase配置或真机验收。

原机路径仅用于说明来源；接手人按实际解压目录运行，不复制原机虚拟环境/缓存。将工具和缓存优先放G/D盘；运行现有Android环境脚本前先阅读G盘/R盘映射约束。
'''

def build(role):
    name='AnjuVision_'+('Mobile' if role=='mobile' else 'Frontend')+'_20261101'
    base=OUT/name;base.mkdir(parents=True,exist_ok=False);provenance={}
    if role=='mobile':
        for tree in ['elder-safety/mobile-app','elder-safety/backend']:copy_tree(base,tree,provenance)
        for rel in ['elder-safety/.env.example','elder-safety/.gitignore','elder-safety/pyproject.toml','elder-safety/scripts/configure.py','elder-safety/scripts/start-backend.ps1','elder-safety/scripts/check-mobile-readiness.py','elder-safety/scripts/prepare-android-tools.py','elder-safety/docs/api-contract.md','elder-safety/docs/openapi.json','elder-safety/docs/android-build-20261030.md']:
            copy_file(base,rel,provenance)
        for rel in ['backend/live_camera.py','backend/hybrid_safety.py','elder-safety/scripts/web-camera-worker.py','elder-safety/ai-service/ai_service/preview_alerts.py']:
            copy_file(base,rel,provenance,'integration-reference/'+rel)
        status=json.loads((ROOT/'elder-safety/tools/android-build/logs/apk-build-status.json').read_text('utf-8-sig'))
        dump(base/'资料/mobile-build-status-checked.json',{k:status.get(k) for k in ['updated_at','state','firebase_enabled','real_phone_tested','apk','stage']})
        handoff=MOBILE
    else:
        for tree in ['frontend','backend','prompts','schemas','examples']:copy_tree(base,tree,provenance,dist=(tree=='frontend'))
        for tree in ['elder-safety/ai-service/ai_service']:copy_tree(base,tree,provenance)
        for rel in ['README.md','requirements.txt','requirements.lock.txt','requirements-dev.txt','pyproject.toml','.env.example','.gitignore','start.ps1','start.bat','elder-safety/.env.example','elder-safety/ai-service/config.example.json','elder-safety/scripts/scan-safety-video.py','elder-safety/scripts/web-camera-worker.py']:
            copy_file(base,rel,provenance)
        # Copied .gitignore is historical; .env remains excluded by it.
        handoff=FRONTEND
    copy_file(base,'docs/safety-analysis-timing-20261101.md',provenance,'资料/safety-analysis-timing-20261101.md')
    put(base,'资料/当前成果与协作约定.md',COMMON);put(base,'资料/接口与数据约定.md',API);put(base,'校验与范围.md',CHECK)
    dump(base/'资料/mobile-routes.json',routes('elder-safety/backend/safety_backend/main.py'))
    dump(base/'资料/web-routes.json',routes('backend/main.py')+routes('backend/live_camera.py','/api/camera'))
    subprocess.run([sys.executable,str(Path.home()/'.codex/skills/session-handoff/scripts/create_handoff.py'),'--root',str(ROOT),'--title',role+'-handoff-20261101','--output',str(base/'README_先读我.md')],check=True,capture_output=True)
    put(base,'README_先读我.md',handoff)
    dump(base/'source-provenance.json',provenance)
    records={}
    for p in sorted(base.rglob('*')):
        if p.is_file():
            data=p.read_bytes();security(p.relative_to(base),data)
            records[p.relative_to(base).as_posix()]={'sha256':sha(data),'bytes':len(data)}
    dump(base/'manifest-sha256.json',{'created_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),'role':role,'files':records})
    archive=OUT/(('安居视界_手机端APP_戈宇晗_' if role=='mobile' else '安居视界_网页前端美化_罗浩冬_')+STAMP+'.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        for p in sorted(base.rglob('*')):
            if p.is_file():z.write(p,name+'/'+p.relative_to(base).as_posix())
    required=(['project/elder-safety/mobile-app/lib/main.dart','project/elder-safety/mobile-app/test/widget_test.dart','project/elder-safety/mobile-app/pubspec.lock','project/elder-safety/mobile-app/android/gradle/wrapper/gradle-wrapper.jar','project/elder-safety/backend/safety_backend/main.py'] if role=='mobile' else ['project/frontend/src/main.tsx','project/frontend/src/LiveCameraPanel.tsx','project/frontend/dist/index.html','project/frontend/package-lock.json','project/backend/main.py'])
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        names=z.namelist();assert len(names)==len(set(names))==len(records)+1
        for rel in required:assert name+'/'+rel in names
        for rel,info in records.items():
            data=z.read(name+'/'+rel);assert sha(data)==info['sha256'] and len(data)==info['bytes'];security(rel,data)
        assert json.loads(z.read(name+'/manifest-sha256.json'))['files']==records
        for rel,info in provenance.items():
            assert sha((ROOT/info['source']).read_bytes())==info['sha256']==sha(z.read(name+'/'+rel))
        for full in names:
            rel=Path(full)
            assert not any(x in SKIP for x in rel.parts if not (role=='frontend' and x=='dist'))
            assert '..' not in rel.parts and not rel.is_absolute()
    h=sha(archive.read_bytes());archive.with_suffix('.zip.sha256').write_text(h+'  '+archive.name+'\n',encoding='utf8')
    return {'role':role,'zip':str(archive),'bytes':archive.stat().st_size,'files':len(records)+1,'source_files':len(provenance),'sha256':h,'crc_ok':True,'manifest_ok':True,'source_copy_ok':True,'credential_scan_ok':True,'required_entries_ok':True,'application_tests_run':False,'application_build_run':False}

def main():
    OUT.mkdir(parents=True,exist_ok=False)
    results=[build(role) for role in ['mobile','frontend']]
    dump(OUT/'打包验收结果.json',{'created_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),'scope':'two source handoffs only','results':results})
    for r in results:print(json.dumps(r,ensure_ascii=True))
    print('VALIDATION='+str(OUT/'打包验收结果.json'))
if __name__=='__main__':main()
