from __future__ import annotations

from dataclasses import dataclass

from copresenter.models import PresentationSession, VoiceProfile


DISCLOSURE_TEXT = (
    "Disclosure: this meeting includes an AI co-presenter using an authorized "
    "synthetic voice. Answers are generated from uploaded presentation materials, "
    "and uncertain answers will be stated as uncertain."
)


class PolicyViolation(ValueError):
    """Raised when a request would violate consent or disclosure requirements."""


@dataclass(frozen=True)
class ConsentPolicy:
    require_voice_consent: bool = True
    require_meeting_disclosure: bool = True

    def validate_voice_profile(self, profile: VoiceProfile) -> None:
        if self.require_voice_consent and not profile.consent_confirmed:
            raise PolicyViolation("Voice profile consent must be confirmed before use.")
        if self.require_meeting_disclosure and not profile.disclosure_enabled:
            raise PolicyViolation("Meeting disclosure must stay enabled for voice use.")

    def validate_session_for_meeting(
        self,
        session: PresentationSession,
        profile: VoiceProfile,
    ) -> None:
        self.validate_voice_profile(profile)
        if not session.document_ids:
            raise PolicyViolation("At least one source document is required for a meeting.")
