"""Build the explicitly requested private team handoff, never runtime credentials."""
from __future__ import annotations
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deliverables" / "team-handoff-20261030"
PROJECT = OUT / "project"
ELDER = ROOT / "elder-safety"
EXCLUDE = {".git", ".venv", ".venv-gpu", "node_modules", "runtime", "data", "recordings", "tools", "weights", "deliverables", "submission", "video", "Ultralytics", "__pycache__", ".pytest_cache", ".dart_tool", ".gradle", ".idea", "build", ".tmp", ".artifacts", ".agents", ".codex", ".aws"}
PRIVATE = {".env", "google-services.json", "GoogleService-Info.plist", "firebase_options.dart", "local.properties", "key.properties", ".flutter-plugins-dependencies", "config.camera-gpu.json", "config.local-models.json", "tsconfig.tsbuildinfo"}
EXT = {".dart", ".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".lock", ".ps1", ".bat", ".sh", ".js", ".mjs", ".ts", ".tsx", ".css", ".html", ".svg", ".xml", ".kt", ".kts", ".gradle", ".properties", ".java", ".png", ".jpg", ".webp", ".jar", ".mmd", ".csv"}
ALLOWED_ROOT_FILES = {"README.md", "requirements.txt", "requirements-dev.txt", "requirements.lock.txt", "requirements-prep.lock.txt", "pyproject.toml", "start.bat", "start.ps1", ".env.example", ".gitignore", ".dockerignore", "Dockerfile", "ms_deploy.json"}
ALLOWED_ROOT_DIRS = {"backend", "frontend", "tests", "scripts", "docs", "prompts", "schemas", "design", "deployment", "evaluation", "examples", "elder-safety"}
BINARY = {".png", ".jpg", ".webp", ".jar", ".pt", ".mp4"}
SECRETS = []
for envfile in [ROOT / ".env", ELDER / ".env"]:
    if not envfile.exists(): continue
    for line in envfile.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in line or line.lstrip().startswith("#"): continue
        key,value = line.split("=",1); value=value.strip().strip("\"'")
        if any(w in key.upper() for w in ("TOKEN", "KEY", "PASSWORD", "CREDENTIAL", "RTSP")):
            if len(value) >= 16: SECRETS.append(value)
            if value.startswith("{"):
                try: SECRETS.extend(v for v in json.loads(value).values() if isinstance(v,str) and len(v)>=16)
                except ValueError: pass

def digest(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""): h.update(block)
    return h.hexdigest()

def put(rel,text):
    p=OUT/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text,encoding="utf-8"); return p

def security_check(p):
    if p.suffix.lower() in BINARY: return
    try: t=p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError: raise RuntimeError("Unexpected non-UTF8 file: "+str(p))
    if any(v in t for v in SECRETS) or re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|sk-[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16}",t):
        raise RuntimeError("Potential secret; do not package: "+str(p.relative_to(ROOT)))

OUT.mkdir(exist_ok=True)
PROJECT.mkdir(exist_ok=True)
count=0
for d,ds,fs in os.walk(ROOT):
    rel=Path(d).relative_to(ROOT)
    ds[:]=[x for x in ds if x not in EXCLUDE and not x.startswith(".venv") and (rel.parts or x in ALLOWED_ROOT_DIRS)]
    for f in fs:
        p=Path(d)/f
        if f in PRIVATE or f.endswith((".pem",".key",".jks",".keystore",".sqlite3",".db",".rar",".zip",".sha256")): continue
        if f.startswith(".env") and f != ".env.example": continue
        if not rel.parts and f not in ALLOWED_ROOT_FILES:continue
        if rel.parts and p.suffix.lower() not in EXT and f not in {".gitignore",".dockerignore",".env.example",".metadata","Dockerfile","gradlew"}:continue
        security_check(p)
        dst=PROJECT/p.relative_to(ROOT);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst);count+=1

video=ROOT/"video"/"10月30日.mp4"
assert video.is_file()
vdst=PROJECT/"video"/video.name;vdst.parent.mkdir();shutil.copy2(video,vdst)
assert digest(video)==digest(vdst)
for name in ["yolo11n-pose.pt","luminous-fire-yolov8n.pt","manifest.json"]:
    src=ELDER/"weights"/name;dst=PROJECT/"elder-safety"/"weights"/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    assert digest(src)==digest(dst)
