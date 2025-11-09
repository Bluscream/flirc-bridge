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

    @field_validator("data", mode="before")
    def _ensure_strings(cls, value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [value]
        raise ValueError("data must be a string or list of strings")


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
    carrier: Optional[int] = Field(None, description="Carrier frequency in Hz")
    repeat: Optional[int] = Field(None, description="Repeat count for IR transmission")

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
