# Agent Instructions

## Cursor Cloud specific instructions

This repository includes a committed cloud agent environment in `.cursor/environment.json`.

- Cloud agents install dependencies automatically via `.cursor/setup.sh` during startup.
- The FastAPI server starts automatically on port `8000` via the cloud environment `start` command.
- Python 3.12+ is required. The Dockerfile in `.cursor/` ensures a compatible runtime.
- Do not reinstall dependencies manually on every run unless `pyproject.toml` changed.
- Run tests from the repository root with `python3 -m pytest`.
- API docs are available at `http://127.0.0.1:8000/docs` once the server is running.
- Start the API manually with `./.cursor/run-api.sh` if it is not already running.
- If port 8000 is busy, the server may already be up. Check `curl http://127.0.0.1:8000/health` before starting another instance.
- Prefer module invocation (`python3 -m pytest`, `python3 -m uvicorn`) because user-local scripts may not be on `PATH`.

If dependency installation fails after a `pyproject.toml` change, rerun:

```bash
./.cursor/setup.sh
```
