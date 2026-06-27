from __future__ import annotations

from dataclasses import dataclass

from copresenter.models import VoiceMode, VoiceProfile
from copresenter.policy import ConsentPolicy


@dataclass(frozen=True)
class SpeechPayload:
    voice_profile_id: str
    voice_mode: VoiceMode
    text: str
    provider_hint: str


class VoiceService:
    def __init__(self, consent_policy: ConsentPolicy) -> None:
        self._consent_policy = consent_policy

    def prepare_speech(self, profile: VoiceProfile, text: str) -> SpeechPayload:
        self._consent_policy.validate_voice_profile(profile)
        provider_hint = (
            "Synthetic placeholder voice. Connect an approved voice provider adapter "
            "to render audio with this profile."
        )
        if profile.voice_mode == VoiceMode.AUTHORIZED_CLONE_ADAPTER:
            provider_hint = (
                "Authorized voice clone adapter requested. Configure a provider that "
                "verifies speaker consent and preserves disclosure."
            )
        return SpeechPayload(
            voice_profile_id=profile.id,
            voice_mode=profile.voice_mode,
            text=text,
            provider_hint=provider_hint,
        )
