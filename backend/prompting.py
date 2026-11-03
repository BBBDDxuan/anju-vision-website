"""Version and fingerprint the actual prompt sent to the model."""
from dataclasses import dataclass
import hashlib
import re

from .config import ROOT


@dataclass(frozen=True)
class EventPrompt:
    text: str
    version: str
    sha256: str


def load_event_prompt(path=None):
    text=(path or ROOT/'prompts/event_extraction.md').read_text(encoding='utf8')
    match=re.search(r'\bv(\d+\.\d+)\b',text.splitlines()[0])
    if not match:
        raise ValueError('事件提示词必须声明版本号。')
    return EventPrompt(text, 'event-v'+match.group(1), hashlib.sha256(text.encode('utf8')).hexdigest())
