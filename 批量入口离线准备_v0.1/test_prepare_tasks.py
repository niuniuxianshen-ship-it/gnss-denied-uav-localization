"""Offline CLI checks. Temporary files stay here and are removed automatically."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
ENTRY = HERE / "prepare_tasks.py"
CONFIG = HERE / "\u6559\u5b66\u914d\u7f6e.json"
PLAN = HERE / "\u6559\u5b66\u4efb\u52a1\u6e05\u5355.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OfflinePreparationChecks(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG.read_text(encoding="utf-8"))
        self.protected = [CONFIG, PLAN, ENTRY]
        for section in ("images", "conditions"):
            for item in self.config[section]:
                absolute = (HERE / item["path"]).resolve()
                item["path"] = absolute.as_posix()
                self.protected.append(absolute)
        self.before = {path: digest(path) for path in self.protected}
        self.temp = tempfile.TemporaryDirectory(prefix="._offline_check_", dir=str(HERE))
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def invoke(self, config, output=None):
        path = self.folder / "config.json"
        with path.open("x", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
        before = digest(path)
        output = output or self.folder / "plan.json"
        result = subprocess.run(
            [sys.executable, "-B", str(ENTRY), "--config", str(path), "--output", str(output)],
            capture_output=True, text=True, encoding="utf-8", check=False
        )
        self.assertEqual(digest(path), before)
        self.assertEqual({path: digest(path) for path in self.protected}, self.before)
        return result, output

    def assert_rejected(self, config, message, output=None):
        existed = output is not None and output.exists()
        old_hash = digest(output) if existed else None
        result, path = self.invoke(config, output)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        if existed:
            self.assertEqual(digest(path), old_hash)
        else:
            self.assertFalse(path.exists())

    def test_01_all_four_pairs_and_status(self):
        result, path = self.invoke(copy.deepcopy(self.config))
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(path.read_text(encoding="utf-8"))
        tasks = plan["tasks"]
        expected = {("DEMO01", "BASE"), ("DEMO01", "ENHANCED"),
                    ("DEMO02", "BASE"), ("DEMO02", "ENHANCED")}
        self.assertEqual(len(tasks), 4)
        self.assertEqual({(t["image_id"], t["condition_id"]) for t in tasks}, expected)
        self.assertEqual(len({t["task_id"] for t in tasks}), 4)
        self.assertEqual(plan["planned_task_count"], 4)
        self.assertEqual(plan["model_call_count"], 0)
        for task in tasks:
            self.assertEqual(task["status"], "\u5c1a\u672a\u8c03\u7528")
            self.assertEqual(task["batch_id"], self.config["batch_id"])
            for key in ("image_input", "condition_input"):
                self.assertEqual(digest(Path(task[key]["path"])), task[key]["sha256"])

    def test_02_duplicate_image_id(self):
        config = copy.deepcopy(self.config)
        config["images"][1]["image_id"] = config["images"][0]["image_id"]
        self.assert_rejected(config, "duplicate image_id")

    def test_03_duplicate_condition_id(self):
        config = copy.deepcopy(self.config)
        config["conditions"][1]["condition_id"] = config["conditions"][0]["condition_id"]
        self.assert_rejected(config, "duplicate condition_id")

    def test_04_image_hash_mismatch(self):
        config = copy.deepcopy(self.config)
        config["images"][0]["sha256"] = "0" * 64
        self.assert_rejected(config, "SHA-256 mismatch")

    def test_05_condition_hash_mismatch(self):
        config = copy.deepcopy(self.config)
        config["conditions"][0]["sha256"] = "0" * 64
        self.assert_rejected(config, "SHA-256 mismatch")

    def test_06_existing_output(self):
        self.assert_rejected(copy.deepcopy(self.config), "output already exists", PLAN)

    def test_07_missing_input(self):
        config = copy.deepcopy(self.config)
        config["images"][0]["path"] = (self.folder / "missing.png").as_posix()
        self.assert_rejected(config, "input missing")

    def tearDown(self):
        self.assertEqual({path: digest(path) for path in self.protected}, self.before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
