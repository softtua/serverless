import asyncio
import hashlib
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

import video_chain_artifacts as artifacts


RUN = "storyboard_9_11_demo"


def _take(run: str, rev: str) -> tuple[dict[str, bytes], bytes]:
    """One scene-1 take: its artifact files and canonical metadata, keyed by run-relative path."""
    files = {
        f"segments/clip_0001.{rev}.mp4": b"segment-" + rev.encode(),
        f"checkpoints/clip_0001.{rev}.safetensors": b"checkpoint-" + rev.encode(),
        f"segments/clip_0001.{rev}.prompt.txt": b"prompt-" + rev.encode(),
        f"checkpoints/clip_0001.{rev}.json": b"{}",
    }
    segment = {"index": 1}
    for field, name in (("segment", "mp4"), ("checkpoint", "safetensors"), ("prompt_file", "prompt.txt")):
        relative = next(path for path in files if path.endswith(name))
        segment[field] = f"h3_chains/{run}/{relative}"
        segment[field + "_sha256"] = hashlib.sha256(files[relative]).hexdigest()
    segment["revision_metadata"] = f"h3_chains/{run}/checkpoints/clip_0001.{rev}.json"
    metadata = json.dumps({"run_name": run, "segment": segment}).encode("utf-8")
    return files, metadata


def _fixture(root: Path, run: str = RUN, rev: str = "rev") -> Path:
    run_root = root / "h3_chains" / run
    files, metadata = _take(run, rev)
    files["checkpoints/clip_0001.json"] = metadata
    for relative, body in files.items():
        path = run_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return run_root


class _Body:
    def __init__(self, data: bytes):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def read(self, _size: int) -> bytes:
        data, self.data = self.data, b""
        return data


class _Bucket:
    """Minimal async S3 client over a dict, recording every download."""

    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects
        self.downloads: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get_object(self, Bucket: str, Key: str):  # noqa: N803 - boto signature
        self.downloads.append(Key)
        return {"Body": _Body(self.objects[Key])}

    async def put_object(self, Bucket: str, Key: str, Body, ContentType: str = ""):  # noqa: N803
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.read()

    async def list_objects_v2(self, Bucket: str, Prefix: str):  # noqa: N803
        return {"Contents": [{"Key": key} for key in self.objects if key.startswith(Prefix)]}

    async def delete_objects(self, Bucket: str, Delete: dict):  # noqa: N803
        for item in Delete["Objects"]:
            self.objects.pop(item["Key"], None)


PREFIX = f"9/video-chains/{RUN}/artifacts/"


def _bucket_with(run: str, rev: str, user_id: int = 9) -> _Bucket:
    files, metadata = _take(run, rev)
    files["checkpoints/clip_0001.json"] = metadata
    prefix = f"{user_id}/video-chains/{run}/artifacts/"
    return _Bucket({prefix + relative: body for relative, body in files.items()})


def _with_hq(bucket: _Bucket, rev: str, latent: bytes = b"hq-latent") -> _Bucket:
    """Add a mirrored HQ latent bound to take `rev` of scene 1."""
    bucket.objects[PREFIX + "checkpoints_hq/clip_0001.safetensors"] = latent
    bucket.objects[PREFIX + "checkpoints_hq/clip_0001.json"] = json.dumps({
        "scene": 1,
        "sha256": hashlib.sha256(latent).hexdigest(),
        "checkpoint_sha256": hashlib.sha256(b"checkpoint-" + rev.encode()).hexdigest(),
    }).encode()
    return bucket


