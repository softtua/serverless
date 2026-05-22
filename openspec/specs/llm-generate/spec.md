# Technical Specification: `POST /llm/generate`

## Endpoint Contract

### Request

**Method**: `POST`  
**Path**: `/llm/generate`  
**Content-Type**: `application/json`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `model` | string | No | `"qwen3:14b"` | Ollama model tag |
| `system` | string | No | `""` | System prompt passed to the agent |
| `prompt` | string | **Yes** | — | User message / task description |
| `stream` | boolean | No | `false` | Must be `false`; streaming is not supported |
| `options` | object | No | `{}` | Generation options (see below) |
| `options.temperature` | float | No | `0.7` | Sampling temperature |
| `options.top_p` | float | No | _(omitted)_ | Nucleus sampling threshold; omitted from LLM config if not provided |

**Example request:**
```json
{
  "model": "qwen3:14b",
  "system": "You are a research assistant.",
  "prompt": "Summarise the content at https://example.com/article",
  "stream": false,
  "options": {
    "temperature": 0.5,
    "top_p": 0.9
  }
}
```

### Response

**HTTP 200 — Success**

| Field | Type | Description |
|-------|------|-------------|
| `model` | string | The model tag used |
| `response` | string | The final assistant text response |
| `done` | boolean | Always `true` |

```json
{
  "model": "qwen3:14b",
  "response": "The article discusses...",
  "done": true
}
```

### Error Responses

| HTTP Status | `error` field | Condition |
|-------------|---------------|-----------|
| `400 Bad Request` | `"Missing required field"` | `prompt` is absent or empty |
| `502 Bad Gateway` | `"Failed to reach ComfyUI"` | ComfyUI unreachable within 5 s |
| `502 Bad Gateway` | `"Failed to query ComfyUI queue"` | ComfyUI queue returns non-200 |
| `502 Bad Gateway` | `"Agent execution failed"` | Unhandled exception in agent thread |
| `503 Service Unavailable` | `"GPU is busy"` | ComfyUI has running or pending jobs |

All error responses share the shape:
```json
{
  "error": "<error>",
  "message": "<detail>"
}
```

---

## GPU Guard Logic

1. Send `GET {COMFYUI_API_QUEUE}` with a **5-second total timeout** via `aiohttp`.
2. If the HTTP call raises an exception → return HTTP 502 `"Failed to reach ComfyUI"`.
3. If the response status is not 200 → return HTTP 502 `"Failed to query ComfyUI queue"`.
4. Parse `queue_running` and `queue_pending` arrays from the JSON response body.
5. If either array is non-empty → return HTTP 503 with running/pending counts in the message.
6. If both arrays are empty → proceed to VRAM Free phase.

---

## VRAM Free Logic

1. Send `POST {COMFYUI_API_FREE}` with body:
   ```json
   {"free_memory": true, "unload_models": false}
   ```
   and a **10-second total timeout**.
2. Log the HTTP response status at `INFO` level.
3. Any exception is caught, logged at `WARNING` level, and **does not abort the request** — this phase is best-effort.
4. Regardless of success or failure, call `await asyncio.sleep(3)` to allow ComfyUI time to complete deallocation before Ollama attempts to allocate VRAM.

---

## Agent Configuration

| Parameter | Value | Source |
|-----------|-------|--------|
| Agent class | `qwen_agent.agents.Assistant` | qwen_agent library |
| LLM backend URL | `{OLLAMA_API_BASE}/v1` | `config.OLLAMA_API_BASE` env var |
| API key | `"ollama"` | Hardcoded (Ollama ignores the key) |
| Default model | `"qwen3:14b"` | Request body `model` field |
| Temperature | `0.7` | Request `options.temperature` (overridable) |
| top_p | _(omitted by default)_ | Request `options.top_p` — only set if present |
| Registered tools | `[FetchURLTool()]` | Instantiated per request |
| System message | From request `system` field | — |
| Execution mode | Synchronous, in `ThreadPoolExecutor(max_workers=1)` | Bridged via `run_in_executor` |

The agent runs a standard ReAct-style loop: it may emit one or more tool calls before
producing a final text response. The endpoint iterates all yielded message batches and
keeps the last non-empty assistant `content` string as the final response.

---

## Tool Interface: `FetchURLTool`

**Module**: `tools.fetch_url`  
**Class**: `FetchURLTool`  
**Inherits**: `qwen_agent.tools.BaseTool`

| Attribute | Value |
|-----------|-------|
| `name` | `"fetch_url"` |
| `description` | `"Fetches and extracts the main text content from a given URL. Use this when you need to read a webpage, article or any online resource."` |

**Parameter schema:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `url` | string | Yes | Full URL to fetch (e.g. `https://example.com/article`) |

**`call()` method behaviour:**

1. **Params normalisation**: if `params` is a `str`, attempt `json.loads()`; if that raises, treat the raw string as a bare URL and wrap as `{'url': params}`.
2. Extract `url`; if empty return `{"error": "URL is required"}`.
3. Send `GET {url}` with a Chrome-like `User-Agent` header and a **15-second timeout**.
4. If `trafilatura` is installed, call `trafilatura.extract(html, include_links=True)`. Otherwise fall back to regex-based tag stripping.
5. Truncate output to **12,000 characters**; set `"truncated": true` if truncation occurred.
6. On success return:
   ```json
   {"url": "...", "text": "...", "char_count": 1234, "truncated": false}
   ```
7. On `requests.RequestException` return `{"error": "Failed to fetch URL: <exc>"}`.
8. On any other exception return `{"error": "Extraction error: <exc>"}`.

---

## Configuration Constants

All defined in `config/config.py` and importable from the `config` package.

| Constant | Environment Variable | Default |
|----------|---------------------|---------|
| `COMFYUI_API_BASE` | `COMFYUI_API_BASE` | `http://127.0.0.1:8189` |
| `COMFYUI_API_QUEUE` | — | `{COMFYUI_API_BASE}/queue` |
| `COMFYUI_API_FREE` | — | `{COMFYUI_API_BASE}/free` |
| `OLLAMA_API_BASE` | `OLLAMA_API_BASE` | `http://127.0.0.1:11434` |
| `OLLAMA_API_GENERATE` | — | `{OLLAMA_API_BASE}/api/generate` _(unused by agent path)_ |

---

## GPU Mutual Exclusion (ComfyUI Worker Guard)

ComfyUI generation workers SHALL check for active LLM inference jobs via the `GpuArbiter` singleton before starting a generation. If LLM jobs are active, the worker SHALL wait until all LLM jobs complete. After LLM jobs finish, the `GpuArbiter` ensures the Ollama model has been evicted from VRAM (via `keep_alive: 0`) before ComfyUI workers are unblocked.

This is the reciprocal guard to the existing ComfyUI → LLM protection: both directions now enforce mutual GPU exclusion.

### Requirement: ComfyUI generation waits for active LLM jobs
The system SHALL check for active LLM inference jobs before starting a ComfyUI generation. If LLM jobs are active, the ComfyUI worker SHALL wait until all LLM jobs complete. After LLM jobs finish, the system SHALL wait for the Ollama model to be evicted from VRAM before starting ComfyUI generation.

#### Scenario: ComfyUI waits when LLM is running
- **WHEN** an LLM async job is in `processing` state and a ComfyUI generation is triggered
- **THEN** the ComfyUI worker SHALL block until the LLM job reaches a terminal state

#### Scenario: ComfyUI proceeds when no LLM jobs active
- **WHEN** no LLM jobs are in `processing` or `waiting_for_gpu` state
- **THEN** the ComfyUI worker SHALL acquire the GPU and begin generation without delay
