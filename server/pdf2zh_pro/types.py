"""Stable results shared by the HTTP service and translation worker."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class TranslationOutputFile:
    output_mode: str
    output_path: Path
    filename: str


@dataclass(frozen=True)
class TranslationResult:
    files: dict[str, TranslationOutputFile]
    translation_summary: dict | None = None
    failed_paragraphs: list = field(default_factory=list)
    quality_summary: dict | None = None
    translation_memory_sync: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    service: str
    model: str | None
    diagnostics: list[dict[str, str]]
    live_test: dict[str, Any]
    status: str = "ok"
    resolved_protocol: str | None = None
    codex_runtime: dict | None = None


@dataclass(frozen=True)
class DiagnosticMessage:
    code: str
    severity: str
    message: str
    suggestion: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.suggestion:
            payload["suggestion"] = self.suggestion
        return payload
