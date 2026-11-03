# 单条老人居家视频的访问申请

核实日期：2026-10-20。仅申请少量样本，不申请批量下载；本文件是申请草稿，未代用户提交任何信息或接受协议。

## Toyota Smarthome Untrimmed：真实老人居家长视频

官方入口：https://project.inria.fr/toyotasmarthome/

官网 `Toyota Smathome Data Request Form` 的字段为 First Name、Last Name、Email、Affiliation、Request dataset type、Short description of your Project、Agreement。数据类型选 **Toyota Smarthome Untrimmed**。

1. 使用本人真实姓名、学校/单位和学校/工作邮箱。官网说明普通个人邮箱申请会被忽略；学校邮箱若采用 Gmail，项目介绍需提供导师姓名和邮箱。
2. 阅读许可后由本人决定是否接受：https://project.inria.fr/toyotasmarthome/files/2020/12/License_v2.pdf
3. 如实说明研究、比赛和展示用途，只需一段约 10–30 分钟的未剪辑 RGB 视频。不要把商业产品测试描述为纯学术用途。
4. 我们当前使用 Qwen 云端视觉 API，会把抽取的视频帧发送至第三方服务。许可限制向第三方提供数据，因此需请作者明确确认这种处理，以及比赛演示/截图是否允许。仅获得下载链接不等于获准上述用途。
5. 官网提示：提交后 3 天仍无下载链接或回复，可以联系 `toyotasmarthome@inria.fr`，或用其备用表单：https://docs.google.com/forms/d/e/1FAIpQLSdl-wH5xMitp6Nh88Ieu3gRcpuFSB_Ja_IgCZ2gGdf8Bn0Kjw/viewform?usp=sf_link 。这不是承诺 3 天内审批。

### 项目介绍英文草稿

根据实际情况填写方括号，不要虚构学校、导师或用途：

> I am [name], a [role] at [institution]. I am developing a home-video monitoring prototype for [accurate research / education / hackathon purpose]. I would like to request one untrimmed RGB recording, approximately 10–30 minutes long, showing older adults performing everyday activities at home. I do not need the complete dataset.
>
> The prototype performs local frame-change screening and uses the Qwen cloud vision API to identify activities from sampled frames. Please confirm whether transmitting sampled frames to this third-party service is permitted. Please also clarify whether brief excerpts or screenshots may be shown in a hackathon demonstration or submission. If these uses require additional permission, I would appreciate your guidance.
>
> I will follow the applicable license and any additional restrictions you specify. I do not plan to redistribute the original recording or train model weights using it.

## SAFER-Activities：可选的连续行为/模拟跌倒素材

官方入口：https://huggingface.co/datasets/SAFER-Activities/SAFER-Activities

1. 本人注册并登录 Hugging Face。
2. 打开数据集页，阅读许可和访问表单；页面明确要求同意向作者分享联系方式。
3. 如实填写用途并自行决定是否接受条款、提交访问请求。Hugging Face 支持自动或人工审核，实际以本页面提示为准，不承诺批准时间。
4. 通过后仅选择一个原始视频及对应标注；没有必要下载整个仓库。云端分析和公开演示用途也应与许可/作者要求核对。

官方访问机制说明：https://huggingface.co/docs/hub/datasets-gated

## 当前可先用的公开跌倒样本

CAUCAFall：https://data.mendeley.com/datasets/7w7fccy7ky/5 ，页面标注 CC BY 4.0。其跌倒为成年人模拟，不应宣称为真实老人跌倒。已选择 Subject.1 / Fall forward / FallForwardS1.avi，仅获取单个视频。

真实老人长视频目前未下载，等待用户取得合法访问和适用用途许可后再继续。
