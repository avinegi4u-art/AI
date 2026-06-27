# AI Co-Presenter

AI Co-Presenter is a consent-first prototype for a meeting assistant that can:

- ingest PowerPoint, Word, PDF, Excel, CSV, Markdown, and text files;
- generate a presentation script from uploaded materials;
- answer audience questions from the stored source documents;
- prepare speech with an authorized voice profile; and
- hand off to a future Teams/Bot/WebRTC adapter for automatic meeting participation.

The repository intentionally does **not** include a hidden impersonation flow. Voice use requires explicit consent, and meeting use requires an AI disclosure message.

## Current prototype scope

Implemented now:

- FastAPI backend
- document upload and text extraction
- local extractive Q&A with citations
- presentation script generation
- voice profile consent checks
- meeting join orchestration boundary

Requires provider configuration later:

- real Microsoft Teams joining
- real microphone/audio injection
- real voice clone rendering
- LLM-based answer generation

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
uvicorn copresenter.app:app --reload
```

Open the API docs at:

```text
http://127.0.0.1:8000/docs
```

## Example flow

1. Upload one or more files with `POST /documents`.
2. Create a consented voice profile with `POST /voice-profiles`.
3. Create a presentation session with `POST /sessions`.
4. Generate the presentation script with `GET /sessions/{session_id}/script`.
5. Ask questions with `POST /sessions/{session_id}/ask`.
6. Prepare meeting participation with `POST /sessions/{session_id}/meeting/join`.

The meeting join endpoint returns `ready_for_adapter` after safety checks pass. A production build would replace the placeholder meeting connector with a real Teams/Bot/WebRTC adapter.

## Safety and consent defaults

The app enforces:

- voice profile consent must be confirmed;
- AI disclosure must remain enabled for voice and meeting use;
- meeting sessions must have at least one source document; and
- unsupported or empty documents are rejected.

Default disclosure text:

> Disclosure: this meeting includes an AI co-presenter using an authorized synthetic voice. Answers are generated from uploaded presentation materials, and uncertain answers will be stated as uncertain.

## Tests

```bash
python3 -m pytest
```

## Cloud agent environment

This repo includes a committed Cursor Cloud environment so agents can run tests without manual dependency setup.

- `.cursor/environment.json` — cloud environment config
- `.cursor/Dockerfile` — Python 3.12+ base image
- `.cursor/setup.sh` — idempotent editable install of `pyproject.toml` dependencies
- `AGENTS.md` — cloud-specific agent instructions

After environment startup, agents should run:

```bash
python3 -m pytest
```

The cloud environment also auto-starts the FastAPI server on port `8000`. Open API docs at:

```text
http://127.0.0.1:8000/docs
```

To refresh dependencies locally after changing `pyproject.toml`:

```bash
./.cursor/setup.sh
```