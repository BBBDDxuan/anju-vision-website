# 居家摄像头系统 失败窗口恢复 v1.1

这是对一个失败窗口的受限恢复。保持原有视觉判断标准、实体身份和证据要求，先看真实帧，不能为了修复格式编造事件。

只返回Extraction对象，顶层键严格为entities、events、uncertainty。不要返回memory、逐帧描述、Markdown或解释正文。
entities字段仅为id、label、aliases、appearance、identity_status、entity_type；名称必须放在label，不用name。entity_type必须明确为person/object/location，人物行为主体必须是person，不能省略后变成默认object。
events字段仅为subject_id、target_id、action、epistemic_type、frame_ids、uncertainty_reasons、model_score。
禁止使用id、type、subject、actor、timestamp_ms、frame_id、source_pts_ms、description、entity_ids替代这些事件字段。frame_ids是数组。
复用已知实体ID时无需重复描述该实体；新实体只描述一次。只记录有意义的位置和状态变化，避免重复报告静止背景；不得省略实际关键动作。
理由精炼为一句中文：可见变化、未拍清的部分和必要条件。不要重复叙述整段故事，不返回模型分配的时间戳或事件ID。
无法确定时保留unknown；推测仍标inferred，并引用至少两个不同时刻的给定帧。所有实体与frame_id都必须来自所给内容。

顶层结构必须为{"entities":[],"events":[],"uncertainty":[]}，数组内容按所给JSON Schema填写。不能用p001/f001等实体ID作为顶层键，不能返回label/description/start_frame/end_frame组成的场景描述。不得从先前无效的文字描述转换或猜测动作，必须重新检查本次提供的真实图像和frame_id。新实体ID由你分配，复用实体必须使用已知候选ID；证据frame_id只能使用本次实际提供的ID。
