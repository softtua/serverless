"""
gpu_arbiter.py
==============
Singleton GPU arbiter that enforces mutual exclusion between ComfyUI generation
workers and LLM (Ollama/qwen_agent) inference.

Usage
-----
    from gpu_arbiter import arbiter

    # In a ComfyUI worker:
    async with arbiter.comfyui_turn():
        # ... submit workflow, wait for completion ...

    # In the LLM async worker:
    async with arbiter.llm_turn():
        # ... run inference ...
        # Call _unload_ollama_model() here, before the context exits,
        # so ComfyUI cannot start before the model is evicted from VRAM.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Literal

logger = logging.getLogger(__name__)

GpuOwner = Literal["none", "comfyui", "llm"]


class GpuArbiter:
    """
    Enforces mutual exclusion between ComfyUI and LLM GPU usage.

    State
    -----
    _owner        : current GPU owner ("none" | "comfyui" | "llm")
    _llm_count    : number of active LLM jobs (supports future concurrency)
    _cond         : asyncio.Condition used for wait / notify_all
    """

    def __init__(self) -> None:
        self._owner: GpuOwner = "none"
        self._llm_count: int = 0
        # Condition is created lazily so it binds to the running event loop.
        self._cond: asyncio.Condition | None = None

    def _get_cond(self) -> asyncio.Condition:
        """Return (or lazily create) the asyncio Condition."""
        if self._cond is None:
            self._cond = asyncio.Condition()
        return self._cond

    @property
    def owner(self) -> GpuOwner:
        return self._owner

    @property
    def llm_active_count(self) -> int:
        return self._llm_count

    # ── ComfyUI side ──────────────────────────────────────────────────────────

    @asynccontextmanager
    async def comfyui_turn(self):
        """
        Async context manager for ComfyUI generation.

        Waits until no LLM jobs are active, then marks the GPU as owned by
        ComfyUI for the duration of the block.  Releases and notifies waiters
        on exit (even if an exception is raised).
        """
        cond = self._get_cond()
        async with cond:
            # Wait until LLM is not using the GPU
            while self._owner == "llm":
                logger.info("GpuArbiter: ComfyUI waiting — LLM is active (%d job(s))", self._llm_count)
                await cond.wait()
            self._owner = "comfyui"
            logger.info("GpuArbiter: ComfyUI acquired GPU")

        try:
            yield
        finally:
            async with cond:
                self._owner = "none"
                logger.info("GpuArbiter: ComfyUI released GPU")
                cond.notify_all()

    # ── LLM side ──────────────────────────────────────────────────────────────

    @asynccontextmanager
    async def llm_turn(self):
        """
        Async context manager for LLM inference.

        Waits until ComfyUI is not using the GPU, then marks the GPU as owned
        by LLM for the duration of the block.  Decrements the active-job counter
        and releases the GPU (notifying waiters) when the last LLM job exits.

        NOTE: Callers are responsible for calling _unload_ollama_model() inside
        this block (before the context exits) so that the model is evicted from
        VRAM before ComfyUI workers are unblocked.
        """
        cond = self._get_cond()
        async with cond:
            # Wait until ComfyUI is not using the GPU
            while self._owner == "comfyui":
                logger.info("GpuArbiter: LLM waiting — ComfyUI is active")
                await cond.wait()
            self._owner = "llm"
            self._llm_count += 1
            logger.info("GpuArbiter: LLM acquired GPU (active jobs: %d)", self._llm_count)

        try:
            yield
        finally:
            async with cond:
                self._llm_count -= 1
                if self._llm_count == 0:
                    self._owner = "none"
                    logger.info("GpuArbiter: LLM released GPU (no more active jobs)")
                    cond.notify_all()
                else:
                    logger.info("GpuArbiter: LLM job finished, %d job(s) still active", self._llm_count)


# Module-level singleton — import this everywhere
arbiter = GpuArbiter()

