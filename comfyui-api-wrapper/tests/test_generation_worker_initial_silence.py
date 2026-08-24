import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from workers.generation_worker import GenerationWorker


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FakeSession:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def ws_connect(self, *args, **kwargs):
        return _AsyncContext(self.websocket)


class _UnusedAwaitable:
    def __await__(self):
        if False:
            yield None
        return None


class _FakeWebSocket:
    def receive(self):
        # asyncio.wait_for is mocked by the test.  A custom awaitable avoids
        # creating an un-awaited coroutine and keeps the test warning-free.
        return _UnusedAwaitable()


class InitialSilenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_prompt_survives_three_initial_websocket_timeouts(self):
        """A live prompt must not fail just because its first WS event was missed."""
        worker = GenerationWorker.__new__(GenerationWorker)
        worker.max_wait_time = 7200
        worker.ws_url = "ws://comfyui.test/ws"
        worker.client_id = "test-client"
        worker.check_if_cached = AsyncMock(return_value=False)
        worker.is_job_running = AsyncMock(return_value=True)
        worker._update_progress = AsyncMock()

        completed_message = SimpleNamespace(
            type=1,  # aiohttp.WSMsgType.TEXT
            data='{"type":"executing","data":{"prompt_id":"prompt-1","node":null}}',
        )
        receive_results = [
            asyncio.TimeoutError(),
            asyncio.TimeoutError(),
            asyncio.TimeoutError(),
            completed_message,
        ]

        with patch(
            "workers.generation_worker.aiohttp.ClientSession",
            return_value=_FakeSession(_FakeWebSocket()),
        ), patch(
            "workers.generation_worker.asyncio.wait_for",
            new=AsyncMock(side_effect=receive_results),
        ):
            result = await worker.wait_for_completion_websocket(
                "prompt-1", "request-1"
            )

        self.assertTrue(result["completed"])
        self.assertEqual(worker.is_job_running.await_count, 3)
        self.assertEqual(worker._update_progress.await_count, 3)


if __name__ == "__main__":
    unittest.main()
