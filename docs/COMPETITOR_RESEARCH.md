# 竞品与开源调研

调研日期：2026-10-18。范围限定官方资料和作者仓库；未购买硬件、订阅或进行产品实测。“未核实”不是“没有”。不比较未经验证的价格与准确率。

## 产品对照

| 产品/来源日期 | 官方已经说明的能力 | 用户问题与构想重合 | 对本项目的含义 |
|---|---|---|---|
| Gemini for Home；发布文 2025-10-01，帮助页未标发布日期 | 摄像头事件 AI 描述、Ask Home 自然语言历史搜索、Home Brief 日摘要；设备、套餐、地区有限制 | 高度重合视频问答、事件摘要与证据片段入口 | “自然语言查视频”不是新能力；帮助页未提供可复现实物容器状态传播与逐项拒答评估，不能据此断言 Google 不具备 |
| Ring Smart Video Search / 当前 Video Search；发布文 2024-10-09，帮助页未标日期 | VLM 匹配文本与画面，日常语言查活动/物体；有订阅、区域条件，帮助页说明无结果时可放宽查询 | 高度重合找物体/事件片段 | 不以搜索框或时间线 UI 当技术差异；未验证它能否处理钥匙→背包→离开视野这类完整链条 |
| Limitless/Rewind；官方公告 FAQ 内日期 2025-12-05、2025-12-19 | 官方宣布被 Meta 收购；Pendant 停售新客，既有客户有续支持安排；中国等地区服务停止；Rewind 自 2025-12-19 停止屏幕/音频捕捉；桌面/网页停止新录制，2026 年仍可访问旧会议 | 主要是个人声音/屏幕记忆，不等同固定摄像头物品追踪 | 不能继续把 Rewind 列作正常面向新客售卖的现行竞品；也不能说所有 Limitless 地区/功能同时全面关停 |
| Frigate；当前文档无发布日期 | 本地 NVR、目标检测/跟踪；对已跟踪目标做图文 embedding 语义搜索，可自动生成描述 | 物品搜索、视频本地索引也有开源基础 | “本地存储+语义搜索”同样不足以区分；本项目不为上传短 MP4 引入完整 NVR 部署 |

来源均于 2026-10-18 访问：

- Google：[发布说明](https://blog.google/products-and-platforms/devices/google-nest/gemini-for-home-launch/)、[摄像头帮助文档](https://support.google.com/googlehome/answer/15542305?hl=en)。
- Ring：[2024 发布说明](https://blog.ring.com/products-innovation/ring-launches-ai-powered-smart-video-search/)、[当前 Video Search 帮助](https://ring.com/support/articles/4l5lb/smart-video-search)。当前帮助页名称已变化，不沿用 2024 发布价格描述 2026 套餐。
- Limitless：[收购公告与 FAQ](https://www.limitless.ai/?fpr=aitoolhunt&via=aitoolhunt)。无查询参数首页抓取失败，此官方地址成功取得正文；不使用如今 rewind.ai 的品牌归属推定原团队状态。
- Frigate：[语义搜索](https://docs.frigate.video/configuration/semantic_search/)、[官方仓库](https://github.com/blakeblackshear/frigate)。

## GitHub 可复用资源与取舍

这里只核查 README/仓库与许可显示，不表示已克隆运行。真正引入代码时固定 commit 并复核依赖和模型许可。

| 资源 | 可借鉴内容 | 已核查许可/限制 | 当前建议 |
|---|---|---|---|
| [VideoAgent（YueFan1014）](https://github.com/YueFan1014/VideoAgent) | 视频记忆构建和工具化查询，包含目标记忆与重识别 | 仓库显示 Apache-2.0；README 实测环境 Ubuntu20.04 + RTX4090 24GB，多环境/模型/API | 借鉴 memory/query 分离；不整体集成到 8GB Windows 冲刺 |
| [HKUDS/VideoRAG](https://github.com/HKUDS/VideoRAG) | 长视频图式文本记忆+多模态检索 | 开源代码可访问；复用前核实各子组件许可和资源 | 不整体集成；60 秒短视频用 SQLite 与实体查询更可控 |
| [EGO4D/episodic-memory](https://github.com/EGO4D/episodic-memory) | NLQ/VQ 时间与空间证据评估格式 | 数据使用与代码许可需分别核对 | 借任务定义，不下载全量数据或训练 |
| [BAI-LAB/MemoryOS](https://github.com/BAI-LAB/MemoryOS) | 存储/更新/检索/生成分离、层级记忆 | 仓库显示 Apache-2.0，面向个性化对话 | 名称撞车依据；不接入长期对话记忆框架 |
| [facebookresearch/reliable_vqa](https://github.com/facebookresearch/reliable_vqa) | 风险、覆盖率与选择性回答的度量实现 | README 基于 MMF/PyTorch；复用前核许可 | 借鉴评估，不训练其选择器 |
| [Frigate](https://github.com/blakeblackshear/frigate) | 事件/目标与原视频的关联、语义搜索 | 仓库显示 MIT；模型各自许可 | 作为竞品与设计参考，不集成 NVR |

## 什么值得在 3 天内实现

**明确推荐**：同一问题同时展示最后直接证据、后续容器证据、当前位置未知原因；用户可点击复核。再用缺失动作、顺序反转和同类物品混淆来验证边界。

| 候选卖点 | 分类 | 能否证明差异 |
|---|---|---|
| 换名称、暗色 UI、聊天气泡、时间线皮肤 | 表达/UI | 不能证明算法或可靠性提升 |
| 自动组装证据卡并定位播放 | 可演示工程/交互能力 | 可验证时间跳转、证据完整性；不是新研究方向 |
| 事件顺序与容器关系的显式维护 | 工程实现 | 可用重排输入、取出反例检验；前提是感知事件正确 |
| 规则约束与引用校验降低错误确定回答 | 待验证技术假设 | 同模型/同视频/同预算对照，报告拒答和覆盖率；实测前不能写“提升” |

目前没有证据支持我们领先所有摄像头 AI。合理定位是一个范围明确、边界透明、评估可复现的黑客松原型。
