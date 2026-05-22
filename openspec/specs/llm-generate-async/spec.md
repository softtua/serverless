## ADDED Requirements

### Requirement: Client can submit an LLM job asynchronously
The system SHALL accept `POST /llm/generate/async` with the same JSON body as `/llm/generate` (fields: `model`, `system`, `prompt`, `stream`, `options`, and optional `callback_url`). The system SHALL respond immediately with HTTP 202 and a JSON body containing `job_id` and `status: "queued"`. The connection SHALL be closed before LLM processing begins.

#### Scenario: Successful async submission
- **WHEN** a client POSTs to `/llm/generate/async` with a valid `prompt`
- **THEN** the system returns HTTP 202 with `{"job_id": "<uuid>", "status": "queued"}`

#### Scenario: Missing prompt field
- **WHEN** a client POSTs to `/llm/generate/async` without a `prompt` field
- **THEN** the system returns HTTP 400 with an error message

### Requirement: Client can poll for LLM job result
The system SHALL expose `GET /llm/result/{job_id}` that returns the current state of an LLM job. The response SHALL include `job_id`, `status` (one of: `queued`, `waiting_for_gpu`, `processing`, `completed`, `failed`), and `result` (populated when `completed`).

#### Scenario: Poll a queued job
- **WHEN** a client GETs `/llm/result/{job_id}` for a job that has not yet started
- **THEN** the system returns HTTP 200 with `{"status": "queued", "result": null}`

#### Scenario: Poll a completed job
- **WHEN** a client GETs `/llm/result/{job_id}` for a finished job
- **THEN** the system returns HTTP 200 with `{"status": "completed", "result": "<llm_output>"}`

#### Scenario: Poll unknown job ID
- **WHEN** a client GETs `/llm/result/{job_id}` for a job that does not exist
- **THEN** the system returns HTTP 404

### Requirement: LLM jobs wait for GPU when ComfyUI is active
The system SHALL queue incoming LLM async jobs and SHALL NOT begin LLM inference while ComfyUI has active generation jobs. The job status SHALL reflect `waiting_for_gpu` during this wait.

#### Scenario: LLM job submitted while ComfyUI is generating
- **WHEN** a ComfyUI generation is in progress and an async LLM job is submitted
- **THEN** the LLM job status SHALL be `waiting_for_gpu` until ComfyUI finishes
- **THEN** the LLM job SHALL start processing after ComfyUI releases the GPU

### Requirement: GPU memory freed before LLM inference
Before starting LLM inference, the system SHALL call ComfyUI `/free` with `{"free_memory": true, "unload_models": false}` and wait at least 3 seconds for memory to be released.

#### Scenario: Memory freed before LLM starts
- **WHEN** the GPU becomes available and an LLM job is next in queue
- **THEN** the system calls ComfyUI `/free` before initiating Ollama inference

### Requirement: Webhook callback on job completion
If the request body contains a `callback_url` field, the system SHALL POST the job result to that URL after the job reaches a terminal state (`completed` or `failed`).

#### Scenario: Callback on success
- **WHEN** an LLM job completes and `callback_url` was provided
- **THEN** the system POSTs `{"job_id": "<id>", "status": "completed", "result": "<text>"}` to the `callback_url`

#### Scenario: Callback failure is non-fatal
- **WHEN** the POST to `callback_url` fails (network error, non-2xx)
- **THEN** the system logs a warning and does NOT retry or fail the job

### Requirement: GPU arbiter enforces mutual exclusion
The system SHALL use a `GpuArbiter` singleton that ensures ComfyUI generation and LLM inference are never active simultaneously on the same GPU. The arbiter SHALL be a shared module imported by both the API layer and ComfyUI workers.

#### Scenario: Only one GPU owner at a time
- **WHEN** either ComfyUI or LLM holds the GPU
- **THEN** the other SHALL wait until the GPU is released before acquiring it

### Requirement: Ollama model evicted from VRAM when GPU passes to ComfyUI
When the LLM worker finishes all active jobs and signals readiness to release the GPU, the system SHALL call Ollama with `{"model": "<model>", "keep_alive": 0}` to evict the model from VRAM before ComfyUI workers are unblocked.

#### Scenario: Model evicted on GPU hand-off
- **WHEN** the last active LLM job completes and a ComfyUI job is waiting
- **THEN** the system calls Ollama to unload the model before unblocking ComfyUI

