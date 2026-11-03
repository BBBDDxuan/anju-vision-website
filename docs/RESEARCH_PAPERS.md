# 重点论文与可借鉴设计

调研日期：2026-10-18。工作流：multi-source-search；以 arXiv、会议/作者页面、作者 GitHub 核对标题和版本，按 arXiv ID/DOI 去重。当前会话未提供学术检索 MCP；技能的 OpenAlex 后备脚本返回 HTTP429，随后使用网页检索访问上述一手来源。不是穷尽性综述，未复现论文。

## 1. Ego4D: Around the World in 3,000 Hours of Egocentric Video

- 作者：Kristen Grauman 等；2021-10-13 首次 arXiv，2022-03-11 v3；CVPR 2022。
- 问题/方法：以第一人称视频研究过去、现在和未来；Episodic Memory 的 NLQ 返回答案所在时间窗，VQ 返回对象最后出现的时空轨迹，MQ 返回活动时间段。
- 借鉴：评测“答案是否被相关时间段支持”，不要仅检查生成文字相似；将最后观察位置作为明确任务。
- 代码：[EGO4D/episodic-memory](https://github.com/EGO4D/episodic-memory)。当前不集成训练/全量数据管线，只借用时间证据标注格式。
- 小实验：为每个问题人工标注一个或多个证据窗，统计证据时间 IoU、最后观察准确性。
- 边界：第一人称移动视频与本项目固定机位不同，不能将其榜单结果当成本项目预计准确率。
- 来源：[论文元数据](https://arxiv.org/abs/2110.07058)、[官方任务定义](https://ego4d-data.org/docs/benchmarks/episodic-memory/)。网页任务文档未标发布日期。

## 2. Online Episodic Memory Visual Query Localization with Egocentric Streaming Object Memory（ESOM）

- 作者（核对 v3）：Zaira Manigrasso、Matteo Dunnhofer、Antonino Furnari、Moritz Nottebaum、Antonio Finocchiaro、Davide Marana、Rosario Forte、Giovanni Maria Farinella、Christian Micheloni。
- 年份：arXiv v1 2024-11-25，v3 2025-12-19；正式会议 WACV 2026。引用时不要只写“2024 已发表”。DOI：10.1109/WACV61042.2026.00385。
- 问题/方法：OVQ2D 在线只看一次视频，以对象发现、跟踪和紧凑对象记忆回答最后出现位置的视觉查询。
- **结果核验**：v3 表1/讨论的真实配置约 4.02% success；理想跟踪约31.91%、理想发现约40.55%，两者均理想为81.92%。这些是作者指定任务和配置的结果，不是我们系统的分数。理想输入的81.92%不能代表现实部署，也不能直接与离线VQ2D比较。
- 代码核验：检查 arXiv v3、作者主页和课题组条目，**本次未找到可确认的官方 ESOM 仓库地址**；不编造 GitHub URL，也不据此声称作者没有代码。
- 借鉴/集成：借对象 ID、时空记录、读写分离；不整体复现。小物体漏检、遮挡和关联是实际风险，不能靠状态机自动修复。
- 小实验：同一物体连续可见、进入不透明容器、两个同类对象交错分别测试；报告身份错误和漏动作，单独记录理想人工事件输入下的推理成绩。
- 来源：[arXiv v3](https://arxiv.org/abs/2411.16934v3)、[全文与表格](https://arxiv.org/html/2411.16934v3)、[会议论文](https://openaccess.thecvf.com/content/WACV2026/papers/Manigrasso_Online_Episodic_Memory_Visual_Query_Localization_with_Egocentric_Streaming_Object_WACV_2026_paper.pdf)、[作者课题组](https://coai-iplab.github.io/research/manigrasso2026online/)。全文引言有 oracle 名称顺序表述差异，以上以表格与摘要一致的值为准。

## 3. VideoRAG: Retrieval-Augmented Generation with Extreme Long-Context Videos

- 作者：Xubin Ren、Lingrui Xu、Long Xia、Shuaiqiang Wang、Dawei Yin、Chao Huang；arXiv 2025-02-03。仓库现标 KDD2026，但本次以已核对的 arXiv 2025 版讨论方法。
- 问题/方法：长视频跨片段/跨视频检索，双通道结合文本知识图和视觉上下文；作者提出 LongerVideos 数据集（160+ 视频、134+ 小时）。本次不摘录未复核的性能提升数。
- 借鉴：文本事件索引必须链接回视觉证据；检索结果不能替代原始画面。
- 代码：[HKUDS/VideoRAG](https://github.com/HKUDS/VideoRAG)。不整体集成；本项目单个短视频无需图数据库、多路 embedding 或完整 GraphRAG。
- 小实验：实体+时间过滤与“所有事件给模型”对照，观察证据遗漏与顺序混淆；固定模型和输入预算。
- 来源：[论文](https://arxiv.org/abs/2502.01549)、上述作者仓库。注意与 arXiv:2501.05874 的同名 VideoRAG、以及 Video-RAG 不是同一论文。

## 4. Memory OS of AI Agent

- 作者：Jiazheng Kang、Mingming Ji、Zhe Zhao、Ting Bai（按当前 arXiv 作者栏）；arXiv 2025-05-30。作者仓库标 EMNLP2025 Oral。
- 问题/方法：长期对话记忆与个性化，分短/中/长期存储，分离 storage、updating、retrieval、generation。
- 借鉴：事件日志与派生状态分离，保留版本和来源；不要用不断覆盖的一段自由文本维护全部记忆。
- 代码：[BAI-LAB/MemoryOS](https://github.com/BAI-LAB/MemoryOS)，Apache-2.0。当前不集成：LoCoMo 对话成绩不能证明视频状态推理有效，也不需要三层对话记忆。
- 小实验：相同事件在乱序到达、重复导入后重建状态，检查结果一致性和幂等性。
- 来源：[论文](https://arxiv.org/abs/2506.06326)、作者仓库。此工作同时构成原项目名称混淆依据。

## 5. Reliable Visual Question Answering: Abstain Rather Than Answer Incorrectly

- 作者：Spencer Whitehead、Suzanne Petryk、Vedaad Shakib、Joseph Gonzalez、Trevor Darrell、Anna Rohrbach、Marcus Rohrbach；arXiv 2022-04-28，ECCV2022。
- 问题/方法：允许拒答，比较 risk/coverage，研究 MaxProb 和多模态选择函数。模型输出的分数不天然等于校准概率。
- 借鉴：一起报告答了多少、答错多少；不能用“全部拒答”制造高可靠性。作者VQAv2结果不迁移为视频可靠性保证。
- 代码：[facebookresearch/reliable_vqa](https://github.com/facebookresearch/reliable_vqa)。借评估定义，不训练选择器、不引入MMF。
- 小实验：缺失动作/离开视野问题与有明确证据问题各占一半，比较不支持的确定回答率、拒答召回和可回答覆盖率。
- 来源：[论文](https://arxiv.org/abs/2204.13631)、作者代码。未摘录未复核的表格数值。

## 主题到当前设计的映射

| 主题 | 依据与采用方式 | 当前不做 |
|---|---|---|
| 第一人称/视觉记忆 | Ego4D、ESOM 的最后观察与证据定位任务 | 用第一人称指标冒充固定机位成绩 |
| 身份/状态变化 | ESOM 提醒感知瓶颈；局部对象 ID + on/in/held_by 关系 | 训练复杂 Re-ID、遮挡后必然同一对象的假设 |
| Temporal Event Graph | 将事件按时间连接；关系边带起止与来源。VideoRAG 提供图式记忆参考，但不是本方案的实证验证 | 宣称自己首创时序事件图；安装图数据库 |
| Multimodal RAG / evidence-grounded VQA | 结构化检索→原始帧核对→引用支持的回答 | 只检索字幕后称理解了视觉细节 |
| Agent 长期记忆 | MemoryOS 的日志/更新/检索分层概念 | 多日、跨会话长期记忆和用户画像 |
| 不确定性/拒答 | ReliableVQA 的风险与覆盖思想；规则检查缺证据 | 把模型自报0.95当95%正确率，或承诺概率校准 |

**研究判断**：贡献应落在有约束的实现与可复现实验，不是发明新研究方向。首先用自摄小样本验证，再决定是否值得扩展。
