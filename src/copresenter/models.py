from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl


class MeetingJoinStatus(str, Enum):
    READY_FOR_ADAPTER = "ready_for_adapter"
    BLOCKED = "blocked"


class VoiceMode(str, Enum):
    SYNTHETIC_PLACEHOLDER = "synthetic_placeholder"
    AUTHORIZED_CLONE_ADAPTER = "authorized_clone_adapter"


@dataclass(frozen=True)
class DocumentRecord:
    title: str
    source_filename: str
    mime_type: str
    text: str
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class VoiceProfile:
    owner_name: str
    consent_confirmed: bool
    disclosure_enabled: bool
    voice_mode: VoiceMode = VoiceMode.SYNTHETIC_PLACEHOLDER
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class PresentationSession:
    name: str
    document_ids: list[str]
    voice_profile_id: str
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class SourceCitation:
    document_id: str
    document_title: str
    chunk_index: int
    text: str
    score: float


class DocumentUploadResponse(BaseModel):
    id: str
    title: str
    source_filename: str
    character_count: int


class VoiceProfileCreateRequest(BaseModel):
    owner_name: str = Field(min_length=1)
    consent_confirmed: bool
    disclosure_enabled: bool = True
    voice_mode: VoiceMode = VoiceMode.SYNTHETIC_PLACEHOLDER


class VoiceProfileResponse(BaseModel):
    id: str
    owner_name: str
    consent_confirmed: bool
    disclosure_enabled: bool
    voice_mode: VoiceMode


class PresentationSessionCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    document_ids: list[str] = Field(min_length=1)
    voice_profile_id: str


class PresentationSessionResponse(BaseModel):
    id: str
    name: str
    document_ids: list[str]
    voice_profile_id: str


class ScriptSection(BaseModel):
    heading: str
    talking_points: list[str]


class PresentationScriptResponse(BaseModel):
    session_id: str
    opening_disclosure: str
    sections: list[ScriptSection]
    closing: str
    speech_preview: str


class AskRequest(BaseModel):
    question: str = Field(min_length=1)


class AnswerResponse(BaseModel):
    answer: str
    citations: list[dict[str, str | int | float]]


class MeetingJoinRequest(BaseModel):
    meeting_url: HttpUrl
    auto_speak: bool = True


class MeetingJoinResponse(BaseModel):
    status: MeetingJoinStatus
    message: str
    disclosure_text: str
