import os
from pathlib import Path
from urllib.parse import urljoin

# Load .env file if it exists (before reading environment variables)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not installed, continue without it
    pass

# Base API configuration
COMFYUI_API_BASE = os.getenv('COMFYUI_API_BASE', 'http://127.0.0.1:8189')

# API endpoints
COMFYUI_API_PROMPT = urljoin(COMFYUI_API_BASE, '/prompt')
COMFYUI_API_QUEUE = urljoin(COMFYUI_API_BASE, '/queue')
COMFYUI_API_HISTORY = urljoin(COMFYUI_API_BASE, '/history')
COMFYUI_API_INTERRUPT = urljoin(COMFYUI_API_BASE, '/api/interrupt')
COMFYUI_API_SYSTEM_STATS = urljoin(COMFYUI_API_BASE, '/system_stats')
COMFYUI_API_FREE = urljoin(COMFYUI_API_BASE, '/free')

# Ollama configuration
OLLAMA_API_BASE = os.getenv('OLLAMA_API_BASE', 'http://127.0.0.1:11434')
OLLAMA_API_GENERATE = urljoin(OLLAMA_API_BASE, '/api/generate')
# /api/ps lists models currently resident in VRAM (loaded by ANY client,
# including ones talking to Ollama directly, bypassing this wrapper)
OLLAMA_API_PS = urljoin(OLLAMA_API_BASE, '/api/ps')

# WebSocket endpoint (convert http to ws, https to wss)
COMFYUI_API_WEBSOCKET = COMFYUI_API_BASE.replace('http://', 'ws://').replace('https://', 'wss://') + '/ws'

# Generation watchdog configuration
# Long video jobs (2K upscale, merge + colour match + h264 encode) can run for
# many minutes inside a single node without emitting any WebSocket message, so
# silence alone must never be treated as a dead job — see GENERATION_CONFIG.
GENERATION_CONFIG = {
    # Hard ceiling for one ComfyUI job (seconds)
    "max_wait_time": int(os.getenv("GENERATION_MAX_WAIT_TIME", "7200")),
    # Seconds to wait for the very first WebSocket message
    "initial_timeout": float(os.getenv("GENERATION_INITIAL_TIMEOUT", "30")),
    # Seconds of WebSocket silence before we check whether the job is still alive
    "message_timeout": float(os.getenv("GENERATION_MESSAGE_TIMEOUT", "600")),
    # Seconds of silence tolerated while ComfyUI still reports the job as running
    "silent_running_timeout": float(os.getenv("GENERATION_SILENT_RUNNING_TIMEOUT", "3600")),
}

# Cache configuration
CACHE_TYPE = "redis" if os.getenv("API_CACHE", "").lower() == "redis" else "memory"
CACHE_TTL = int(os.getenv("API_CACHE_TTL", 21600))  # 6 hours as default

# Directory configuration using pathlib
COMFYUI_INSTALL_DIR = Path(os.getenv('COMFYUI_INSTALL_PATH', '/opt/ComfyUI'))
INPUT_DIR = COMFYUI_INSTALL_DIR / 'input'
OUTPUT_DIR = COMFYUI_INSTALL_DIR / 'output'

# S3 Configuration (fallback from environment)
S3_CONFIG = {
    "access_key_id": os.getenv("S3_ACCESS_KEY_ID", ""),
    "secret_access_key": os.getenv("S3_SECRET_ACCESS_KEY", ""),
    "endpoint_url": os.getenv("S3_ENDPOINT_URL", ""),
    "bucket_name": os.getenv("S3_BUCKET_NAME", ""),
    "region": os.getenv("S3_REGION", ""),
    "connect_timeout": int(os.getenv("S3_CONNECT_TIMEOUT", "60")),
    "connect_attempts": int(os.getenv("S3_CONNECT_ATTEMPTS", "3"))
}

# Check if S3 is configured via environment
S3_ENABLED = bool(
    S3_CONFIG["access_key_id"] and 
    S3_CONFIG["secret_access_key"] and 
    S3_CONFIG["bucket_name"]
)

# Webhook Configuration (fallback from environment)
WEBHOOK_CONFIG = {
    "url": os.getenv("WEBHOOK_URL", ""),
    "timeout": int(os.getenv("WEBHOOK_TIMEOUT", "30"))
}

# Check if webhook is configured via environment
WEBHOOK_ENABLED = bool(WEBHOOK_CONFIG["url"])

# Worker Configuration
WORKER_CONFIG = {
    "preprocess_workers": int(os.getenv("PREPROCESS_WORKERS", "2")),
    "generation_workers": int(os.getenv("GENERATION_WORKERS", "1")),
    "postprocess_workers": int(os.getenv("POSTPROCESS_WORKERS", "2")),
    "max_queue_size": int(os.getenv("MAX_QUEUE_SIZE", "100"))
}

# Redis Configuration (if using Redis cache)
REDIS_CONFIG = {
    "host": os.getenv("REDIS_HOST", "localhost"),
    "port": int(os.getenv("REDIS_PORT", "6379")),
    "db": int(os.getenv("REDIS_DB", "0")),
    "password": os.getenv("REDIS_PASSWORD", ""),
    "decode_responses": True
}

# Development/Debug Configuration (actually used for debug output)
DEBUG_ENABLED = os.getenv("DEBUG", "false").lower() == "true"

# Print configuration summary if debug enabled
if DEBUG_ENABLED:
    print("🔧 Configuration Summary:")
    print(f"   ComfyUI API: {COMFYUI_API_BASE}")
    print(f"   Cache Type: {CACHE_TYPE}")
    print(f"   Workers: {WORKER_CONFIG['preprocess_workers']}/{WORKER_CONFIG['generation_workers']}/{WORKER_CONFIG['postprocess_workers']}")
    print(f"   S3 Enabled: {S3_ENABLED}")
    print(f"   Webhook Enabled: {WEBHOOK_ENABLED}")
    if os.path.exists('.env'):
        print("   📄 .env file loaded")
    else:
        print("   📄 No .env file found")