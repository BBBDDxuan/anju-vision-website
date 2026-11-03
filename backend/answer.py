import re

from .errors import AppError
from .state import replay
from .replay import build_replay
from .events import ENVIRONMENT_ACTIONS, PERSON_ACTIONS, VISUAL_INFERENCE_RULES
from .grouping import build_event_groups
from .semantics import event_description


def clock(ms):
    seconds = ms / 1000
    if seconds >= 3600:
        return f"{int(seconds // 3600):02d}:{int(seconds // 60 % 60):02d}:{seconds % 60:04.1f}"
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"


def describe(event, entities):
    prefix = '上下文推测，可能发生：' if event['epistemic_type'] == 'inferred' else '观察记录显示'
    return f"在{clock(event['start_ms'])}–{clock(event['end_ms'])}，{prefix}{event_description(event, entities)}。"


def merge_fall_claims(result, document, groups):
    """One cautious claim for a shared fall, without promoting inferred sources."""
    events = {e['id']: e for e in document['events']}
    entities = {e['id']: e for e in document['entities']}
    for group in groups:
        members = [events[eid] for eid in group['event_ids']]
        if (not members or any(e['action'] != 'fall' for e in members)
                or len({e['epistemic_type'] for e in members}) < 2):
            continue
        ids = set(group['event_ids'])
        selected = [c for kind in ['observations', 'inferences'] for c in result[kind]
                    if c.get('event_ids') and set(c['event_ids']) <= ids]
        if len(selected) < 2:
            continue
        cited = {eid for c in selected for eid in c['event_ids']}
        sources = [e for e in members if e['id'] in cited]
        for kind in ['observations', 'inferences']:
            result[kind] = [c for c in result[kind] if c not in selected]
        result['inferences'].append({
            'text': f"{clock(min(e['start_ms'] for e in sources))}–{clock(max(e['end_ms'] for e in sources))}，"
                    f"可能：{entities[sources[0]['subject_id']]['label']}出现疑似跌倒过程。"
                    '同一次事件的模型观察与上下文推测已合并，动作解释存在不确定性，请回看原片。',
            'event_ids': [e['id'] for e in sources],
            'evidence_ids': list(dict.fromkeys(ref for e in sources for ref in e['evidence_ids'])),
            'epistemic_type': 'inferred', 'record_summary': True})


def resolve_entities(entities, text, explicit=None):
    if explicit:
        return [entities[explicit]] if explicit in entities else []
    matches = []
    for entity in entities.values():
        names = [x.strip().lower() for x in [entity["label"], *entity["aliases"]] if x.strip()]
        if entity.get('entity_type') == 'person':
            anonymous = re.match(r'^人物\d+', entity['label'])
            if anonymous:
                names.append(anonymous.group())
        occurrences = [(text.find(alias), -len(alias)) for alias in names if alias in text]
        if occurrences:
            matches.append((min(occurrences), entity))
    if not matches:
        return []
    best = min(score for score, _ in matches)
    return [entity for score, entity in matches if score == best]