# Model manifest is provenance only; local paths are rebased, hashes remain original.
mfile=PROJECT/"elder-safety/weights/manifest.json";m=json.loads(mfile.read_text(encoding="utf-8-sig"))
for model in m["models"]: model["path"]="./"+model["file"]
mfile.write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding="utf-8")
license_file=Path(r"D:\Anaconda\envs\gear_test\Lib\site-packages\ultralytics-8.4.118.dist-info\licenses\LICENSE")
assert license_file.is_file()
shutil.copy2(license_file,OUT/"AGPL-3.0.txt")
base=json.loads((ELDER/"ai-service/config.camera-gpu.json").read_text(encoding="utf-8-sig"))
base["models"]["pose_weights"]="weights/yolo11n-pose.pt";base["models"]["flame_weights"]="weights/luminous-fire-yolov8n.pt";base["models"]["device"]="cpu"
base["recording"]["enabled"]=False;base["recording"]["directory"]=None;base["recording"]["ffmpeg_path"]=None
put("project/team-tools/config.baseline.json",json.dumps(base,ensure_ascii=False,indent=2))

reference=json.loads((ELDER/"ai-service/config.camera-gpu.json").read_text(encoding="utf-8-sig"))
reference["models"]["pose_weights"]="G:/elder-safety-team-local/weights/yolo11n-pose.pt"
reference["models"]["flame_weights"]="G:/elder-safety-team-local/weights/luminous-fire-yolov8n.pt"
reference["models"]["device"]="cpu"
reference["recording"]["directory"]="G:/elder-safety-team-local/recordings"
reference["recording"]["ffmpeg_path"]=None
put("project/elder-safety/ai-service/config.camera-gpu.json",json.dumps(reference,ensure_ascii=False,indent=2))

