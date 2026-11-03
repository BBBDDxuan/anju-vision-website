import json
import math
from typing import Literal
from uuid import uuid4

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import ROOT
from .errors import AppError

Action = Literal["seen_at", "place_on", "pick_up", "put_in", "take_out", "move", "open", "close", "explicit_exit", "occluded", "unknown",
                 "walk", "sit_down", "stand_up", "lie_down", "lying_on_floor", "fall", "climb", "help_gesture", "no_person_visible",
                 "smoke", "flame", "sparks"]
PERSON_ACTIONS = {'walk', 'sit_down', 'stand_up', 'lie_down', 'lying_on_floor', 'fall', 'climb', 'help_gesture'}
ENVIRONMENT_ACTIONS = {'smoke', 'flame', 'sparks'}
VISUAL_INFERENCE_RULES = {"visual_context_v1", "unverified_visual_candidate_v1"}


class RawEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=60)
    aliases: list[str] = Field(default_factory=list, max_length=8)
    appearance: str = Field(default="", max_length=300)
    identity_status: Literal["distinct_candidate", "ambiguous"] = "distinct_candidate"
    entity_type: Literal["object", "person", "location"] = "object"


class RawEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str
    target_id: str | None = None
    action: Action
    epistemic_type: Literal["observed", "inferred", "unknown"] = "observed"
    frame_ids: list[str] = Field(max_length=16)
    uncertainty_reasons: list[str] = Field(default_factory=list, max_length=8)
    model_score: float | None = Field(default=None, ge=0, le=1)

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action_name(cls, value):
        return {"appear": "seen_at", "put_down": "place_on", "leave_view": "explicit_exit"}.get(value, value)


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[RawEntity] = Field(max_length=30)
    events: list[RawEvent] = Field(max_length=40)
    uncertainty: list[str] = Field(default_factory=list, max_length=8)


