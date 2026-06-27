import pytest

from copresenter.models import PresentationSession, VoiceProfile
from copresenter.policy import ConsentPolicy, PolicyViolation


def test_voice_profile_requires_consent() -> None:
    policy = ConsentPolicy()
    profile = VoiceProfile(
        owner_name="Presenter",
        consent_confirmed=False,
        disclosure_enabled=True,
    )

    with pytest.raises(PolicyViolation, match="consent"):
        policy.validate_voice_profile(profile)


def test_voice_profile_requires_disclosure() -> None:
    policy = ConsentPolicy()
    profile = VoiceProfile(
        owner_name="Presenter",
        consent_confirmed=True,
        disclosure_enabled=False,
    )

    with pytest.raises(PolicyViolation, match="disclosure"):
        policy.validate_voice_profile(profile)


def test_meeting_requires_source_documents() -> None:
    policy = ConsentPolicy()
    profile = VoiceProfile(
        owner_name="Presenter",
        consent_confirmed=True,
        disclosure_enabled=True,
    )
    session = PresentationSession(
        name="Empty meeting",
        document_ids=[],
        voice_profile_id=profile.id,
    )

    with pytest.raises(PolicyViolation, match="source document"):
        policy.validate_session_for_meeting(session, profile)
