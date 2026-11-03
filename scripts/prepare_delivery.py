"""Curate source, an integrity-checked real recording, and reproducible submission materials."""
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import Settings
from backend.db import Database
from backend.media import file_sha256


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip()+'\n', encoding='utf8')


def main():
    settings = Settings.from_env()
    db = Database(settings.data_dir)
    pipeline = json.loads((ROOT/'data/validation/real-pipeline.json').read_text(encoding='utf8'))
    demo = json.loads((ROOT/'data/validation/demo-recording/report.json').read_text(encoding='utf8'))
    usage = db.usage_summary()
    video = db.video(demo['video_id'])
    memory, job = db.memory(video['id'])
    app = ROOT/'submission/application'
    bundle = app/'demo-recording'
    bundle.mkdir(parents=True, exist_ok=True)
    for name, document in [('memory.json',memory), ('job.json',job)]:
        write(bundle/name,json.dumps(document,ensure_ascii=False,indent=2))
    shutil.copy2(video['original_path'],bundle/'video.mp4')
    shutil.copy2(video['playback_path'],bundle/'playback.mp4')
    (bundle/'frames').mkdir(exist_ok=True)
    for frame in memory['frames']:
        shutil.copy2(settings.data_dir/'runs'/job['id']/'frames'/(frame['id']+'.jpg'),bundle/'frames'/(frame['id']+'.jpg'))
    manifest = {'kind':'saved_real_model_recording_not_mock','model':job['model'],'prompt_version':memory['model_run']['prompt_version'],
                'source_mapping':demo['source_mapping'],'created_at_utc':job['created_at'],
                'description':'Previously completed real Qwen analysis. Opening this bundled recording makes zero model calls.',
                'sha256':{str(p.relative_to(bundle)).replace('\\','/'):file_sha256(p) for p in bundle.rglob('*') if p.is_file() and p.name!='manifest.json'}}
    write(bundle/'manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    rows = '\n'.join(f"| {r['filename']} | {r['job']['calls']} | {r['events']} | {r['job']['elapsed_ms']/1000:.3f}s | {r['job']['usage']['prompt_tokens']:,} / {r['job']['usage']['completion_tokens']:,} | PASS |" for r in pipeline['clips'])
    rows += f"\n| 浏览器新上传18秒片段 | {job['calls']} | {len(memory['events'])} | {job['elapsed_ms']/1000:.3f}s | {job['usage']['prompt_tokens']:,} / {job['usage']['completion_tokens']:,} | PASS |"
    write(ROOT/'docs/PROJECT_INTRODUCTION.md', '''# 拾迹 Home——基于时序视觉记忆的家庭 AI 助手

让家拥有记忆，让过去可以被追问。

## 简短版

拾迹 Home 将预录制视频转化为可追问的物品记忆。用户上传视频后，系统通过 Qwen 视觉模型提取带时间戳的事件，在本地保存物品位置和历史，并用原视频证据回答“最后在哪”“什么时候被拿起”等问题。LifeReplay 串起物品经历，点击即可回看。系统分别展示观察、条件推断与未知，避免把最后一次看见的位置当成现实当前位置。现已完成真实模型和真实拍摄视频的本地闭环验证。

## 完整版

物品找不到时，人往往需要回忆“上次在哪里”“后来被谁移动”。传统视频回看需要逐段拖动进度条，单次视频问答又难以保留物品经历与答案出处。拾迹 Home 以物品的时间线作为检索入口，让用户从自然语言问题回到可以核对的视频片段。

当前产品支持最长60秒的单机位 MP4 上传、视频播放、Qwen 多帧事件抽取、事件时间线、物品经历回放及带证据引用的自然语言问答。LifeReplay 为每个物品组织有序事件和关键帧，用户可以从一个结论跳到对应视频时间。无匹配物品、动作缺失、身份歧义或分析失败时，界面会说明限制。

系统采用 React / TypeScript、FastAPI、OpenCV / FFmpeg 和 SQLite。模型只负责从有时间信息的真实画面提取候选观察，后端执行 JSON Schema 与引用校验、时间排序、事件去重和可重复的状态规则。问答通过实体与意图路由检索事件，不依赖第二次大模型自由生成事实。一层容器关系只支持带前提的传播推断，后续取出或直接观察结束旧关系。

产品的核心是可追溯的时序记忆：保留视频帧、事件与推断来源，把直接观察、条件推断、未知分别呈现。现已用真实手机录制的室内摆拍视频完成 API 与浏览器闭环测试，识别出笔记本电脑从桌面被拿起并放入置物架等事件。测试素材是会议室模拟生活场景，并非真实家庭监控部署；没有标注评估集，因此不宣称事件识别准确率。

当前版本可本地运行，也附带有明确标识的真实历史分析记录，方便无需 API 密钥时回放核对。新视频识别仍需配置本人 Qwen 服务。实时摄像头、24小时监控、跨镜头追踪、多用户及复杂重识别不在当前实现范围。未来优先补充遮挡、小物体和放入后取出的评估样本，改善身份连续性，再考虑摄像头集成。
''')
    write(ROOT/'docs/TEST_RESULTS.md',f'''# 实际测试结果

记录日期：2026-10-18。PASS 表示相应检查通过，不能解释为所有真实场景均正确。所有 Mock 测试均独立标注。

| 项目 | 状态 | 证据与边界 |
|---|---|---|
| 后端自动化 | PASS | 38项，evidence/backend-tests.xml；合成视频及 MockTransport，不消费API |
| 前端生产构建 | PASS | TypeScript编译及Vite构建 |
| Mock浏览器闭环 | PASS | 上传/时间线/条件推断/未知/证据跳转/刷新恢复/手机无横向溢出；明显模拟标识 |
| 真实文本请求 | PASS | 4.261s，输入18/输出2 token |
| 真实单帧视觉请求 | PASS | 6.106s，输入99/输出224 token，描述实际背包与桌面场景 |
| 真实视频上传/分窗/Schema/存储 | PASS | 下表3次真实分析，非Mock，含两段不同源视频与一次浏览器新上传 |
| 真实浏览器问答和证据 | PASS | 最后出现问答，跳转12.0秒，误差小于0.25秒，关键帧加载，无page error |
| LifeReplay | PASS | 真实对象选择、时间有序、缩略图与视频定位 |
| 当前未知/无匹配对象 | PASS | 真实记录查询明确无法确认当前位置；未找到护照不生成证据 |
| 错误处理 | PASS | 注入401、超时、无效JSON、截断、预算越界、异常上传；不是线上故障实验 |
| 容器传播与取出终止 | PASS | 合成规则测试，未用真实放包后取出/包离开视频验证 |
| 人工标注准确率/召回率 | NOT_RUN | 没有独立gold标注，不能计算性能分数 |
| 真实遮挡/相似物混淆/快速小物体 | NOT_RUN | 尚无系统化压力样本与保留集 |
| 人民币金额硬上限 | BLOCKED | 未核实当前账号单价与实际账单，只启用严格请求规模限制 |
| 官方提交字段/时区/封面尺寸 | BLOCKED | 需要本人登录赛事页面核实；未提交 |

| 真实输入 | 请求 | 事件 | 实际后端耗时 | 输入 / 输出token | 状态 |
|---|---:|---:|---:|---:|---|
{rows}

当日真实调用合计：{usage['requests']}次（含文本/图像探针），输入{usage['input_tokens']:,}、输出{usage['output_tokens']:,} token，失败{usage['failed']}。金额未知；不以估算值代替账单。该统计不是准确率实验，也不将问答规则产生的19次以上本地查询计作模型请求。

## 视频证据核对

已查看原片稀疏与10秒全时长联系表，并核对模型引用的关键画面。第一段片段0–2秒笔记本在桌面，3–4秒被拿起，5–7秒被送到置物架，12秒可见已放在架格；可支持本次主要事件。第二段9–10秒有手部接触并移动桌面线状物，手部遮挡保留为不确定原因。这里只做定性抽查，不是逐事件人工标注或“无幻觉”证明。

## 发现与修复

首次两次真实分析均在调用模型前失败：Windows中文目录下cv2.imwrite无法保存关键帧。保留原失败记录，改用imencode加Python文件写入，增加中文路径回归后重跑通过；失败两次均为0 API请求。原始视频未覆盖。

导出演示时旧版FFmpeg无法解码浏览器VP9；仅为视频导出在D盘隔离安装imageio-ffmpeg 0.6.0自带FFmpeg7.1，生成H.264中文字幕版。应用的真实MP4路径已通过本机现有FFmpeg验证，没有将导出工具作为运行依赖。

已知局限：跨窗同物身份依赖模型复用ID；架体/架格等层级位置未做语义统一，重叠描述可能触发保守歧义；1fps可能漏掉快速动作。真实容器“放入后不可见→随容器移动”的完整链尚未拍摄验证。自动化通过不能消除这些风险。
''')
    write(ROOT/'docs/TECHNICAL_REPORT.md',f'''# 拾迹 Home 技术报告

版本v0.2，2026-10-18；模型qwen3.6-plus，提示event-v0.3，memory Schema 0.1（兼容新增open/close）。

## 背景与系统

目标是把短视频中的物品经历保存成可追问、可回看、能说明未知边界的记忆。本机应用由React/TypeScript页面和FastAPI单进程服务组成，SQLite保存数据；没有向量数据库、图数据库或重型Agent框架。可编辑架构在design/architecture.mmd。

数据流：MP4上传→检查容器/轨道/时长/尺寸/旋转→H.264播放副本→FFprobe读取原PTS、OpenCV解码抽帧→带帧ID和PTS的多图Qwen请求→Pydantic Extraction→JSON Schema及语义校验→SQLite事件和状态→意图/实体检索→观察/条件推断/未知→原视频及关键帧引用。

## 视频与视觉适配

首版60秒/100MB，单视频轨道。按源PTS减首帧PTS定位，抽帧率可配置0.2–4fps，默认1fps；8秒窗口、6秒步长。手机旋转元数据非零时明确拒绝，避免默默将方向错误画面用于识别。本轮目视检查后使用已正确逆时针处理的版本，原片哈希保留；剪辑时间加源偏移可映射原片。

VisionProvider协议隔离模型实现，Qwen走兼容Chat Completions HTTPS接口，base64有序图片、关闭thinking、JSON Object输出；本轮实际服务地址由VLM_BASE_URL配置为千问平台maas兼容端点。模型候选只含实体、动作、证据帧和不确定性，不能填预期演示故事。事件时间及ID由程序从实际帧生成，避免信任模型自编时间。

校验覆盖唯一ID、同视频引用、帧归属、时间范围、实体存在、自身关系、循环容器、原帧禁止占位等。put_in/take_out/explicit_exit至少两个不同PTS证据，缺失拒绝该窗口。重复动作只按明确相同实体ID/动作/目标且时间交叠归并，不按颜色强行合并身份。原始模型响应只保存在私有本地data/runs，不混入公开交付包。

## 时序记忆与问答

SQLite包含videos/jobs/memories/events/states/requests/charges；事件时间有索引。事件按时间重放，状态保留最后观察、最后位置相关事件、容器来源、可见性与歧义。取出或新的直接位置观察终止旧包含关系，遮挡不等于离开。推断只支持一层容器，引用“放入”和后续“容器移动”两组来源，并显示“若未被取出”等前提，不能当作包内实物被看见。

问答使用物品名称/别名与规则意图路由，支持最后出现、拿起/放入/取出/打开/关闭/离开时间、历史回顾、当前状态以及as_of_ms截止检索。最早出现的对象名称优先，LifeReplay明确选择的entity_id支持代词与消歧。不可支持的开放问题说明范围，不靠自由生成补事实。每条观察和推断都返回实际evidence_ids，查询未来时间会拒绝，当前现实位置始终不保证。

LifeReplay API按对象组合直接观察与有来源的条件链，前端显示时间、缩略图、状态标签、未知说明，点击可定位原播放器。分析状态、失败窗口与进度可见；服务重启中断的作业明确标记interrupted，失败重分析不覆盖已有有效记忆。

## 请求规模与安全

密钥已由不安全示例模板迁移到本地.env，先在D盘私有目录备份；示例已清空，Git忽略配置、视频、数据、日志和依赖。当前仓库没有提交历史或已跟踪密钥，未发现公开的证据；不能据此证明从未在其他位置泄露。HTTP错误只返回脱敏消息，API Key不出现在报告或异常表示中。仅监听127.0.0.1，限制Host与操作Origin。

严格限量模式：每次分析≤12次请求、≤128次帧引用、最大输出token总预算8192，日请求上限24，最多2个任务，单请求45秒，暂时错误最多重试1次且计入全部限制。请求账本用SQLite立即事务原子预占，失败/超时计数。记录真实usage、耗时和状态。默认不发网络请求；新配置需要填写密钥与显式网络开关。

当前未确认本账号单价，采用VLM_BILLING_MODE=request_limited；人民币限额不能声称已生效。可选择priced模式并配置已确认单价，预留与结算仍依赖供应商token估计/计费规则，不替代控制台余额限制。实际当日{usage['requests']}次调用，金额未知。

## 测试、交付与局限

38项自动化测试、前端构建、独立Mock浏览器流程通过。真实文本/图片探针与3次视频分析、一次完整浏览器新上传闭环通过；具体token和耗时见TEST_RESULTS.md。演示为109.36秒实际界面录屏加中文字幕，无剪辑加速，无语音；不是模拟录像。附带真实历史结果离线回放，显著标识，导入不调用模型，不代表每次打开都重新分析。

尚无人工标注保留集，未测准确率和召回率；当前素材是室内会议桌摆拍，不能推导复杂家庭环境鲁棒性。小物体、遮挡、身份连续性、层级位置和快速动作仍有风险。容器传播通过规则单元测试，尚未完成对应真实包内取出/离开压力测试。优先补拍并人工标注这些样本，再考虑采样调优和摄像头扩展。当前不做用户账户、实时监控、多摄像头、3D建模或模型训练。

## 官方技术来源

- 千问平台文档：https://platform.qianwenai.com/docs/developer-guides/getting-started/pricing （未据此假定当前账号单价）。
- 模型介绍：https://www.qianwenai.com/models/qwen3.6-plus 。
- 百炼兼容接口说明：https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions 。服务账户和地区以本人配置为准。
''')
    write(ROOT/'docs/DEMO_SCRIPT.md','''# 真实演示脚本与复现说明

成片：submission/demo/ShijiHome-Demo.mp4，H.264 / yuv420p，1440×1000，109.36秒，中文字幕烧录，无语音。不是官方规格；待核实赛事对比例/大小/字幕及AI标识要求。没有模型等待快进，原浏览器WebM另保存在私有data/validation，不打包无关录制文件。

| 时间 | 实际内容 | 旁白或字幕要点 |
|---|---|---|
| 00–04 | 首页 | 让家拥有记忆，让过去可以被追问 |
| 04–10 | 上传并播放真实18秒片段 | 原始正确方向视频16–34秒，无预写结果 |
| 10–45 | 点击分析，真实等待 | Qwen按8秒窗读取关键帧，3请求，35.331秒 |
| 45–55 | 5条事件与提问 | 桌面、拿起、放入置物架；询问最后出现 |
| 55–67 | 回答与证据跳转 | 定位12秒，核对原视频和关键帧 |
| 67–74 | LifeReplay | 选择笔记本，串联经历，点击缩略图回放 |
| 74–80 | 询问现在是否仍在那里 | 明确无法确认现实当前位置 |
| 80–85 | 询问未匹配护照 | 说明未知，不编造 |
| 85–109 | 物品历史与结尾 | 观察、条件推断、未知；说明当前范围 |

字幕编辑源为ShijiHome-Demo.srt与captions.ass。真实录制脚本在scripts/record_real_demo.py；重录会触发付费模型，脚本已有报告时拒绝重复运行，应先检查请求预算，不能无意义重跑。导出脚本scripts/export_demo_media.py使用DEMO_FFMPEG指定支持VP9与libass的FFmpeg。

无需新调用的演示：启动交付应用，选择“真实模型结果回放”，打开LifeReplay并选择笔记本，提问“最后在哪里出现”“什么时候被拿起”“现在还在那里吗”，点击证据；此过程回放已保存真实结果，界面明确标识。新上传视频和开始分析才会执行新的模型调用。

若赛事需要横向16:9或配音，保留原始页面画面重新排版/录制并核对字幕；当前成片无旁白录音。出镜队员与素材使用授权、最终赛事字段须由团队确认，当前没有公开发布。
''')
    write(ROOT/'docs/FINAL_DELIVERY.md',f'''# 本轮最终交付

2026-10-18。比赛截止2026-10-21 23:59，官方时区待核实，内部按Asia/Shanghai规划。**已完成真实Qwen、真实视频与浏览器闭环；未向赛事或公网提交。**

已完成：MP4上传/校验/播放、PTS抽帧与可配置窗口、Qwen适配与有限重试、结构及语义校验、事件与状态SQLite持久化、查询意图/动作路由、一层条件容器规则、观察/推断/未知分栏、LifeReplay缩略图与原视频定位、Windows start.bat、真实历史记录离线回放、可编辑设计与封面、109.36秒实际录屏及字幕、长短介绍、技术与测试报告、精选交付目录。

实际验证：38项后端自动化、前端构建、独立Mock浏览器流程、真实文本/单图、两段源视频派生的3次真实分析、真实Chrome完整上传到问答和证据跳转。共{usage['requests']}次真实调用，输入{usage['input_tokens']:,}/输出{usage['output_tokens']:,} token，未确认金额。Mock和真实结果在报告中分开。

当前限制：没有人工gold准确率；没有系统化真实容器取出/离开链、同物混淆与快速动作压力评测；人民币硬上限未确认；原始手机带旋转元数据需先正确导出；新机器需安装Python依赖、FFmpeg/FFprobe，已有前端构建无需Node运行；实时摄像头等P2不开发。

本机双击start.bat，或powershell -ExecutionPolicy Bypass -File .\\start.ps1。打开http://127.0.0.1:8000。已有本地.env可直接调用配置好的模型；交付包没有密钥，离线真实记录仍可回放。详见README.md与TEST_RESULTS.md。

需要团队完成：核对账号账单/单价与额度；确认素材中人员同意用于赛事展示；登录本人赛事页面确认时区、正式赛道、封面/视频/压缩包规格与字数限制、团队资料；预演后由队长实际提交并保留回执。封面1600×900与演示尺寸是当前输出，不是已核实官方要求。

下一轮优先：①独立人工标注与遮挡/放入取出/相似物样本；②跨窗身份与架体/架格层级歧义处理；③依据真实账单固定费用配置；④按官方字段调整并冻结提交包。没有这些补充不宣传识别准确率或生产级家庭监控能力。
''')
    write(ROOT/'README.md','''# 拾迹 Home

让家拥有记忆，让过去可以被追问。

**2026-10-18：真实Qwen与真实手机视频闭环已跑通，38项后端测试及真实/模拟浏览器检查通过，实际演示和交付包已生成。** 截止2026-10-21 23:59，官方时区待核实。未公开发布或提交赛事。

本地视频上传 → PTS关键帧 → Qwen分窗观察 → Schema/证据校验 → SQLite时序记忆 → 带证据问答 → LifeReplay和原视频回看。当前是预录制单机位短视频MVP。

## 本机运行

双击 `start.bat`，或：

```powershell
Set-Location 'F:\\比赛\\AI黑客松'
.\\start.ps1
```

打开 http://127.0.0.1:8000 。服务只监听本机；已经运行时直接打开网页。Ctrl+C停止。正式交付目录为 `submission/`，包内含前端构建与明确标识的真实历史结果回放，新环境无需API密钥即可查询这份记录，未将其冒充现场重新识别。

数据位于本机.env中DATA_DIR指定目录，当前为项目data；原视频、帧与响应只保存在本地，不修改video下原片。密钥已经安全迁移到.env并清空公开模板，不要将.env交给赛事或发到聊天。

## 新环境安装

Windows、Python3.13、支持H.264的FFmpeg及FFprobe。交付包运行不需要Node；修改前端才需Node/npm。

```powershell
py -3.13 -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt
# 仅当没有.env时复制，切勿覆盖已有配置
Copy-Item .env.example .env
.\\start.ps1
```

start.ps1按 `-Python`参数、SHIJI_PYTHON、项目.venv、本机D:\\MemoryOSHomePrep\\venv顺序寻找环境，检查Python依赖；FFmpeg/FFprobe可用PATH或.env的FFMPEG_PATH/FFPROBE_PATH配置。不自动下载二进制或模型。如果没有前端dist，脚本会执行npm ci和npm run build。

## 配置新视频识别

本机已有有效配置；提交包模板为空密钥且默认关闭网络。在本地.env设置：

| 变量 | 含义 |
|---|---|
| VLM_PROVIDER | qwen |
| VLM_API_KEY | 本人密钥，严禁提交或打印 |
| VLM_BASE_URL | 本轮已验证 https://maas.qianwenaiapi.com/compatible-mode/v1 |
| VLM_MODEL | 本轮已验证 qwen3.6-plus |
| VLM_ENABLE_NETWORK | 明确使用API后设true，重启服务 |
| VLM_BILLING_MODE | request_limited严格限制请求规模；不是人民币硬上限 |
| MAX_MODEL_CALLS_PER_VIDEO / MAX_MODEL_CALLS_PER_DAY | 每次分析12、每日24，失败和重试计数 |
| MAX_FRAME_REFERENCES / MAX_OUTPUT_TOKENS_PER_VIDEO | 128 / 8192，重复帧与重试占用额度 |
| VIDEO_SAMPLING_FPS / VIDEO_WINDOW_SECONDS / VIDEO_WINDOW_STRIDE_SECONDS | 默认1 / 8 / 6 |
| MAX_CONCURRENCY / REQUEST_TIMEOUT_SECONDS / VLM_MAX_RETRIES | 默认2 / 45 / 1 |
| DATA_DIR | 默认./data，可用绝对路径 |

金额模式priced还需VLM_COST_CONFIRMED=true和已核实的VLM_INPUT_RMB_PER_MILLION、VLM_OUTPUT_RMB_PER_MILLION；MAX_COST_RMB_PER_VIDEO/DAY默认2/5仅在该模式参与预算。未核实当前账号价格，实际调用采用request_limited，费用未知；控制台账单/余额限制仍需本人核对。

## 使用与验证

上传≤60秒/100MB的MP4，检查方向后点击分析，查看事件；选择LifeReplay物品后可用“这个物品”提问，清除选择后按名称问其他物品。支持最后出现、拿起/放入/取出/打开/关闭/离开时间与移动经历；超出检索范围的问题明确未知。点击时间引用显示关键帧并定位。观察是模型候选主张，未经人工确认，当前现实位置不可保证。

```powershell
# 自动化不发真实模型请求
& 'D:\\MemoryOSHomePrep\\venv\\Scripts\\python.exe' -m pytest -q
# 独立Mock浏览器检查需先停止占用8000的正常服务
& 'D:\\MemoryOSHomePrep\\venv\\Scripts\\python.exe' scripts/browser_smoke.py
# 修改前端
Set-Location frontend
npm ci
npm run build
```

测试依赖在requirements-dev.txt；Playwright使用已安装Chrome。开发模式分别启动uvicorn backend.main:create_app --factory --host 127.0.0.1 --port 8000与frontend下npm run dev（5173）。

详见 docs/FINAL_DELIVERY.md、docs/TEST_RESULTS.md、docs/TECHNICAL_REPORT.md、docs/PROJECT_INTRODUCTION.md、docs/DEMO_SCRIPT.md。旧PREPARATION_REPORT/MVP_DELIVERY是历史快照，当前完成状态以FINAL_DELIVERY为准。真实原始运行记录在私有data/validation，不含在公开源代码中；精选证据与去密钥记录随交付包提供。

未实现：实时摄像头、多摄像头、用户账户、高级重识别。没有标注评估集，未测识别准确率；容器规则用合成单测验证，不能称真实复杂放包场景已验证。官方上传规格、素材许可确认、最终比赛提交仍由团队完成。
''')
    # Keep historical decisions, with an explicit current-status pointer.
    for name in ['PREPARATION_REPORT','MVP_SCOPE','ARCHITECTURE','DEVELOPMENT_PLAN','TEST_PLAN','MVP_DELIVERY','SUBMISSION_CHECKLIST','DEMO_STORYBOARD']:
        path = ROOT/'docs'/f'{name}.md'
        text = path.read_text(encoding='utf8')
        marker = '> 历史阶段快照：当前真实模型、视频验证与交付状态请见 [FINAL_DELIVERY](FINAL_DELIVERY.md) 和 [TEST_RESULTS](TEST_RESULTS.md)。\n\n'
        if not text.startswith(marker):
            write(path,marker+text)
    write(ROOT/'submission/README.md','''# 拾迹 Home 交付包

作品：拾迹 Home——基于时序视觉记忆的家庭 AI 助手。

- application：必要源代码、前端构建、依赖锁定、start.bat，以及有明确标识的已保存真实模型记录。
- design：可编辑SVG封面、1600×900 PNG、HTML设计源、Mermaid架构、实际界面截图。
- demo：109.36秒真实操作MP4、字幕编辑源与复现脚本。
- documents：长短作品介绍、技术报告、测试结果、交付说明与第三方说明。

先读application/README.md，安装Python依赖及FFmpeg/FFprobe后双击application/start.bat。交付包没有.env和密钥，启动会导入一份真实历史记录，首次查询请选择“真实模型结果回放”；不调用模型即可回顾其证据。要识别新视频请复制空模板并填写本地Qwen配置，重启服务。

演示视频为真实Chrome应用录屏加中文字幕，无语音、未快进模型等待、无Mock事件。当前尺寸/封面是内部输出，未假定官方要求；需要本人确认赛事字段与素材出镜授权后实际提交。未部署公网或代为提交。

测试与真实性边界见documents/TEST_RESULTS.md。没有人工标注准确率，人民币费用尚待账单核实。交付检查和SHA256在package-validation.json与manifest-sha256.json。
''')
    # Curated source only; no raw data, .env, git history, dependencies or debug logs.
    for directory in ['backend','schemas','prompts','tests']:
        shutil.copytree(ROOT/directory, app/directory,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for relative in ['frontend/src','frontend/dist']:
        shutil.copytree(ROOT/relative,app/relative,dirs_exist_ok=True)
    for name in ['package.json','package-lock.json','tsconfig.json','vite.config.ts','index.html']:
        (app/'frontend').mkdir(exist_ok=True)
        shutil.copy2(ROOT/'frontend'/name,app/'frontend'/name)
    for name in ['README.md','start.bat','start.ps1','.env.example','.gitignore','requirements.txt','requirements-dev.txt','requirements.lock.txt','pyproject.toml']:
        shutil.copy2(ROOT/name,app/name)
    for name in ['import_demo.py','browser_smoke.py']:
        (app/'scripts').mkdir(exist_ok=True)
        shutil.copy2(ROOT/'scripts'/name,app/'scripts'/name)
    (app/'evidence').mkdir(exist_ok=True)
    shutil.copy2(ROOT/'evidence/backend-tests.xml',app/'evidence/backend-tests.xml')
    (ROOT/'submission/documents').mkdir(exist_ok=True)
    for name in ['PROJECT_INTRODUCTION','TECHNICAL_REPORT','TEST_RESULTS','FINAL_DELIVERY','DEMO_SCRIPT']:
        shutil.copy2(ROOT/'docs'/f'{name}.md',ROOT/'submission/documents'/f'{name}.md')
    shutil.copytree(ROOT/'design',ROOT/'submission/design',dirs_exist_ok=True)
    for name in ['ui-real-answer.png','ui-real-replay.png']:
        shutil.copy2(ROOT/'evidence'/name, ROOT/'submission/design'/name)
    for name in ['record_real_demo.py','export_demo_media.py']:
        shutil.copy2(ROOT/'scripts'/name,ROOT/'submission/demo'/name)
    # Actual MIT notices for bundled React, React DOM and Scheduler runtime code.
    notices=[]
    for package in ['react','react-dom','scheduler']:
        notices.append(f'## {package}\n\n'+(ROOT/'frontend/node_modules'/package/'LICENSE').read_text(encoding='utf8'))
    write(ROOT/'submission/documents/THIRD_PARTY_NOTICES.md','''# 第三方组件与素材说明

前端构建打包React、React DOM及Scheduler，MIT许可原文如下。Python库、Node依赖与FFmpeg二进制不随包分发，版本由requirements与package-lock记录；新机器按各上游许可安装。FastAPI、Pydantic、HTTPX、NumPy、OpenCV、JSONSchema等未引入训练权重或第三方论文实现。

视觉服务为本人配置的Qwen3.6-plus API，使用与付费依据账户协议，模型权重未分发。演示源为用户提供的手机实拍视频，经正确方向版本派生连续片段；团队仍需确认出镜人员与赛事展示使用权。封面图形为项目SVG绘制，字体引用系统字体，未打包字体文件。AI用于代码辅助及视觉事件提取，模型主张在界面标识待核对。

本项目原创代码未擅自指定开源许可证，提交授权范围以作者决定及赛事协议为准。

'''+ '\n\n'.join(notices))
    shutil.copy2(ROOT/'submission/documents/THIRD_PARTY_NOTICES.md',app/'THIRD_PARTY_NOTICES.md')
    summary={'generated_at_utc':datetime.now(timezone.utc).isoformat(),'real_usage':usage,'model':settings.model,'model_key_configured_locally':bool(settings.api_key),'source_invariants':'Original source videos unchanged; see final hash verification.'}
    write(ROOT/'evidence/final-summary.json',json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps({'status':'CURATED','real_requests':usage['requests'],'input_tokens':usage['input_tokens'],'output_tokens':usage['output_tokens'],'files':sum(p.is_file() for p in (ROOT/'submission').rglob('*'))},ensure_ascii=False))


if __name__=='__main__':
    main()
