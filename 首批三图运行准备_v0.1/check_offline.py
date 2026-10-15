"""Reproducible offline checks. No API worker, credentials or remote transport."""
import ast
import contextlib
import copy
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import run_batch as batch


ROOT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root):
    return {str(p.relative_to(root)): digest(p) for p in root.rglob("*") if p.is_file()}


class OfflineChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="_offline_check_", dir=ROOT)
        self.root = Path(self.temp.name)
        assert self.root.resolve().is_relative_to(ROOT.resolve())
        self.addCleanup(self.temp.cleanup)
        self.config = json.loads((ROOT / "配置草案.json").read_bytes())
        self.config_path = self.root / "配置草案.json"
        batch.save_json(self.config_path, self.config)
        # Fail rather than read a credential or make a connection, even accidentally.
        self.key_guard = mock.patch.object(batch, "api_key", side_effect=AssertionError("credential read"))
        self.transport_guard = mock.patch.object(batch, "post_once", side_effect=AssertionError("remote request"))
        self.socket_guard = mock.patch("socket.create_connection", side_effect=AssertionError("network"))
        for guard in (self.key_guard, self.transport_guard, self.socket_guard):
            guard.start()
            self.addCleanup(guard.stop)

    def test_combinations_order_and_exact_text(self):
        plan = batch.prepare(self.config_path, self.root)
        batch.check(self.config_path, self.root)
        self.assertEqual(len(plan["uploads"]), 3)
        self.assertEqual(len(plan["tasks"]), 6)
        self.assertEqual([(t["image_id"], t["condition_id"]) for t in plan["tasks"]], batch.ORDER)
        self.assertEqual(len({t["task_id"] for t in plan["tasks"]}), 6)
        self.assertEqual({(x["image_id"], c) for x in self.config["images"]
                          for c in self.config["conditions"]}, set(batch.ORDER))
        self.assertTrue(all(u["file_id"] is None for u in plan["uploads"]))
        for t in plan["tasks"]:
            raw = (self.root / t["template"]).read_bytes()
            value = json.loads(raw)
            self.assertEqual(set(value), set(batch.SETTINGS) | {"messages"})
            self.assertEqual(len(value["messages"]), 1)
            message = value["messages"][0]
            self.assertEqual(message["role"], "user")
            text, image = message["content"]
            expected = (batch.source(self.config["conditions"][t["condition_id"]])
                        + b"\n\n" + batch.source(self.config["appendix"]))
            self.assertEqual(text, {"type": "text", "text": expected.decode("utf-8")})
            self.assertEqual(text["text"].encode("utf-8"), expected)
            self.assertEqual(image, {"type": "file", "file_id": None})
            self.assertEqual(t["text_utf8_sha256"], batch.sha(expected))

    def test_missing_id_blocks_submission_before_credentials(self):
        plan = batch.prepare(self.config_path, self.root)
        with self.assertRaisesRegex(ValueError, "真实file_id"):
            batch.real_payload(batch.read_json(self.root / plan["tasks"][0]["template"]), None)
        with self.assertRaisesRegex(ValueError, "真实上传记录"):
            batch.execute(self.config_path, "run", True, self.root)
        self.assertFalse((self.root / "运行档案").exists())

    def test_no_execute_flags(self):
        batch.prepare(self.config_path, self.root)
        for stage in ("upload", "run"):
            with self.assertRaisesRegex(ValueError, "显式执行选项"):
                batch.execute(self.config_path, stage, False, self.root)
        self.assertFalse((self.root / "运行档案").exists())

    def test_refuse_overwrite_without_changes(self):
        batch.prepare(self.config_path, self.root)
        before = snapshot(self.root)
        with self.assertRaisesRegex(ValueError, "拒绝覆盖"):
            batch.prepare(self.config_path, self.root)
        self.assertEqual(before, snapshot(self.root))

    def test_bad_hash_missing_file_and_duplicate_id(self):
        for name in ("hash", "missing", "duplicate"):
            with self.subTest(case=name):
                root = self.root / name
                root.mkdir()
                config = copy.deepcopy(self.config)
                if name == "hash":
                    config["appendix"]["sha256"] = "0" * 64
                elif name == "missing":
                    config["conditions"]["BASE"]["path"] = "首批三图运行准备_v0.1/不存在的输入.txt"
                else:
                    config["images"][1]["image_id"] = "IMG001"
                path = root / "配置草案.json"
                batch.save_json(path, config)
                before = snapshot(root)
                with self.assertRaises((OSError, ValueError)):
                    batch.prepare(path, root)
                self.assertEqual(before, snapshot(root))

    def test_attempted_stage_cannot_execute_again(self):
        batch.prepare(self.config_path, self.root)
        for stage, name in (("upload", "上传"), ("run", "定位")):
            (self.root / "运行档案" / name).mkdir(parents=True)
            before = snapshot(self.root)
            with self.assertRaisesRegex(ValueError, "已尝试"):
                batch.execute(self.config_path, stage, True, self.root)
            self.assertEqual(before, snapshot(self.root))

    def test_multipart_contains_only_anonymous_file(self):
        plan = batch.prepare(self.config_path, self.root)
        from email.parser import BytesParser
        from email.policy import default
        for item in plan["uploads"]:
            raw = batch.source(item)
            body, content_type = batch.multipart(item, raw)
            mail = BytesParser(policy=default).parsebytes(
                ("MIME-Version: 1.0\r\nContent-Type: " + content_type
                 + "\r\n\r\n").encode("ascii") + body)
            parts = {p.get_param("name", header="Content-Disposition"): p for p in mail.iter_parts()}
            self.assertEqual(set(parts), {"purpose", "expires_after[anchor]",
                                         "expires_after[seconds]", "file"})
            self.assertEqual(parts["file"].get_filename(), item["image_id"] + ".png")
            self.assertEqual(parts["file"].get_payload(decode=True), raw)
            self.assertEqual(parts["expires_after[seconds]"].get_payload(decode=True), b"604800")

    def test_static_guards(self):
        text = (ROOT / "run_batch.py").read_text(encoding="utf-8")
        tree = ast.parse(text)
        require_names = {"source", "inputs", "payload", "prepare", "check", "post_once", "api_key"}
        self.assertTrue(require_names <= {n.name for n in tree.body if isinstance(n, ast.FunctionDef)})
        imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertFalse(imports & {"openai", "requests", "httpx", "subprocess", "keyring", "urllib"})
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        self.assertFalse(any(isinstance(n.func, ast.Name) and n.func.id in {"eval", "exec"} for n in calls))
        self.assertNotIn("worker.py", text)
        self.assertNotIn('"seed"', text)
        self.assertNotIn('"detail"', text)
        self.assertEqual(text.count('connection.request("POST"'), 1)


def main():
    cfg = json.loads((ROOT / "配置草案.json").read_bytes())
    records = cfg["images"] + list(cfg["conditions"].values()) + [cfg["appendix"]] + cfg["management_read_sources"]
    paths = {(batch.PROJECT / r["path"]).resolve() for r in records}
    before = {str(p): digest(p) for p in paths}
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(OfflineChecks))
    after = {str(p): digest(p) for p in paths}
    print(output.getvalue())
    print(json.dumps({"checked_at": datetime.now().astimezone().isoformat(),
                      "tests": result.testsRun, "passed": result.wasSuccessful(),
                      "input_files_checked": len(paths), "input_hashes_unchanged": before == after,
                      "remaining_temporary_directories": len(list(ROOT.glob("_offline_check_*"))),
                      "actual_uploads": 0, "actual_model_calls": 0}, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() and before == after else 1


if __name__ == "__main__":
    raise SystemExit(main())
