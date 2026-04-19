# Design: Agentic LLM Endpoint

## Architecture Overview

The endpoint sits inside the existing `comfyui-api-wrapper` FastAPI service. It does not
introduce a new process; it reuses the existing aiohttp session pattern for ComfyUI
communication and adds a thread-pool branch for synchronous qwen_agent execution.

```
┌──────────────────────────────────────────────────────────────┐
│                    comfyui-api-wrapper                       │
│                                                              │
│  POST /llm/generate                                          │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  1. GPU Guard                                          │  │
│  │     aiohttp GET → ComfyUI /queue                       │  │
│  │     if busy → HTTP 503                                 │  │
│  ├────────────────────────────────────────────────────────┤  │
│  │  2. VRAM Free                                          │  │
│  │     aiohttp POST → ComfyUI /free                       │  │
│  │     asyncio.sleep(3)                                   │  │
│  ├────────────────────────────────────────────────────────┤  │
│  │  3. Agent Execution (ThreadPoolExecutor)               │  │
│  │     qwen_agent Assistant                               │  │
│  │       ├── LLM backend: Ollama /v1 (OpenAI-compatible)  │  │
│  │       └── Tool: FetchURLTool                           │  │
│  │             requests.get → trafilatura.extract         │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
           │                              │
           ▼                              ▼
    ComfyUI :8189                   Ollama :11434
```

## Component Responsibilities

### `main.py` — `llm_generate()` (async endpoint)
Orchestrates the three phases sequentially. All I/O in phases 1 and 2 is async (aiohttp).
Phase 3 bridges into sync territory via `run_in_executor`.

### `main.py` — `_run_llm_agent()` (sync helper)
Constructs the `qwen_agent` `Assistant` with Ollama as the LLM backend, iterates the
agent run loop, and extracts the final assistant message. Imported lazily (inside the
function) to avoid issues at module load time.

### `tools/fetch_url.py` — `FetchURLTool`
A `qwen_agent.tools.BaseTool` subclass. Fetches a URL with `requests`, extracts main text
with `trafilatura`, and truncates to 12,000 characters to avoid context overflow.

### `config/config.py`
Provides `OLLAMA_API_BASE`, `COMFYUI_API_QUEUE`, and `COMFYUI_API_FREE` as resolved URL
strings derived from environment variables.

## Data Flow

```
Client
  │  POST /llm/generate  {model, system, prompt, options}
  ▼
llm_generate()
  │
  ├─ aiohttp GET COMFYUI_API_QUEUE
  │    ├─ [busy] ──────────────────────────────► HTTP 503
  │    └─ [free] ─────────────────────────────┐
  │                                           │
  ├─ aiohttp POST COMFYUI_API_FREE            │
  │    asyncio.sleep(3)                       │
  │                                           ▼
  └─ run_in_executor(_run_llm_agent)
       │
       └─ qwen_agent Assistant.run(messages)
            │
            ├─ [tool call: fetch_url]
            │     FetchURLTool.call({url})
            │       requests.get(url)
            │       trafilatura.extract()
            │       → {url, text, char_count, truncated}
            │
            └─ [final text]
                    │
  HTTP 200  {model, response, done: true} ◄──┘
```

## Key Design Decisions

### 1. Thread-pool isolation for qwen_agent
`qwen_agent` is entirely synchronous (blocking HTTP calls to Ollama). Running it directly
in an async endpoint would block the event loop. The solution is
`asyncio.get_event_loop().run_in_executor()` with a fresh single-threaded
`ThreadPoolExecutor` per request. A single thread is sufficient because the endpoint is
not expected to handle concurrent LLM requests (GPU is exclusive).

### 2. GPU guard before model load
Ollama requires contiguous VRAM to load a model. Rather than letting Ollama fail silently,
the endpoint proactively checks ComfyUI's queue. This provides a clean HTTP 503 with an
actionable error message rather than a cryptic CUDA OOM.

### 3. `free_memory: true, unload_models: false`
`unload_models: false` preserves ComfyUI's loaded model state so that subsequent image
generation requests are faster. Only intermediate activation tensors and cached data are
released — sufficient to free VRAM for a 14B LLM.

### 4. Ollama via OpenAI-compatible `/v1` endpoint
`qwen_agent` natively supports any OpenAI-compatible endpoint. Pointing it at
`OLLAMA_API_BASE/v1` with `api_key: 'ollama'` (a dummy key Ollama accepts) requires zero
custom transport code.

### 5. Params normalisation in FetchURLTool
`qwen_agent` occasionally serialises tool arguments as a JSON string rather than a dict,
depending on the model's output format. The `call()` method handles both: if `params` is
a `str`, it is JSON-parsed first; if it is a bare URL string (invalid JSON), it is wrapped
as `{'url': params}`.

## Error Handling Strategy

| Phase | Failure Mode | Behaviour |
|-------|-------------|-----------|
| Queue check | ComfyUI unreachable | HTTP 502 `"Failed to reach ComfyUI"` |
| Queue check | Jobs running/pending | HTTP 503 `"GPU is busy"` with job counts |
| Queue check | Non-200 response | HTTP 502 `"Failed to query ComfyUI queue"` |
| VRAM free | ComfyUI /free fails | Warning logged, execution continues (non-fatal) |
| Agent execution | `prompt` missing | HTTP 400 `"'prompt' is required"` |
| Agent execution | Any exception | HTTP 502 `"Agent execution failed"` with message |
| fetch_url tool | Network error | Returns `{error: "Failed to fetch URL: ..."}` — agent sees this and continues |
| fetch_url tool | trafilatura missing | Falls back to regex tag stripping |

