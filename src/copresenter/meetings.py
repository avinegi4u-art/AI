from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from copresenter.models import MeetingJoinStatus, PresentationSession, VoiceProfile
from copresenter.policy import DISCLOSURE_TEXT, ConsentPolicy


@dataclass(frozen=True)
class MeetingJoinPlan:
    status: MeetingJoinStatus
    message: str
    disclosure_text: str


class MeetingConnector(Protocol):
    def prepare_join(
        self,
        meeting_url: str,
        session: PresentationSession,
        profile: VoiceProfile,
        auto_speak: bool,
    ) -> MeetingJoinPlan:
        """Prepare a provider-specific meeting join operation."""


class AdapterRequiredMeetingConnector:
    def prepare_join(
        self,
        meeting_url: str,
        session: PresentationSession,
        profile: VoiceProfile,
        auto_speak: bool,
    ) -> MeetingJoinPlan:
        auto_speak_text = "auto-speak enabled" if auto_speak else "auto-speak disabled"
        return MeetingJoinPlan(
            status=MeetingJoinStatus.READY_FOR_ADAPTER,
            message=(
                "Meeting safety checks passed. Configure a Teams/Bot/WebRTC adapter to "
                f"join {meeting_url} for session {session.id} with {auto_speak_text}."
            ),
            disclosure_text=DISCLOSURE_TEXT,
        )


class MeetingOrchestrator:
    def __init__(
        self,
        consent_policy: ConsentPolicy,
        connector: MeetingConnector | None = None,
    ) -> None:
        self._consent_policy = consent_policy
        self._connector = connector or AdapterRequiredMeetingConnector()

    def prepare_join(
        self,
        meeting_url: str,
        session: PresentationSession,
        profile: VoiceProfile,
        auto_speak: bool,
    ) -> MeetingJoinPlan:
        self._consent_policy.validate_session_for_meeting(session, profile)
        return self._connector.prepare_join(meeting_url, session, profile, auto_speak)
