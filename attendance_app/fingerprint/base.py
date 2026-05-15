from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EnrollmentResult:
    template_ref: str
    quality_score: int | None = None
    message: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MatchResult:
    template_ref: str
    confidence: int | None = None
    message: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)


class FingerprintProvider(ABC):
    name = "base"

    @abstractmethod
    def enroll(self, staff_code: str) -> EnrollmentResult:
        raise NotImplementedError

    @abstractmethod
    def identify(self, hint: str | None = None) -> MatchResult | None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, template_ref: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def healthcheck(self) -> dict[str, Any]:
        raise NotImplementedError
