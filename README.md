# ComfyUI API Wrapper for Vast.ai Serverless

Custom implementation of ComfyUI API wrapper with serverless support for Vast.ai cloud infrastructure.

## Overview

This project provides:
- **ComfyUI API Wrapper**: FastAPI-based wrapper for ComfyUI with queue management and S3 storage support
- **Serverless Scripts**: Automated provisioning and entrypoint scripts for Vast.ai serverless containers
- **Video Generation Support**: Pre-configured workflows for video generation with Wan 2.2 models

## Scripts Directory

The `scripts/` directory contains hook scripts for Vast.ai serverless system:

- **`entrypoint.sh`**: Container startup script that configures rclone, cloudflared tunnel, and initializes the ComfyUI environment
- **`provisioning_video.sh`**: Provisions video generation models, custom nodes, and required dependencies

## Environment Variables

### Container Startup (entrypoint.sh)

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `R2_ACCESS_KEY` | Cloudflare R2 access key for rclone configuration | No | - |
| `R2_SECRET_KEY` | Cloudflare R2 secret key for rclone configuration | No | - |
| `CF_TOKEN` | Cloudflare tunnel token for cloudflared service | No | - |
| `COMFYUI_VERSION` | ComfyUI version to checkout (tag or "latest") | No | `latest` |
| `WORKSPACE` | Workspace directory path | No | `/workspace` |

### ComfyUI API Configuration (config.py)

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `COMFYUI_API_BASE` | Base URL for ComfyUI API | No | `http://localhost:18188` |
| `COMFYUI_INSTALL_PATH` | ComfyUI installation directory | No | `/workspace/ComfyUI` |
| `API_CACHE` | Cache type (set to "redis" for Redis) | No | `memory` |
| `API_CACHE_TTL` | Cache TTL in seconds | No | `21600` (6 hours) |

### S3 Storage Configuration (config.py)

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `S3_ACCESS_KEY_ID` | S3 access key ID | No* | - |
| `S3_SECRET_ACCESS_KEY` | S3 secret access key | No* | - |
| `S3_ENDPOINT_URL` | S3 endpoint URL | No* | - |
| `S3_BUCKET_NAME` | S3 bucket name | No* | - |
| `S3_REGION` | S3 region | No | - |
| `S3_CONNECT_TIMEOUT` | S3 connection timeout in seconds | No | `60` |
| `S3_CONNECT_ATTEMPTS` | S3 connection retry attempts | No | `3` |

\* All three S3 credentials (`S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`) must be set to enable S3 storage.

### Provisioning Configuration (provisioning_video.sh)

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `WORKSPACE` | Workspace directory path | No | `/workspace` |
| `MODEL_LOG` | Path to model download log file | No | `/var/log/portal/comfyui.log` |

### Runtime Environment (set by entrypoint.sh)

| Variable | Value | Description |
|----------|-------|-------------|
| `SERVERLESS` | `true` | Indicates serverless mode |
| `BACKEND` | `comfyui-json` | Backend type identifier |
| `MODEL_LOG` | `/var/log/portal/comfyui.log` | Model provisioning log path |

## Quick Start

1. Set required environment variables in your Vast.ai instance template
2. The entrypoint script will automatically configure rclone and cloudflared
3. Provisioning script will download models and custom nodes
4. ComfyUI API wrapper will start and be ready to accept requests

## Features

- Automatic model provisioning from HuggingFace
- R2/S3 storage integration via rclone
- Cloudflare tunnel support
- Video generation workflows (Wan 2.2)
- Custom node management
- Disk space cleanup automation

