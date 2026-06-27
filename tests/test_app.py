from fastapi.testclient import TestClient

from copresenter.app import app


client = TestClient(app)


def test_voice_profile_without_consent_is_rejected() -> None:
    response = client.post(
        "/voice-profiles",
        json={
            "owner_name": "Presenter",
            "consent_confirmed": False,
            "disclosure_enabled": True,
        },
    )

    assert response.status_code == 400
    assert "consent" in response.json()["detail"]


def test_end_to_end_presentation_session_flow() -> None:
    upload = client.post(
        "/documents",
        files={
            "file": (
                "program.txt",
                b"The program improves onboarding. Support readiness is the main goal.",
                "text/plain",
            )
        },
    )
    assert upload.status_code == 200
    document_id = upload.json()["id"]

    voice = client.post(
        "/voice-profiles",
        json={
            "owner_name": "Presenter",
            "consent_confirmed": True,
            "disclosure_enabled": True,
        },
    )
    assert voice.status_code == 200
    voice_profile_id = voice.json()["id"]

    session = client.post(
        "/sessions",
        json={
            "name": "Program update",
            "document_ids": [document_id],
            "voice_profile_id": voice_profile_id,
        },
    )
    assert session.status_code == 200
    session_id = session.json()["id"]

    script = client.get(f"/sessions/{session_id}/script")
    assert script.status_code == 200
    assert "Disclosure" in script.json()["opening_disclosure"]
    assert "onboarding" in script.json()["speech_preview"]

    answer = client.post(
        f"/sessions/{session_id}/ask",
        json={"question": "What does the program improve?"},
    )
    assert answer.status_code == 200
    assert "onboarding" in answer.json()["answer"]
    assert answer.json()["citations"]

    join = client.post(
        f"/sessions/{session_id}/meeting/join",
        json={"meeting_url": "https://teams.microsoft.com/l/meetup-join/example"},
    )
    assert join.status_code == 200
    assert join.json()["status"] == "ready_for_adapter"
    assert "Configure a Teams" in join.json()["message"]
