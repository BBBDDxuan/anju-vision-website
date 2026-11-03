"""Summarize measured real-model/UI results, preserving failures and caveats."""
from collections import Counter
import csv
import json
from statistics import median

from backend.config import ROOT

OUT = ROOT/'data/validation/fall-suite-20261020'


def stamp(ms):
    return f'{ms/1000:.1f}s'


def main():
    suite = json.loads((OUT/'suite.json').read_text(encoding='utf8'))
    browser = json.loads((OUT/'browser-audit.json').read_text(encoding='utf8'))
    visual = json.loads((OUT/'visual-review.json').read_text(encoding='utf8'))
    ui = {r['subject']:r for r in browser['results']}
    refs = {r['subject']:r for r in visual['clips']}
    rows = []
    for record in suite['clips']:
        subject = record['subject']
        model_ranges = ' / '.join(f'{stamp(e["start_ms"])}–{stamp(e["end_ms"])}' for e in record['fall_events'])
        kind = ' / '.join(e['epistemic_type'] for e in record['fall_events'])
        review = ('窗口缺口：单帧躺卧事件导致拒收' if subject == 1 else
                  '动作字段与说明结论矛盾，不能计为可靠检出' if subject == 9 else
                  '辅助出现事件打断持续躺卧分组' if subject == 10 else
                  '未知手臂动作导致保守拆组' if subject == 7 else '见模型证据，仍需用户核对')
        rows.append([f'S{subject:02d}',record['filename'],record['action'],record['job']['status'],model_ranges,kind,
                     ui[subject]['fall_alerts'],ui[subject]['fall_replay_entries'],
                     '失败' if ui[subject]['global_query_issue'] else '成功',ui[subject]['fall_query_claims'],
                     round(record['job']['elapsed_ms']/1000,2),review])
    with (OUT/'results.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['样本','文件','发布方动作','任务状态','模型证据区间','事实类型','红色跌倒提醒数',
                         '跌倒回溯项数','全局人物问答','指定人物回答项数','任务耗时秒','检查备注'])
        writer.writerows(rows)
    types = Counter(e['epistemic_type'] for r in suite['clips'] for e in r['fall_events'])
    timings = [r['job']['elapsed_ms']/1000 for r in suite['clips'] if not r.get('reused_existing_analysis')]
    issues = [
        {'priority':'P1','id':'invalid_event_rejects_window','subjects':[1],
         'confirmed':'S01 0–8秒的lying_on_floor仅引用f000140，触发PERSON_EVIDENCE，整窗拒收；同一响应另有5–7秒fall候选未保存。最终只保留后窗6–7秒推测。',
         'recommendation':'在不降低跌倒证据标准的前提下隔离不合格事件；保留可验证事件及拒收审计，或将语义校验失败纳入受限重观察。必须继续标注覆盖缺口。'},
        {'priority':'P1','id':'action_rationale_conflict','subjects':[9],
         'confirmed':'S09 action=fall、inferred、自评分0.6；同条说明的最终结论选择lie_down。仍产生一条红色跌倒预警。不能把该fall字段直接计为正确检出。',
         'recommendation':'增加动作与最终说明的一致性校验。矛盾记录暂作未决候选，必要时受限重观察；不依据样本文件名强行改成fall。限制理由长度，保留替代解释，禁止无画面依据的健康状态猜测。'},
        {'priority':'P1','id':'global_person_query_rejected','subjects':list(range(1,11)),
         'confirmed':'10段直接问“视频中什么时候有人跌倒？”均返回unknown、无证据，并要求选择物品；指定人物后10段均可返回已有跌倒证据。',
         'recommendation':'为人物动作实现全片事件检索路径；只有问题确实指向特定但身份有歧义的人物时才要求选择对象。'},
        {'priority':'P2','id':'ongoing_floor_posture_split','subjects':[10],
         'confirmed':'S10躺卧记录4–7秒与6–13秒合并为4–13秒，但12–14秒又单独展示。其间seen_at辅助观察打断了同一姿态的活动分组。',
         'recommendation':'区分持续状态整合与同一次动作候选去重；辅助出现记录不应打断兼容姿态，起身/行走等反证仍应阻止整合。保留分段证据，不宣称采样间绝对连续。'},
        {'priority':'P2','id':'mixed_reasons_force_yellow','subjects':list(range(1,11)),
         'confirmed':'10条跌倒均为黄色，其中7条为observed且自评分达到0.9或以上；uncertainty_reasons混入了直接画面说明，前端任何非空理由均降为黄色。',
         'recommendation':'区分证据说明与真正的不确定性。保留推测和歧义的黄色上限，不单纯删除理由或根据高自评分升绿。'}
    ]
    result = {'is_mock':False,'clips':10,'completed':suite['completed_clips'],
              'clips_with_fall_field':suite['clips_with_extracted_falls'],'fall_types':dict(types),
              'new_paid_requests':suite['new_requests'],'usage_after':suite['usage_after'],
              'global_query_failures':sum(r['global_query_issue'] for r in browser['results']),
              'ui_checked':browser['clips_checked'],'fall_alert_count_per_clip':[r['fall_alerts'] for r in browser['results']],
              'source_videos_unchanged':suite['original_videos_unchanged'],
              'previous_analyses_unchanged':suite['existing_completed_analyses_unchanged'],
              'new_job_seconds':{'minimum':min(timings),'median':median(timings),'maximum':max(timings)},
              'issues':issues,'limitations':suite['limitations'],
              'production_recognition_rules_changed':False}
    (OUT/'findings.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    table = '\n'.join(f'| [S{r["subject"]:02d}](http://127.0.0.1:8000/workspace?video={r["video_id"]}) | {r["action"]} | {r["job"]["status"]} | '
                      + ' / '.join(f'{stamp(e["start_ms"])}–{stamp(e["end_ms"])} {e["epistemic_type"]}' for e in r['fall_events'])
                      + f' | {ui[r["subject"]]["fall_alerts"]} |' for r in suite['clips'])
    findings = '\n\n'.join(f'### {i["priority"]} · {i["id"]}\n\n{i["confirmed"]}\n\n建议：{i["recommendation"]}' for i in issues)
    doc = f'''# 本地模拟跌倒视频验证报告（2026-10-20）

本轮对10段本地CAUCAFall模拟跌倒视频完成真实结果与网页核对，其中复用2段已完成分析，其余8段新增16次Qwen请求。模型为`{suite['model']}`，基础提示词`event-v1.4`。每日上限保持48，结束时累计47/48，未提高限额。人民币金额未在本地核定，以服务账单为准。

## 结果

- 9段完整完成；S01仅完成后半窗口，不能称为全片成功。
- 10段都有fall候选字段：7条observed、3条inferred。但S09动作与说明结论矛盾，不能据字段统计“10段全部识别正确”。
- 每段网页各有1条跌倒时间线、1条红色跌倒提醒、1条跌倒回溯项；本轮未发现重复跌倒预警。
- 10段指定人物的问答、视频证据跳转和预警证据图片均可用，浏览器未报告脚本错误。全局人物问答则10段均复现问题。
- 所有已保存记忆通过结构及证据引用检查；原始视频、此前已完成的分析均保持不变。结构通过不代表语义正确。
- 本轮新增任务耗时范围{min(timings):.1f}–{max(timings):.1f}秒，中位数{median(timings):.1f}秒。为当前网络/API下的实测任务耗时，不含复用旧结果，也不是12小时录像速度估计。

## 逐段结果

表中区间是模型引用证据的时间范围，不是人工精确标注的动作起止。点击样本打开本地系统。

| 样本 | 发布方类别 | 完成状态 | 模型跌倒证据区间与类型 | 红色跌倒提醒数 |
| --- | --- | --- | --- | --- |
{table}

## 已确认的问题与优先级

{findings}

## 不应误删的相似记录

S03在0秒出现与6–8秒倒地后的出现，中间经历行走/跌倒，是不同阶段，不能只看标题相同就删除。S07的两条躺卧之间有明确保留的unknown手臂活动记录，因此当前保守规则主动分开；这与S10仅被seen_at辅助观察打断不同，不宜直接套同一删重规则。

## 验证边界与复查

本轮逐段检查了0.5秒间隔的原片联系图，均可见一次向地面下降过程；该检查不是用户人工核验，也不是数据集逐帧真值。全部都是同一数据集中的模拟跌倒正样本，场景相近，没有正常活动负样本。因此不能给出日常家庭误报率、真实老人跌倒准确率或临床安全结论。文件名/发布方类别只作对照，未作为模型输入提示或补造识别事件。

本轮保留现有识别规则做基线验证，发现的问题尚未修改。建议先修复三个P1问题，再处理持续姿态分组与颜色理由分离；下一轮加入正常坐下、弯腰、俯卧撑和主动躺下等负样本。

原始结果和证据索引：`data/validation/fall-suite-20261020/`。其中`results.csv`为逐段结果；`suite.json`为实际请求、任务、事件和预警记录；`browser-audit.json`为10段网页检查；`visual-review.json`为独立帧核对说明；`Sxx-contact.jpg`和三张`Sxx-browser.png`供回看。原始模型响应仍在各自`data/runs/<job_id>/`目录。

复现：`python -m scripts.fall_suite_browser_audit`仅读已保存结果，不调用模型。`scripts.fall_sample_suite`默认只准备检查图；付费测试必须显式传`--run-paid`，并受既有每日额度约束。
'''
    (ROOT/'docs/FALL_VALIDATION_REPORT_20261020.md').write_text(doc,encoding='utf8')
    print(json.dumps({k:result[k] for k in ['clips','completed','clips_with_fall_field','fall_types',
        'new_paid_requests','global_query_failures','ui_checked','new_job_seconds']},ensure_ascii=False))


if __name__ == '__main__':
    main()
