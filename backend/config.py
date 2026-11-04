from dataclasses import dataclass, field
import os
import math
from pathlib import Path
import shutil

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    data_dir: Path = ROOT / "data"
    allowed_hosts: tuple[str, ...] = ('127.0.0.1', 'localhost', '[::1]')
    allowed_origins: tuple[str, ...] = ('http://localhost:5173', 'http://127.0.0.1:5173', 'http://localhost:8000', 'http://127.0.0.1:8000')
    cloud_mode: bool = False
    lan_token: str = field(default="", repr=False)
    trusted_proxy_cidrs: tuple[str, ...] = ()
    cloud_cookie_secure: bool = True
    cloud_max_videos: int = 20
    cloud_videos_per_visitor: int = 3
    cloud_storage_mb: int = 1024
    provider: str = "qwen"
    api_key: str = field(default="", repr=False)
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen3.8-max"
    review_model: str = "qwen3.8-max"
    hybrid_safety_enabled: bool = True
    safety_model_config: Path = ROOT / "elder-safety/ai-service/config.camera-gpu.json"
    safety_python: Path = ROOT / "elder-safety/.venv-gpu/Scripts/python.exe"
    review_input_price: float = 0
    review_output_price: float = 0
    review_cost_confirmed: bool = False
    enable_network: bool = False
    record_qa_enabled: bool = True
    cost_confirmed: bool = False
    input_price: float = 0
    output_price: float = 0
    max_video_cost: float = 2
    max_daily_cost: float = 5
    max_video_seconds: int = 86400
    max_upload_bytes: int = 65536 * 1024 * 1024
    media_timeout: int = 1800
    max_sampled_frames: int = 100000
    window_output_tokens: int = 4096
    long_video_threshold_seconds: int = 120
    long_sampling_fps: float = .5
    long_window_seconds: int = 30
    long_window_stride_seconds: int = 28
    activity_filter_enabled: bool = True
    activity_filter_fps: float = 1
    activity_filter_pixel_threshold: int = 18
    activity_filter_change_ratio: float = .003
    activity_filter_review_seconds: int = 300
    activity_filter_person_review_seconds: int = 60
    activity_filter_postroll_seconds: int = 30
    max_calls: int = 12
    max_frame_references: int = 128
    max_output_tokens: int = 8192
    max_daily_calls: int = 24
    absence_alert_seconds: int = 300
    max_concurrency: int = 2
    sampling_fps: float = 1
    enhanced_safety_sampling: bool = True
    safety_sampling_fps: float = 4
    safety_window_seconds: int = 4
    safety_window_stride_seconds: int = 3
    window_seconds: int = 8
    window_stride_seconds: int = 6
    max_retries: int = 1
    billing_mode: str = "priced"
    timeout: float = 90
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    def __post_init__(self):
        if self.lan_token and (len(self.lan_token) < 32 or not self.lan_token.isascii()
                               or any(c.isspace() for c in self.lan_token)):
            raise ValueError('手机访问令牌须为至少32位的无空白ASCII字符串。')
        if self.lan_token and self.cloud_mode:
            raise ValueError('手机局域网入口与云部署模式应分别启动。')
        if not self.allowed_hosts or '*' in self.allowed_hosts:
            raise ValueError('须配置明确的访问域名，不能允许所有域名。')
        if min(self.cloud_max_videos, self.cloud_videos_per_visitor) < 1 or self.cloud_storage_mb < 0:
            raise ValueError('录像数量须为正数；存储配额可设为0以取消固定配额。')
        if (not .2 <= self.safety_sampling_fps <= 4 or not 2 <= self.safety_window_seconds <= 30
                or not 1 <= self.safety_window_stride_seconds <= self.safety_window_seconds):
            raise ValueError('危险事件增强采样的帧率或窗口配置无效。')
        if (not .5 <= self.activity_filter_fps <= 4
                or not 1 <= self.activity_filter_pixel_threshold <= 255
                or not 0 < self.activity_filter_change_ratio <= 1
                or not 1 <= self.activity_filter_person_review_seconds <= self.activity_filter_review_seconds <= 3600
                or not 0 <= self.activity_filter_postroll_seconds <= 300):
            raise ValueError('静止过滤的采样率、变化阈值或抽查间隔无效。')
        if self.absence_alert_seconds < 60:
            raise ValueError('长时间未出现提醒阈值不得低于60秒。')
        if any(not math.isfinite(v) or v < 0 for v in [self.input_price, self.output_price, self.review_input_price, self.review_output_price, self.max_video_cost, self.max_daily_cost]):
            raise ValueError("计费价格和预算必须为非负有限数值。")
        if self.max_calls < 1 or self.max_frame_references < 1 or self.max_output_tokens < 1:
            raise ValueError("调用、帧和输出预算必须大于零。")
        if not .2 <= self.sampling_fps <= 4 or not 1 <= self.max_concurrency <= 2:
            raise ValueError("抽帧率必须为0.2–4fps，并发必须为1–2。")
        if not 2 <= self.window_seconds <= 30 or not 1 <= self.window_stride_seconds <= self.window_seconds:
            raise ValueError("窗口长度/步长无效。")
        if self.billing_mode not in {"priced", "request_limited"} or self.max_daily_calls < 1 or not 0 <= self.max_retries <= 1:
            raise ValueError("费用模式、日请求上限或重试上限无效。")
        if min(self.max_video_seconds, self.max_upload_bytes) < 0 or min(self.media_timeout, self.max_sampled_frames) <= 0 or not 512 <= self.window_output_tokens <= 4096:
            raise ValueError("文件和时长上限可设0表示不限制；媒体处理预算须为正数，每窗口输出预算须为512–4096。")
        if self.long_video_threshold_seconds < 1 or not .2 <= self.long_sampling_fps <= 4 or not 2 <= self.long_window_seconds <= 30 or not 1 <= self.long_window_stride_seconds <= self.long_window_seconds:
            raise ValueError('长视频采样、窗口或阈值配置无效。')

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env", override=False)
        flag = lambda n: os.getenv(n, "false").lower() == "true"
        def project_path(value):
            path = Path(value)
            return path.resolve() if path.is_absolute() else (ROOT / path).resolve()
        def media_tool(name, fallback):
            configured = os.getenv(name)
            if configured:
                command = shutil.which(configured)
                return command or str(project_path(configured))
            return shutil.which(fallback) or str(ROOT / f"elder-safety/tools/ffmpeg/{fallback}.exe")
        return cls(
            allowed_hosts=tuple(x.strip() for x in os.getenv('APP_ALLOWED_HOSTS', ','.join(cls.allowed_hosts)).split(',') if x.strip()),
            allowed_origins=tuple(x.strip().rstrip('/') for x in os.getenv('APP_ALLOWED_ORIGINS', ','.join(cls.allowed_origins)).split(',') if x.strip()),
            cloud_mode=flag('CLOUD_MODE'),
            lan_token=os.getenv('APP_LAN_TOKEN', ''),
            trusted_proxy_cidrs=tuple(x.strip() for x in os.getenv('APP_TRUSTED_PROXY_CIDRS', '').split(',') if x.strip()),
            cloud_cookie_secure=os.getenv('CLOUD_COOKIE_SECURE', 'true').lower() == 'true',
            cloud_max_videos=int(os.getenv('CLOUD_MAX_VIDEOS', '20')),
            cloud_videos_per_visitor=int(os.getenv('CLOUD_VIDEOS_PER_VISITOR', '3')),
            cloud_storage_mb=int(os.getenv('CLOUD_STORAGE_MB', '1024')),
            provider=os.getenv("VLM_PROVIDER", "qwen"),
            data_dir=project_path(os.getenv("DATA_DIR", "data")),
            api_key=os.getenv("VLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY", ""),
            base_url=os.getenv("VLM_BASE_URL", cls.base_url).rstrip("/"),
            model=os.getenv("VLM_MODEL", cls.model),
            review_model=os.getenv("VLM_REVIEW_MODEL", cls.review_model),
            hybrid_safety_enabled=os.getenv("HYBRID_SAFETY_ENABLED", "true").lower() == "true",
            safety_model_config=project_path(os.getenv("SAFETY_MODEL_CONFIG", "runtime/team-camera.json")),
            safety_python=project_path(os.getenv("SAFETY_PYTHON", "elder-safety/.venv-gpu/Scripts/python.exe")),
            review_input_price=float(os.getenv('VLM_REVIEW_INPUT_RMB_PER_MILLION', '0')),
            review_output_price=float(os.getenv('VLM_REVIEW_OUTPUT_RMB_PER_MILLION', '0')),
            review_cost_confirmed=flag('VLM_REVIEW_COST_CONFIRMED'),
            enable_network=flag("VLM_ENABLE_NETWORK"), cost_confirmed=flag("VLM_COST_CONFIRMED"),
            record_qa_enabled=os.getenv('RECORD_QA_ENABLED', 'true').lower() == 'true',
            input_price=float(os.getenv("VLM_INPUT_RMB_PER_MILLION", "0")),
            output_price=float(os.getenv("VLM_OUTPUT_RMB_PER_MILLION", "0")),
            max_video_cost=float(os.getenv("MAX_COST_RMB_PER_VIDEO", "2")),
            max_daily_cost=float(os.getenv("MAX_COST_RMB_PER_DAY", "5")),
            max_video_seconds=int(os.getenv("MAX_VIDEO_SECONDS", "86400")),
            max_upload_bytes=int(os.getenv("MAX_UPLOAD_MB", "65536")) * 1024 * 1024,
            media_timeout=int(os.getenv("MEDIA_TIMEOUT_SECONDS", "1800")),
            max_sampled_frames=int(os.getenv("MAX_SAMPLED_FRAMES", "100000")),
            window_output_tokens=int(os.getenv("VLM_OUTPUT_TOKENS_PER_WINDOW", "4096")),
            long_video_threshold_seconds=int(os.getenv('LONG_VIDEO_THRESHOLD_SECONDS', '120')),
            long_sampling_fps=float(os.getenv('LONG_VIDEO_SAMPLING_FPS', '.5')),
            long_window_seconds=int(os.getenv('LONG_VIDEO_WINDOW_SECONDS', '30')),
            long_window_stride_seconds=int(os.getenv('LONG_VIDEO_WINDOW_STRIDE_SECONDS', '28')),
            activity_filter_enabled=os.getenv('ACTIVITY_FILTER_ENABLED', 'true').lower() == 'true',
            activity_filter_fps=float(os.getenv('ACTIVITY_FILTER_FPS', '1')),
            activity_filter_pixel_threshold=int(os.getenv('ACTIVITY_FILTER_PIXEL_THRESHOLD', '18')),
            activity_filter_change_ratio=float(os.getenv('ACTIVITY_FILTER_CHANGE_RATIO', '.003')),
            activity_filter_review_seconds=int(os.getenv('ACTIVITY_FILTER_REVIEW_SECONDS', '300')),
            activity_filter_person_review_seconds=int(os.getenv('ACTIVITY_FILTER_PERSON_REVIEW_SECONDS', '60')),
            activity_filter_postroll_seconds=int(os.getenv('ACTIVITY_FILTER_POSTROLL_SECONDS', '30')),
            max_calls=int(os.getenv("MAX_MODEL_CALLS_PER_VIDEO", "12")),
            max_frame_references=int(os.getenv("MAX_FRAME_REFERENCES", "128")),
            max_output_tokens=int(os.getenv("MAX_OUTPUT_TOKENS_PER_VIDEO", "8192")),
            max_daily_calls=int(os.getenv("MAX_MODEL_CALLS_PER_DAY", "24")),
            absence_alert_seconds=int(os.getenv('PERSON_ABSENCE_ALERT_SECONDS', '300')),
            max_concurrency=int(os.getenv("MAX_CONCURRENCY", "2")),
            sampling_fps=float(os.getenv("VIDEO_SAMPLING_FPS", "1")),
            enhanced_safety_sampling=os.getenv('SAFETY_SAMPLING_ENABLED', 'true').lower() == 'true',
            safety_sampling_fps=float(os.getenv('SAFETY_SAMPLING_FPS', '4')),
            safety_window_seconds=int(os.getenv('SAFETY_WINDOW_SECONDS', '4')),
            safety_window_stride_seconds=int(os.getenv('SAFETY_WINDOW_STRIDE_SECONDS', '3')),
            window_seconds=int(os.getenv("VIDEO_WINDOW_SECONDS", "8")),
            window_stride_seconds=int(os.getenv("VIDEO_WINDOW_STRIDE_SECONDS", "6")),
            max_retries=int(os.getenv("VLM_MAX_RETRIES", "1")),
            billing_mode=os.getenv("VLM_BILLING_MODE", "priced"),
            timeout=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "90")),
            ffmpeg=media_tool("FFMPEG_PATH", "ffmpeg"),
            ffprobe=media_tool("FFPROBE_PATH", "ffprobe"),
        )
