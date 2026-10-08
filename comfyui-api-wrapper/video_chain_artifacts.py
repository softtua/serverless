"""Portable MiniMax H3 chain artifacts backed by the worker's S3/R2 store.

The endpoints in this module are intentionally independent from the ComfyUI
generation queue.  Confirming a scene starts an asynchronous upload, while a
resume restores only missing files before the generation request is queued.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import aiobotocore.session
from aiobotocore.config import AioConfig
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field


router = APIRouter(prefix="/video-chain", tags=["video-chain"])

_OUTPUT_ROOT = Path(os.environ.get("COMFYUI_OUTPUT_DIR", "/opt/ComfyUI/output")).resolve()
_RUN_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ARTIFACT_FIELDS = (
    ("segment", "segment_sha256"),
    ("checkpoint", "checkpoint_sha256"),
    ("generated_audio", "generated_audio_sha256"),
    ("generated_audio_overlap", "generated_audio_overlap_sha256"),
    ("blend_segment", "blend_segment_sha256"),
    ("prompt_file", "prompt_file_sha256"),
    # Compact audio overlap written by the LatentTiler resume patch; final
    # assembly reads it instead of the (pruned) native checkpoint.
    ("generated_audio_overlap", "generated_audio_overlap_sha256"),
    ("revision_metadata", None),
)
_operations: dict[str, dict[str, Any]] = {}
_tasks: set[asyncio.Task] = set()


class ChainRequest(BaseModel):
    user_id: int = Field(gt=0)
    run_name: str
    scene: int = Field(gt=0)
    issued_at: int
    # Confirmed scenes produced on another worker. A local copy of such a
    # scene is only a leftover of an unconfirmed take, however consistent its
    # own hashes are, so restore checks it against the bucket's canonical copy.
    remote_scenes: list[int] = Field(default_factory=list)


class ProbeRequest(BaseModel):
    run_name: str
    scenes: list[int]
    issued_at: int


def _s3_config() -> dict[str, str]:
    config = {
        "access_key_id": os.environ.get("S3_ACCESS_KEY_ID", ""),
        "secret_access_key": os.environ.get("S3_SECRET_ACCESS_KEY", ""),
        "endpoint_url": os.environ.get("S3_ENDPOINT_URL", ""),
        "bucket_name": os.environ.get("S3_BUCKET_NAME", ""),
        "region": os.environ.get("S3_REGION", "auto") or "auto",
    }
    if not all(config[key] for key in ("access_key_id", "secret_access_key", "endpoint_url", "bucket_name")):
        raise RuntimeError("The worker's S3/R2 configuration is incomplete.")
    return config


async def _authorize(request: Request, signature: str | None) -> bytes:
    body = await request.body()
    secret = _s3_config()["secret_access_key"].encode("utf-8")
    request_target = request.url.path
    if request.url.query:
        request_target += "?" + request.url.query
    canonical = b"\n".join((
        request.method.upper().encode("ascii"),
        request_target.encode("utf-8"),
        hashlib.sha256(body).hexdigest().encode("ascii"),
    ))
    expected = hmac.new(secret, canonical, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid chain signature.")
    return body


def _validate_request(run_name: str, issued_at: int) -> None:
    if not _RUN_RE.fullmatch(run_name):
        raise HTTPException(status_code=422, detail="Invalid chain run name.")
    if abs(int(time.time()) - int(issued_at)) > 300:
        raise HTTPException(status_code=401, detail="Expired chain request.")


def _run_root(run_name: str) -> Path:
    root = (_OUTPUT_ROOT / "h3_chains" / run_name).resolve()
    expected_parent = (_OUTPUT_ROOT / "h3_chains").resolve()
    if root.parent != expected_parent:
        raise ValueError("Invalid chain run path.")
    return root


def _inside_run(run_root: Path, address: str) -> Path:
    path = (_OUTPUT_ROOT / str(address)).resolve()
    if path != run_root and run_root not in path.parents:
        raise ValueError("Chain metadata points outside its run directory.")
    return path


def _relative_artifact(run_root: Path, path: Path) -> str:
    return path.relative_to(run_root).as_posix()


def _remote_prefix(user_id: int, run_name: str) -> str:
    return f"{user_id}/video-chains/{run_name}/artifacts/"


def _metadata_path(run_root: Path, scene: int) -> Path:
    return run_root / "checkpoints" / f"clip_{scene:04d}.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_metadata(run_root: Path, scene: int) -> tuple[Path, dict[str, Any]]:
    path = _metadata_path(run_root, scene)
    if not path.is_file():
        raise FileNotFoundError(f"Scene {scene} metadata is missing.")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    segment = metadata.get("segment")
    if not isinstance(segment, dict) or int(segment.get("index", -1)) != scene:
        raise ValueError(f"Scene {scene} metadata has an invalid segment record.")
    if str(metadata.get("run_name") or "") != run_root.name:
        raise ValueError(f"Scene {scene} metadata belongs to another run.")
    return path, metadata


def _scene_files(run_root: Path, scene: int, include_checkpoint: bool = True) -> list[tuple[Path, str | None]]:
    metadata_path, metadata = _read_metadata(run_root, scene)
    segment = metadata["segment"]
    files: list[tuple[Path, str | None]] = [(metadata_path, None)]
    for field, hash_field in _ARTIFACT_FIELDS:
        if field == "checkpoint" and not include_checkpoint:
            continue
        address = segment.get(field)
        if not isinstance(address, str) or not address:
            continue
        path = _inside_run(run_root, address)
        expected = str(segment.get(hash_field) or "") if hash_field else None
        files.append((path, expected or None))
    unique: dict[str, tuple[Path, str | None]] = {}
    for path, expected in files:
        unique[str(path)] = (path, expected)
    return list(unique.values())


def _verify_local(files: list[tuple[Path, str | None]]) -> None:
    for path, expected in files:
        if not path.is_file():
            raise FileNotFoundError(f"Required chain artifact is missing: {path}")
        if expected and _sha256(path) != expected:
            raise ValueError(f"Chain artifact failed SHA-256 verification: {path}")


def _client_kwargs(config: dict[str, str]) -> dict[str, Any]:
    return {
        "aws_access_key_id": config["access_key_id"],
        "aws_secret_access_key": config["secret_access_key"],
        "endpoint_url": config["endpoint_url"],
        "region_name": config["region"],
        "config": AioConfig(connect_timeout=30, read_timeout=900, retries={"max_attempts": 4}),
    }


async def _upload_file(client, bucket: str, key: str, path: Path) -> None:
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as handle:
        await client.put_object(Bucket=bucket, Key=key, Body=handle, ContentType=content_type)


async def _upload_scene(operation_id: str, payload: ChainRequest) -> None:
    operation = _operations[operation_id]
    operation["status"] = "running"
    try:
        run_root = _run_root(payload.run_name)
        files = _scene_files(run_root, payload.scene, include_checkpoint=True)
        _verify_local(files)
        config = _s3_config()
        prefix = _remote_prefix(payload.user_id, payload.run_name)
        uploaded: list[str] = []
        session = aiobotocore.session.get_session()
        async with session.create_client("s3", **_client_kwargs(config)) as client:
            # Upload every file before rotating checkpoints. A failed upload
            # therefore leaves the previous confirmed frontier intact.
            for path, _expected in files:
                key = prefix + _relative_artifact(run_root, path)
                await _upload_file(client, config["bucket_name"], key, path)
                uploaded.append(key)
            uploaded.extend(await _upload_hq_latent(client, config["bucket_name"], prefix, run_root, payload.scene))

            checkpoint_prefix = prefix + "checkpoints/"
            response = await client.list_objects_v2(Bucket=config["bucket_name"], Prefix=checkpoint_prefix)
            current = {
                key for key in uploaded
                if key.startswith(checkpoint_prefix) and key.endswith(".safetensors")
            }
            stale = [
                {"Key": item["Key"]}
                for item in response.get("Contents", [])
                if str(item.get("Key", "")).endswith(".safetensors")
                and str(item.get("Key")) not in current
            ]
            if stale:
                await client.delete_objects(
                    Bucket=config["bucket_name"], Delete={"Objects": stale, "Quiet": True})
            # Same rotation for HQ latents: only the newest confirmed scene's
            # latent is ever spliced into the next scene.
            hq_prefix = prefix + "checkpoints_hq/"
            response = await client.list_objects_v2(Bucket=config["bucket_name"], Prefix=hq_prefix)
            stale_hq = [
                {"Key": item["Key"]}
                for item in response.get("Contents", [])
                if str(item.get("Key")) not in uploaded
            ]
            if stale_hq:
                await client.delete_objects(
                    Bucket=config["bucket_name"], Delete={"Objects": stale_hq, "Quiet": True})

            # Written last, after every native artifact is durable and stale
            # checkpoint rotation succeeded. Drupal can use this tiny marker
            # to reconcile a completed upload if the bridge restarts and its
            # in-memory operation registry is lost.
            marker_key = prefix + f"confirmed/clip_{payload.scene:04d}.{operation_id}.json"
            marker = json.dumps({
                "run_name": payload.run_name,
                "scene": payload.scene,
                "operation_id": operation_id,
                "artifacts": uploaded,
                "completed_at": int(time.time()),
            }, separators=(",", ":")).encode("utf-8")
            await client.put_object(
                Bucket=config["bucket_name"], Key=marker_key, Body=marker,
                ContentType="application/json")
            uploaded.append(marker_key)

        operation.update(status="completed", artifacts=uploaded, completed_at=int(time.time()))
    except Exception as exc:  # noqa: BLE001 - surfaced through operation status.
        operation.update(status="failed", error=str(exc), completed_at=int(time.time()))


async def _download_object(client, bucket: str, key: str, destination: Path) -> None:
    response = await client.get_object(Bucket=bucket, Key=key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + f".part-{uuid.uuid4().hex}")
    try:
        async with response["Body"] as stream:
            with temporary.open("wb") as handle:
                while True:
                    chunk = await stream.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _local_segment(run_root: Path, scene: int) -> dict[str, Any] | None:
    try:
        return _read_metadata(run_root, scene)[1]["segment"]
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return None


def _drop_hq_latents(run_root: Path, scene: int) -> None:
    """Remove a scene's HQ latents, which carry no revision in their name.

    HQ Context Splice reads `checkpoints_hq/clip_NNNN.safetensors` of the
    predecessor by index alone. After a scene is replaced by another take the
    old file would splice the wrong take into the next scene; a missing file
    is bypassed cleanly by the splice node instead.
    """
    hq_dir = run_root / "checkpoints_hq"
    for path in (hq_dir / f"clip_{scene:04d}.safetensors", *hq_dir.glob(f"clip_{scene:04d}_chunk_*.safetensors")):
        path.unlink(missing_ok=True)


def _hq_latent_path(run_root: Path, scene: int) -> Path:
    return run_root / "checkpoints_hq" / f"clip_{scene:04d}.safetensors"


def _hq_keys(prefix: str, scene: int) -> tuple[str, str]:
    base = prefix + f"checkpoints_hq/clip_{scene:04d}"
    return base + ".safetensors", base + ".json"


async def _upload_hq_latent(client, bucket: str, prefix: str, run_root: Path, scene: int) -> list[str]:
    """Mirror a confirmed scene's HQ latent, bound to its native checkpoint.

    Only 2K chains write one. The file name has no revision, so the sidecar
    records the checkpoint hash of the take it belongs to; restore uses that to
    refuse a latent of another take.
    """
    path = _hq_latent_path(run_root, scene)
    if not path.is_file():
        return []
    segment = _read_metadata(run_root, scene)[1]["segment"]
    latent_key, sidecar_key = _hq_keys(prefix, scene)
    await _upload_file(client, bucket, latent_key, path)
    sidecar = json.dumps({
        "scene": scene,
        "sha256": _sha256(path),
        "checkpoint_sha256": str(segment.get("checkpoint_sha256") or ""),
    }, separators=(",", ":")).encode("utf-8")
    await client.put_object(Bucket=bucket, Key=sidecar_key, Body=sidecar, ContentType="application/json")
    return [latent_key, sidecar_key]


async def _ensure_hq_latent(client, bucket: str, prefix: str, run_root: Path, scene: int, trust_local: bool) -> str:
    """Give the predecessor the HQ latent of its confirmed take, or none.

    HQ Context Splice reads it by index and bypasses cleanly when it is
    missing, so this never fails a restore: a wrong latent is removed rather
    than kept, and bucket problems only mean no splice for this boundary.
    """
    path = _hq_latent_path(run_root, scene)
    if trust_local and path.is_file():
        return "local"
    latent_key, sidecar_key = _hq_keys(prefix, scene)
    try:
        response = await client.get_object(Bucket=bucket, Key=sidecar_key)
        body = b""
        async with response["Body"] as stream:
            while chunk := await stream.read(64 * 1024):
                body += chunk
        sidecar = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001 - absent or unreadable sidecar: no HQ latent.
        sidecar = None
    canonical = str((_local_segment(run_root, scene) or {}).get("checkpoint_sha256") or "")
    if not isinstance(sidecar, dict) or not canonical or sidecar.get("checkpoint_sha256") != canonical:
        if not trust_local:
            _drop_hq_latents(run_root, scene)
        return "absent"
    expected = str(sidecar.get("sha256") or "")
    if path.is_file() and _sha256(path) == expected:
        return "local"
    try:
        await _download_object(client, bucket, latent_key, path)
    except Exception:  # noqa: BLE001 - bypass instead of failing the scene.
        _drop_hq_latents(run_root, scene)
        return "absent"
    if _sha256(path) != expected:
        _drop_hq_latents(run_root, scene)
        return "absent"
    return "restored"


async def _restore_scene_from_bucket(
    client, bucket: str, prefix: str, run_root: Path, scene: int, include_checkpoint: bool,
) -> tuple[list[str], bool]:
    """Make one scene match its canonical bucket copy.

    Returns the downloaded keys and whether the local take was replaced.
    """
    destination = _metadata_path(run_root, scene)
    key = prefix + _relative_artifact(run_root, destination)
    # Validate the canonical document before it replaces anything local.
    incoming = destination.with_name(destination.name + f".remote-{uuid.uuid4().hex}")
    try:
        await _download_object(client, bucket, key, incoming)
        metadata = json.loads(incoming.read_text(encoding="utf-8"))
        segment = metadata.get("segment")
        if not isinstance(segment, dict) or int(segment.get("index", -1)) != scene:
            raise ValueError(f"Scene {scene} bucket metadata has an invalid segment record.")
        if str(metadata.get("run_name") or "") != run_root.name:
            raise ValueError(f"Scene {scene} bucket metadata belongs to another run.")
        replaced = _local_segment(run_root, scene) != segment
        os.replace(incoming, destination)
    finally:
        incoming.unlink(missing_ok=True)

    restored = [key]
    if replaced:
        _drop_hq_latents(run_root, scene)
    for field, hash_field in _ARTIFACT_FIELDS:
        if field == "checkpoint" and not include_checkpoint:
            continue
        address = segment.get(field)
        if not isinstance(address, str) or not address:
            continue
        path = _inside_run(run_root, address)
        expected = str(segment.get(hash_field) or "") if hash_field else ""
        if path.is_file() and (not expected or _sha256(path) == expected):
            continue
        key = prefix + _relative_artifact(run_root, path)
        await _download_object(client, bucket, key, path)
        if expected and _sha256(path) != expected:
            path.unlink(missing_ok=True)
            raise ValueError(f"Restored artifact failed SHA-256 verification: {key}")
        restored.append(key)
    return restored, replaced


async def _restore_scene(payload: ChainRequest) -> dict[str, Any]:
    run_root = _run_root(payload.run_name)
    predecessor = payload.scene - 1
    if predecessor <= 0:
        return {"ready": True, "source": "initial", "restored": []}
    remote_scenes = {scene for scene in payload.remote_scenes if 1 <= scene <= predecessor}

    # The normal case is same-worker continuation. Verify it locally and do
    # no bucket traffic if the predecessor pack is already complete.
    if not remote_scenes:
        try:
            local_files: list[tuple[Path, str | None]] = []
            for scene in range(1, predecessor + 1):
                local_files.extend(_scene_files(run_root, scene, include_checkpoint=(scene == predecessor)))
            _verify_local(local_files)
            if _hq_latent_path(run_root, predecessor).is_file():
                return {"ready": True, "source": "local", "restored": [], "hq": "local"}
            # Native pack is local but its HQ latent is not (the scene was
            # restored here before HQ latents were mirrored): fetch it if the
            # bucket has one for this take.
            hq = "absent"
            try:
                config = _s3_config()
                session = aiobotocore.session.get_session()
                async with session.create_client("s3", **_client_kwargs(config)) as client:
                    hq = await _ensure_hq_latent(
                        client, config["bucket_name"], _remote_prefix(payload.user_id, payload.run_name),
                        run_root, predecessor, trust_local=True)
            except Exception:  # noqa: BLE001 - the splice bypasses a missing latent.
                pass
            return {"ready": True, "source": "local", "restored": [], "hq": hq}
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            pass

    config = _s3_config()
    prefix = _remote_prefix(payload.user_id, payload.run_name)
    restored: list[str] = []
    replaced_scenes: list[int] = []
    from_bucket: set[int] = set()
    session = aiobotocore.session.get_session()
    async with session.create_client("s3", **_client_kwargs(config)) as client:
        for scene in range(1, predecessor + 1):
            include_checkpoint = scene == predecessor
            # A scene confirmed on this worker is authoritative locally: its
            # bucket mirror may still be uploading, and the bucket copy could
            # be the previous take. Only fall back to the bucket if it is
            # missing or damaged here.
            if scene not in remote_scenes:
                try:
                    _verify_local(_scene_files(run_root, scene, include_checkpoint=include_checkpoint))
                    continue
                except (FileNotFoundError, ValueError, json.JSONDecodeError):
                    pass
            keys, replaced = await _restore_scene_from_bucket(
                client, config["bucket_name"], prefix, run_root, scene, include_checkpoint)
            restored.extend(keys)
            from_bucket.add(scene)
            if replaced:
                replaced_scenes.append(scene)
        # Only the predecessor's HQ latent is ever spliced (by index).
        hq = await _ensure_hq_latent(
            client, config["bucket_name"], prefix, run_root, predecessor,
            trust_local=predecessor not in from_bucket)

    # Verify the complete selective-resume working set after all atomic moves.
    verified: list[tuple[Path, str | None]] = []
    for scene in range(1, predecessor + 1):
        verified.extend(_scene_files(run_root, scene, include_checkpoint=(scene == predecessor)))
    _verify_local(verified)
    return {
        "ready": True,
        "source": "r2" if restored else "local",
        "restored": restored,
        "replaced_scenes": replaced_scenes,
        "hq": hq,
    }


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_confirmed_scene(
    request: Request,
    x_proxima_chain_signature: str | None = Header(default=None),
):
    body = await _authorize(request, x_proxima_chain_signature)
    payload = ChainRequest.model_validate_json(body)
    _validate_request(payload.run_name, payload.issued_at)

    run_root = _run_root(payload.run_name)
    files = _scene_files(run_root, payload.scene, include_checkpoint=True)
    _verify_local(files)

    operation_id = uuid.uuid4().hex
    _operations[operation_id] = {
        "id": operation_id,
        "status": "queued",
        "run_name": payload.run_name,
        "scene": payload.scene,
        "created_at": int(time.time()),
    }
    task = asyncio.create_task(_upload_scene(operation_id, payload))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"accepted": True, "operation_id": operation_id}


@router.get("/operation/{operation_id}")
async def operation_status(
    operation_id: str,
    request: Request,
    issued_at: int,
    x_proxima_chain_signature: str | None = Header(default=None),
):
    await _authorize(request, x_proxima_chain_signature)
    _validate_request("probe", issued_at)
    operation = _operations.get(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Unknown chain operation.")
    return operation


@router.post("/probe")
async def probe_checkpoints(
    request: Request,
    x_proxima_chain_signature: str | None = Header(default=None),
):
    body = await _authorize(request, x_proxima_chain_signature)
    payload = ProbeRequest.model_validate_json(body)
    _validate_request(payload.run_name, payload.issued_at)
    run_root = _run_root(payload.run_name)
    available: dict[str, bool] = {}
    for scene in sorted(set(payload.scenes)):
        if scene < 1:
            continue
        try:
            files = _scene_files(run_root, scene, include_checkpoint=True)
            _verify_local(files)
            available[str(scene)] = True
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            available[str(scene)] = False
    return {"available": available}


@router.post("/restore")
async def restore_for_scene(
    request: Request,
    x_proxima_chain_signature: str | None = Header(default=None),
):
    body = await _authorize(request, x_proxima_chain_signature)
    payload = ChainRequest.model_validate_json(body)
    _validate_request(payload.run_name, payload.issued_at)
    try:
        return await _restore_scene(payload)
    except Exception as exc:  # noqa: BLE001 - turn storage failures into a safe gate.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
