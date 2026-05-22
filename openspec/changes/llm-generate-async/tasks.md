## 1. GPU Arbiter Module

- [x] 1.1 Create `comfyui-api-wrapper/gpu_arbiter.py` with `GpuArbiter` class
- [x] 1.2 Implement `gpu_owner` state (`none | comfyui | llm`) and `llm_active_count` counter
- [x] 1.3 Implement `async with arbiter.comfyui_turn()` context manager — waits for LLM jobs to finish, acquires GPU, releases on exit
- [x] 1.4 Implement `async with arbiter.llm_turn()` context manager — waits for ComfyUI to finish, acquires GPU, increments/decrements `llm_active_count`, releases on exit
- [x] 1.5 Use `asyncio.Condition` internally to avoid busy-polling
- [x] 1.6 Export a module-level singleton `arbiter = GpuArbiter()`

## 2. LLM Job Store and Queue

- [x] 2.1 Define `LlmJob` dataclass/TypedDict in `main.py` with fields: `job_id`, `status`, `result`, `error`, `callback_url`, `model`
- [x] 2.2 Add `llm_job_store: dict[str, LlmJob]` and `llm_job_queue: asyncio.Queue` in `main.py`
- [x] 2.3 Add `POST /llm/generate/async` endpoint — validates `prompt`, creates job, enqueues, returns HTTP 202 with `job_id`
- [x] 2.4 Add `GET /llm/result/{job_id}` endpoint — looks up `llm_job_store`, returns 404 if missing

## 3. LLM Async Worker

- [x] 3.1 Create `_llm_async_worker()` coroutine in `main.py` that loops on `llm_job_queue`
- [x] 3.2 Worker sets job status to `waiting_for_gpu`, then enters `arbiter.llm_turn()`
- [x] 3.3 Inside `llm_turn`, call ComfyUI `/free` and `asyncio.sleep(3)` before running inference
- [x] 3.4 Run `_run_llm_agent(...)` in `ThreadPoolExecutor`, set status to `processing` while running
- [x] 3.5 On success, set `status = completed`, `result = final_text`
- [x] 3.6 On exception, set `status = failed`, `error = str(e)`
- [x] 3.7 After job finishes, call `_unload_ollama_model(model)` then release `llm_turn` (or handle in context manager exit)
- [x] 3.8 Register `_llm_async_worker` as an `asyncio.create_task` in `startup_event`

## 4. Webhook Callback

- [x] 4.1 Implement `_send_callback(url, job_id, status, result)` async helper using `aiohttp`
- [x] 4.2 Call `asyncio.create_task(_send_callback(...))` after job reaches terminal state if `callback_url` is set
- [x] 4.3 Log warning on callback failure, do not retry

## 5. Ollama Model Eviction Helper

- [x] 5.1 Implement `_unload_ollama_model(model)` async helper in `main.py` (calls `POST /api/generate` with `keep_alive: 0`)
- [x] 5.2 Call from `GpuArbiter` (or worker) when releasing GPU from LLM side
- [x] 5.3 Log success/failure, treat failure as non-fatal

## 6. ComfyUI Worker Integration

- [x] 6.1 Import `arbiter` from `gpu_arbiter` in `workers/generation_worker.py`
- [x] 6.2 Wrap the ComfyUI generation call (API submit + polling) with `async with arbiter.comfyui_turn()`
- [x] 6.3 Verify generation worker still handles exceptions correctly inside the context manager

## 7. Validation and Testing

- [ ] 7.1 Manual test: submit async LLM job, poll result until `completed`
- [ ] 7.2 Manual test: submit ComfyUI job while LLM is processing — verify ComfyUI waits
- [ ] 7.3 Manual test: submit LLM async job while ComfyUI is generating — verify `waiting_for_gpu` status
- [ ] 7.4 Manual test: provide `callback_url` pointing to a request bin, verify POST received on completion
- [ ] 7.5 Manual test: submit job with missing `prompt` field, verify HTTP 400
- [ ] 7.6 Manual test: poll unknown `job_id`, verify HTTP 404

