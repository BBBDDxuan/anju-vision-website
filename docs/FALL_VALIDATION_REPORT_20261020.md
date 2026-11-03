# 本地模拟跌倒视频验证报告（2026-10-20）

本轮对10段本地CAUCAFall模拟跌倒视频完成真实结果与网页核对，其中复用2段已完成分析，其余8段新增16次Qwen请求。模型为`qwen3.8-max`，基础提示词`event-v1.4`。每日上限保持48，结束时累计47/48，未提高限额。人民币金额未在本地核定，以服务账单为准。

## 结果

- 9段完整完成；S01仅完成后半窗口，不能称为全片成功。
- 10段都有fall候选字段：7条observed、3条inferred。但S09动作与说明结论矛盾，不能据字段统计“10段全部识别正确”。
- 每段网页各有1条跌倒时间线、1条红色跌倒提醒、1条跌倒回溯项；本轮未发现重复跌倒预警。
- 10段指定人物的问答、视频证据跳转和预警证据图片均可用，浏览器未报告脚本错误。全局人物问答则10段均复现问题。
- 所有已保存记忆通过结构及证据引用检查；原始视频、此前已完成的分析均保持不变。结构通过不代表语义正确。
- 本轮新增任务耗时范围18.3–62.9秒，中位数38.3秒。为当前网络/API下的实测任务耗时，不含复用旧结果，也不是12小时录像速度估计。

## 逐段结果

表中区间是模型引用证据的时间范围，不是人工精确标注的动作起止。点击样本打开本地系统。

| 样本 | 发布方类别 | 完成状态 | 模型跌倒证据区间与类型 | 红色跌倒提醒数 |
| --- | --- | --- | --- | --- |
| [S01](http://127.0.0.1:8000/workspace?video=v_95bf56e433f8497dac079acf3fa9fc12) | Fall forward | partial | 6.0s–7.0s inferred | 1 |
| [S02](http://127.0.0.1:8000/workspace?video=v_bcd1ac849034412cb54b02120bdfdf08) | Fall backwards | completed | 2.0s–4.0s observed | 1 |
| [S03](http://127.0.0.1:8000/workspace?video=v_f7eb8af0ed134a17be9206db6e5b3097) | Fall left | completed | 4.0s–6.0s observed | 1 |
| [S04](http://127.0.0.1:8000/workspace?video=v_c9c1bfa2fc7347ada3e58efda1301507) | Fall right | completed | 1.0s–3.0s inferred | 1 |
| [S05](http://127.0.0.1:8000/workspace?video=v_6bed3b09c5ff4682aca2f3ff15e15769) | Fall sitting | completed | 2.0s–4.0s observed | 1 |
| [S06](http://127.0.0.1:8000/workspace?video=v_29ddd6fdea8d4635ad8e3892898a97fa) | Fall forward | completed | 3.0s–5.0s observed | 1 |
| [S07](http://127.0.0.1:8000/workspace?video=v_8a9f82cd922944a487625b25419e1181) | Fall backwards | completed | 3.0s–5.0s observed | 1 |
| [S08](http://127.0.0.1:8000/workspace?video=v_f8d58a4843804b7bacbfb8ef4d125cf5) | Fall left | completed | 1.0s–3.0s observed | 1 |
| [S09](http://127.0.0.1:8000/workspace?video=v_74e2b8463ce64d41acba2b8e15a4ed03) | Fall right | completed | 2.0s–3.0s inferred | 1 |
| [S10](http://127.0.0.1:8000/workspace?video=v_604b4bfb73964f60a5559c74ac008564) | Fall sitting | completed | 2.0s–4.0s observed | 1 |

## 已确认的问题与优先级

### P1 · invalid_event_rejects_window

S01 0–8秒的lying_on_floor仅引用f000140，触发PERSON_EVIDENCE，整窗拒收；同一响应另有5–7秒fall候选未保存。最终只保留后窗6–7秒推测。

建议：在不降低跌倒证据标准的前提下隔离不合格事件；保留可验证事件及拒收审计，或将语义校验失败纳入受限重观察。必须继续标注覆盖缺口。

### P1 · action_rationale_conflict

S09 action=fall、inferred、自评分0.6；同条说明的最终结论选择lie_down。仍产生一条红色跌倒预警。不能把该fall字段直接计为正确检出。

建议：增加动作与最终说明的一致性校验。矛盾记录暂作未决候选，必要时受限重观察；不依据样本文件名强行改成fall。限制理由长度，保留替代解释，禁止无画面依据的健康状态猜测。

### P1 · global_person_query_rejected

10段直接问“视频中什么时候有人跌倒？”均返回unknown、无证据，并要求选择物品；指定人物后10段均可返回已有跌倒证据。

建议：为人物动作实现全片事件检索路径；只有问题确实指向特定但身份有歧义的人物时才要求选择对象。

### P2 · ongoing_floor_posture_split

S10躺卧记录4–7秒与6–13秒合并为4–13秒，但12–14秒又单独展示。其间seen_at辅助观察打断了同一姿态的活动分组。

建议：区分持续状态整合与同一次动作候选去重；辅助出现记录不应打断兼容姿态，起身/行走等反证仍应阻止整合。保留分段证据，不宣称采样间绝对连续。

### P2 · mixed_reasons_force_yellow

10条跌倒均为黄色，其中7条为observed且自评分达到0.9或以上；uncertainty_reasons混入了直接画面说明，前端任何非空理由均降为黄色。

建议：区分证据说明与真正的不确定性。保留推测和歧义的黄色上限，不单纯删除理由或根据高自评分升绿。

## 不应误删的相似记录

S03在0秒出现与6–8秒倒地后的出现，中间经历行走/跌倒，是不同阶段，不能只看标题相同就删除。S07的两条躺卧之间有明确保留的unknown手臂活动记录，因此当前保守规则主动分开；这与S10仅被seen_at辅助观察打断不同，不宜直接套同一删重规则。

## 验证边界与复查

本轮逐段检查了0.5秒间隔的原片联系图，均可见一次向地面下降过程；该检查不是用户人工核验，也不是数据集逐帧真值。全部都是同一数据集中的模拟跌倒正样本，场景相近，没有正常活动负样本。因此不能给出日常家庭误报率、真实老人跌倒准确率或临床安全结论。文件名/发布方类别只作对照，未作为模型输入提示或补造识别事件。

本轮保留现有识别规则做基线验证，发现的问题尚未修改。建议先修复三个P1问题，再处理持续姿态分组与颜色理由分离；下一轮加入正常坐下、弯腰、俯卧撑和主动躺下等负样本。

原始结果和证据索引：`data/validation/fall-suite-20261020/`。其中`results.csv`为逐段结果；`suite.json`为实际请求、任务、事件和预警记录；`browser-audit.json`为10段网页检查；`visual-review.json`为独立帧核对说明；`Sxx-contact.jpg`和三张`Sxx-browser.png`供回看。原始模型响应仍在各自`data/runs/<job_id>/`目录。

复现：`python -m scripts.fall_suite_browser_audit`仅读已保存结果，不调用模型。`scripts.fall_sample_suite`默认只准备检查图；付费测试必须显式传`--run-paid`，并受既有每日额度约束。
