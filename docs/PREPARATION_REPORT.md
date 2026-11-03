> 历史阶段快照：当前真实模型、视频验证与交付状态请见 [FINAL_DELIVERY](FINAL_DELIVERY.md) 和 [TEST_RESULTS](TEST_RESULTS.md)。

# 拾迹 Home：调研与开发准备报告

> 历史准备阶段快照。用户已于本日确认开发，当前实现和验证状态以 [MVP_DELIVERY](MVP_DELIVERY.md) 与根目录README为准；以下“尚未实现”描述保留当时事实。

日期：2026-10-18，Asia/Shanghai。范围：环境核查、限时调研、技术路线、设计与开发准备；未进入完整产品开发。原始名称MemoryOS Home。用户最新截止：**2026-10-21 23:59**，官方时区待核。

时间盒记录：本轮约14:11开始核查，14:39完成准备文件检查，总计约30分钟；不再扩展竞品/论文清单。

## 结论先行

**推荐做“拾迹 Home：基于预录制视频证据的物品历史问答”。主方向是C（观察/推断/未知有明确边界），最多一个辅助亮点是B（LifeReplay证据时间线）。A只作为最小事件与关系状态底座。**

保留Slogan“让家拥有记忆，让过去可以被追问。”不把普通视频搜索当原创；不承诺实时摄像头、全屋追踪或镜头之外的当前位置。

## 调研得到的5个关键判断

