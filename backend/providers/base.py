from dataclasses import dataclass
from typing import Protocol

from ..events import Extraction


@dataclass
class ProviderResult:
    extraction: Extraction
    usage: dict


class VisionProvider(Protocol):
    name: str
    model: str
    is_mock: bool

    def ready(self) -> None: ...

    def extract(self, *, window: dict, entities: list[dict], video: dict,
                job_id: str, output_tokens: int, recovery: bool = False) -> ProviderResult: ...
