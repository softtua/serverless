## Context

The `comfyui-api-wrapper` runs on a single-GPU machine. ComfyUI and Ollama both load large models into VRAM and cannot safely share the GPU simultaneously. The existing `/llm/generate` endpoint is synchronous (blocks the HTTP connection for up to 60 s) and only guards one direction: it refuses LLM requests when ComfyUI is busy, but ComfyUI workers have no reciprocal awareness of LLM activity.

The goal is to introduce async LLM job handling and a shared GPU arbiter that enforces mutual exclusion in both directions.

## Goals / Non-Goals

**Goals:**
- `POST /llm/generate/async` returns immediately with a `job_id` (HTTP 202)
- `GET /llm/result/{job_id}` allows polling for status and result
- Optional `callback_url` — wrapper POSTs result when job completes
- GPU arbiter enforces: ComfyUI jobs wait for LLM jobs to finish; LLM jobs wait for ComfyUI jobs to finish
- When switching GPU from LLM → ComfyUI, unload the Ollama model from VRAM (`keep_alive: 0`)
- When switching GPU from ComfyUI → LLM, run `/free` on ComfyUI before starting

**Non-Goals:**
- Real-time streaming of LLM tokens over the async channel
- Multi-GPU awareness
- Persistent job storage across restarts (in-memory only)
- Changes to the existing synchronous `/llm/generate` endpoint

## Decisions

### 1. GPU Arbiter as a shared singleton module

**Decision**: Introduce `gpu_arbiter.py` with a `GpuArbiter` class instantiated once at module level and imported by both `main.py` and the ComfyUI workers.

**Alternatives considered**:
- Global variables in `main.py` passed via `worker_config` — works but creates a circular coupling between `main.py` and workers
- Redis-based distributed lock — overkill for a single-process deployment

**Rationale**: A module-level singleton is the simplest pattern for a single-process FastAPI app with asyncio workers. No circular imports — workers import from `gpu_arbiter`, not from `main`.

### 2. LLM job queue as an `asyncio.Queue` drained by a single background worker

**Decision**: A single LLM background worker task processes jobs from `llm_job_queue` sequentially. The worker itself waits for `GpuArbiter` to grant the GPU before proceeding.

**Alternatives considered**:
- `ThreadPoolExecutor` with multiple threads — Ollama handles its own parallelism; multiple concurrent requests would compete for VRAM
- Separate FastAPI background tasks per request — no serialization, hard to reason about GPU state

**Rationale**: Sequential processing is safe and predictable. Ollama/qwen can internally parallelize token generation; we just need to ensure we don't overlap with ComfyUI.

### 3. GpuArbiter state machine with asyncio primitives

**Decision**: `GpuArbiter` tracks `gpu_owner: Literal["none", "comfyui", "llm"]` and `llm_active_count: int`. Provides two async context managers: `async with arbiter.comfyui_turn():` and `async with arbiter.llm_turn():`. Internally uses `asyncio.Lock` + `asyncio.Condition` for wait/notify.

**Rationale**: Context managers ensure release even on exceptions. `asyncio.Condition` avoids busy-polling (no `sleep` loops).

### 4. Ollama model unload when releasing GPU to ComfyUI

**Decision**: After all LLM jobs finish, the arbiter (or worker) calls Ollama with `{"model": "<model>", "keep_alive": 0}` to evict the model from VRAM, then waits 2 s before signalling ComfyUI waiters.

**Rationale**: Without explicit unload, Ollama keeps the model in VRAM by default for 5 minutes, starving ComfyUI of memory.

### 5. In-memory job store with no TTL cleanup

**Decision**: `llm_job_store: dict[str, LlmJob]` lives in `main.py` memory. No automatic expiry in MVP.

**Alternatives considered**: `aiocache` (already present) — adds unnecessary complexity for a simple dict. Redis persistence — not needed for MVP.

**Rationale**: Jobs complete in under 2 minutes; the server restarts periodically. Cleanup can be added later.

## Risks / Trade-offs

- **Risk**: ComfyUI worker holds GPU lock indefinitely if generation hangs → **Mitigation**: Workers already have timeouts; arbiter lock is released in a `finally` block
- **Risk**: LLM job store grows unbounded in long-running deployments → **Mitigation**: Add TTL-based cleanup in a follow-up; acceptable for MVP
- **Risk**: Ollama `keep_alive: 0` call fails silently, leaving model in VRAM → **Mitigation**: Log warning; ComfyUI will still get the GPU (it may just OOM on load — same as today)
- **Trade-off**: Sequential LLM job processing limits throughput → Acceptable: single GPU can only run one model at a time anyway

## Migration Plan

1. Deploy new `gpu_arbiter.py`
2. Update `main.py`: import arbiter, add new endpoints, start LLM worker on startup
3. Update `workers/generation_worker.py`: wrap generation in `arbiter.comfyui_turn()`
4. No schema/database migrations required
5. **Rollback**: revert the three files above; no persistent state to unwind

## Open Questions

- Should completed LLM jobs be evicted from `llm_job_store` after a TTL (e.g., 1 hour)? → Defer to follow-up
- Should `/llm/generate` (sync) also go through the arbiter? → Yes, but as a follow-up to keep this change focused

