# ComfyUI API Wrapper for AWS SageMaker

Custom implementation of ComfyUI API wrapper with serverless support for AWS SageMaker inference.

## Overview

This project provides:
- **ComfyUI API Wrapper**: FastAPI-based wrapper for ComfyUI with queue management and S3 storage support
- **Serverless Scripts**: Automated provisioning, AWS SageMaker inference endpoints
- **Face2Photo Workflow**: Pre-configured workflow for face-to-photo generation using Qwen-Image-Edit model and custom LoRAs

## Scripts Directory

The `scripts/` directory contains hook scripts for AWS SageMaker deployment:

- **`provisioning_face2photo.sh`**: Provisions face-to-photo generation models (Qwen-Image-Edit), custom nodes, and required dependencies

## Environment Variables

### ComfyUI API Wrapper Startup

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `S3_ACCESS_KEY_ID` | AWS S3 access key for storage configuration | No | - |
| `S3_SECRET_ACCESS_KEY` | AWS S3 secret key for storage configuration | No | - |
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

### Provisioning Configuration (provisioning_face2photo.sh)

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `WORKSPACE` | Workspace directory path | No | `/opt` |
| `MODEL_LOG` | Path to model download log file | No | `/var/log/provisioning.log` |


## Quick Start

1. Set required environment variables in your SageMaker inference endpoint configuration
2. Provisioning script will download models and custom nodes
3. ComfyUI API wrapper will start and be ready to accept requests

## Features

- Automatic model provisioning from HuggingFace
- AWS S3 storage integration
- Face2Photo generation workflow (Qwen-Image-Edit models)
- Custom node management
- Disk space cleanup automation

