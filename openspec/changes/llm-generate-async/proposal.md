## Why

The current `/llm/generate` endpoint is synchronous — the HTTP connection stays open until the LLM finishes, which can take 30–60+ seconds. Long-lived HTTP connections are fragile (timeouts, proxy drops, Drupal request limits). An async pattern with job IDs and callbacks is more resilient and allows multiple LLM requests to be queued and processed without blocking the caller.

Additionally, GPU memory must be managed cooperatively between ComfyUI and Ollama. Currently only the LLM endpoint guards against ComfyUI being busy, but there is no reciprocal guard: ComfyUI workers can start while an LLM job is still holding the GPU. A shared GPU arbiter is needed to enforce mutual exclusion in both directions.

## What Changes

- Add `POST /llm/generate/async` — accepts the same body as `/llm/generate`, immediately returns a `job_id` (HTTP 202)
- Add `GET /llm/result/{job_id}` — returns current status and result for an LLM job
- Add `gpu_arbiter` module with a `GpuArbiter` singleton that enforces mutual exclusion between ComfyUI workers and LLM workers
- LLM async worker: background `asyncio` task that drains `llm_job_queue`, waits for GPU to be free of ComfyUI activity, then runs the LLM in a thread
- ComfyUI workers: acquire GPU lock before generation, release after; if LLM jobs are active they wait first and unload the Ollama model from VRAM before proceeding
- Optional `callback_url` field in request body: wrapper POSTs result to it when job completes

## Capabilities

### New Capabilities
- `llm-generate-async`: Async LLM generation endpoint with job queue, polling result endpoint, GPU mutual-exclusion arbiter, and optional webhook callback

### Modified Capabilities
- `llm-generate`: Guard extended — ComfyUI workers now also check for active LLM jobs before starting generation (reciprocal GPU protection)

## Impact

- **New endpoints**: `POST /llm/generate/async`, `GET /llm/result/{job_id}`
- **New module**: `comfyui-api-wrapper/gpu_arbiter.py`
- **Modified**: `workers/generation_worker.py` — acquire/release GPU lock
- **Modified**: `main.py` — register new endpoints, start LLM worker task, wire up `GpuArbiter`
- **Dependencies**: no new packages required (`asyncio`, `aiohttp`, `concurrent.futures` already present)
- **Backward compatible**: existing `/llm/generate` (sync) endpoint is unchanged