def query_memory(document, job, question: str, as_of_ms: int | None = None, entity_id: str | None = None):
    duration = document["video_duration_ms"]
    cutoff = duration if as_of_ms is None else as_of_ms
    if not 0 <= cutoff <= duration:
        raise AppError("QUERY_TIME", "查询截止时间必须在视频覆盖范围内。")
    entities = {e["id"]: e for e in document["entities"]}
    result = {"status": "unknown", "question": question, "video_id": document["video_id"], "as_of_ms": cutoff,
              "observations": [], "inferences": [], "unknowns": [], "evidence": [], "candidates": [],
              "is_mock": job.get("is_mock", False), "analysis_status": job["status"],
              "coverage_note": "结论只涉及上传视频的历史观察，不能保证现实中的当前位置。"}

    def finish():
        if any(span['start_ms'] < cutoff for span in job.get('skipped_static_ranges', [])):
            note = '部分静止时段仅做本地画面变化筛查，未调用视觉模型；不能据此确认无人、物品未移动或没有危险。'
            result['coverage_note'] += ' ' + note
            result['unknowns'].append(note)
            if result['status'] == 'answered':
                result['status'] = 'partial'
        result["answer"] = "\n".join([c["text"] for c in result["observations"] + result["inferences"]] + result["unknowns"])
        return result

    text = question.lower().strip()
    history = bool(re.search(r"过程|经过|轨迹|历史|回顾|发生了什么|发生的事|概括|总结|概述|值得关注|需要关注|做了什么|做过什么|history|replay", text))
    contextual = bool(re.search(r"推测|推断|可能|猜测|解释|context|infer", text))
    if contextual and not re.search(r"哪里|在哪|位置|放进|放入|装进|取出|拿起|移动|离开|最后|现在|目前", text):
        history = True
    last_seen = bool(re.search(r"最后|上次|最近一次|last seen|last.*where", text))
    current = bool(re.search(r"现在|目前|还在|确定.*在|current|still", text))
    spatial = bool(re.search(r"哪里|在哪|何处|where|位置|什么地方", text))
    action = next((name for pattern, name in [(r"烟雾|冒烟", "smoke"), (r"火花|电弧", "sparks"), (r"明火|火焰", "flame"),
                  (r"取出|拿出来", "take_out"), (r"放进|放入|装进", "put_in"),
                  (r"拿起|取走", "pick_up"), (r"离开|出.*视野", "explicit_exit"), (r"打开", "open"),
                  (r"关闭|关上", "close"), (r"跌倒|摔倒", "fall"), (r"躺.*地|倒地", "lying_on_floor"),
                  (r"求助", "help_gesture"), (r"攀爬", "climb"), (r"起身|站起", "stand_up"), (r"坐下", "sit_down"),
                  (r"躺下", "lie_down"), (r"行走|走路", "walk"), (r"移动", "move"), (r"放下|放到|放在", "place_on")] if re.search(pattern, text)), None)
    if not (history or last_seen or current or spatial or action):
        result["unknowns"] = ["可询问人物活动经过、摔倒或求助动作的时间，或物体最后出现的位置；无法据此判断健康状况。"]
        return finish()
    candidates = resolve_entities(entities, text, entity_id)
    global_environment = not entity_id and action in ENVIRONMENT_ACTIONS and not candidates
    global_person = (not entity_id and action in PERSON_ACTIONS and not candidates
                     and not re.search(r'人物\d+', text)
                     and bool(re.search(r'视频|画面|有人|这段|video', text)))
    global_history = global_environment or global_person or (not entity_id and history and not candidates and (contextual or bool(re.search(r"视频|录像|这段|发生了什么|概括|总结|概述|值得关注|需要关注|所有|全部|video", text))))
    if global_environment:
        result['coverage_note'] = '按全片已有环境候选检索，位置以每条证据为准；不能确认未匹配位置或当前现场，也不等于火灾诊断。'
    if not global_history and len(candidates) != 1:
        result["status"] = "ambiguous" if len(candidates) > 1 else "unknown"
        result["candidates"] = [{"id": e["id"], "label": e["label"], "appearance": e["appearance"]} for e in candidates or entities.values()]
        result["unknowns"] = ["存在多个匹配物品，请在踪迹回溯选择具体物品后查询。" if candidates else "没有找到明确匹配的物品。可在踪迹回溯选择对象，未检索到不代表它从未出现。"]
        return finish()
    states = replay(document, cutoff)
    subject = candidates[0] if candidates else None
    state = states[subject["id"]] if subject else None
    eligible = sorted((e for e in document["events"] if e["end_ms"] <= cutoff), key=lambda e: (e["start_ms"], e["end_ms"]))
    if state and state.ambiguous:
        result["status"] = "ambiguous"
        result["unknowns"].append("该物品的身份或重叠事件存在歧义，无法据此给出确定结论。")
    observed = [e for e in eligible if e["epistemic_type"] == "observed" and e["action"] not in {"occluded", "unknown"}]
    if subject:
        observed = [e for e in observed if e["subject_id"] == subject["id"]]
    groups = build_event_groups(document, job, cutoff)
    group_kinds = {identifier: group['kind'] for group in groups for identifier in group['event_ids']}
    by_id = {e['id']: e for e in eligible}

    def grouped_selection(selected):
        ids = {e['id'] for e in selected}
        return [[by_id[identifier] for identifier in group['event_ids'] if identifier in ids]
                for group in groups if any(identifier in ids for identifier in group['event_ids'])]

    if history:
        chosen = grouped_selection(observed)[-20:]
    elif action and not current and not last_seen:
        chosen = grouped_selection([e for e in observed if e['action'] == action])[-8:]
    else:
        last = (state.last_position or state.last_direct) if state else None
        chosen = [[last]] if last else []
    cited = set()

    def claim(members):
        event = {**members[0], 'start_ms': min(e['start_ms'] for e in members),
                 'end_ms': max(e['end_ms'] for e in members)}
        refs = list(dict.fromkeys(ref for e in members for ref in e['evidence_ids']))
        cited.update(refs)
        text = describe(event, entities)
        if len(members) > 1:
            if group_kinds[event['id']] == 'repeated_observations':
                text = (f"在{clock(min(e['start_ms'] for e in members))}–{clock(max(e['end_ms'] for e in members))}内的"
                        f"{len(members)}条采样观察中，多次看见{event_description(event, entities)}。"
                        "仅代表所引用的采样时刻，不表示期间始终在场。")
            elif group_kinds[event['id']] == 'consecutive_actions':
                text = (f"{clock(min(e['start_ms'] for e in members))}–{clock(max(e['end_ms'] for e in members))}："
                        f"{event_description(event, entities)}。{len(members)}条相邻行为记录合并展示；"
                        "仅代表采样证据，未确认采样间动作始终连续。")
            else:
                text += f" 已将{len(members)}条共享证据的重叠记录合并展示，原始记录保留。"
        return {"text": text, "event_ids": [e['id'] for e in members], "evidence_ids": refs,
                "epistemic_type": "observed", "review_status": event['review_status']}

    result["observations"] = [claim(e) for e in chosen]
    hypotheses = [e for e in eligible if e['epistemic_type'] == 'inferred' and e['rule_id'] in VISUAL_INFERENCE_RULES
                  and (not subject or e['subject_id'] == subject['id'])]
    if not last_seen or contextual:
        if action and not history:
            hypotheses = [e for e in hypotheses if e['action'] == action]
        for members in grouped_selection(hypotheses)[-8:]:
            event = {**members[0], 'start_ms': min(e['start_ms'] for e in members),
                     'end_ms': max(e['end_ms'] for e in members)}
            refs = list(dict.fromkeys(ref for e in members for ref in e['evidence_ids']))
            reasons = list(dict.fromkeys(reason for e in members for reason in e['uncertainty_reasons']))
            cited.update(refs)
            merged_note = f'（{len(members)}条重叠候选合并展示）' if len(members) > 1 else ''
            result['inferences'].append({'text': describe(event, entities) + merged_note + ' 依据与限制：' + '；'.join(reasons),
                                         'event_ids': [e['id'] for e in members], 'evidence_ids': refs,
                                         'epistemic_type': 'inferred', 'rule_id': event['rule_id']})
    if any(e['action'] == 'unknown' and any('复核' in reason for reason in e['uncertainty_reasons'])
           for e in eligible if not subject or e['subject_id'] == subject['id']):
        result['unknowns'].append('部分事件待复核或未获复核支持，已排除相关动作及其容器关系推断；请到人工复核面板核对。')
    if subject and not state.ambiguous and (history or current or (spatial and not action)):
        chain = build_replay(document, job, subject["id"], cutoff)
        links = [entry for entry in chain["entries"] if entry.get('rule_id') == 'conditional_containment_v1']
        # A new direct observation (e.g. take_out) ends old-container assumptions for current queries.
        if not history and not state.container_event:
            links = []
        for entry in links[-8:]:
            cited.update(entry["evidence_ids"])
            result["inferences"].append({"text": describe(entry["event"], entities) + " " + entry["condition"],
                                         "event_ids": entry["source_event_ids"], "evidence_ids": entry["evidence_ids"],
                                         "epistemic_type": "inferred", "rule_id": entry["rule_id"]})
    if current or (spatial and not last_seen and not action and not history):
        result["unknowns"].append("无法确认当前位置。以上是视频中的最后直接观察；后续离开画面、遮挡或视频结束后不能保证位置未变。")
    if state and current and state.visibility in {"occluded", "uncertain", "out_of_view", "inside_not_visible"}:
        result["unknowns"].append("后续直接可见信息不足，不能把容器关系推断当作直接看见。")
    if job["status"] != "completed":
        result["unknowns"].append("本次分析存在失败窗口或尚未完成；不能跨越缺口保证事件链完整。")
    merge_fall_claims(result, document, groups)
    if not result["observations"] and not any(by_id[eid]['epistemic_type'] == 'observed'
            for claim in result['inferences'] for eid in claim['event_ids'] if eid in by_id):
        result["unknowns"].append("在所选时间范围内没有找到可支持该动作/位置答案的直接证据。")
    if result["status"] != "ambiguous":
        result["status"] = ("partial" if result["unknowns"] or result['inferences'] else "answered") if result["observations"] or result['inferences'] else "unknown"
    result["evidence"] = [e for e in document["evidence"] if e["id"] in cited and e["end_ms"] <= cutoff]
    return finish()
