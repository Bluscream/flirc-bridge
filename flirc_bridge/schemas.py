from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

PatternFormatLiteral = Literal["raw", "csv", "pronto"]


class PatternModel(BaseModel):
    id: Optional[str] = Field(None, description="Pattern identifier (UUID)")
    format: PatternFormatLiteral = Field("raw", description="Pattern format identifier")
    data: List[str] = Field(
        default_factory=list,
        description="Pattern payload as list of strings",
    )
    repeat: int = Field(1, ge=1, description="Repeat count for the pattern")
    ik: int = Field(23, ge=1, description="Inter-key delay")
    hash: Optional[str] = Field(None, description="MD5 hash of format+data+repeat+ik")
    created_at: Optional[str] = Field(None, description="ISO timestamp when pattern was created")
    updated_at: Optional[str] = Field(None, description="ISO timestamp when pattern was last updated")
    sent_at: Optional[str] = Field(None, description="ISO timestamp when pattern was last sent")

    @field_validator("data", mode="before")
    def _ensure_strings(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            if not value:
                return []
            return [value]
        raise ValueError("data must be a string or list of strings")

    @field_validator("repeat", mode="before")
    def _normalize_repeat(cls, value: Any) -> int:
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
    def _normalize_ik(cls, value: Any) -> int:
        if value is None:
            return 23
        try:
            ik_value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("ik must be an integer") from exc
        if ik_value <= 0:
            raise ValueError("ik must be > 0")
        return ik_value


class PatternRecord(BaseModel):
    device_id: Optional[str] = Field(None, description="Device identifier (UUID)")
    device: Optional[str] = Field(None, description="Device name")
    device_description: Optional[str] = Field("", description="Device description")
    action_id: Optional[str] = Field(None, description="Action identifier (UUID)")
    action: Optional[str] = Field(None, description="Action name")
    action_description: Optional[str] = Field("", description="Action description")
    patterns: List[PatternModel] = Field(..., min_items=1, description="Patterns linked to this action")

    @model_validator(mode="after")
    def _ensure_identifiers(self) -> "PatternRecord":
        if not self.device_id and not self.device:
            self.device = None
        if not self.action_id and not self.action:
            self.action = None
        return self


class ActionResponse(BaseModel):
    id: str
    device_id: str
    name: str
    description: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    patterns: List[PatternModel]


class DeviceResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    actions: List[ActionResponse]


ActionResponse.model_rebuild()
DeviceResponse.model_rebuild()


class PatternListResponse(BaseModel):
    devices: List[DeviceResponse]


class SendPatternPayload(BaseModel):
    device: Optional[str] = Field(None, description="Device identifier (UUID or name)")
    device_name: Optional[str] = Field(None, description="Device name override")
    action: Optional[str] = Field(None, description="Action identifier (UUID or name)")
    action_name: Optional[str] = Field(None, description="Action name override")
    pattern: Optional[str] = Field(None, description="Pattern identifier (UUID)")
    format: Optional[PatternFormatLiteral] = Field(None, description="Pattern format for custom payload")
    data: Optional[List[str]] = Field(None, description="Custom pattern payload")
    repeat: Optional[int] = Field(None, ge=1, description="Repeat override")
    ik: Optional[int] = Field(None, ge=1, description="Inter-key delay override")
    save: Optional[bool] = Field(False, description="Persist custom payload")

    @field_validator("data", mode="before")
    def _normalize_data(cls, value: Any) -> Optional[List[str]]:
        if value is None:
            return None
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            if not value:
                return []
            return [value]
        raise ValueError("data must be a list or string")

    @field_validator("repeat", "ik", mode="before")
    def _normalize_ints(cls, value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("repeat/ik must be integers") from exc

    @field_validator("save", mode="before")
    def _normalize_bool(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(value, (int, float)):
            return bool(value)
        return False

    @model_validator(mode="after")
    def _validate_payload(self) -> "SendPatternPayload":
        if not self.pattern and not self.action and not self.device and not self.data:
            raise ValueError("Provide pattern id, action/device identifiers, or custom data to send")
        if self.data and not self.format:
            raise ValueError("format is required when providing custom data")
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


class DevicePayload(BaseModel):
    name: Optional[str] = Field(None, description="Device name")
    description: Optional[str] = Field("", description="Device description")

    @model_validator(mode="after")
    def _ensure_name(self) -> "DevicePayload":
        if not self.name or not self.name.strip():
            raise ValueError("Device name is required")
        self.name = self.name.strip()
        return self


class DeviceUpdatePayload(BaseModel):
    name: Optional[str] = Field(None, description="Updated device name")
    description: Optional[str] = Field(None, description="Updated device description")


class ActionPayload(BaseModel):
    device_id: Optional[str] = Field(None, description="Existing device identifier")
    device_name: Optional[str] = Field(None, description="Device name (if id not provided)")
    name: Optional[str] = Field(None, description="Action name")
    description: Optional[str] = Field("", description="Action description")

    @model_validator(mode="after")
    def _ensure_fields(self) -> "ActionPayload":
        if not self.device_id and (not self.device_name or not self.device_name.strip()):
            raise ValueError("device_id or device_name is required")
        if not self.name or not self.name.strip():
            raise ValueError("Action name is required")
        self.name = self.name.strip()
        if self.device_name is not None:
            self.device_name = self.device_name.strip()
        return self


class ActionUpdatePayload(BaseModel):
    name: Optional[str] = Field(None, description="Updated action name")
    description: Optional[str] = Field(None, description="Updated action description")
