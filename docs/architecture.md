# AI Co-Presenter Architecture

## Goal

Build a meeting co-presenter that can read prepared materials aloud and answer questions from those materials using an authorized voice profile.

## Core flow

1. User uploads presentation source files.
2. The backend extracts plain text and stores document records.
3. A presentation session binds documents to a consented voice profile.
4. The script generator creates a disclosure-first presentation script.
5. The Q&A engine answers only from uploaded document chunks and returns citations.
6. The meeting orchestrator validates consent/disclosure and hands control to a provider adapter.

## Provider adapter boundaries

The prototype keeps external integrations behind explicit boundaries:

- `VoiceService` prepares text for an authorized voice provider.
- `MeetingOrchestrator` validates safety requirements before a meeting provider runs.
- `MeetingConnector` is the protocol a Teams, Bot Framework, or WebRTC adapter must implement.

This keeps the document intelligence and safety policy independent from whichever meeting and voice vendors are selected later.

## Production integration options

Common next adapters:

- Microsoft Teams bot or Graph communications integration for joining meetings.
- Browser/WebRTC automation for controlled internal demos.
- Voice provider with speaker-consent verification for synthetic voice rendering.
- LLM provider for better answer synthesis while retaining document citations.

## Non-goals

- Secretly impersonating a person in a meeting.
- Generating answers from outside the uploaded source corpus by default.
- Bypassing platform consent, recording, bot, or disclosure requirements.
