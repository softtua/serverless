# Proposal: Agentic LLM Endpoint

## Status
Implemented — this is a retrospective proposal document.

## Problem Statement
The ComfyUI API Wrapper service runs on a single GPU host. That GPU is shared between
ComfyUI (image generation) and Ollama (LLM inference). Previously, there was no HTTP
endpoint to invoke the LLM from external callers in a GPU-safe way. Callers had to talk
to Ollama directly, which risked VRAM conflicts when ComfyUI was active.

Additionally, certain use-cases require the LLM to autonomously fetch and read external
web content before producing an answer (e.g. summarisation, research assistance, RAG-lite
workflows). A raw Ollama endpoint provides no tool-calling orchestration.

## Motivation
- **GPU contention**: ComfyUI and Ollama compete for the same VRAM. Loading an LLM model
  while a ComfyUI workflow is running causes OOM errors or model eviction.
- **Agentic capability**: Simple prompt–response is insufficient for tasks that require
  live web lookups. An agent loop with a `fetch_url` tool enables multi-step reasoning
  over online content.
- **Unified API surface**: Clients should go through a single service (the wrapper) rather
  than talking to Ollama directly, so GPU scheduling and authentication can be centralised.

## Proposed Solution
Add a `POST /llm/generate` endpoint to the FastAPI wrapper that:
1. Checks the ComfyUI queue before doing anything — refuses with HTTP 503 if the GPU is
   occupied.
2. Instructs ComfyUI to release VRAM before loading the LLM.
3. Runs a `qwen_agent` `Assistant` (backed by Ollama) with a `fetch_url` tool registered.
4. Returns the final assistant response in a shape compatible with the Ollama
   `/api/generate` response schema.

The request schema mirrors Ollama's `/api/generate` to minimise client-side changes.

## Non-Goals
- **Streaming**: `stream: true` is explicitly not supported. The endpoint is designed for
  single-shot completions only.
- **Multi-turn conversation**: The endpoint is stateless. Each request is an independent
  agent run; conversation history is not persisted.
- **Horizontal scaling**: The design assumes a single GPU host. No distributed queue or
  load-balancing is addressed.
- **Model management**: Pulling, updating, or listing Ollama models is out of scope.
- **Authentication / rate limiting**: Not addressed by this feature; assumed to be handled
  at a gateway layer.

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| GPU still occupied after queue check (race condition) | Low | The 3-second VRAM-free sleep provides a small buffer; Ollama itself will fail gracefully if VRAM is insufficient |
| qwen_agent blocking the event loop | High (without mitigation) | Runs inside `ThreadPoolExecutor` via `run_in_executor` |
| `fetch_url` returning malicious or extremely large content | Medium | Content is truncated at 12,000 characters; errors are caught and returned as structured dicts |
| Ollama server unavailable | Medium | Propagated as HTTP 502 with agent error message |
| trafilatura not installed | Low | Fallback regex tag-stripping is implemented in `FetchURLTool` |

