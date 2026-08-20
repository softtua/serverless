# generation_worker
import asyncio
import aiohttp
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from config import COMFYUI_API_PROMPT, COMFYUI_API_HISTORY, COMFYUI_API_INTERRUPT, COMFYUI_API_WEBSOCKET
from config import COMFYUI_API_QUEUE, GENERATION_CONFIG
from config import OLLAMA_API_PS, OLLAMA_API_GENERATE
from gpu_arbiter import arbiter

logger = logging.getLogger(__name__)


class GenerationWorker:
    """
    Send payload to ComfyUI and await completion using WebSocket
    """
    def __init__(self, worker_id, kwargs):
        self.worker_id = worker_id
        self.preprocess_queue = kwargs["preprocess_queue"]
        self.generation_queue = kwargs["generation_queue"]
        self.postprocess_queue = kwargs["postprocess_queue"]
        self.request_store = kwargs["request_store"]
        self.response_store = kwargs["response_store"]
        self.generation_lock = kwargs.get("generation_lock")  # Optional, for future use

        # Configuration
        self.max_wait_time = GENERATION_CONFIG["max_wait_time"]
        self.ws_url = COMFYUI_API_WEBSOCKET
        self.client_id = f"worker_{worker_id}_{datetime.now().timestamp()}"

    async def work(self):
        logger.info(f"GenerationWorker {self.worker_id}: waiting for jobs")
        while True:
            # Get a task from the job queue
            request_id = await self.generation_queue.get()
            if request_id is None:
                # None is a signal that there are no more tasks
                break

            # Process the job
            logger.info(f"GenerationWorker {self.worker_id} processing job: {request_id}")
            
            try:
                # Get request and result from stores
                request = await self.request_store.get(request_id)
                result = await self.response_store.get(request_id)
                
                if not request:
                    raise Exception(f"Request {request_id} not found in store")
                if not result:
                    raise Exception(f"Result {request_id} not found in store")

                # Check for cancellation
                if result and getattr(result, 'status', '') == 'cancelled':
                    logger.info(f"PreprocessWorker {self.worker_id} skipping cancelled job: {request_id} - jumping to postprocess")
                    await self.postprocess_queue.put(request_id)
                    self.generation_queue.task_done()
                    continue

                # Acquire GPU — waits if LLM inference is active (through our own API)
                async with arbiter.comfyui_turn():
                    # Also check Ollama directly — a client could be talking to
                    # it outside of our wrapper (e.g. curl/CLI). ComfyUI always
                    # has priority, so force-evict any resident model.
                    await self._ensure_ollama_idle()

                    # Submit workflow to ComfyUI
                    comfyui_job_id = await self.post_workflow(request)
                    logger.info(f"Submitted job {request_id} to ComfyUI as {comfyui_job_id}")
                    
                    # Update status to show generation started
                    result.status = "generating"
                    result.message = f"Generation started (ComfyUI job: {comfyui_job_id})"
                    await self.response_store.set(request_id, result)

                    # Check if job is already complete (cached result)
                    is_cached = await self.check_if_cached(comfyui_job_id)
                    
                    if is_cached:
                        logger.info(f"Job {comfyui_job_id} completed immediately (cached result)")
                        execution_result = {
                            "prompt_id": comfyui_job_id,
                            "nodes_executed": [],
                            "progress_updates": [],
                            "completed": True,
                            "cached": True,
                            "error": None
                        }
                    else:
                        # Wait for completion using WebSocket
                        execution_result = await self.wait_for_completion_websocket(
                            comfyui_job_id, 
                            request_id
                        )
                    
                    # Get the final result from ComfyUI history
                    comfyui_response = await self.get_result(comfyui_job_id)
                    logger.info(f"Retrieved ComfyUI result for {request_id}")
                    logger.debug(f"ComfyUI response structure: {json.dumps(comfyui_response, indent=2)[:500]}...")  # First 500 chars

                # GPU released — update result and forward to post-processing
                result.status = "generated"
                result.message = "Generation complete. Queued for post-processing."
                result.comfyui_response = comfyui_response
                # Store execution details in the comfyui_response if needed
                if execution_result:
                    # Merge execution details into the response
                    if isinstance(result.comfyui_response, dict):
                        result.comfyui_response["execution_details"] = execution_result
                await self.response_store.set(request_id, result)
                
                # Send for post-processing
                await self.postprocess_queue.put(request_id)
                logger.info(f"GenerationWorker {self.worker_id} completed job: {request_id}")
                
            except Exception as e:
                logger.error(f"GenerationWorker {self.worker_id} failed job {request_id}: {e}")
                
                try:
                    # Update result to show failure
                    result = await self.response_store.get(request_id)
                    if result:
                        result.status = "failed"
                        result.message = f"Generation failed: {str(e)}"
                        await self.response_store.set(request_id, result)
                    
                    # Send job to postprocess for cleanup
                    await self.postprocess_queue.put(request_id)
                    
                except Exception as store_error:
                    logger.error(f"Failed to update result store for {request_id}: {store_error}")
            
            finally:
                # Mark the job as complete
                self.generation_queue.task_done()

        logger.info(f"GenerationWorker {self.worker_id} finished")

    async def _ensure_ollama_idle(self):
        """
        Check whether Ollama currently has any model resident in VRAM —
        regardless of whether it was loaded via our /llm/generate endpoints
        or by a client talking to Ollama directly (e.g. someone SSH-ed into
        the server running `ollama run ...` or curling /api/generate).

        ComfyUI generation always takes priority: if Ollama is not idle,
        force-evict every loaded model (keep_alive=0) before proceeding.
        Non-fatal — logs and continues on any failure.
        """
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(OLLAMA_API_PS) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()

            models = data.get("models", []) if isinstance(data, dict) else []
            if not models:
                return  # Ollama is idle, nothing to do

            model_names = [
                m.get("name") or m.get("model")
                for m in models
                if m.get("name") or m.get("model")
            ]
            logger.info(
                f"GenerationWorker {self.worker_id}: Ollama is busy ({model_names}) — "
                f"forcing eviction so ComfyUI gets GPU priority"
            )

            evict_timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=evict_timeout) as session:
                for model_name in model_names:
                    try:
                        async with session.post(
                            OLLAMA_API_GENERATE,
                            json={"model": model_name, "keep_alive": 0},
                            headers={"Content-Type": "application/json"},
                        ) as unload_resp:
                            logger.info(
                                f"GenerationWorker {self.worker_id}: evicted Ollama model "
                                f"'{model_name}' (status {unload_resp.status})"
                            )
                    except Exception as e:
                        logger.warning(
                            f"GenerationWorker {self.worker_id}: failed to evict Ollama "
                            f"model '{model_name}': {e}"
                        )

            # Give Ollama a moment to actually free VRAM before we proceed
            await asyncio.sleep(2)

        except Exception as e:
            # Ollama might not be reachable/installed on this deployment — that's fine
            logger.debug(f"GenerationWorker {self.worker_id}: could not check Ollama status: {e}")

    async def post_workflow(self, request) -> str:
        """Submit workflow to ComfyUI API"""
        payload = {
            "prompt": request.input.workflow_json,
            "client_id": self.client_id  # Use our worker's client ID
        }
        
        headers = {
            'Content-Type': 'application/json'
        }
        
        timeout = aiohttp.ClientTimeout(total=30)  # 30 second timeout
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                logger.debug(f"Posting workflow to {COMFYUI_API_PROMPT}")
                logger.debug(f"Workflow keys: {list(request.input.workflow_json.keys()) if isinstance(request.input.workflow_json, dict) else 'not a dict'}")
                
                async with session.post(
                    COMFYUI_API_PROMPT, 
                    data=json.dumps(payload),
                    headers=headers
                ) as response:
                    
                    response_text = await response.text()
                    logger.debug(f"ComfyUI API response status: {response.status}")
                    logger.debug(f"ComfyUI API response: {response_text[:500]}...")  # First 500 chars
                    
                    if response.status >= 400:
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=response.history,
                            status=response.status,
                            message=f"ComfyUI API error: {response_text}"
                        )
                    
                    response_data = json.loads(response_text)
                    
                    if "prompt_id" in response_data:
                        return response_data["prompt_id"]
                    elif "node_errors" in response_data:
                        error_details = json.dumps(response_data["node_errors"], indent=2)
                        raise Exception(f"ComfyUI node errors: {error_details}")
                    elif "error" in response_data:
                        raise Exception(f"ComfyUI error: {response_data['error']}")
                    else:
                        raise Exception(f"Unexpected response from ComfyUI: {response_data}")
                        
            except asyncio.TimeoutError:
                raise Exception("Timeout posting workflow to ComfyUI")
            except aiohttp.ClientError as e:
                raise Exception(f"Network error posting to ComfyUI: {e}")
            except json.JSONDecodeError as e:
                raise Exception(f"Invalid JSON response from ComfyUI: {e}")

    async def check_if_cached(self, comfyui_job_id: str) -> bool:
        """Check if job is already complete (cached result)"""
        await asyncio.sleep(0.5)  # Give ComfyUI a moment to process
        
        timeout = aiohttp.ClientTimeout(total=5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"{COMFYUI_API_HISTORY}/{comfyui_job_id}"
                async with session.get(url) as response:
                    if response.status == 200:
                        history_data = await response.json()
                        # If we get non-empty data, the job is complete
                        if history_data and history_data != {}:
                            logger.info(f"Job {comfyui_job_id} found in history (cached)")
                            return True
            return False
        except Exception as e:
            logger.debug(f"Error checking cache status: {e}")
            return False
    
    async def is_job_running(self, comfyui_job_id: str) -> bool:
        """
        Ask ComfyUI whether the prompt is still executing or waiting in the queue.

        This is the authoritative liveness signal: the WebSocket can stay silent for
        a long time on nodes that report no progress (video merge, colour match,
        h264 encode), and killing such a job wastes the whole generation.
        """
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(COMFYUI_API_QUEUE) as response:
                if response.status != 200:
                    raise Exception(f"ComfyUI queue returned status {response.status}")
                queue_data = await response.json()

        for key in ("queue_running", "queue_pending"):
            for entry in queue_data.get(key, []) or []:
                # Entries look like [number, prompt_id, prompt, extra_data, outputs]
                if isinstance(entry, (list, tuple)) and len(entry) > 1:
                    if entry[1] == comfyui_job_id:
                        return True
                elif isinstance(entry, dict) and entry.get("prompt_id") == comfyui_job_id:
                    return True
        return False

    async def wait_for_completion_websocket(self, comfyui_job_id: str, request_id: str) -> Dict[str, Any]:
        """
        Wait for ComfyUI job completion using WebSocket connection
        Returns execution result details
        """
        execution_result = {
            "prompt_id": comfyui_job_id,
            "nodes_executed": [],
            "progress_updates": [],
            "completed": False,
            "error": None
        }
        
        timeout = aiohttp.ClientTimeout(total=self.max_wait_time)
        
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                logger.info(f"Connecting to ComfyUI WebSocket at {self.ws_url}")
                
                async with session.ws_connect(
                    self.ws_url,
                    params={"clientId": self.client_id}
                ) as ws:
                    logger.info(f"WebSocket connected for job {comfyui_job_id}")
                    
                    # Start listening for messages
                    start_time = asyncio.get_event_loop().time()
                    last_update_time = start_time
                    last_message_time = start_time
                    last_cancellation_check = start_time
                    
                    # Progressive timeout strategy
                    initial_timeout = GENERATION_CONFIG["initial_timeout"]
                    # Silence between messages is normal: nodes like the time merge,
                    # colour match and the h264 encode of a long 2K clip run for
                    # minutes without any WebSocket traffic.
                    message_timeout = GENERATION_CONFIG["message_timeout"]
                    # How long we keep waiting while ComfyUI still reports the prompt
                    # as running (measured from the last message we received).
                    silent_running_timeout = GENERATION_CONFIG["silent_running_timeout"]
                    max_no_message_retries = 3  # Number of times to retry when no messages received
                    no_message_retry_count = 0
                    
                    while True:
                        try:
                            # Set timeout based on whether we've received any messages
                            timeout_duration = initial_timeout if last_message_time == start_time else message_timeout
                            
                            msg = await asyncio.wait_for(
                                ws.receive(), 
                                timeout=timeout_duration
                            )
                            
                            last_message_time = asyncio.get_event_loop().time()
                            # Reset retry count since we received a message
                            no_message_retry_count = 0

                            current_time = asyncio.get_event_loop().time()
                            if current_time - last_cancellation_check > 5.0:  # Check every 5 seconds
                                if await self._check_if_cancelled(request_id):
                                    logger.info(f"Job {request_id} was cancelled during generation - aborting WebSocket")
                                    # Cancel the ComfyUI job
                                    await self.cancel_comfyui_job(comfyui_job_id)
                                    raise Exception(f"Job {request_id} was cancelled during generation")
                                last_cancellation_check = current_time
                            
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                    message_type = data.get("type")
                                    
                                    logger.debug(f"WebSocket message type: {message_type}")
                                    
                                    # Check if this message is for our prompt
                                    if data.get("data", {}).get("prompt_id") == comfyui_job_id:
                                        
                                        if message_type == "execution_start":
                                            logger.info(f"Execution started for {comfyui_job_id}")
                                            await self._update_progress(
                                                request_id, 
                                                "Execution started..."
                                            )
                                        
                                        elif message_type == "execution_cached":
                                            nodes = data.get("data", {}).get("nodes", [])
                                            logger.info(f"Using cached results for nodes: {nodes}")
                                            execution_result["nodes_executed"].extend(nodes)
                                        
                                        elif message_type == "executing":
                                            node = data.get("data", {}).get("node")
                                            if node:
                                                logger.info(f"Executing node: {node}")
                                                execution_result["nodes_executed"].append(node)
                                                await self._update_progress(
                                                    request_id, 
                                                    f"Processing node: {node}"
                                                )
                                            elif data.get("data", {}).get("node") is None:
                                                # node = None means execution is complete
                                                logger.info(f"Execution complete for {comfyui_job_id}")
                                                execution_result["completed"] = True
                                                return execution_result
                                        
                                        elif message_type == "progress":
                                            progress_data = data.get("data", {})
                                            value = progress_data.get("value", 0)
                                            max_value = progress_data.get("max", 100)
                                            
                                            progress_pct = (value / max_value * 100) if max_value > 0 else 0
                                            progress_msg = f"Progress: {progress_pct:.1f}% ({value}/{max_value})"
                                            
                                            logger.info(f"Progress update: {progress_msg}")
                                            execution_result["progress_updates"].append({
                                                "time": asyncio.get_event_loop().time() - start_time,
                                                "value": value,
                                                "max": max_value,
                                                "percentage": progress_pct
                                            })
                                            
                                            # Update status every few seconds to avoid spam
                                            current_time = asyncio.get_event_loop().time()
                                            if current_time - last_update_time > 2:  # Update every 2 seconds
                                                await self._update_progress(request_id, progress_msg)
                                                last_update_time = current_time
                                        
                                        elif message_type == "execution_error":
                                            error_data = data.get("data", {})
                                            error_msg = f"Execution error: {error_data}"
                                            logger.error(error_msg)
                                            execution_result["error"] = error_data
                                            raise Exception(error_msg)
                                        
                                        elif message_type == "executed":
                                            node = data.get("data", {}).get("node")
                                            output = data.get("data", {}).get("output")
                                            logger.info(f"Node {node} executed successfully")
                                            logger.debug(f"Node output: {json.dumps(output, indent=2)[:500]}...")
                                    
                                except json.JSONDecodeError as e:
                                    logger.warning(f"Failed to parse WebSocket message: {e}")
                                    logger.debug(f"Raw message: {msg.data}")
                        
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                logger.error(f"WebSocket error: {ws.exception()}")
                                raise Exception(f"WebSocket error: {ws.exception()}")
                            
                            elif msg.type == aiohttp.WSMsgType.CLOSED:
                                logger.warning("WebSocket connection closed")
                                break
                            
                        except asyncio.TimeoutError:
                            no_message_retry_count += 1
                            elapsed = asyncio.get_event_loop().time() - start_time
                            
                            # If we haven't received any messages, try to check job status before giving up
                            if last_message_time == start_time:
                                logger.warning(f"No WebSocket messages received for {comfyui_job_id} "
                                            f"(attempt {no_message_retry_count}/{max_no_message_retries}) "
                                            f"after {elapsed:.1f}s - checking job status")
                                
                                # Check if the job is complete/cached
                                try:
                                    if await self.check_if_cached(comfyui_job_id):
                                        logger.info(f"Job {comfyui_job_id} is complete (cached)")
                                        execution_result["completed"] = True
                                        execution_result["cached"] = True
                                        return execution_result
                                except Exception as check_error:
                                    logger.warning(f"Error checking job status: {check_error}")
                                
                                # If we've exhausted retries, give up
                                if no_message_retry_count >= max_no_message_retries:
                                    logger.error(f"No WebSocket messages received for {comfyui_job_id} "
                                            f"after {max_no_message_retries} attempts and {elapsed:.1f}s")
                                    raise Exception(f"No WebSocket messages received for job {comfyui_job_id} "
                                                f"after {max_no_message_retries} retry attempts")
                                
                                # Wait a bit before retrying (exponential backoff)
                                wait_time = min(5 * (2 ** (no_message_retry_count - 1)), 30)  # Cap at 30 seconds
                                logger.info(f"Waiting {wait_time}s before retry {no_message_retry_count + 1}")
                                await asyncio.sleep(wait_time)
                                
                            else:
                                # We were receiving messages but they stopped
                                logger.warning(f"WebSocket message timeout for job {comfyui_job_id} "
                                            f"(no message for {timeout_duration}s, elapsed: {elapsed:.1f}s)")
                                
                                # Try to check job status before giving up completely
                                try:
                                    if await self.check_if_cached(comfyui_job_id):
                                        logger.info(f"Job {comfyui_job_id} completed despite message timeout")
                                        execution_result["completed"] = True
                                        return execution_result
                                except Exception as check_error:
                                    logger.warning(f"Error checking job status after timeout: {check_error}")
                                
                                # Silence is not death: ask ComfyUI whether the prompt is
                                # still executing. Long-running silent nodes (video merge,
                                # colour match, h264 encode) legitimately produce no
                                # WebSocket traffic for many minutes.
                                silent_for = asyncio.get_event_loop().time() - last_message_time
                                try:
                                    still_running = await self.is_job_running(comfyui_job_id)
                                except Exception as queue_error:
                                    logger.warning(f"Error checking ComfyUI queue for {comfyui_job_id}: {queue_error}")
                                    still_running = None

                                if still_running and elapsed > self.max_wait_time:
                                    logger.error(f"Job {comfyui_job_id} still running but exceeded "
                                                f"max wait time ({elapsed:.1f}s) - giving up")
                                    raise Exception(f"Timeout waiting for job {comfyui_job_id} "
                                                f"after {elapsed:.1f} seconds")

                                if still_running and silent_for < silent_running_timeout:
                                    logger.info(f"Job {comfyui_job_id} is still running in ComfyUI "
                                                f"(silent for {silent_for:.1f}s of {silent_running_timeout}s) "
                                                f"- continuing to wait")
                                    await self._update_progress(
                                        request_id,
                                        f"Still processing (no progress updates for {silent_for:.0f}s)"
                                    )
                                    continue

                                if still_running:
                                    logger.error(f"Job {comfyui_job_id} still running but silent for "
                                                f"{silent_for:.1f}s - giving up")
                                    raise Exception(f"Job {comfyui_job_id} stalled: no WebSocket messages "
                                                f"for {silent_for:.1f} seconds while still running")

                                # If still no completion after timeout, raise error
                                raise Exception(f"WebSocket message timeout for job {comfyui_job_id} "
                                            f"after {timeout_duration} seconds without messages")
                        
                        # Check for overall timeout
                        elapsed = asyncio.get_event_loop().time() - start_time
                        if elapsed > self.max_wait_time:
                            raise Exception(f"Timeout waiting for job {comfyui_job_id} after {elapsed:.1f} seconds")
                    
                    # If we exit the loop without completion, something went wrong
                    if not execution_result["completed"]:
                        # Final check before giving up
                        try:
                            if await self.check_if_cached(comfyui_job_id):
                                logger.info(f"Job {comfyui_job_id} completed (final check)")
                                execution_result["completed"] = True
                                return execution_result
                        except Exception as check_error:
                            logger.warning(f"Error in final job status check: {check_error}")
                        
                        raise Exception(f"WebSocket closed without completion for job {comfyui_job_id}")
                    
                    return execution_result
                    
        except asyncio.TimeoutError:
            logger.warning(f"WebSocket overall timeout for job {comfyui_job_id} - attempting to cancel")
            await self.cancel_comfyui_job(comfyui_job_id)
            raise Exception(f"WebSocket timeout for job {comfyui_job_id}")
        except aiohttp.ClientError as e:
            # Cancel the job since we can't monitor it anymore
            logger.warning(f"WebSocket connection error for job {comfyui_job_id} - attempting to cancel")
            await self.cancel_comfyui_job(comfyui_job_id)
            raise Exception(f"WebSocket connection error: {e}")
        except Exception as e:
            logger.error(f"WebSocket error for job {comfyui_job_id}: {e}")
            # Cancel on other errors to be safe
            await self.cancel_comfyui_job(comfyui_job_id)
            raise

    async def _update_progress(self, request_id: str, message: str):
        """Helper to update progress in the response store"""
        try:
            result = await self.response_store.get(request_id)
            if result:
                result.message = message
                await self.response_store.set(request_id, result)
        except Exception as e:
            logger.warning(f"Failed to update progress for {request_id}: {e}")

    async def get_result(self, comfyui_job_id: str) -> Optional[dict]:
        """Get the final result from ComfyUI history"""
        timeout = aiohttp.ClientTimeout(total=30)
        
        # Wait a moment for history to be updated
        await asyncio.sleep(0.5)
        
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"{COMFYUI_API_HISTORY}/{comfyui_job_id}"
                logger.debug(f"Fetching result from: {url}")
                
                async with session.get(url) as response:
                    response_text = await response.text()
                    logger.debug(f"History API status: {response.status}")
                    
                    if response.status == 200:
                        history_data = json.loads(response_text)
                        
                        # Check if we got actual data
                        if not history_data or history_data == {}:
                            logger.warning(f"Empty history response for job {comfyui_job_id}")
                            # Try the general history endpoint
                            return await self._get_result_from_general_history(comfyui_job_id)
                        
                        logger.info(f"Retrieved ComfyUI history for job {comfyui_job_id}")
                        return history_data
                    else:
                        raise Exception(f"Failed to get result (status {response.status}): {response_text}")
                        
        except asyncio.TimeoutError:
            raise Exception(f"Timeout getting result for job {comfyui_job_id}")
        except aiohttp.ClientError as e:
            raise Exception(f"Network error getting result: {e}")
        except json.JSONDecodeError as e:
            raise Exception(f"Invalid JSON in result: {e}")

    async def _get_result_from_general_history(self, comfyui_job_id: str) -> Optional[dict]:
        """Fallback: Get result from general history endpoint"""
        timeout = aiohttp.ClientTimeout(total=30)
        
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Try the general history endpoint
                url = COMFYUI_API_HISTORY.rstrip(f"/{comfyui_job_id}")
                logger.debug(f"Trying general history endpoint: {url}")
                
                async with session.get(url) as response:
                    if response.status == 200:
                        all_history = await response.json()
                        
                        # Look for our job in the history
                        if comfyui_job_id in all_history:
                            logger.info(f"Found job {comfyui_job_id} in general history")
                            return {comfyui_job_id: all_history[comfyui_job_id]}
                        else:
                            logger.warning(f"Job {comfyui_job_id} not found in general history")
                            return {}
                    else:
                        return {}
                        
        except Exception as e:
            logger.error(f"Failed to get result from general history: {e}")
            return {}

    async def _check_if_cancelled(self, request_id: str) -> bool:
        """Check if the job has been cancelled"""
        try:
            result = await self.response_store.get(request_id)
            return result and getattr(result, 'status', '') == 'cancelled'
        except Exception as e:
            logger.warning(f"Error checking cancellation status for {request_id}: {e}")
            return False

    async def cancel_comfyui_job(self, comfyui_job_id: str):
        """Cancel a running job in ComfyUI"""
        try:       
            if not COMFYUI_API_INTERRUPT:
                logger.warning("COMFYUI_API_INTERRUPT not configured, cannot cancel job")
                return False
                
            payload = {
                "prompt_id": comfyui_job_id
            }
            
            headers = {
                'Content-Type': 'application/json'
            }
                
            timeout = aiohttp.ClientTimeout(total=5.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                cancel_url = COMFYUI_API_INTERRUPT
                
                async with session.post(
                    cancel_url,
                    data=json.dumps(payload),
                    headers=headers
                ) as response:
                    
                    if response.status == 200:
                        logger.info(f"Successfully cancelled ComfyUI job {comfyui_job_id}")
                        return True
                    else:
                        response_text = await response.text()
                        logger.warning(f"Failed to cancel ComfyUI job {comfyui_job_id}: HTTP {response.status} - {response_text}")
                        return False
                    
        except Exception as e:
            logger.error(f"Error cancelling ComfyUI job {comfyui_job_id}: {e}")
            return False
