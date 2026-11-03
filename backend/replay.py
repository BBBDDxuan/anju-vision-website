"""Deterministic evidence timeline. Container links are conditional, never new observations."""
from .errors import AppError
from .state import replay


def build_replay(document, job, entity_id, as_of_ms=None):
    cutoff = document["video_duration_ms"] if as_of_ms is None else as_of_ms
    if not 0 <= cutoff <= document["video_duration_ms"]:
        raise AppError("QUERY_TIME", "查询时间超出视频范围。")
    entities = {e["id"]: e for e in document["entities"]}
    if entity_id not in entities:
        raise AppError("NOT_FOUND", "未找到该物品。", 404)
    events = sorted((e for e in document["events"] if e["end_ms"] <= cutoff), key=lambda e: (e["start_ms"], e["end_ms"]))
    entries, container_entry = [], None
    for event in events:
        if event["subject_id"] == entity_id:
            entries.append({"id": event["id"], "start_ms": event["start_ms"], "end_ms": event["end_ms"],
                            "epistemic_type": event["epistemic_type"], "event": event, "source_event_ids": event['source_event_ids'] or [event["id"]], "evidence_ids": event["evidence_ids"],
                            "rule_id": event['rule_id']})
            if event["epistemic_type"] == "observed":
                if event["action"] == "put_in":
                    container_entry = event
                elif event["action"] in {"take_out", "pick_up", "place_on", "move", "seen_at"}:
                    container_entry = None
        elif container_entry and event["subject_id"] == container_entry["target_id"] and event["start_ms"] >= container_entry["end_ms"] and event["epistemic_type"] == "observed" and event["action"] in {"move", "place_on", "explicit_exit"}:
            entries.append({"id": f"conditional-{event['id']}", "start_ms": event["start_ms"], "end_ms": event["end_ms"],
                            "epistemic_type": "inferred", "event": event, "source_event_ids": [container_entry["id"], event["id"]],
                            "evidence_ids": list(dict.fromkeys(container_entry["evidence_ids"] + event["evidence_ids"])),
                            "condition": f"若{entities[entity_id]['label']}其后未被取出，可能仍随{entities[container_entry['target_id']]['label']}移动；包内物品未被直接看见。",
                            "rule_id": "conditional_containment_v1"})
    state = replay(document, cutoff)[entity_id]
    unknowns = []
    if state.ambiguous:
        unknowns.append("物品身份或重叠事件存在歧义，请结合原视频核对。")
    if state.visibility in {"inside_not_visible", "occluded", "out_of_view", "uncertain", "not_observed"}:
        unknowns.append("缺少后续直接观察，无法据此确认当前实际位置。")
    if job["status"] != "completed":
        unknowns.append("分析存在缺口，不能保证物品历史完整。")
    if any(span['start_ms'] < cutoff for span in job.get('skipped_static_ranges', [])):
        unknowns.append('部分静止时段未调用视觉模型，回溯不包含这些时段的完整人物或物品事件。')
    cited = {ref for entry in entries for ref in entry["evidence_ids"]}
    return {"video_id": document["video_id"], "entity": entities[entity_id], "as_of_ms": cutoff,
            "entries": entries, "unknowns": unknowns, "evidence": [e for e in document["evidence"] if e["id"] in cited],
            "is_mock": job.get("is_mock", False), "rule_version": "state-v0.2", "analysis_status": job["status"]}
