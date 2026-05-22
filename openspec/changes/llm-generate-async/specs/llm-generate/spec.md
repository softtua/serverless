## MODIFIED Requirements

### Requirement: ComfyUI generation waits for active LLM jobs
The system SHALL check for active LLM inference jobs before starting a ComfyUI generation. If LLM jobs are active, the ComfyUI worker SHALL wait until all LLM jobs complete. After LLM jobs finish, the system SHALL wait for the Ollama model to be evicted from VRAM before starting ComfyUI generation.

#### Scenario: ComfyUI waits when LLM is running
- **WHEN** an LLM async job is in `processing` state and a ComfyUI generation is triggered
- **THEN** the ComfyUI worker SHALL block until the LLM job reaches a terminal state

#### Scenario: ComfyUI proceeds when no LLM jobs active
- **WHEN** no LLM jobs are in `processing` or `waiting_for_gpu` state
- **THEN** the ComfyUI worker SHALL acquire the GPU and begin generation without delay

