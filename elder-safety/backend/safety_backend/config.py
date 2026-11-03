from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: ROOT / "runtime")
    database_url: str = ""
    user_token: str = ""
    device_tokens: dict[str, str] = field(default_factory=dict)
    review_mode: str = "mock"
    push_mode: str = "mock"
    qwen_model: str = "qwen3-vl-plus"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    allow_cloud_images: bool = False
    firebase_credentials: str = ""
    escalation_seconds: int = 120
    offline_seconds: int = 60
    dedup_seconds: int = 30
    evidence_ttl_seconds: int = 86400
    retry_seconds: float = 5
    review_interval_seconds: float = 5
    request_timeout_seconds: float = 20
    max_attempts: int = 3
    max_upload_bytes: int = 20 * 1024 * 1024
    worker_enabled: bool = True

    def validate(self):
        tokens = [self.user_token, *self.device_tokens.values()]
        if not self.device_tokens or any(len(t) < 24 or "REPLACE_" in t for t in tokens):
            raise ValueError("Configure distinct random user/device tokens (24+ chars); run scripts/configure.py")
        if len(set(tokens)) != len(tokens):
            raise ValueError("User and device tokens must be distinct")
        if self.review_mode not in {"mock", "qwen"} or self.push_mode not in {"mock", "fcm"}:
            raise ValueError("Invalid provider mode")
        if min(self.escalation_seconds, self.offline_seconds, self.evidence_ttl_seconds, self.max_attempts) <= 0:
            raise ValueError("Durations and attempts must be positive")
        if min(self.dedup_seconds, self.retry_seconds, self.review_interval_seconds) < 0 or self.request_timeout_seconds <= 0:
            raise ValueError("Invalid timeout")
        self.data_dir = Path(self.data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = "sqlite:///" + (self.data_dir / "safety.db").as_posix()
        if self.database_url.startswith("sqlite:///./"):
            self.database_url = "sqlite:///" + (ROOT / self.database_url.removeprefix("sqlite:///")).resolve().as_posix()
        if self.review_mode == "qwen" and not self.qwen_base_url.startswith("https://"):
            raise ValueError("Qwen endpoint must use HTTPS")
        return self

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env", override=False)
        def value(name, default):
            return os.getenv("SAFETY_" + name, default)
        return cls(
            data_dir=Path(value("DATA_DIR", str(ROOT / "runtime"))) if Path(value("DATA_DIR", str(ROOT / "runtime"))).is_absolute() else ROOT / value("DATA_DIR", "runtime"),
            database_url=value("DATABASE_URL", ""), user_token=value("USER_TOKEN", ""),
            device_tokens=json.loads(value("DEVICE_TOKENS", "{}")),
            review_mode=value("REVIEW_MODE", "mock"), push_mode=value("PUSH_MODE", "mock"),
            qwen_model=value("QWEN_MODEL", "qwen3-vl-plus"),
            qwen_base_url=value("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            api_key=os.getenv("DASHSCOPE_API_KEY", ""), allow_cloud_images=value("ALLOW_CLOUD_IMAGES", "false").lower() == "true",
            firebase_credentials=value("FIREBASE_CREDENTIALS", ""),
            escalation_seconds=int(value("ESCALATION_SECONDS", "120")), offline_seconds=int(value("OFFLINE_SECONDS", "60")),
            dedup_seconds=int(value("DEDUP_SECONDS", "30")), evidence_ttl_seconds=int(value("EVIDENCE_TTL_SECONDS", "86400")),
            retry_seconds=float(value("RETRY_SECONDS", "5")), review_interval_seconds=float(value("REVIEW_INTERVAL_SECONDS", "5")),
            request_timeout_seconds=float(value("REQUEST_TIMEOUT_SECONDS", "20")), max_attempts=int(value("MAX_ATTEMPTS", "3")),
        ).validate()
