# Tasks: Agentic LLM Endpoint

All tasks listed below have been completed.
This document serves as a retrospective implementation record.

---

## Phase 1 — Configuration

- [x] Add `OLLAMA_API_BASE` environment variable support to `config/config.py`
- [x] Derive `OLLAMA_API_GENERATE` URL from `OLLAMA_API_BASE` (`/api/generate`)
- [x] Add `COMFYUI_API_FREE` URL constant (`{COMFYUI_API_BASE}/free`)
- [x] Export new constants from `config/__init__.py`
- [x] Import new constants in `main.py`

---

## Phase 2 — FetchURLTool

- [x] Create `tools/fetch_url.py`
- [x] Implement `FetchURLTool` as a `qwen_agent.tools.BaseTool` subclass
- [x] Define `name`, `description`, and `parameters` schema
- [x] Implement `call()` with `dict` / `str` params normalisation
- [x] Add `requests.get()` with Chrome-like User-Agent header and 15-second timeout
- [x] Integrate `trafilatura.extract()` for main content extraction
- [x] Add regex fallback for when trafilatura is not installed
- [x] Truncate output to 12,000 characters and set `truncated` flag
- [x] Return structured `{url, text, char_count, truncated}` on success
- [x] Return structured `{error}` on network or extraction failure

---

## Phase 3 — `_run_llm_agent` Helper

- [x] Define `_run_llm_agent(model, system_message, user_prompt, temperature, top_p)` in `main.py`
- [x] Build `llm_cfg` dict pointing at `OLLAMA_API_BASE/v1`
- [x] Conditionally include `top_p` in `generate_cfg` only when provided by caller
- [x] Instantiate `qwen_agent.agents.Assistant` with `llm_cfg`, `system_message`, and `[FetchURLTool()]`
- [x] Iterate `agent.run(messages)` generator and extract final assistant message
- [x] Handle multimodal content lists by joining text parts
- [x] Return final response string

---

## Phase 4 — `POST /llm/generate` Endpoint

- [x] Define `@app.post('/llm/generate')` in `main.py`
- [x] Parse raw JSON body via `request.json()` (untyped, mirrors Ollama request schema)
- [x] Implement GPU guard: `aiohttp GET COMFYUI_API_QUEUE` with 5-second timeout
- [x] Return HTTP 502 if ComfyUI is unreachable or returns non-200
- [x] Return HTTP 503 with running/pending counts if queue is occupied
- [x] Implement VRAM free: `aiohttp POST COMFYUI_API_FREE` with `{"free_memory": true, "unload_models": false}`
- [x] Log VRAM free response status at INFO level
- [x] Catch VRAM free exceptions as non-fatal; log at WARNING and continue
- [x] `await asyncio.sleep(3)` after VRAM free call
- [x] Extract `model`, `system`, `prompt`, `options` from body with defaults
- [x] Return HTTP 400 if `prompt` is empty or missing
- [x] Run `_run_llm_agent` via `run_in_executor` with `ThreadPoolExecutor(max_workers=1)`
- [x] Return `{model, response, done: true}` on success
- [x] Catch agent exceptions and return HTTP 502 with error message

---

## Phase 5 — Dependencies

- [x] Add `qwen-agent` to `requirements.txt`
- [x] Add `trafilatura` to `requirements.txt`
- [x] Verify `requests` is present in `requirements.txt` (used by FetchURLTool)

---

## Bug Fixes

- [x] Fix `AttributeError: 'str' object has no attribute 'get'` in `FetchURLTool.call()` —
  `qwen_agent` can pass tool arguments as a JSON string instead of a dict; added
  normalisation logic to handle both shapes
- [x] Clear stale `__pycache__` on server to ensure fix takes effect

