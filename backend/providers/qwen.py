import base64
import json
import hashlib
from pathlib import Path
import time
from uuid import uuid4

import httpx
from pydantic import ValidationError

from ..config import ROOT, Settings
from ..db import Database, encode
from ..errors import AppError
from ..events import Extraction
from .base import ProviderResult
from ..prompting import load_event_prompt
from .formatting import parse_extraction


class QwenProvider:
    name, is_mock = "qwen", False

    def __init__(self, settings: Settings, db: Database, transport=None, event_prompt=None):
        self.settings, self.db, self.transport = settings, db, transport
        self.model = settings.model
        self.event_prompt = event_prompt or load_event_prompt()

    def ready(self):
        s = self.settings
        if s.provider != "qwen":
            raise AppError("UNSUPPORTED_PROVIDER", "当前版本已实现Qwen适配器，请配置VLM_PROVIDER=qwen。", 503)
        if not s.api_key:
            raise AppError("MODEL_NOT_CONFIGURED", "请在本地.env配置VLM_API_KEY（或DASHSCOPE_API_KEY）。", 503)
        if not s.enable_network:
            raise AppError("NETWORK_DISABLED", "模型网络调用未开启，请配置VLM_ENABLE_NETWORK=true。", 503)
        if s.billing_mode == "priced" and (not s.cost_confirmed or s.input_price <= 0 or s.output_price <= 0):
            raise AppError("COST_NOT_CONFIRMED", "未确认金额计费；核实单价或明确使用VLM_BILLING_MODE=request_limited严格限量模式。", 503)
        if not s.base_url.startswith("https://"):
            raise AppError("INSECURE_ENDPOINT", "视觉API地址必须使用HTTPS。", 503)

    def request(self, *, content, video_id, job_id, output_tokens, input_bound, frame_ids=None, kind="events", prompt_version=None, prompt_sha256=None):
        self.ready()
        s = self.settings
        charge = None
        if s.billing_mode == "priced":
            amount = (input_bound * s.input_price + output_tokens * s.output_price) / 1_000_000
            charge = self.db.reserve(video_id, job_id, amount, s.max_video_cost, s.max_daily_cost)
        request_id = self.db.reserve_request(video_id, job_id, s.max_daily_calls, s.max_calls)
        payload = {"model": self.model, "messages": [{"role": "user", "content": content}],
                   "max_tokens": output_tokens, "temperature": 0, "enable_thinking": False}
        if kind in {"events", "verification", "memory_qa"}:
            payload["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        metadata = {"kind": kind, "frame_ids": frame_ids or [], "usage": {}, "billing_mode": s.billing_mode,
                    "rmb_cost": None, "latency_ms": None}
        if prompt_version:
            metadata.update(prompt_version=prompt_version, prompt_sha256=prompt_sha256)
        try:
            with httpx.Client(timeout=s.timeout, transport=self.transport, follow_redirects=False) as client:
                response = client.post(s.base_url + "/chat/completions", headers={"Authorization": "Bearer " + s.api_key}, json=payload)
            metadata["latency_ms"] = round((time.monotonic() - started) * 1000)
            metadata["http_status"] = response.status_code
            if response.status_code != 200:
                # Keep sanitized service diagnostics locally; never lose the cause of a failed paid request.
                try:
                    diagnostic = response.json()
                except ValueError:
                    diagnostic = {'http_status': response.status_code, 'error': 'Non-JSON provider error'}
                record_dir = s.data_dir / 'runs' / job_id
                record_dir.mkdir(parents=True, exist_ok=True)
                (record_dir / f'error-{uuid4().hex}.json').write_text(
                    encode({'request_metadata': metadata, 'response': diagnostic}).replace(s.api_key, '[REDACTED]'), encoding='utf8')
                retryable = response.status_code in {408, 429, 500, 502, 503, 504}
                raise AppError("PROVIDER_HTTP", f"视觉服务返回HTTP {response.status_code}；请检查账号、额度、模型与地区配置。", 503, retryable)
            body = response.json()
            record_dir = s.data_dir / "runs" / job_id
            record_dir.mkdir(parents=True, exist_ok=True)
            safe_record = encode({"request_metadata": metadata, "frame_ids": frame_ids or [], "response": body}).replace(s.api_key, "[REDACTED]")
            (record_dir / f"response-{uuid4().hex}.json").write_text(safe_record, encoding="utf-8")
            usage = body.get("usage", {})
            metadata["usage"] = {k: usage[k] for k in ["prompt_tokens", "completion_tokens", "total_tokens"] if isinstance(usage.get(k), int) and usage[k] >= 0}
            if all(k in metadata["usage"] for k in ["prompt_tokens", "completion_tokens"]):
                if charge is not None:
                    actual = (usage["prompt_tokens"] * s.input_price + usage["completion_tokens"] * s.output_price) / 1_000_000
                    self.db.settle(charge, actual, metadata["usage"])
                    metadata["rmb_cost"] = actual
                if usage["prompt_tokens"] > input_bound or usage["completion_tokens"] > output_tokens:
                    raise AppError("TOKEN_BOUND_EXCEEDED", "用量超出请求预留上界，已停止后续调用，请检查模型计费与输出设置。", 429)
            choice = body["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise AppError("MODEL_TRUNCATED", "模型输出未完整结束，该窗口未写入。", 422, False)
            result = choice["message"]["content"]
            if not isinstance(result, str):
                raise ValueError("Invalid content type")
            metadata["response_id"] = body.get("id")
            self.db.finish_request(request_id, "success", metadata)
            return result, metadata
        except httpx.TimeoutException:
            error = AppError("PROVIDER_TIMEOUT", "视觉服务请求超时，该窗口未被标记为成功。", 504, True)
        except httpx.RequestError:
            error = AppError("PROVIDER_UNAVAILABLE", "无法连接视觉服务，请检查网络和API地址。", 503, True)
        except AppError as exc:
            error = exc
        except (ValueError, KeyError, IndexError, TypeError):
            error = AppError("MODEL_OUTPUT_INVALID", "视觉服务响应无效，该窗口未写入。", 422, False)
        metadata.update(latency_ms=round((time.monotonic() - started) * 1000), error=error.public())
        self.db.finish_request(request_id, "failed", metadata)
        raise error from None

    def extract(self, *, window, entities, video, job_id, output_tokens, recovery=False, object_only=None):
        instructions = self.event_prompt.text
        prompt_version, prompt_hash = self.event_prompt.version, self.event_prompt.sha256
        if recovery:
            extra = load_event_prompt(ROOT / 'prompts/event_recovery.md')
            instructions += '\n' + extra.text
            prompt_version += '+recovery-' + extra.version.removeprefix('event-')
            prompt_hash = hashlib.sha256(instructions.encode('utf8')).hexdigest()
        text = instructions + "\n严格返回以下JSON Schema所定义的Extraction，不要返回完整memory。\n" + encode(Extraction.model_json_schema())
        if object_only is None:
            object_only = self.settings.hybrid_safety_enabled and not self.is_mock
        if object_only:
            # Task context, like the schema/entities below; keep the base/recovery
            # prompt fingerprint stable and audit this mode separately in metadata.
            text += ("\n【物品/寻迹模式 object_only】本任务是独立物品追踪，安全候选由另一独立链路处理。"
                     "本次仅提取物品、寻迹与普通活动；禁止生成fall、help_gesture、lying_on_floor、climb、"
                     "flame、smoke、sparks等安全事件，既不能观察确认也不能推测报警。"
                     "可以保留walk、sit_down、stand_up、lie_down及满足证据要求的no_person_visible。"
                     "即使看见危险也不在本次Extraction重复输出；不能从未报告危险推断安全。")
        text += "\n已知实体候选（充分视觉依据才复用id，否则新建）：" + encode(entities)
        content = [{"type": "text", "text": text}]
        for frame in window["frames"]:
            content.append({"type": "text", "text": f"frame_id={frame['id']}; source_pts_ms={frame['pts_ms']}"})
            image = base64.b64encode(Path(frame["path"]).read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image}, "max_pixels": 960 * 960})
        raw, metadata = self.request(content=content, video_id=video["id"], job_id=job_id, output_tokens=output_tokens,
                                     input_bound=len(text.encode("utf-8")) + 2048 + 4096 * len(window["frames"]),
                                     frame_ids=[f["id"] for f in window["frames"]], prompt_version=prompt_version, prompt_sha256=prompt_hash)
        if object_only:
            metadata['extraction_mode'] = 'object_only'
            metadata['request_text_sha256'] = hashlib.sha256(text.encode('utf8')).hexdigest()
        try:
            extraction, repairs = parse_extraction(raw)
            if repairs:
                metadata['format_repairs'] = repairs
                folder = self.settings.data_dir / 'runs' / job_id
                (folder / ('format-repair-' + uuid4().hex + '.json')).write_text(
                    encode({'source_response_id': metadata.get('response_id'), 'repairs': repairs,
                            'extraction': extraction.model_dump()}).replace(self.settings.api_key, '[REDACTED]'), encoding='utf8')
            return ProviderResult(extraction, metadata)
        except (ValueError, ValidationError, TypeError, KeyError):
            # The job manager owns the bounded retry and all request budgets.
            # Re-observe the images using the recovery prompt; never translate
            # an arbitrary caption into visual facts locally.
            raise AppError("MODEL_OUTPUT_INVALID", "视觉模型JSON不符合事件格式；原始响应已在本地保存。", 422, True) from None

    def verify_event(self, *, instructions, frames, video, job_id, regions, focus):
        import cv2
        import numpy as np
        import hashlib
        content = [{'type': 'text', 'text': instructions}]
        for frame in frames:
            content.append({'type': 'text', 'text': f"完整原图 frame_id={frame['id']}; source_pts_ms={frame['pts_ms']}"})
            image = base64.b64encode(Path(frame['path']).read_bytes()).decode('ascii')
            content.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + image}, 'max_pixels': 960 * 960})
        # A user-selected crop is additional detail, never a substitute original frame.
        crops = 0
        if focus:
            for frame in (frames[0], frames[-1]):
                original = cv2.imdecode(np.frombuffer(Path(frame['path']).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
                h, w = original.shape[:2]
                crop = original[int(focus['y1']*h):max(int(focus['y1']*h)+1,int(focus['y2']*h)),
                                int(focus['x1']*w):max(int(focus['x1']*w)+1,int(focus['x2']*w))]
                if not crop.size:
                    continue
                ok, data = cv2.imencode('.jpg', crop)
                if ok:
                    content.append({'type': 'text', 'text': f"辅助局部放大，源frame_id={frame['id']}；区域={json.dumps(focus,ensure_ascii=False)}；坐标仍须以完整原图为准。"})
                    content.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,'+base64.b64encode(data).decode('ascii')}})
                    crops += 1
        return self.request(content=content, video_id=video['id'], job_id=job_id, output_tokens=2048,
                            input_bound=len(instructions.encode('utf8')) + 4096 * (len(frames)+crops) + 4096,
                            frame_ids=[f['id'] for f in frames], kind='verification', prompt_version='verification-v1.0',
                            prompt_sha256=hashlib.sha256(instructions.encode('utf8')).hexdigest())