def normalize(raw: Extraction, video_id: str, window: dict, known: dict):
    entities = dict(known)
    mapping = {key: key for key in known}
    seen_local = set()
    for entity in raw.entities:
        if entity.id in seen_local:
            raise AppError("MODEL_ENTITIES", "模型返回重复实体ID，该窗口未写入。")
        seen_local.add(entity.id)
        if entity.id in known:
            # The model must explicitly reference the supplied ID; never merge by label/color alone.
            mapping[entity.id] = entity.id
            continue
        identifier = "o_" + uuid4().hex[:12]
        mapping[entity.id] = identifier
        entities[identifier] = {**entity.model_dump(), "id": identifier}
    frames = {f["id"]: f for f in window["frames"]}
    evidence, events = [], []
    for item in raw.events:
        if item.subject_id not in mapping or (item.target_id is not None and item.target_id not in mapping):
            raise AppError("MODEL_ENTITY_REFERENCE", "模型引用了未定义的物品，该窗口未写入。")
        if (entities[mapping[item.subject_id]].get('entity_type') == 'person'
                and item.action in {'pick_up', 'place_on', 'open', 'close'} and item.target_id is None):
            item = item.model_copy(update={'action': 'unknown', 'uncertainty_reasons':
                [*item.uncertainty_reasons, '人物交互缺少明确被操作物体，无法确定主客体关系。'][:8]})
        if (entities[mapping[item.subject_id]].get('entity_type') == 'person' and item.action in {'move', 'walk', 'seen_at'}
                and item.target_id is not None and entities[mapping[item.target_id]].get('entity_type') != 'location'):
            item = item.model_copy(update={'action': 'unknown', 'uncertainty_reasons':
                [*item.uncertainty_reasons, '人物的位置或移动目标被写成物体，动作角色与空间关系无法确定。'][:8]})
        if item.action in PERSON_ACTIONS and entities[mapping[item.subject_id]].get('entity_type') != 'person':
            raise AppError('PERSON_REFERENCE', '人物行为必须引用明确标记为person的匿名人物。')
        if item.action in ENVIRONMENT_ACTIONS and entities[mapping[item.subject_id]].get('entity_type') not in {'object', 'location'}:
            raise AppError('HAZARD_REFERENCE', '环境危险现象应引用物体或位置，不能将人物作为冒烟或起火的主体。')
        if item.action == 'no_person_visible' and (entities[mapping[item.subject_id]].get('entity_type') != 'location'
                                                   or item.epistemic_type != 'observed'):
            raise AppError('ABSENCE_REFERENCE', '无人画面必须是对摄像头画面位置的直接观察。')
        if len(item.frame_ids) != len(set(item.frame_ids)) or any(fid not in frames for fid in item.frame_ids):
            raise AppError("MODEL_FRAME_REFERENCE", "模型引用了不属于本窗口的证据帧，该窗口未写入。")
        if item.action != "unknown" and not item.frame_ids:
            raise AppError("MISSING_EVIDENCE", "模型事件缺少视频证据，该窗口未写入。")
        if item.action in {'put_in', 'take_out', 'place_on'} and item.target_id is None:
            # A missing relation target is NOT a fact. Retain the candidate as explicitly unknown.
            item = item.model_copy(update={'action': 'unknown', 'uncertainty_reasons': [*item.uncertainty_reasons, '模型未能确定关系动作的目标，无法证实该放置/进出关系，已标为未知。'][:8]})
        if item.action in {"put_in", "take_out", "explicit_exit"} and len({frames[f]["pts_ms"] for f in item.frame_ids}) < 2:
            raise AppError("ACTION_EVIDENCE", "入包、取出和离开画面需要至少两个不同时刻的动作证据。")
        if item.action in PERSON_ACTIONS | {'no_person_visible'} and len({frames[f]['pts_ms'] for f in item.frame_ids}) < 2:
            raise AppError('PERSON_EVIDENCE', '人物行为需要至少两个不同时刻的原片证据，单帧只能记录出现。')
        if item.action in ENVIRONMENT_ACTIONS and len({frames[f]['pts_ms'] for f in item.frame_ids}) < 2:
            raise AppError('HAZARD_EVIDENCE', '危险现象需要至少两个不同时刻的现象及前后画面证据。')
        if item.subject_id == item.target_id:
            raise AppError("SELF_RELATION", "物品不能与自己构成放入或位置关系。")
        selected = sorted((frames[f] for f in item.frame_ids), key=lambda f: f["pts_ms"])
        start = selected[0]["pts_ms"] if selected else window["start_ms"]
        end = selected[-1]["pts_ms"] if selected else start
        eid = "e_" + uuid4().hex
        refs = []
        if selected:
            reference = "ev_" + uuid4().hex
            refs = [reference]
            evidence.append({"id": reference, "video_id": video_id, "start_ms": start, "end_ms": end, "frame_ids": [f["id"] for f in selected]})
        reasons = list(item.uncertainty_reasons)
        epistemic = "unknown" if item.action == "unknown" or item.epistemic_type == "unknown" else item.epistemic_type
        if epistemic == "inferred" and (not reasons or len({f['pts_ms'] for f in selected}) < 2):
            raise AppError("INFERENCE_EVIDENCE", "上下文推测必须给出理由和至少两个不同时刻的真实画面。")
        if epistemic == "unknown" and item.action != "unknown":
            item = item.model_copy(update={"action": "unknown"})
        if item.action == "unknown" and not reasons:
            reasons = ["模型无法从当前画面确定相关动作。"]
        events.append({"id": eid, "video_id": video_id, "start_ms": start, "end_ms": end,
                       "subject_id": mapping[item.subject_id], "target_id": mapping.get(item.target_id),
                       "action": item.action, "epistemic_type": epistemic,
                       "review_status": "model_unreviewed", "evidence_ids": refs, "source_event_ids": [],
                       "rule_id": "visual_context_v1" if epistemic == "inferred" else None,
                       "model_score": item.model_score, "uncertainty_reasons": reasons})
    return entities, evidence, events


