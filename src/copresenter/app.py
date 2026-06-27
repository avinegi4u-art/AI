from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, status

from copresenter.documents import DocumentExtractionError, extract_text
from copresenter.meetings import MeetingOrchestrator
from copresenter.models import (
    AnswerResponse,
    AskRequest,
    DocumentRecord,
    DocumentUploadResponse,
    MeetingJoinRequest,
    MeetingJoinResponse,
    PresentationScriptResponse,
    PresentationSession,
    PresentationSessionCreateRequest,
    PresentationSessionResponse,
    VoiceProfile,
    VoiceProfileCreateRequest,
    VoiceProfileResponse,
)
from copresenter.policy import ConsentPolicy, PolicyViolation
from copresenter.presentation import build_script, build_speech_preview
from copresenter.retrieval import answer_question
from copresenter.store import InMemoryStore, RecordNotFound
from copresenter.voice import VoiceService


app = FastAPI(
    title="AI Co-Presenter",
    version="0.1.0",
    description=(
        "Consent-first prototype for document-grounded presentations, Q&A, "
        "voice preparation, and meeting adapter orchestration."
    ),
)

store = InMemoryStore()
consent_policy = ConsentPolicy()
voice_service = VoiceService(consent_policy)
meeting_orchestrator = MeetingOrchestrator(consent_policy)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/documents", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...)) -> DocumentUploadResponse:
    content = await file.read()
    try:
        text = extract_text(file.filename or "uploaded-file", content)
    except DocumentExtractionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document did not contain extractable text.",
        )

    source_filename = file.filename or "uploaded-file"
    document = store.add_document(
        DocumentRecord(
            title=Path(source_filename).stem or source_filename,
            source_filename=source_filename,
            mime_type=file.content_type or "application/octet-stream",
            text=text,
        )
    )
    return DocumentUploadResponse(
        id=document.id,
        title=document.title,
        source_filename=document.source_filename,
        character_count=len(document.text),
    )


@app.post("/voice-profiles", response_model=VoiceProfileResponse)
def create_voice_profile(request: VoiceProfileCreateRequest) -> VoiceProfileResponse:
    profile = VoiceProfile(
        owner_name=request.owner_name,
        consent_confirmed=request.consent_confirmed,
        disclosure_enabled=request.disclosure_enabled,
        voice_mode=request.voice_mode,
    )
    try:
        consent_policy.validate_voice_profile(profile)
    except PolicyViolation as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    store.add_voice_profile(profile)
    return _voice_profile_response(profile)


@app.post("/sessions", response_model=PresentationSessionResponse)
def create_session(request: PresentationSessionCreateRequest) -> PresentationSessionResponse:
    try:
        store.get_documents(request.document_ids)
        store.get_voice_profile(request.voice_profile_id)
    except RecordNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    session = store.add_session(
        PresentationSession(
            name=request.name,
            document_ids=request.document_ids,
            voice_profile_id=request.voice_profile_id,
        )
    )
    return _session_response(session)


@app.get("/sessions/{session_id}/script", response_model=PresentationScriptResponse)
def get_script(session_id: str) -> PresentationScriptResponse:
    session, profile, documents = _load_session_context(session_id)
    opening, sections, closing = build_script(documents)
    speech_preview = build_speech_preview(opening, sections, closing)
    voice_service.prepare_speech(profile, speech_preview)
    return PresentationScriptResponse(
        session_id=session.id,
        opening_disclosure=opening,
        sections=sections,
        closing=closing,
        speech_preview=speech_preview,
    )


@app.post("/sessions/{session_id}/ask", response_model=AnswerResponse)
def ask_question(session_id: str, request: AskRequest) -> AnswerResponse:
    question = request.question
    _, _, documents = _load_session_context(session_id)
    answer, citations = answer_question(question, documents)
    return AnswerResponse(
        answer=answer,
        citations=[
            {
                "document_id": citation.document_id,
                "document_title": citation.document_title,
                "chunk_index": citation.chunk_index,
                "score": citation.score,
                "text": citation.text,
            }
            for citation in citations
        ],
    )


@app.post("/sessions/{session_id}/meeting/join", response_model=MeetingJoinResponse)
def prepare_meeting_join(
    session_id: str,
    request: MeetingJoinRequest,
) -> MeetingJoinResponse:
    session, profile, _ = _load_session_context(session_id)
    try:
        plan = meeting_orchestrator.prepare_join(
            meeting_url=str(request.meeting_url),
            session=session,
            profile=profile,
            auto_speak=request.auto_speak,
        )
    except PolicyViolation as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return MeetingJoinResponse(
        status=plan.status,
        message=plan.message,
        disclosure_text=plan.disclosure_text,
    )


def _load_session_context(
    session_id: str,
) -> tuple[PresentationSession, VoiceProfile, list[DocumentRecord]]:
    try:
        session = store.get_session(session_id)
        profile = store.get_voice_profile(session.voice_profile_id)
        documents = store.get_documents(session.document_ids)
    except RecordNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return session, profile, documents


def _voice_profile_response(profile: VoiceProfile) -> VoiceProfileResponse:
    return VoiceProfileResponse(
        id=profile.id,
        owner_name=profile.owner_name,
        consent_confirmed=profile.consent_confirmed,
        disclosure_enabled=profile.disclosure_enabled,
        voice_mode=profile.voice_mode,
    )


def _session_response(session: PresentationSession) -> PresentationSessionResponse:
    return PresentationSessionResponse(
        id=session.id,
        name=session.name,
        document_ids=session.document_ids,
        voice_profile_id=session.voice_profile_id,
    )


