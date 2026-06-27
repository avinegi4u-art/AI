from __future__ import annotations

from copresenter.models import DocumentRecord, PresentationSession, VoiceProfile


class RecordNotFound(KeyError):
    """Raised when a requested in-memory record does not exist."""


class InMemoryStore:
    def __init__(self) -> None:
        self._documents: dict[str, DocumentRecord] = {}
        self._voice_profiles: dict[str, VoiceProfile] = {}
        self._sessions: dict[str, PresentationSession] = {}

    def add_document(self, document: DocumentRecord) -> DocumentRecord:
        self._documents[document.id] = document
        return document

    def get_document(self, document_id: str) -> DocumentRecord:
        try:
            return self._documents[document_id]
        except KeyError as exc:
            raise RecordNotFound(f"Document '{document_id}' was not found.") from exc

    def get_documents(self, document_ids: list[str]) -> list[DocumentRecord]:
        return [self.get_document(document_id) for document_id in document_ids]

    def add_voice_profile(self, profile: VoiceProfile) -> VoiceProfile:
        self._voice_profiles[profile.id] = profile
        return profile

    def get_voice_profile(self, profile_id: str) -> VoiceProfile:
        try:
            return self._voice_profiles[profile_id]
        except KeyError as exc:
            raise RecordNotFound(f"Voice profile '{profile_id}' was not found.") from exc

    def add_session(self, session: PresentationSession) -> PresentationSession:
        self._sessions[session.id] = session
        return session

    def get_session(self, session_id: str) -> PresentationSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise RecordNotFound(f"Session '{session_id}' was not found.") from exc
