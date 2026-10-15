"""Repeatable offline checks; temporary fixtures stay inside this directory."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
DRAFTS = HERE / "教学文本草案"
MANAGEMENT = DRAFTS / "组装清单_管理用.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AssemblyChecks(unittest.TestCase):
    def setUp(self):
        self.management = json.loads(MANAGEMENT.read_bytes().decode("utf-8"))
        self.manifest = Path(self.management["source_manifest"]["path"])
        self.appendix = Path(self.management["appendix_input"]["path"])
        self.plan = json.loads(self.manifest.read_bytes().decode("utf-8"))
        paths = [Path(s["path"]) for s in self.management["source_inputs"]]
        paths += [MANAGEMENT, HERE / "assemble_texts.py"]
        paths += [Path(t["text_draft"]["path"]) for t in self.management["tasks"]]
        self.protected = {p: sha(p) for p in paths}
        self.temp = tempfile.TemporaryDirectory(prefix="._assembly_check_", dir=HERE)
        self.scratch = Path(self.temp.name).resolve()
        if self.scratch.parent != HERE:
            self.temp.cleanup()
            raise RuntimeError("临时目录超出交付目录")
        self.addCleanup(self.temp.cleanup)

    def tearDown(self):
        for path, original_hash in self.protected.items():
            self.assertEqual(sha(path), original_hash, str(path))

    def run_entry(self, manifest, output):
        return subprocess.run([
            sys.executable, "-B", str(HERE / "assemble_texts.py"),
            "--manifest", str(manifest), "--appendix", str(self.appendix),
            "--output-dir", str(output)
        ], capture_output=True, text=True, encoding="utf-8", check=False)

    def test_normal_assembly_and_exact_bodies(self):
        output = self.scratch / "normal"
        result = self.run_entry(self.manifest, output)
        self.assertEqual(result.returncode, 0, result.stderr)
        rebuilt = json.loads(
            (output / "组装清单_管理用.json").read_bytes().decode("utf-8"))
        self.assertEqual(len(rebuilt["tasks"]), 4)
        self.assertEqual(rebuilt["text_draft_count"], 4)
        expected_pairs = {("DEMO01", "BASE"), ("DEMO01", "ENHANCED"),
                          ("DEMO02", "BASE"), ("DEMO02", "ENHANCED")}
        self.assertEqual({(t["image_id"], t["condition_id"])
                          for t in rebuilt["tasks"]}, expected_pairs)
        self.assertEqual([t["task_id"] for t in rebuilt["tasks"]],
                         [t["task_id"] for t in self.plan["tasks"]])
        self.assertEqual(len({t["task_id"] for t in rebuilt["tasks"]}), 4)
        appendix = self.appendix.read_bytes()
        for original, rebuilt_task, delivered in zip(
                self.plan["tasks"], rebuilt["tasks"], self.management["tasks"]):
            prompt = Path(original["condition_input"]["path"]).read_bytes()
            expected = prompt + b"\n\n" + appendix
            for record in (rebuilt_task, delivered):
                text_path = Path(record["text_draft"]["path"])
                self.assertEqual(text_path.read_bytes(), expected)
                self.assertEqual(sha(text_path), record["text_draft"]["sha256"])
                self.assertEqual(record["appendix_input"]["sha256"], sha(self.appendix))
                self.assertEqual(record["status"], "尚未调用")

    def check_hash_failure(self, input_key):
        bad = copy.deepcopy(self.plan)
        bad["tasks"][0][input_key]["sha256"] = "0" * 64
        manifest = self.scratch / "bad_manifest.json"
        manifest.write_bytes(json.dumps(bad, ensure_ascii=False).encode("utf-8"))
        output = self.scratch / "must_not_exist"
        result = self.run_entry(manifest, output)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("输入哈希不符", result.stderr)
        self.assertFalse(output.exists())

    def test_image_hash_mismatch(self):
        self.check_hash_failure("image_input")

    def test_prompt_hash_mismatch(self):
        self.check_hash_failure("condition_input")

    def test_existing_output_refused(self):
        result = self.run_entry(self.manifest, DRAFTS)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("输出目录已存在", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
