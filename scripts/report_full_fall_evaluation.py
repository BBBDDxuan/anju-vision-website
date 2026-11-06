"""Summarize a completed frozen full-video evaluation without model calls."""
from collections import Counter
import argparse
import json
from pathlib import Path
import statistics


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    out=args.output.resolve()
    data=json.loads((out/'results.json').read_text(encoding='utf-8'))
    records=data['records']
    assert len(records)==100 and all(r['scan_complete'] and r['review_complete'] for r in records)
    verdicts=Counter(e['result']['verdict'] for r in records for e in r['reviews'])
    paid=[e for r in records for e in r['reviews'] if e['result']['verdict']!='duplicate']
    times=[e['complete_seconds'] for e in paid]
    delays=[c['ready_video_seconds']-c['trigger_video_seconds'] for r in records for c in r['candidate_timings']
            if c['id'] in {x['id'] for x in r['candidates']}]
    summary={'candidate_events':sum(r['candidate_count'] for r in records),'verdict_counts':dict(verdicts),
             'review_wall_seconds':{'median':statistics.median(times),'min':min(times),'max':max(times)} if times else None,
             'evidence_ready_minus_trigger_seconds':{'median':statistics.median(delays),'min':min(delays),'max':max(delays)} if delays else None,
             'errors':[(r['id'],e['result'].get('error')) for r in records for e in r['reviews'] if e['result'].get('error')],
             'uncertain_videos':[r['id'] for r in records if any(e['result']['verdict']=='uncertain' for e in r['reviews'])]}
    repeats={r['id']:[e['candidate_t'] for e in r['reviews'] if e['confirmed_event']] for r in records
             if sum(e['confirmed_event'] for e in r['reviews'])>1}
    summary['multiple_confirmation_videos']=repeats
    def pct(value):return '—' if value is None else f'{100*value:.1f}%'
    def cell(values):return '、'.join(values) if values else '无'
    lines=['# CAUCAFall 全部 100 段原视频评估','',
        '固定当前 YOLO11s、候选规则及 Qwen 提示词，完整扫描本地 CAUCAFall 全部 100 段原始视频：10 名参与者，每人 5 种模拟跌倒和 5 种正常动作；50 正例、50 负例。原视频合计 993.85 秒（约 16.56 分钟），没有截取关键片段、没有人工遮挡或置信度置零。', '',
        '**这是完整开发集的视频级评估。该数据已参与调参，不是独立泛化测试；视频级确认不代表准确命中跌倒发生时刻，也不代表真实老人、所有遮挡场景或手机通知已通过。**','',
        '## 整体结果','',
        '| 阶段 | TP | FN | FP | TN | 视频召回率 | 特异度 | 精确率 | 准确率 | F1 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name,label in [('local','本地提出候选'),('final','Qwen 后视觉确认')]:
        m=data['metrics'][name]
        assert m['evaluated']==100 and m['missing']==0
        lines.append(f"| {label} | {m['tp']} | {m['fn']} | {m['fp']} | {m['tn']} | {pct(m['recall'])} | {pct(m['specificity'])} | {pct(m['precision'])} | {pct(m['accuracy'])} | {pct(m['f1'])} |")
    lines += ['', 'TP/FN 指跌倒来源视频有/无对应输出，FP/TN 指正常动作视频有/无输出。全片只要出现一个确认，即视频级阳性；这不验证确认时刻是否与跌倒过程吻合。第一阶段的 FP 是正常视频送审，不等于最终误报警。第二阶段 `uncertain`、`rejected` 或不可用均不产生确认事件，计入未确认，而非从分母剔除；各类结果另行保留。','',
        '**额外发现：S05_Fall_forward 在 2.6 秒和 6.4 秒各得到一次确认。对原视频的本轮视觉检查可见一次跌倒后持续伏地，第二次证据重复包含此前过程，存在重复确认；因此不能把上述视频级 100% 写成事件级零误报或零重复。**','',
        '## 各动作结果','',
        '| 动作 | 真值 | 视频数 | 有本地候选 | Qwen 后确认 | 最终漏报/误报 |',
        '|---|---|---:|---:|---:|---:|']
    for action,stats in data['per_action'].items():
        local,final=stats['local'],stats['final']
        positive=action.startswith('Fall ')
        lines.append(f"| {action} | {'跌倒' if positive else '正常'} | {local['evaluated']} | {local['tp']+local['fp']} | {final['tp']+final['fp']} | {final['fn'] if positive else final['fp']} |")
    lines += ['', '## 失败及不确定样本','',
        '- 最终正常动作误报：'+cell(data['false_positives']),
        '- 最终跌倒视频未确认：'+cell(data['false_negatives']),
        '- 出现过不确定回答的视频（部分后续补证可能已确认）：'+cell(summary['uncertain_videos']),
        '- 请求/校验错误：'+cell([f'{ident}: {error}' for ident,error in summary['errors']]),'',
        '- 多次确认的视频：'+cell([f'{ident}: {values}' for ident,values in repeats.items()]),
        '- 两次 `SAFETY_REVIEW_INVALID` 均是返回了未提供的帧 ID；这两条确认未被接受，后来由不同候选的新证据获得确认。没有重试相同请求，诊断见 `review-validation-diagnostics.json`。','',
        '重复确认需要继续修复：同一 tracker 的事件编号从 `person-1:1` 变成 `person-1:2`，导致已确认同事件的去重未覆盖新编号。结合原片持续伏地，推测二维“恢复正常”姿态代理误触发了事件重开；尚未重新推理逐帧证明该中间状态。本轮不改规则、不覆盖成绩。对照图为 `S05-Fall-forward-repeat-audit.jpg`。','',
        '以下保留所有最终错误视频的回答依据；不据此修改提示词或规则后覆盖本轮成绩。','']
    failures=set(data['false_positives']+data['false_negatives'])
    for row in records:
        if row['id'] not in failures:continue
        lines.append(f"### {row['id']}")
        lines.append('')
        if not row['reviews']:lines.append('没有本地跌倒候选，未调用 Qwen。')
        for entry in row['reviews']:
            result=entry['result']
            lines.append(f"- 候选 {entry['candidate_t']:.2f} 秒；`{result['verdict']}`：{result.get('reason','')}")
        lines.append('')
    lines += ['## 完整性、调用及时间','',
        f"- 100/100 段完成原片解码，合计 {sum(r['decoded_frames'] for r in records)} 帧；本地推理 {sum(r['inferred_frames'] for r in records)} 帧。目标 10 FPS，源视频全程覆盖，采样及 CFR 时间基准见各 `scan.json`。",
        f"- 本地跌倒候选 {summary['candidate_events']} 个；复核结果计数 {dict(verdicts)}。同事件已确认后的补证记为 `duplicate`，不重复付费或生成确认事件。",
        f"- 本轮实际新增云端请求 {data['new_cloud_calls']} 次，上限 120；用量 {data['usage_before']['requests']}→{data['usage_after']['requests']}。无自动重试；日常 `.env` 上限仍为 {data['production_daily_cap_after']}，临时额度只存在于本轮进程。",
        f"- 评估总墙上耗时 {data['elapsed_seconds']:.2f} 秒{'（包含评估工具对照检查修复及补齐首片的时间）' if data.get('includes_tool_verification_recovery_time') else ''}；扫描使用一个 GPU 进程，跨视频最多两路复核，同视频按候选顺序处理。",
        '- 为缩短重复模型加载时间，跨视频复用同一权重实例，每片重新建立跟踪/时序/取证状态。首片与普通生产扫描的候选时间、元数据、时间戳和 JPEG 哈希逐项一致。内部 ID 因采样参数 10 和 10.0 的 JSON 编码导致命名空间哈希不同，不用作内容对照，见 `pooling-verified.json`。首轮工具把 ID 不同也视为失败，已完成的 99 片复核保持原样，仅补上此前未请求的首片，没有重试已发送请求。']
    lines.append('- 临时评估进程结束后不再拥有额外调用额度；日常服务仍按原 24 次上限检查，今日累计已超过该上限，普通新请求会被额度限制拦截。')
    if times:
        lines.append(f"- Qwen 复核完整耗时中位数 {statistics.median(times):.2f} 秒，范围 {min(times):.2f}～{max(times):.2f} 秒（两路跨视频并发条件，非实时摄像头队列测试）。")
    if delays:
        lines.append(f"- 视频时间轴上，从触发到证据可用的间隔中位数 {statistics.median(delays):.2f} 秒，范围 {min(delays):.2f}～{max(delays):.2f} 秒；短片尾部会提前保存实际画面。")
    lines += ['- 没有发送真实通知。Qwen 视觉确认通过正式结果校验和事件对象转换，但没有写入生产视频记忆、报警队列或手机通知系统。不能把离线扫描墙上耗时当作跌倒到手机的总时延。','',
        '## 已有人工时间标注的补充检查','',
        '完整语料只有三段当前可核对的人工跌倒起止时间，下表单独展示。其余视频仅用发布者动作类别计算视频级指标；不能据此宣称全量事件级召回率。','',
        '| 视频 | 人工区间/秒 | 本地候选/秒 | 最终确认候选/秒 |',
        '|---|---|---|---|']
    root=Path(__file__).resolve().parents[1]
    labels=json.loads((root/'runtime/fall-experiments/s-hackathon-20261106-101000/human-reviews-20261106-110708/reviews.json').read_text(encoding='utf-8'))
    mapping={r['id']:r for r in records}
    for label in labels['records']:
        row=mapping[label['id']]
        assert row['source_sha256']==label['source_sha256']
        candidate_times=', '.join(f"{c['t']:.2f}" for c in row['candidates']) or '无'
        confirmed_times=', '.join(f"{e['candidate_t']:.2f}" for e in row['reviews'] if e['confirmed_event']) or '无'
        lines.append(f"| {row['id']} | {label['start_seconds']:.2f}～{label['end_seconds']:.2f} | {candidate_times} | {confirmed_times} |")
    lines += ['', '## 证据与适用边界','',
        '- `protocol.json`：固定权重、配置、提示词、检测器源码、预算、并发和指标口径；`manifest.json`：完整 100 片来源、真值和 SHA256。',
        '- `results.json`：逐片扫描、候选、Qwen 回答及事件转换结果；`videos.csv`：100 片汇总。`clips/<id>/safety/`：完整扫描记录、证据帧与提交清单。',
        '- `clips/<id>/*-review.json`：每次复核记录。服务商原始响应及账本仍存于原生产数据目录的本次 `full_cauca_` 任务下，没有覆盖旧实验。',
        '- 语料包括前倒、后倒、左右倒、坐姿跌倒，以及走路、跳跃、跪下、拾物、坐下各十段；不等于所有现实情况均覆盖。公开数据是参与者模拟动作，且已用于开发，后续仍需独立场景及连续正常监控视频。','']
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(metrics=data['metrics'],verdicts=dict(verdicts),new_cloud_calls=data['new_cloud_calls'],
                         false_positives=data['false_positives'],false_negatives=data['false_negatives']),ensure_ascii=False))


if __name__=='__main__':
    main()