1. **自然语言搜索摄像头历史和事件摘要已经有商业实现。** Google官方帮助列出Ask Home和Home Brief；Ring官方文档列出自然语言Video Search。搜索框、摘要或时间线皮肤本身不构成技术差异。[Google帮助](https://support.google.com/googlehome/answer/15542305?hl=en)、[Ring帮助](https://ring.com/support/articles/4l5lb/smart-video-search)
2. **“MemoryOS”名称已有直接混淆对象。** BAI-LAB已有同名AI记忆研究与开源框架，建议中文“拾迹 Home”。新候选未做商标权属验证。[作者仓库](https://github.com/BAI-LAB/MemoryOS)
3. **物品最后出现位置和视觉记忆不是新研究方向；真实感知仍很难。** Ego4D已定义相关查询；ESOM真实检测/跟踪配置约4.02% success，而81.92%依赖理想检测与跟踪输入，不能作为真实效果引用。本项目不复现整套追踪系统。[Ego4D任务](https://ego4d-data.org/docs/benchmarks/episodic-memory/)、[ESOM v3](https://arxiv.org/html/2411.16934v3)
4. **个人记忆产品状态必须更新。** Limitless官方公告收购与停售新Pendant，Rewind停止新屏幕/音频捕捉，中国等地区服务有终止安排；不能继续把它们都当成正常面向新用户的现行产品，也不能概括为全球所有功能一并关闭。[官方公告](https://www.limitless.ai/?fpr=aitoolhunt&via=aitoolhunt)
5. **可以验证的项目价值是减少不支持的确定断言，同时保留可答能力。** 证据链、条件推断与拒答是已有方向；本项目能否更可靠，要做同模型/同视频对照和固定事件规则消融，联合报告错误确定率与覆盖率。目前没有这类实验成绩。[Reliable VQA](https://arxiv.org/abs/2204.13631)

论文重点核对5项：Ego4D、ESOM、HKUDS VideoRAG、Memory OS of AI Agent、ReliableVQA。ESOM的标题/作者/版本/结果已核实，官方代码地址本次未确认；不虚构链接。学术MCP在当前会话不可用，OpenAlex后备脚本HTTP429，随后使用arXiv/作者/会议网页；详情与来源日期见对应研究文档。

## 最重要的风险及明确选择

**最大风险是视觉感知，不是聊天UI。** 小钥匙、入包瞬间、遮挡、两个相似背包和缺失动作会让事件本身错误；规则只能避免某些过度推断，不能自动证明错误观察为真。

首选“本地稀疏抽帧 + 分窗有序多图VLM + Python关系状态规则”。首个待测模型服务为百炼账户可用的`qwen3-vl-plus`，API不可用则换可用视觉型号或Gemini适配器；本地2B视觉模型作为有时限的备选，不在本阶段下载大模型。这里没有将模型文档能力当作本机实测能力。

首先验证**真实视频里可见put_in是否被正确识别，以及漏拍/出视野时是否保留unknown**。3段开发视频、固定人工时间窗，事件P/R各≥0.8、6类固定安全反例无错误确定当前位置、可答正确覆盖≥80%作为工程继续门槛；都尚未测试。不能仅演示一个成功故事。

## 推荐最小架构

FastAPI本地后端 + React/TypeScript薄前端 + OpenCV/项目内FFmpeg + SQLite + 可插拔视觉适配器；不引入向量库、图数据库、Agent框架、训练或多摄像头。

视频校验→PTS时间映射→1fps保底/8秒窗/6秒步长→视觉事件抽取→JSON与跨记录证据校验→不可变事件库→按时间重放状态→自然语言意图与实体检索→观察/条件推断/未知的短回答与视频证据。

每60秒视频总尝试（含重试）≤12次，帧引用（含重复）≤128；无凭据和价格上界则不发请求。建议2元/视频、5元/日是后续预算提案，尚无实际模型花费/消费授权或已实现的限额保护。首版支持少量对象、一层容器；容器内物品不可见后只能条件推断，离开视野后不能断言当前位置。

## 实际执行与验证边界

| 已执行 | 实际结果 | 不代表什么 |
|---|---|---|
| 目录、工具与密钥配置状态检查 | 初始只有赛事PDF、无Git/代码/模板；Node/npm可用；常见API环境变量在Process/User/Machine均未配置 | 不意味着用户在别处从未保存过密钥 |
| 独立D:准备环境 | FastAPI0.143.3等已安装并锁版本；进程内HTTP200，SQLite往返一致 | 未实现或启动业务服务，无端到端产品 |
| 合成媒体探针 | OpenCV100帧解码、4个抽帧时间点一致；旧FFmpeg H.264转码成功并可解码 | 不是真实摄像头视频、不是识别/浏览器视频播放成绩 |
| GPU环境检查 | RTX4060 8GB可见；全局torch为CPU版，已有其他环境CUDA可用 | 本项目未安装/运行本地视觉模型，不修改课程环境 |
| 赛事PDF | 4页已渲染阅读，原文件保留 | PDF没给封面规格/字数/截止；不是已核实全部赛事规则 |
| 用户报名页 | 公共页面可达，独立Chrome显示登录框 | 未读取登录后字段、未登录或提交 |
| 设计与契约 | SVG/HTML源已创建，Chrome预览已逐一目视；JSON Schema与手写示例通过结构校验 | 设计稿与示例数据不是实际模型分析记录；业务语义校验未实现 |

没有真实视频（用户确认尚未准备），未发现本项目可用API凭据，因此**视觉抽取、状态规则、产品问答、准确率、模型延迟与费用均未验证**。所有产品测试记录为NOT_RUN。

## 接下来约3天

- **10/18**：确认开发后先拍3段真实视频、配置视觉服务、验证put_in/缺证据；通过后固定契约。另3人并行做数据标注、设计、提交字段与录制准备。
- **10/19**：完成新视频上传→事件→问题→证据的第一版；前后端集成。若晚间未过关，删除全部P1。
- **10/20**：核心反例、顺序/身份/故障测试；有余力才加LifeReplay证据卡；封面定稿和真实演示录屏。
- **10/21**：上午最终本地验证与保留集记录，中午冻结；下午整理程序/源文件/视频/文案，20:00前提交，留出到23:59的缓冲。

详尽负责人、时段、交付门槛与失败降级见DEVELOPMENT_PLAN。官方时区/封面/视频/字数/赛道/附件限制由队员D在本人登录页核实，不用第三方网页补造规则。

## 文件导航（项目根目录：F:\比赛\AI黑客松）

| 交付内容 | 文件 |
|---|---|
| 当前报告与运行边界 | `docs/PREPARATION_REPORT.md`、`README.md` |
| 产品、命名、介绍、创新边界 | `docs/PRODUCT_BRIEF.md` |
| 竞品与论文调研 | `docs/COMPETITOR_RESEARCH.md`、`docs/RESEARCH_PAPERS.md` |
| 环境与技术验证方案 | `docs/TECHNICAL_FEASIBILITY.md` |
| 功能边界与架构 | `docs/MVP_SCOPE.md`、`docs/ARCHITECTURE.md` |
| 排期与团队分工 | `docs/DEVELOPMENT_PLAN.md` |
| 测试、指标与结果模板 | `docs/TEST_PLAN.md`、`evaluation/results_template.csv` |
| 演示脚本与提交核对 | `docs/DEMO_STORYBOARD.md`、`docs/SUBMISSION_CHECKLIST.md` |
| 可编辑设计 | `docs/DESIGN_GUIDE.md`、`design/cover.svg`、`design/ui-wireframe.html`、`design/architecture.mmd` |
| 视觉预览 | `design/cover-preview.png`、`design/ui-preview.png` |
| 数据契约与提示草案 | `schemas/memory.schema.json`、`examples/design_fixture.json`、`prompts/event_extraction.md` |
| 可重跑环境/媒体探针 | `scripts/preflight.py`、`scripts/video_probe.py`、`requirements-prep.lock.txt` |
| 实际检查记录 | `evidence/environment.json`、`evidence/video_probe.json`、`evidence/preparation_checks.json` |
| 安全配置模板 | `.env.example`、`.gitignore` |

不覆盖原赛事PDF；未初始化远程仓库、发布网站、发送消息或提交比赛。本阶段到此停止，等待用户确认进入开发。
