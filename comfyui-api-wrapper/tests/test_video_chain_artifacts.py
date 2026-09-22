import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video_chain_artifacts as artifacts


def _fixture(root: Path, run: str = "storyboard_9_11_demo") -> Path:
    run_root = root / "h3_chains" / run
    segment = run_root / "segments" / "clip_0001.rev.mp4"
    checkpoint = run_root / "checkpoints" / "clip_0001.rev.safetensors"
    prompt = run_root / "segments" / "clip_0001.rev.prompt.txt"
    revision = run_root / "checkpoints" / "clip_0001.rev.json"
    for path, body in ((segment, b"segment"), (checkpoint, b"checkpoint"), (prompt, b"prompt")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    revision.write_text("{}", encoding="utf-8")
    canonical = run_root / "checkpoints" / "clip_0001.json"
    canonical.write_text(json.dumps({
        "run_name": run,
        "segment": {
            "index": 1,
            "segment": f"h3_chains/{run}/segments/{segment.name}",
            "segment_sha256": hashlib.sha256(b"segment").hexdigest(),
            "checkpoint": f"h3_chains/{run}/checkpoints/{checkpoint.name}",
            "checkpoint_sha256": hashlib.sha256(b"checkpoint").hexdigest(),
            "prompt_file": f"h3_chains/{run}/segments/{prompt.name}",
            "prompt_file_sha256": hashlib.sha256(b"prompt").hexdigest(),
            "revision_metadata": f"h3_chains/{run}/checkpoints/{revision.name}",
        },
    }), encoding="utf-8")
    return run_root


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

    def test_scene_files_reject_tampered_checkpoint(self):
        run_root = _fixture(self.root)
        (run_root / "checkpoints" / "clip_0001.rev.safetensors").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            artifacts._verify_local(artifacts._scene_files(run_root, 1))

    def test_inside_run_rejects_traversal(self):
        run_root = (self.root / "h3_chains" / "demo").resolve()
        with self.assertRaises(ValueError):
            artifacts._inside_run(run_root, "../secret")


if __name__ == "__main__":
    unittest.main()
