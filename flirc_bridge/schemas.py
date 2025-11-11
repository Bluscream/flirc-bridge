from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

PatternFormatLiteral = Literal["raw", "csv", "pronto", "lirc", "array", "json"]


class PatternFormatModel(BaseModel):
    format: PatternFormatLiteral = Field(description="Pattern format identifier")
    data: List[str] = Field(
        default_factory=list,
        description="List of pattern values, stored as strings for portability",
    )
    hash: Optional[str] = Field(
        default=None,
        description="SHA-256 hash of the pattern payload",
    )
    repeat: Optional[int] = Field(
        default=1,
        ge=1,
        description="Repeat count to apply when transmitting this pattern",
    )
    ik: Optional[int] = Field(
        default=23000,
        ge=1,
        description="Inter-key delay to apply (maps to --ik)",
    )

    @field_validator("data", mode="before")
    def _ensure_strings(cls, value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [value]
        raise ValueError("data must be a string or list of strings")

    @field_validator("repeat", mode="before")
    def _normalize_repeat(cls, value: Any) -> Optional[int]:
        if value is None:
            return 1
        try:
            repeat_value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("repeat must be an integer") from exc
        if repeat_value < 1:
            raise ValueError("repeat must be >= 1")
        return repeat_value

    @field_validator("ik", mode="before")
    def _normalize_ik(cls, value: Any) -> Optional[int]:
        if value is None:
            return 23000
        try:
            ik_value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("ik must be an integer") from exc
        if ik_value <= 0:
            raise ValueError("ik must be > 0")
        return ik_value


class PatternRecord(BaseModel):
    device: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)
    formats: List[PatternFormatModel] = Field(..., min_items=1)


class PatternListResponse(BaseModel):
    patterns: List[PatternRecord]


class SendPatternRequest(BaseModel):
    device: Optional[str] = Field(None, description="Device name for stored pattern")
    action: Optional[str] = Field(None, description="Action name for stored pattern")
    format: Optional[PatternFormatLiteral] = Field(
        None,
        description="Format for custom pattern",
    )
    data: Optional[List[str]] = Field(
        None,
        description="Payload for custom pattern (applies when device/action are omitted)",
    )
    ik: Optional[int] = Field(None, description="Inter-key delay (alias for carrier)")
    carrier: Optional[int] = Field(None, description="Deprecated alias for ik")
    repeat: Optional[int] = Field(None, description="Repeat count for IR transmission", ge=1)

    @field_validator("data", mode="before")
    def _ensure_optional_list(cls, value: Any) -> Optional[List[str]]:
        if value is None:
            return None
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [value]
        raise ValueError("data must be a list or string when provided")

    @model_validator(mode="after")
    def _validate_custom_pattern(self) -> "SendPatternRequest":
        if not self.device and not self.action:
            if self.format is None:
                raise ValueError("format is required for custom patterns")
            if not self.data:
                raise ValueError("data is required for custom patterns")
        if self.ik is None and self.carrier is not None:
            self.ik = self.carrier
        if self.repeat is not None and self.repeat < 1:
            raise ValueError("repeat must be >= 1 when provided")
        if self.ik is not None and self.ik <= 0:
            raise ValueError("ik must be > 0 when provided")
        return self


class ReceivePatternResponse(BaseModel):
    format: str
    data: List[str]
    raw_output: str


class ReceivePatternRequest(BaseModel):
    save: bool = Field(False, description="Persist received pattern to the database")
    device: Optional[str] = Field(None, description="Device name when saving")
    action: Optional[str] = Field(None, description="Action name when saving")
    format: PatternFormatLiteral = Field("raw", description="Expected output format")
    timeout: int = Field(10, description="Listening duration in seconds")


class ErrorResponse(BaseModel):
    detail: str