metadata={"file":"video/10月30日.mp4","sha256":digest(video),"bytes":video.stat().st_size,"labels_source":"user described fall, squat, hands-on-head, help; time intervals not yet annotated","annotation_status":"pending_human_annotation","purpose":"shared development and regression demo; NOT an independent held-out test","privacy":"private team only; no public repository or cloud upload"}
put("project/team-tools/demo-manifest.json",json.dumps(metadata,ensure_ascii=False,indent=2))
put("project/team-tools/annotations.csv","start_seconds,end_seconds,action,expected_event,notes\n")
put("project/team-tools/prepare_config.py",r'''"""Prepare portable local-only configs; never read camera credentials."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ELDER=ROOT/'elder-safety'
sys.path.insert(0,str(ELDER/'ai-service'))
from ai_service.config import Config

def prepare(device='cpu'):
    data=json.loads((ROOT/'team-tools/config.baseline.json').read_text(encoding='utf-8'))
    data['models']['pose_weights']=str(ELDER/'weights/yolo11n-pose.pt')
    data['models']['flame_weights']=str(ELDER/'weights/luminous-fire-yolov8n.pt')
    data['models']['device']=device
    data['recording']['enabled']=False
    dest=ELDER/'ai-service/config.team-local.json'
    dest.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    Config.load(str(dest))
    return dest
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--device',default='cpu')
    a=p.parse_args();print(prepare(a.device))
''')
put("project/team-tools/run_demo.py",r'''"""Offline demo replay. No RTSP, HTTP client, FCM or cloud upload is started."""
import argparse,hashlib,json,os,sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];ELDER=ROOT/'elder-safety'
sys.path.insert(0,str(ELDER/'ai-service'))
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--device',default='cpu');p.add_argument('--config',type=Path)
p.add_argument('--output',type=Path);p.add_argument('--max-frames',type=int)
p.add_argument('--with-fire',action='store_true');p.add_argument('--check-input',action='store_true')
a=p.parse_args()
if a.max_frames is not None and a.max_frames<1:p.error('--max-frames must be positive')
manifest=json.loads((ROOT/'team-tools/demo-manifest.json').read_text(encoding='utf-8'))
source=ROOT/manifest['file']
h=hashlib.sha256()
with source.open('rb') as f:
    for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
if h.hexdigest()!=manifest['sha256']:raise SystemExit('Demo SHA256 mismatch; source must remain unchanged')
# Keep caches on the extracted project drive before importing inference packages.
from ai_service.runtime_env import configure_model_runtime
configure_model_runtime()
import cv2
cap=cv2.VideoCapture(str(source));fps=cap.get(cv2.CAP_PROP_FPS);frames=cap.get(cv2.CAP_PROP_FRAME_COUNT);ok,_=cap.read();cap.release()
if not ok:raise SystemExit('Cannot decode the demo')
info={'input_sha256_verified':True,'fps':fps,'frames':int(frames),'duration_seconds':frames/fps if fps else None,'network_delivery':False,'independent_accuracy_estimate':False}
if a.check_input:print(json.dumps(info,ensure_ascii=False,indent=2));raise SystemExit(0)
from prepare_config import prepare
from ai_service.config import Config
from ai_service.local_validate import validate_file
cfg=Config.load(str(a.config.resolve() if a.config else prepare(a.device)))
if not a.config:cfg.models.device=a.device
cfg.recording.enabled=False
if not a.with_fire:cfg.models.flame_weights=None
cfg.validate()
output=(a.output or ELDER/'runtime'/('team-demo-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))).resolve()
report=validate_file(source,output,cfg,max_frames=a.max_frames)
report['team_handoff']={**info,'with_fire':a.with_fire,'full_demo_processed':a.max_frames is None or report['frames']>=int(frames),'manual_labels_available':False}
(output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:report[k] for k in ['output','frames','events','wall_seconds']},ensure_ascii=False,indent=2))
''')
put("project/team-tools/compare_reports.py",r'''"""Compare predictions, not accuracy: manual labels are required for quality claims."""
import argparse,json
from collections import Counter
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('baseline',type=Path);p.add_argument('candidate',type=Path);a=p.parse_args()
reports=[json.loads(x.read_text(encoding='utf-8-sig')) for x in [a.baseline,a.candidate]]
if reports[0]['source_sha256']!=reports[1]['source_sha256']:raise SystemExit('Different source videos')
if reports[0]['frames']!=reports[1]['frames']:raise SystemExit('Different frame coverage; cannot compare directly')
for name,r in zip(['baseline','candidate'],reports):
    print(json.dumps({'run':name,'frames':r['frames'],'event_counts':dict(Counter(x['event_type'] for x in r['events'])),'events':[{'t':x['stream_seconds'],'event_type':x['event_type']} for x in r['events']],'warm_inference_ms':r['inference_ms_p50_warm']},ensure_ascii=False,indent=2))
print('Prediction differences only. No manual ground truth or independent accuracy estimate.')
''')
put("project/team-tools/start_web.ps1",r'''param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $root '.venv\Scripts\python.exe' }
& (Join-Path $root 'start.ps1') -Python $Python
''')
put("project/team-tools/start_frontend.ps1",r'''$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $root 'frontend')
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    & npm.cmd run dev
} finally { Pop-Location }
''')
put("01_林子涵_摔倒识别.md", """# 林子涵：摔倒识别\n\n## 从哪里开始\n- `project/elder-safety/ai-service/ai_service/temporal.py`：FallDetector 的站立历史、下落、躺倒与持续时序。该文件也含 FireDetector，勿误改火焰逻辑。\n- `tracking.py`：人体跟踪；`models.py`：姿态模型与图像缩放；`engine.py`：事件融合。共享文件的修改先协调。\n- 测试：`test_algorithms.py`、`test_fall_sampling.py`、`test_regressions.py`，并补充你自己的回归用例。\n\n## 必做\n1. 先离线跑统一 demo 保存 baseline，不先改阈值；对照原视频人工记录摔倒时段、蹲下等反例。\n2. 查漏检是姿态关键点缺失、跟踪断裂、直立历史不足、下落阈值还是确认时间造成。记录证据，不能只凭躺倒横框判定。\n3. 最小修改后保存 candidate，与 baseline 使用相同视频、相同帧覆盖、相同硬件比较。避免以降低所有阈值换召回。\n4. 加测弯腰、蹲下、坐下、正常躺卧、遮挡、离开画面；未拍到的新场景写未验证。\n\n## 交付与验收\n提交源码 diff/补丁、配置差异、权重来源和许可证（若更换）、运行命令、正负例标注、漏检/误报/延迟及已知限制。\n目标是 demo 中人工标注的摔倒能够触发、蹲下等反例不误触发；实际达成情况据实填写，不预先承诺准确率。\n只改本人模块；火焰、通知接口、循环录像、求助代码不做无关改动。`web-camera-worker.py` 的实时抽样策略与离线逐帧回放不同，离线通过后必须由集成负责人再测真实摄像头。\n""")
put("02_张雨暄_求助识别.md", """# 张雨暄：求助识别\n\n## 入口与问题\n- `project/elder-safety/ai-service/ai_service/help_gesture.py`：手势几何、朝向与时序。\n- `types.py`、`engine.py`：保持现有 `help_requested` 事件契约。\n- 重点测试：`test_help_gesture.py`、`test_help_facing.py`、`test_help_integration.py`。\n\n## 必做\n1. 在共同 demo 中人工标注真正举手求助及双手抱头时段，先留 baseline。\n2. 明确现有约定：持续双手举起候选，不是语音呼救、眼球注视或真实求救意图识别。\n3. 减少双手抱头误报，同时测试整理头发、伸懒腰、短暂抬手、侧脸/低头、遮挡与多人。\n4. 当前配置 `require_facing_camera=true`；它是姿态面部点的朝向启发式，不是眼神跟踪。是否改为辅助条件，请提交开/关对照结果并与负责人确认，不擅自改变产品约定。\n5. 核对持续时长、关键点置信度与冷却，避免每帧重复报警；新阈值集中在配置里。\n\n## 交付与验收\n提交模块修改、相关测试、相同视频覆盖的前后报告、正反例时间标注和已知限制。\n目标是 demo 中人工标注的求助能触发、抱头不误触发；结果据实记录。不要以关闭全部求助或极高阈值换取零误报。\n共享模型、跟踪与实时 worker 的修改先协调，不覆盖林子涵的摔倒逻辑。\n""")
put("03_戈宇晗_手机端APP.md", """# 戈宇晗：手机端 App\n\n不是从零重写：包内已有 Flutter 界面、API 调用、事件确认/解除、推送适配及 Android 工程。\n\n## 先读\n- `project/elder-safety/docs/手机端开发交接文档-20261030.md`（详细交接）。\n- `project/elder-safety/mobile-app/README.md`、`docs/api-contract.md`、`docs/mobile-notifications.md`。\n- `mobile-app/lib/services/api.dart`、`push_service.dart`、`android_notices.dart`、`lib/ui/`、`android/`。\n\n## 优先交付\n1. 在自己机器先 `flutter pub get`、分析和单元/widget 测试；可先使用项目 demo 数据，不需要摄像头或真实 Firebase。\n2. 对接 8010 安全后端的 `/api/v1/`，不是 8000 网页服务的上传/分析接口。用户令牌和设备令牌不能混用。\n3. 打通真实事件列表→详情→证据→确认/解除。HTTP 接口成功和系统推送送达分别验收。\n4. 用户尚需使用自己的 Google 账户配置 Firebase。客户端 `google-services.json` 只放本地；服务账户私钥只留后端，绝不能嵌入 APK。\n5. 真机分别验收前台、后台、锁屏、点击跳转、重复通知与网络重连。强制停止/通知权限受限写实际结果，不承诺必达。\n\n## 构建边界\n- SDK/JDK/Gradle 缓存、虚拟环境与 APK 不在本交接包内；源码不等于已构建/真机送达。\n- `tool/android-env.ps1` 是原机 G: + R: 别名构建方案，别在未阅读时直接运行；先按自己的 G/D SDK 路径配置。也可用已安装 Flutter 的普通命令构建。\n- 交付可安装 APK、源码 diff、构建命令和版本、后端配置模板、真实手机/平板测试记录；勿提交私钥与本地凭据。\n- 平板适配、实时视频播放不是当前已验证功能，如扩展请单列范围。\n\n可复现命令（在 `project/elder-safety/mobile-app`）：\n```powershell\nflutter pub get\nflutter analyze --no-fatal-infos\nflutter test\nflutter build apk --debug --dart-define=USE_FIREBASE=false\n```\n以上为交接运行步骤，本次打包不重新构建 APK，也未验证 FCM 真机送达。\n""")
put("04_罗浩冬_前端美化.md", """# 罗浩冬：前端美化\n\n## 修改真正使用的前端\n入口是 `project/frontend/src/` 的 React + TypeScript + Vite，**不是** `elder-safety/mobile-app`，也不是旧 submission 副本。\n重点：`LiveCameraPanel.tsx`、`live-camera.css`、`Shell.tsx`、`SafetyPanel.tsx`、`SafetyPanel` 相关样式、`main.tsx`。\n\n## 任务\n- 改善整体视觉、排版、对比度、手机宽度响应式和状态反馈；保留现有中文含义和事件定义。\n- 明确区分连接中、模型加载、在线、断流、重连、录像中、停止、无新报警。不要显示假 FPS 或伪造在线状态。\n- 摄像头区域刷新时应有稳定位置和加载占位，避免先出现旧界面再跳入模块。\n- 保留开启/停止摄像头、录像状态、报警卡片、证据与错误提示；不为了美观隐藏功能。\n- demo 视频用于离线算法优化，网页摄像头入口仍是实时 RTSP；不要把网页上传后的云端分析当成本地模型回放。\n\n## 本地与验收\n```powershell\n# 在 project/frontend\nnpm ci\nnpm run dev\n# 完成后\nnpm run build\n```\nVite 的 API 代理规则以 `vite.config.ts` 为准；需要接口时另开 8000 网页后端，按总说明启动。无摄像头/凭据时正常展示离线/错误态，不伪造实时画面。\n只提交前端源码、必要依赖锁文件、构建结果和前后截图；不改报警阈值、不改 API 路径、不提交 node_modules。\n公共 API、后端和 AI 配置由负责人协调。测试正常/连接中/断流/错误四类状态，记录实际验证情况。\n""")
put("05_运行与接口说明.md", """# 运行与接口说明\n\n## 两套后端不要混淆\n- `project/backend/`：现有网页后端，默认 8000，页面 `/workspace`；`project/frontend/` 是对应网页。\n- `project/elder-safety/backend/`：安全事件/通知后端，默认 8010，契约 `elder-safety/docs/api-contract.md`，真实路由以 `safety_backend/main.py` 为准。\n- `elder-safety/ai-service/`：姿态、摔倒、求助手势和明火；`scripts/web-camera-worker.py` 为实时网页桥接。\n- `elder-safety/mobile-app/`：Flutter Android 项目。\n\n事件类型保留 `suspected_fall`、`help_requested`、`fire_detected` 等实际枚举，以 `backend/safety_backend/schemas.py` 和 `docs/openapi.json` 为准；不要擅自创造另一套同义字段。离线脚本生成本地报告，不向 8010 发送事件，不向手机发通知。\n\n## 首次离线环境（下载由组员自行执行，本次无新增下载）\n建议解压到 G/D 盘短路径。不要复制原机虚拟环境，已有 CUDA PyTorch 的人可复用兼容环境。下面 Python 路径替换成自己可用的 Python 3.11：\n```powershell\n# 在解压后的 project\n$basePython = 'D:\\Anaconda\\python.exe'\n& $basePython -m venv elder-safety/.venv\n$py = (Resolve-Path elder-safety/.venv/Scripts/python.exe).Path\n$env:PIP_CACHE_DIR = Join-Path (Get-Location) 'elder-safety/runtime/pip-cache'\n& $py -m pip install -r elder-safety/ai-service/requirements-inference.txt -r elder-safety/backend/requirements-dev.txt\n& $py team-tools/run_demo.py --check-input\n& $py team-tools/run_demo.py --device cpu --max-frames 45\n# 有可用 CUDA 环境再用 --device 0，不能只靠参数假定支持。\n# 完整视频：不加 --max-frames；默认不跑火焰以专注两个人体任务。\n& $py team-tools/run_demo.py --device cpu --output elder-safety/runtime/demo-baseline\n# --with-fire 可额外启用包内明火模型。\n```\n输出为 annotated.mp4、trace.jsonl、report.json 和本地证据。输出目录必须不存在，防止覆盖 baseline。输入校验必须匹配 demo-manifest.json。\n`team-tools/config.baseline.json` 是可移植模板，不能直接给 Config.load；先运行 `team-tools/prepare_config.py` 生成绝对本机路径，再用 `--config` 指定候选配置。默认关闭循环录像。\n```powershell\n& $py team-tools/prepare_config.py --device cpu\n# 复制并修改生成的 config.team-local.json 中本人阈值，再运行：\n& $py team-tools/run_demo.py --config elder-safety/ai-service/config.candidate.json --output elder-safety/runtime/demo-candidate\n& $py team-tools/compare_reports.py elder-safety/runtime/demo-baseline/report.json elder-safety/runtime/demo-candidate/report.json\n# 测试在 elder-safety 目录运行\nPush-Location elder-safety\n& $py -m pytest -q --basetemp runtime/pytest-team-UNIQUE -p no:cacheprovider\nPop-Location\n```\n\n## 网页独立环境\n根目录 requirements.txt 和 AI requirements-inference.txt 的 NumPy 版本约束不同，**不要一次装进同一个环境**。网页用 project/.venv，算法/8010 用 elder-safety/.venv。\n```powershell\n& $basePython -m venv .venv\n& ./.venv/Scripts/python.exe -m pip install -r requirements.txt\n# 在 project/frontend 执行 npm ci; npm run build\n./team-tools/start_web.ps1\n```\n然后打开 http://127.0.0.1:8000/workspace；开发热更新用前端 `npm run dev` 的实际地址。`.env.example` 仅示例，云分析默认不要启用。\n网页摄像头还依赖 `.venv-gpu`、原机基础解释器、私有 RTSP 配置和本机绝对模型路径：本包没有这些机器资源，组员不能开箱直连用户摄像头；由项目负责人在原机做最终联调。\n\n## 8010 安全后端\n在 elder-safety 目录运行 `scripts/start-backend.ps1`，第一次会生成本地随机测试令牌。多个服务联调须由负责人私下统一分配测试令牌。手机填写电脑局域网 IP，不是手机自己的 127.0.0.1；对手机开放监听/防火墙前单独确认安全范围。\n不在群内分享真实凭据，不把原视频上传公共仓库或第三方推理 API。\n""")
put("06_demo标注与统一验收.md", """# demo 标注与统一验收\n\n用户指定的 `10月30日.mp4` 随包原样提供，不剪辑、不压缩转码。SHA-256 见 `project/team-tools/demo-manifest.json`。\n用户描述含摔倒、蹲下、双手抱头、求助；尚未逐段人工标注。本包 `annotations.csv` 只含表头，**没有猜测时间戳**。\n\n## 标注步骤\n先看原视频，为每个动作填写 start_seconds/end_seconds/action/expected_event/notes。摔倒和求助是正例，蹲下与抱头是明确反例；以画面实际动作及共同确认的定义为准。\n两位算法负责人共同确认同一份标注，不各自修改真值来适应自己的算法。保留完整原视频作为基准，修改配置、权重和硬件均需记版本。\n\n## 对照要求\n- baseline 和 candidate 处理相同源视频、相同全帧范围；短 smoke 仅测能运行，不用于结论。\n- 记录每个人工正例是否被匹配、首次报警相对动作起点的延迟、每段反例是否误报、重复报警次数。匹配窗口先共同确定并写明。\n- 保留带标注视频、trace 与 report；compare_reports 仅比较预测，不会自动给出准确率。\n- 单个 demo 是开发/回归素材，不是泛化证据。最终追加未用于调参的新视频和其他机位/光照再验收。\n- 离线逐帧测时不能代替实时摄像头 FPS。实时还涉及最新帧丢弃、抽样、推流、前端渲染与通知延迟。\n\n## 每人回交模板\n改了什么；修改文件；依赖/权重许可；环境/命令；baseline 结果；candidate 结果；正反例与失败时段；单元测试；实际视频验证；未验证项；如何回退。\n涉及跌倒采样请用安全动作/既有视频，勿要求他人危险摔倒；不要求明火测试。\n""")
put("07_集成清单.md", """# 项目负责人集成清单\n\n建议原项目负责人负责公共接口和最终合并；四人并行不新增第五位开发者。\n\n- [ ] 2026-10-31 发同一份快照；四人核对 demo SHA256。\n- [ ] 各自建立分支，如 codex/fall-linzihan、codex/help-zhangyuxuan、codex/mobile-geyuhan、codex/frontend-luohaodong（若有 Git）。\n- [ ] 冻结 event 类型和 8010 契约；公共 tracking/models/config/engine/worker 改动先协调。\n- [ ] 林子涵主要编辑 temporal.py 的 FallDetector；张雨暄主要编辑 help_gesture.py；不要互相覆盖配置文件。\n- [ ] 两位算法负责人交付相同 demo 前后报告；真值独立保存，原视频不改动。\n- [ ] App 用测试数据先联调 8010，再测真实推送；FCM 失败不能阻塞本地报警。\n- [ ] 前端同步开始，不等最后一天；保留状态和接口。\n- [ ] 原机真摄像头联调：开/停、断流、录像路径 G:/比赛/AI黑客松/video、事件列表和手机通知分别检查。\n- [ ] 正式采集录像清理规则与开发 demo 分开；本包 demo 不注册为循环录像。\n- [ ] 演示前冻结可用版本，保留可回退代码与配置。\n\n本次整理不优化算法、不训练、不改生产配置、不重启摄像头、不创建 Firebase、不发送通知、不公开部署。\n""")
put("THIRD_PARTY_NOTICES.md", """# 第三方与隐私说明\n\n此为用户明确要求的项目组内部交接快照，**不是公开发布许可**。项目自身未因此获得新的开源许可。\n\n包内 YOLO11n Pose 与 luminous-fire-yolov8n 权重的来源、记录哈希和已有许可审查说明见 `project/elder-safety/weights/manifest.json`，保留 AGPL-3.0 全文 `AGPL-3.0.txt`。\nAGPL 文本从原机已安装 Ultralytics 分发中的 LICENSE 复制；来源地址与上游对应源码按 manifest 记录保留。分发前后修改、产品上线和商业用途需自行复核相应许可义务，不将模型许可等同于媒体授权。\n权重来自现有本地文件，没有新增下载。本次校验原文件与包内一致，不宣称独立重新审计了上游版权。\n\n视频为用户指定的内部开发素材，仅发送给约定负责人；请确认出镜人员同意用途。请勿将本包或视频公开分享、提交公共仓库或上传云模型。\nSDK、虚拟环境、运行数据库、历史录像、运行证据、真实 .env、摄像头密码、API Key、Firebase 配置和私钥、签名密钥均排除。\n""")
put("README_先读我.md", """# 安居视界四人分工交接包\n\n日期：2026-10-30；分工讨论：2026-10-31。\n\n## 发送方式\n把整个 ZIP 发送给四位负责人，每人先看 `00_分工与交付要求.md`，再读自己姓名对应的任务单。所有人用同一份 project 快照和原始 demo，不使用旧提交包、旧私用含 APIKey 包。\n\n- 林子涵：`01_林子涵_摔倒识别.md`\n- 张雨暄：`02_张雨暄_求助识别.md`\n- 戈宇晗：`03_戈宇晗_手机端APP.md`\n- 罗浩冬：`04_罗浩冬_前端美化.md`\n- 安装、服务和回放：`05_运行与接口说明.md`\n- 统一标注/验收：`06_demo标注与统一验收.md`\n- 总集成：`07_集成清单.md`\n\n## 内容\n`project/` 含当前实际网页、两套后端、AI 服务、Flutter/Android 源码、测试、说明和必要模型；`project/video/10月30日.mp4` 为用户指定原视频。\n`project/team-tools/` 含可移植模型配置生成器、无网络报警投递的离线 demo 回放、预测对照与待填写标注模板。\n本包不是开箱即用的虚拟机：依赖/SDK 要在组员 G/D 盘准备；摄像头凭据及 Firebase 需本人单独配置。\n历史文档可能记录旧日期、旧状态或原机路径；本包 00–07 文档与验收记录优先，当前源码/API 是实现依据。\n本次执行过的检查与未验证项见 `验收结果.json`；包内 `manifest-sha256.json` 可核验每个文件。\n\n## 优先级\n按时交付 > 端到端功能可靠 > 创新点可演示 > 实验结果可信 > 架构复杂度。先保留能工作的版本，避免重写全部系统。\n""")
# Package-specific ignore rules, no modification to the user's original tree.
gitignore=PROJECT/".gitignore"
with gitignore.open('a',encoding='utf-8') as f:f.write('\n# Private team demo/config outputs\nvideo/\nelder-safety/weights/*.pt\nelder-safety/ai-service/config.team-local.json\n')
report={"status":"assembled_pending_verification","source_files":count,"demo":metadata,"source_modified":False,"runtime_credentials_included":False,"models_included":["yolo11n-pose.pt","luminous-fire-yolov8n.pt"],"sdk_or_environments_included":False,"apk_built_this_turn":False,"cloud_or_camera_used":False,"manual_labels":"pending","quality_evaluation":"not performed"}
put("验收结果.json",json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({"handoff":str(OUT),"source_files":count,"demo_bytes":metadata['bytes'],"status":report['status']},ensure_ascii=False))