class VideoChainArtifactTests(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output_patch = patch.object(artifacts, "_OUTPUT_ROOT", self.root)
        self.output_patch.start()

    def tearDown(self):
        self.output_patch.stop()
        self.temporary.cleanup()

    def test_scene_files_preserve_native_revision_paths(self):
        run_root = _fixture(self.root)
        files = artifacts._scene_files(run_root, 1)
        relative = {path.relative_to(run_root).as_posix() for path, _expected in files}
        self.assertEqual(relative, {
            "checkpoints/clip_0001.json",
            "checkpoints/clip_0001.rev.json",
            "checkpoints/clip_0001.rev.safetensors",
            "segments/clip_0001.rev.mp4",
            "segments/clip_0001.rev.prompt.txt",
        })
        artifacts._verify_local(files)

    def test_scene_files_include_the_audio_overlap_sidecar(self):
        run_root = _fixture(self.root)
        overlap = run_root / "generated_audio" / "clip_0001.rev.overlap.safetensors"
        overlap.parent.mkdir(parents=True, exist_ok=True)
        overlap.write_bytes(b"overlap")
        canonical = run_root / "checkpoints" / "clip_0001.json"
        document = json.loads(canonical.read_text())
        document["segment"]["generated_audio_overlap"] = f"h3_chains/{RUN}/generated_audio/{overlap.name}"
        document["segment"]["generated_audio_overlap_sha256"] = hashlib.sha256(b"overlap").hexdigest()
        canonical.write_text(json.dumps(document))
        files = artifacts._scene_files(run_root, 1, include_checkpoint=False)
        self.assertIn(overlap, {path for path, _expected in files})
        artifacts._verify_local(files)

    def test_scene_files_reject_tampered_checkpoint(self):
        run_root = _fixture(self.root)
        (run_root / "checkpoints" / "clip_0001.rev.safetensors").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            artifacts._verify_local(artifacts._scene_files(run_root, 1))

    def test_inside_run_rejects_traversal(self):
        run_root = (self.root / "h3_chains" / "demo").resolve()
        with self.assertRaises(ValueError):
            artifacts._inside_run(run_root, "../secret")

    def _restore(self, bucket: _Bucket | None, remote_scenes: list[int]) -> dict:
        session = unittest.mock.MagicMock()
        if bucket is None:
            session.create_client.side_effect = AssertionError("bucket must not be contacted")
        else:
            session.create_client.return_value = bucket
        payload = artifacts.ChainRequest(
            user_id=9, run_name=RUN, scene=2, issued_at=0, remote_scenes=remote_scenes)
        with patch.object(artifacts, "_s3_config", return_value={
            "access_key_id": "a", "secret_access_key": "s", "endpoint_url": "e",
            "bucket_name": "b", "region": "auto",
        }), patch.object(artifacts.aiobotocore.session, "get_session", return_value=session):
            return asyncio.run(artifacts._restore_scene(payload))

    def _hq(self, run_root: Path) -> list[Path]:
        hq = run_root / "checkpoints_hq"
        hq.mkdir(parents=True, exist_ok=True)
        paths = [hq / "clip_0001.safetensors", hq / "clip_0001_chunk_0000.safetensors"]
        for path in paths:
            path.write_bytes(b"hq")
        return paths

    def test_restore_same_worker_stays_local(self):
        run_root = _fixture(self.root)
        hq = self._hq(run_root)
        result = self._restore(None, [])
        self.assertEqual(result["source"], "local")
        self.assertTrue(all(path.exists() for path in hq))

    def test_restore_replaces_unconfirmed_local_take(self):
        # Scene 1 was rendered here but confirmed as a different take elsewhere.
        run_root = _fixture(self.root, rev="unconfirmed")
        hq = self._hq(run_root)
        bucket = _bucket_with(RUN, "confirmed")
        result = self._restore(bucket, [1])

        self.assertEqual(result["replaced_scenes"], [1])
        segment = json.loads((run_root / "checkpoints" / "clip_0001.json").read_text())["segment"]
        self.assertTrue(segment["checkpoint"].endswith("clip_0001.confirmed.safetensors"))
        self.assertEqual(
            (run_root / "checkpoints" / "clip_0001.confirmed.safetensors").read_bytes(),
            b"checkpoint-confirmed")
        self.assertFalse(any(path.exists() for path in hq))
        self.assertEqual(list((run_root / "checkpoints").glob("*.remote-*")), [])

    def test_restore_keeps_matching_take_from_other_worker(self):
        # This worker already holds the confirmed take and its HQ latent
        # (e.g. restored earlier): only the two small documents are fetched.
        run_root = _fixture(self.root, rev="confirmed")
        hq = self._hq(run_root)
        bucket = _with_hq(_bucket_with(RUN, "confirmed"), "confirmed", latent=b"hq")
        result = self._restore(bucket, [1])

        self.assertEqual(result["replaced_scenes"], [])
        self.assertEqual(result["hq"], "local")
        self.assertEqual(bucket.downloads, [
            PREFIX + "checkpoints/clip_0001.json", PREFIX + "checkpoints_hq/clip_0001.json"])
        self.assertTrue(all(path.exists() for path in hq))

    def test_restore_fetches_the_hq_latent_of_the_confirmed_take(self):
        run_root = _fixture(self.root, rev="unconfirmed")
        self._hq(run_root)
        bucket = _with_hq(_bucket_with(RUN, "confirmed"), "confirmed")
        result = self._restore(bucket, [1])

        self.assertEqual(result["hq"], "restored")
        self.assertEqual((run_root / "checkpoints_hq" / "clip_0001.safetensors").read_bytes(), b"hq-latent")

    def test_restore_refuses_an_hq_latent_of_another_take(self):
        run_root = _fixture(self.root, rev="unconfirmed")
        hq = self._hq(run_root)
        bucket = _with_hq(_bucket_with(RUN, "confirmed"), "some-older-take")
        result = self._restore(bucket, [1])

        self.assertEqual(result["hq"], "absent")
        self.assertFalse(any(path.exists() for path in hq))

    def test_local_resume_fetches_a_missing_hq_latent(self):
        # Scene 1 is valid locally but was restored here before HQ latents
        # were mirrored, so the splice file is missing.
        run_root = _fixture(self.root, rev="confirmed")
        bucket = _with_hq(_bucket_with(RUN, "confirmed"), "confirmed")
        result = self._restore(bucket, [])

        self.assertEqual((result["source"], result["hq"]), ("local", "restored"))
        self.assertTrue((run_root / "checkpoints_hq" / "clip_0001.safetensors").is_file())

    def test_upload_mirrors_the_hq_latent_and_rotates_older_ones(self):
        run_root = _fixture(self.root, rev="confirmed")
        (run_root / "checkpoints_hq").mkdir()
        (run_root / "checkpoints_hq" / "clip_0001.safetensors").write_bytes(b"hq-latent")
        bucket = _Bucket({PREFIX + "checkpoints_hq/clip_0007.safetensors": b"old",
                          PREFIX + "checkpoints_hq/clip_0007.json": b"{}"})
        session = unittest.mock.MagicMock()
        session.create_client.return_value = bucket
        artifacts._operations["op"] = {"id": "op"}
        payload = artifacts.ChainRequest(user_id=9, run_name=RUN, scene=1, issued_at=0)
        with patch.object(artifacts, "_s3_config", return_value={
            "access_key_id": "a", "secret_access_key": "s", "endpoint_url": "e",
            "bucket_name": "b", "region": "auto",
        }), patch.object(artifacts.aiobotocore.session, "get_session", return_value=session):
            asyncio.run(artifacts._upload_scene("op", payload))

        self.assertEqual(artifacts._operations["op"]["status"], "completed", artifacts._operations["op"])
        hq_keys = sorted(key for key in bucket.objects if "/checkpoints_hq/" in key)
        self.assertEqual(hq_keys, [PREFIX + "checkpoints_hq/clip_0001.json",
                                   PREFIX + "checkpoints_hq/clip_0001.safetensors"])
        sidecar = json.loads(bucket.objects[PREFIX + "checkpoints_hq/clip_0001.json"])
        self.assertEqual(sidecar["checkpoint_sha256"], hashlib.sha256(b"checkpoint-confirmed").hexdigest())

    def test_restore_rejects_foreign_bucket_metadata(self):
        run_root = _fixture(self.root, rev="unconfirmed")
        bucket = _bucket_with("storyboard_other", "confirmed")
        bucket.objects = {
            key.replace("storyboard_other", RUN, 1): body for key, body in bucket.objects.items()}
        with self.assertRaisesRegex(ValueError, "another run"):
            self._restore(bucket, [1])
        # The local take is left untouched when the canonical copy is rejected.
        segment = json.loads((run_root / "checkpoints" / "clip_0001.json").read_text())["segment"]
        self.assertTrue(segment["checkpoint"].endswith("clip_0001.unconfirmed.safetensors"))


if __name__ == "__main__":
    unittest.main()