SCHEMA = json.loads((ROOT / "schemas/memory.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)


def validate_memory(document, expected_frames=None):
    if list(VALIDATOR.iter_errors(document)):
        raise AppError("MEMORY_SCHEMA", "事件记忆未通过结构校验，未保存为有效分析结果。")
    duration, video_id = document["video_duration_ms"], document["video_id"]
    maps = {}
    for name in ["entities", "frames", "evidence", "events"]:
        records = document[name]
        maps[name] = {r["id"]: r for r in records}
        if len(maps[name]) != len(records):
            raise AppError("DUPLICATE_ID", "记忆中存在重复ID。")
    for frame in document["frames"]:
        if not 0 <= frame["pts_ms"] < duration:
            raise AppError("FRAME_TIME", "证据帧时间超出视频范围。")
        if expected_frames is not None and (frame["id"] not in expected_frames or frame["pts_ms"] != expected_frames[frame["id"]]["pts_ms"]):
            raise AppError("FORGED_FRAME", "证据帧与原始抽帧记录不一致。")
        if document["fixture_kind"] == "model_analysis" and frame["kind"] != "original_frame":
            raise AppError("PLACEHOLDER_EVIDENCE", "模型分析不能使用手写占位证据。")
    for ev in document["evidence"]:
        if ev["video_id"] != video_id or not 0 <= ev["start_ms"] <= ev["end_ms"] < duration:
            raise AppError("EVIDENCE_TIME", "证据不属于本视频或时间越界。")
        if any(f not in maps["frames"] or not ev["start_ms"] <= maps["frames"][f]["pts_ms"] <= ev["end_ms"] for f in ev["frame_ids"]):
            raise AppError("EVIDENCE_FRAME", "证据引用了不匹配的帧。")
    containment = {}
    for event in sorted(document["events"], key=lambda e: (e["start_ms"], e["end_ms"])):
        if event["video_id"] != video_id or not 0 <= event["start_ms"] <= event["end_ms"] < duration:
            raise AppError("EVENT_TIME", "事件不属于本视频或时间越界。")
        if event["subject_id"] not in maps["entities"] or (event["target_id"] is not None and event["target_id"] not in maps["entities"]):
            raise AppError("EVENT_ENTITY", "事件物品引用不存在。")
        if event['action'] in ENVIRONMENT_ACTIONS:
            support = {maps['frames'][fid]['pts_ms'] for rid in event['evidence_ids']
                       for fid in maps['evidence'].get(rid, {}).get('frame_ids', []) if fid in maps['frames']}
            if maps['entities'][event['subject_id']].get('entity_type') not in {'object', 'location'} or len(support) < 2:
                raise AppError('HAZARD_EVIDENCE', '环境危险现象的主体类型或多时刻证据无效。')
        if event['action'] in PERSON_ACTIONS | {'no_person_visible'}:
            support = {maps['frames'][fid]['pts_ms'] for rid in event['evidence_ids']
                       for fid in maps['evidence'].get(rid, {}).get('frame_ids', []) if fid in maps['frames']}
            expected_type = 'location' if event['action'] == 'no_person_visible' else 'person'
            if maps['entities'][event['subject_id']].get('entity_type') != expected_type or len(support) < 2:
                raise AppError('PERSON_EVIDENCE', '人物行为的主体类型或多时刻证据无效。')
            if event['action'] == 'no_person_visible' and event['epistemic_type'] != 'observed':
                raise AppError('ABSENCE_REFERENCE', '无人画面不能由推测替代。')
        for rid in event["evidence_ids"]:
            ev = maps["evidence"].get(rid)
            if not ev or ev["start_ms"] < event["start_ms"] or ev["end_ms"] > event["end_ms"]:
                raise AppError("EVENT_EVIDENCE", "事件证据不存在或与时间不匹配。")
        for parent in event["source_event_ids"]:
            source = maps["events"].get(parent)
            if not source or source["end_ms"] > event["end_ms"] or parent == event["id"]:
                raise AppError("INFERENCE_SOURCE", "推断引用了无效或未来事件。")
        if event['epistemic_type'] == 'inferred' and event['rule_id'] in VISUAL_INFERENCE_RULES:
            support = {maps['frames'][fid]['pts_ms'] for rid in event['evidence_ids'] for fid in maps['evidence'][rid]['frame_ids']}
            if len(support) < 2:
                raise AppError('INFERENCE_EVIDENCE', '上下文推测需要至少两个不同时刻的真实画面。')
        if event["subject_id"] == event["target_id"]:
            raise AppError("SELF_RELATION", "不能建立自身关系。")
        subject, target = event["subject_id"], event["target_id"]
        if event["epistemic_type"] == "observed" and event["action"] in {"take_out", "pick_up", "place_on"}:
            containment.pop(subject, None)
        if event["action"] == "put_in" and event["epistemic_type"] == "observed":
            if not target:
                raise AppError("MISSING_TARGET", "放入事件缺少容器。")
            node, visited = target, {subject}
            while node is not None:
                if node in visited:
                    raise AppError("CONTAINMENT_CYCLE", "检测到循环容器关系。")
                visited.add(node)
                node = containment.get(node)
            containment[subject] = target
        score = event["model_score"]
        if score is not None and not math.isfinite(score):
            raise AppError("INVALID_SCORE", "模型分数不是有效数值。")
