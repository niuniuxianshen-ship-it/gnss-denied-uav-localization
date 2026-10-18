"""仅离线检查；拦截凭据读取、HTTP、socket；临时档案不是API结果。"""
import argparse
import ast
import contextlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("relation_comparison_runner", ROOT / "run_comparison.py")
r = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = r
spec.loader.exec_module(r)  # Only this new entry; no old batch is imported or executed.
ORIGINAL_PROJECT = r.PROJECT
CONFIG = r.read_json(ROOT / r.CONFIG)
SOURCE_BASELINE = {x["path"]: r.sha((ORIGINAL_PROJECT / x["path"]).read_bytes())
                   for x in CONFIG["source_snapshot"]}
EVIDENCE = {}


class OfflineChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="_offline_check_", dir=ROOT)
        self.temp_path = Path(self.temp.name).resolve()
        self.assertTrue(self.temp_path.is_relative_to(ROOT))
        self.addCleanup(self.temp.cleanup)
        self.project = self.temp_path / "project"
        self.root = self.project / "entry"
        self.root.mkdir(parents=True)
        for name in [r.CONFIG, r.MAP]:
            r.save(self.root / name, (ROOT / name).read_bytes())
        for record in CONFIG["source_snapshot"]:
            target = self.project / record["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            r.save(target, (ORIGINAL_PROJECT / record["path"]).read_bytes())
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(r, "PROJECT", self.project))
        self.credential_blocker = self.stack.enter_context(
            patch.object(r, "api_key", side_effect=AssertionError("离线禁止读取真实凭据")))
        self.transport_blocker = self.stack.enter_context(
            patch.object(r, "post_once", side_effect=AssertionError("离线禁止真实传输")))
        self.network_blocks = []
        for target, name in [(http.client, "HTTPSConnection"), (http.client, "HTTPConnection"),
                             (socket, "create_connection"), (socket, "getaddrinfo"),
                             (socket.socket, "connect"), (socket.socket, "connect_ex")]:
            blocker = self.stack.enter_context(patch.object(
                target, name, side_effect=AssertionError("离线禁止HTTP及socket")))
            self.network_blocks.append(blocker)

    def tearDown(self):
        self.assertEqual(self.credential_blocker.call_count, 0)
        self.assertEqual(self.transport_blocker.call_count, 0)
        for blocker in self.network_blocks:
            self.assertEqual(blocker.call_count, 0)

    def archive_hashes(self, directory):
        return {p.relative_to(directory).as_posix(): r.sha(p.read_bytes())
                for p in directory.rglob("*") if p.is_file()}

    def test_01_normal_check_order_and_one_user_message(self):
        before = self.archive_hashes(self.root)
        config, mapping, texts = r.check(self.root)
        self.assertEqual([x["task_id"] for x in mapping["tasks"]], r.IDS)
        self.assertEqual(len(set(r.IDS)), 8)
        for task in mapping["tasks"]:
            text = texts[task["task_id"]]
            self.assertEqual(text, (self.project / task["path"]).read_bytes())
            body = r.payload(config, text)
            self.assertEqual(set(body), set(config["settings"]) | {"messages"})
            self.assertEqual(body["messages"], [{"role": "user", "content": text.decode("utf-8")}])
            self.assertEqual(r.sha(r.encoded(body)), task["api_body_sha256"])
        self.assertEqual(before, self.archive_hashes(self.root))
        self.assertFalse((self.root / "运行档案").exists())
        EVIDENCE["order"] = r.IDS
        EVIDENCE["independent_user_messages"] = 8

    def test_02_changed_request_rejected_before_credentials(self):
        task = CONFIG["requests"][0]
        request = self.project / task["path"]
        request.write_bytes(request.read_bytes() + b"OFFLINE_TAMPER")
        changed = request.read_bytes()
        with self.assertRaisesRegex(ValueError, "输入字节或哈希不符"):
            r.execute(True, root=self.root)
        self.assertEqual(request.read_bytes(), changed)
        self.assertFalse((self.root / "运行档案").exists())
        EVIDENCE["request_hash_change"] = "rejected_before_credentials_or_HTTP"

    def test_03_missing_switch_and_freeze_rejected(self):
        with self.assertRaisesRegex(ValueError, "显式"):
            r.execute(False, root=self.root)
        with self.assertRaisesRegex(ValueError, "缺少本批冻结登记"):
            r.execute(True, root=self.root)
        self.assertFalse((self.root / "运行档案").exists())
        EVIDENCE["execution_gates"] = ["missing_switch_rejected", "missing_batch_freeze_rejected"]

    def test_04_existing_archive_refuses_resume_and_preserves_bytes(self):
        archive = self.root / "运行档案"
        archive.mkdir()
        r.save(archive / "离线保护标记.txt", "离线检查；不是API结果".encode("utf-8"))
        before = self.archive_hashes(archive)
        with self.assertRaisesRegex(ValueError, "已有首次档案"):
            r.execute(True, root=self.root)
        self.assertEqual(before, self.archive_hashes(archive))
        EVIDENCE["existing_archive"] = "rejected_and_bytes_unchanged"

    def test_05_first_technical_failure_stops_remaining_seven(self):
        # No frozen record is written. Only this isolated test bypasses that gate in memory.
        # Credential stub returns a non-secret literal; real reader/HTTP/socket remain blocked.
        with patch.object(r, "verify_freeze", return_value={"sha256": None,
                          "approval_reference": "OFFLINE_IN_MEMORY_STUB_NOT_APPROVAL"}), \
                patch.object(r, "api_key", return_value="OFFLINE_NO_CREDENTIAL"), \
                patch.object(r, "post_once", side_effect=OSError("offline injected first failure")) as sender:
            with self.assertRaisesRegex(ValueError, "阶段失败"):
                r.execute(True, root=self.root)
            self.assertEqual(sender.call_count, 1)
        archive = self.root / "运行档案"
        summary = r.read_json(archive / "summary.json")
        first = archive / r.IDS[0]
        meta = r.read_json(first / "metadata.json")
        self.assertEqual(summary["attempted"], r.IDS[:1])
        self.assertEqual(summary["completed"], [])
        self.assertEqual(summary["unexecuted"], r.IDS[1:])
        self.assertEqual(summary["record_kind"], "offline_check")
        self.assertEqual(summary["actual_api_calls"], 0)
        self.assertIsNone(summary["freeze_sha256"])
        self.assertEqual(meta["record_kind"], "offline_check")
        self.assertEqual(meta["technical_status"], "failed")
        self.assertFalse(meta["network_attempt_started"])
        self.assertIsNone(meta["returned_model"])
        self.assertIsNone(meta["usage"])
        self.assertTrue((first / "request.json").is_file())
        for missing in ["response.json", "output.txt"]:
            self.assertFalse((first / missing).exists())
            self.assertIn(missing, meta["missing_artifacts"])
        for task_id in r.IDS[1:]:
            self.assertFalse((archive / task_id).exists())
        self.assertFalse((self.root / "本批冻结登记.json").exists())
        before = self.archive_hashes(archive)
        with self.assertRaisesRegex(ValueError, "已有首次档案"):
            r.execute(True, root=self.root)
        self.assertEqual(before, self.archive_hashes(archive))
        EVIDENCE["failure_injection"] = {"record_kind": "offline_check", "stub_transport_calls": 1,
            "actual_api_calls": 0, "attempted": summary["attempted"],
            "unexecuted": summary["unexecuted"], "missing_artifacts": meta["missing_artifacts"],
            "frozen_record_created": False, "repeat_execution_rejected": True}

    def test_06_invalid_freeze_cannot_enable_execution(self):
        config, mapping, _ = r.check(self.root)
        path = self.root / "本批冻结登记.json"
        # All fixture records explicitly say NOT frozen; none claims approval.
        r.save_json(path, {"schema_version": 1, "batch_id": r.BATCH_ID,
                           "status": "offline_check_not_frozen", "execution_authorized": False})
        digest = r.sha(path.read_bytes())
        with self.assertRaisesRegex(ValueError, "freeze-sha256"):
            r.verify_freeze(self.root, config, mapping, None)
        with self.assertRaisesRegex(ValueError, "冻结登记哈希不符"):
            r.verify_freeze(self.root, config, mapping, "0" * 64)
        with self.assertRaisesRegex(ValueError, "未冻结或未授权"):
            r.verify_freeze(self.root, config, mapping, digest)
        path.write_bytes(r.encoded({"schema_version": 1, "batch_id": "UNRELATED_OFFLINE_FIXTURE",
                                   "status": "offline_check_not_frozen"}))
        with self.assertRaisesRegex(ValueError, "不是本批"):
            r.verify_freeze(self.root, config, mapping, r.sha(path.read_bytes()))
        EVIDENCE["freeze_negative_checks"] = ["missing_pin", "wrong_pin", "not_frozen", "wrong_batch"]

    def test_07_reused_functions_ast_equal_without_running_old_entry(self):
        old_text = (ORIGINAL_PROJECT / "首批三图API评审准备_v0.1/run_reviews.py").read_text(encoding="utf-8")
        new_text = (ROOT / "run_comparison.py").read_text(encoding="utf-8")
        def definitions(text):
            return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(text).body
                    if isinstance(n, ast.FunctionDef)}
        a, b = definitions(old_text), definitions(new_text)
        names = ["now", "sha", "encoded", "save", "save_json", "require", "read_json",
                 "project_path", "checked_source", "payload", "api_key", "post_once", "classify_completion"]
        for name in names:
            self.assertEqual(a[name], b[name])
        EVIDENCE["reused_functions_ast_equal"] = names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", help="可选：只在本目录独占新增JSON核查结果，已有文件拒绝覆盖")
    args = parser.parse_args()
    target = None
    if args.report:
        target = (ROOT / args.report).resolve()
        r.require(target.parent == ROOT and not target.exists(), "核查报告须为本目录未占用文件")
    initial_temps = set(ROOT.glob("_offline_check_*"))
    started = r.now()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(OfflineChecks))
    unchanged = []
    for rel, digest in SOURCE_BASELINE.items():
        record = {"path": rel, "before_sha256": digest,
                  "after_sha256": r.sha((ORIGINAL_PROJECT / rel).read_bytes())}
        record["unchanged"] = record["before_sha256"] == record["after_sha256"]
        unchanged.append(record)
    cleaned = not (set(ROOT.glob("_offline_check_*")) - initial_temps)
    report = {"record_kind": "offline_checks_only_not_API_results", "started_at": started,
              "ended_at": r.now(), "python_version": sys.version.split()[0],
              "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "all_passed": result.wasSuccessful() and all(x["unchanged"] for x in unchanged) and cleaned,
              "actual_api_calls": 0, "real_credentials_read": False,
              "HTTP_and_socket_blocked": True, "evidence": EVIDENCE,
              "input_hash_checks": unchanged, "temporary_check_directories_cleaned": cleaned,
              "real_execution_archive_created": (ROOT / "运行档案").exists(),
              "frozen_record_created": (ROOT / "本批冻结登记.json").exists(),
              "limitation": "未实测API或内容；首项失败测试以明确离线内存桩代替冻结和凭据；未创建已冻结登记"}
    if target:
        r.save_json(target, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
