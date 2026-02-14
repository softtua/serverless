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

## API Usage

### Request Payload Structure

The API accepts POST requests with the following structure:

```json
{
  "input": {
    "request_id": "optional-uuid-v4",
    "user_id": "user-identifier",
    "modifier": "ModifierClassName",
    "modifications": {
      "parameter1": "value1",
      "parameter2": 42
    },
    "workflow_json": {
      // Alternative to modifier: direct ComfyUI workflow
    },
    "s3": {
      "access_key_id": "your-access-key",
      "secret_access_key": "your-secret-key",
      "endpoint_url": "https://s3.amazonaws.com",
      "bucket_name": "your-bucket",
      "region": "us-east-1"
    },
    "webhook": {
      "url": "https://your-webhook-endpoint.com",
      "extra_params": {
        "custom_field": "value"
      }
    }
  }
}
```

**Note:** `modifier` and `workflow_json` are mutually exclusive - you must provide one or the other, but not both.

### Face2Photo Modifier

The `Face2Photo` modifier transforms face images into professional portraits using the Qwen-Image-Edit model with custom LoRAs.

#### Available Parameters

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `input_image` | string | S3 key or URL of the input face image | **Required** |
| `prompt` | string | Text prompt describing the desired output | "territory orange style, portrait of the same person..." |
| `seed` | int | Random seed for generation (0 to 4294967295) | Random value |
| `steps` | int | Number of sampling steps | 8 (normal) / 5 (fast) |
| `sampler_name` | string | Sampler algorithm (e.g., "euler", "dpmpp_2m") | "euler" |
| `scheduler` | string | Scheduler type (e.g., "simple", "normal") | "simple" |
| `number_images` | int | Number of images to generate in a single batch | 1 |
| `width` | int | Output image width in pixels | 1024 |
| `height` | int | Output image height in pixels | 1024 |
| `face_strength` | int | Face similarity strength (0-100). Higher values preserve more facial features | 70 |
| `mode` | string | Generation mode: "normal" (8 steps, higher quality) or "fast" (5 steps, faster) | "normal" |
| `style` | string | Style preset: "territory-orange" (custom branded style) or "classic-studio" (neutral style) | "territory-orange" |

#### Example Request - Face2Photo with S3 Storage

```json
{
  "input": {
    "request_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "user_id": "user_12345",
    "modifier": "Face2Photo",
    "modifications": {
      "input_image": "input/face_photo_001.jpg",
      "prompt": "territory orange style, professional portrait of the same person, studio lighting with soft shadows, neutral background, business attire, confident expression",
      "seed": 123456789,
      "steps": 8,
      "sampler_name": "euler",
      "scheduler": "simple",
      "number_images": 1,
      "width": 1024,
      "height": 1024,
      "face_strength": 80,
      "mode": "normal",
      "style": "territory-orange"
    },
    "s3": {
      "access_key_id": "AKIAIOSFODNN7EXAMPLE",
      "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
      "endpoint_url": "https://s3.amazonaws.com",
      "bucket_name": "my-comfyui-outputs",
      "region": "us-east-1"
    },
    "webhook": {
      "url": "https://api.myapp.com/webhooks/image-complete",
      "extra_params": {
        "user_id": "user_12345",
        "job_type": "face2photo"
      }
    }
  }
}
```

#### Example Request - Face2Photo with URL Input

```json
{
  "input": {
    "request_id": "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d",
    "user_id": "user_67890",
    "modifier": "Face2Photo",
    "modifications": {
      "input_image": "https://example.com/images/source-face.jpg",
      "prompt": "territory orange style, cinematic portrait of the same person, dramatic lighting, moody atmosphere",
      "steps": 10,
      "style": "classic-studio"
    },
    "s3": {
      "bucket_name": "my-outputs",
      "access_key_id": "your-key",
      "secret_access_key": "your-secret"
    }
  }
}
```

#### Example Request - Minimal Face2Photo

```json
{
  "input": {
    "modifier": "Face2Photo",
    "modifications": {
      "input_image": "input/my-face.jpg"
    }
  }
}
```

**Note:** When using minimal configuration, default values will be applied for all parameters except `input_image`, which is required.

### Response Format

The API returns a JSON response with the generated images:

```json
{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "status": "completed",
  "message": "Generation completed successfully",
  "output": [
    {
      "filename": "face2photo_20260207-143022.png",
      "local_path": "/workspace/ComfyUI/output/face2photo_20260207-143022.png",
      "url": "s3://my-comfyui-outputs/media/user_12345/output/face2photo_f47ac10b-58cc-4372.png",
      "type": "output",
      "node_id": "9"
    }
  ]
}
```

**Note:** When S3 is configured with `user_id`, files are organized as `s3://{bucket}/media/{user_id}/{type}/{filename}_{request_id_prefix}.{ext}`. The `url` field contains the full S3 path that can be used to generate presigned URLs on-demand.

## Features

- Automatic model provisioning from HuggingFace
- AWS S3 storage integration
- Face2Photo generation workflow (Qwen-Image-Edit models)
- Custom node management
- Disk space cleanup automation
- Webhook notifications for async processing

