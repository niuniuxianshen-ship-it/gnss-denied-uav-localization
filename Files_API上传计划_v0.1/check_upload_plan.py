"""Local checks; temporary manifests and outputs are removed automatically."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "prepare_upload_plan.py"
PLAN = ROOT / "上传计划与任务关联.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class UploadPlanChecks(unittest.TestCase):
    def setUp(self):
        self.delivered = json.loads(PLAN.read_bytes().decode("utf-8"))
        self.manifest_path = Path(self.delivered["source_manifest"]["path"])
        self.manifest = json.loads(self.manifest_path.read_bytes().decode("utf-8-sig"))
        protected = [Path(record["path"]) for record in self.delivered["source_inputs"]]
        protected += [PLAN, ENTRY, Path(__file__).resolve()]
        self.before = {path: digest(path) for path in protected}
        self.temp = tempfile.TemporaryDirectory(prefix="._upload_check_", dir=ROOT)
        self.scratch = Path(self.temp.name).resolve()
        if self.scratch.parent != ROOT:
            raise RuntimeError("临时目录超出交付目录，停止清理和检查")
        self.addCleanup(self.temp.cleanup)

    def tearDown(self):
        self.assertEqual({path: digest(path) for path in self.before}, self.before)

    def invoke(self, manifest, name, output=None):
        source = self.scratch / (name + "_manifest.json")
        with source.open("xb") as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
        original = digest(source)
        output = output or self.scratch / (name + "_plan.json")
        result = subprocess.run([
            sys.executable, "-B", str(ENTRY), "--manifest", str(source),
            "--output", str(output)
        ], capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(digest(source), original)
        return result, output

    def test_01_normal_generation_and_associations(self):
        result, output = self.invoke(copy.deepcopy(self.manifest), "normal")
        self.assertEqual(result.returncode, 0, result.stderr)
        rebuilt = json.loads(output.read_bytes().decode("utf-8"))
        for plan in (rebuilt, self.delivered):
            self.assertEqual(plan["planned_upload_file_count"], 2)
            self.assertEqual(plan["associated_location_task_count"], 4)
            self.assertEqual(plan["actual_upload_count"], 0)
            self.assertEqual(plan["actual_model_call_count"], 0)
            uploads = {(u["batch_id"], u["image_id"]): u for u in plan["uploads"]}
            self.assertEqual(len(plan["uploads"]), 2)
            self.assertEqual(set(uploads), {("DEMO_BATCH_001", "DEMO01"),
                                            ("DEMO_BATCH_001", "DEMO02")})
            self.assertEqual(len(plan["tasks"]), 4)
            self.assertEqual({t["task_id"] for t in plan["tasks"]},
                             {t["task_id"] for t in self.manifest["tasks"]})
            original = {t["task_id"]: t for t in self.manifest["tasks"]}
            for task in plan["tasks"]:
                source = original[task["task_id"]]
                for field in ("batch_id", "image_id", "condition_id"):
                    self.assertEqual(task[field], source[field])
                upload = uploads[(task["batch_id"], task["image_id"])]
                self.assertEqual(upload["local_path"], source["image_input"]["path"])
                self.assertEqual(upload["sha256"], source["image_input"]["sha256"])
                self.assertEqual(digest(Path(upload["local_path"])), upload["sha256"])
                self.assertEqual(Path(upload["local_path"]).stat().st_size, upload["bytes"])
                self.assertEqual(upload["upload_filename"], task["image_id"] + ".png")
                self.assertIsNone(upload["file_id"])
                self.assertEqual(upload["status"], "待上传")
                self.assertEqual(task["text_path"], source["text_draft"]["path"])
                self.assertEqual(task["text_sha256"], source["text_draft"]["sha256"])
                self.assertEqual(digest(Path(task["text_path"])), task["text_sha256"])
                self.assertEqual(task["status"], "尚未调用")

    def test_02_same_image_identifier_different_file(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["tasks"][1]["image_input"] = copy.deepcopy(manifest["tasks"][2]["image_input"])
        result, output = self.invoke(manifest, "conflict")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("同批图片编号对应不同路径或哈希", result.stderr)
        self.assertFalse(output.exists())

    def test_03_hash_mismatch(self):
        for key in ("image_input", "text_draft"):
            with self.subTest(input_kind=key):
                manifest = copy.deepcopy(self.manifest)
                manifest["tasks"][0][key]["sha256"] = "0" * 64
                result, output = self.invoke(manifest, "hash_" + key)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("输入哈希不符", result.stderr)
                self.assertFalse(output.exists())

    def test_04_existing_output_refused(self):
        result, _ = self.invoke(copy.deepcopy(self.manifest), "existing", PLAN)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("输出已存在，拒绝覆盖", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
