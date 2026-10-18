"""三图六份首次定位的专用评审入口；默认离线，run --execute-review另需授权。"""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
CONFIG = "配置.json"
MAP = "管理关联表.json"
FREEZE_PATH = "首批三图运行准备_v0.1/批次冻结登记_v0.1.json"
FREEZE_SHA = "0661b1234f1d3bc4ae52f3209151c4d5c54f7b5984bb931034527378392e656b"
PLACEHOLDER = "在此放入完整待评文本".encode("utf-8")
START = "【待评文本开始】".encode("utf-8")
END = "【待评文本结束】".encode("utf-8")
REF_START = ("\n\n【参考材料：共同参考附录开始】\n"
             "以下为与定位相同的共同参考附录，仅供核查依据；不属于待评正文。\n").encode("utf-8")
REF_END = "\n【参考材料：共同参考附录结束】\n".encode("utf-8")
IDS = [f"J{i:02d}" for i in range(1, 7)]


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def save(path, raw):
    with Path(path).open("xb") as handle:
        handle.write(raw)


def save_json(path, value):
    save(path, encoded(value))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read_json(path):
    return json.loads(Path(path).read_bytes())


def project_path(name):
    path = (PROJECT / name).resolve()
    require(path.is_relative_to(PROJECT), "输入必须位于项目目录内")
    return path


def checked_source(record):
    raw = project_path(record["path"]).read_bytes()
    require(sha(raw) == record["sha256"] and len(raw) == record["bytes"],
            "输入字节或哈希不符：" + record["path"])
    return raw


def load_sources(root=ROOT):
    config = read_json(Path(root) / CONFIG)
    freeze_raw = project_path(FREEZE_PATH).read_bytes()
    require(sha(freeze_raw) == FREEZE_SHA == config["freeze_sha256"], "冻结登记哈希不符")
    freeze = json.loads(freeze_raw)
    evaluation = freeze["evaluation_plan"]
    require(config["settings"] == evaluation["settings"]
            and config["socket_timeout_seconds"] == evaluation["socket_timeout_seconds"]
            and config["client_retries"] == evaluation["client_retries"] == 0,
            "评审设置与冻结登记不符")
    require(config["base_url"] == "https://api.deepseek.com"
            and config["endpoint"] == "/chat/completions"
            and config["api_key_env"] == "DEEPSEEK_API_KEY"
            and config["system_message"] is None and config["execution_order"] == IDS,
            "地址、系统消息、凭据机制或顺序不符")
    require(config["batch_id"] == freeze["batch_id"], "批次编号不符")
    sources = config["source_snapshot"]
    indexed = {x["path"]: x for x in sources}
    require(len(indexed) == len(sources), "来源快照编号重复")
    for record in sources:
        checked_source(record)
    for record in freeze["evidence_files"]:
        checked_source(record)
    proposal = project_path(FREEZE_PATH).parent / freeze["proposal"]["path"]
    require(sha(proposal.read_bytes()) == freeze["proposal"]["sha256"], "冻结建议哈希不符")
    prompt_path = Path(evaluation["review_prompt_path"]).as_posix()
    prompt_record = indexed[prompt_path]
    require(prompt_record["sha256"] == evaluation["review_prompt_sha256"], "评审指令哈希不符")
    appendix_record = next(x for x in freeze["evidence_files"]
                           if x["path"] == "开发输入/参考附录正文.md")
    prompt, appendix = checked_source(prompt_record), checked_source(appendix_record)
    require(prompt.count(PLACEHOLDER) == 1, "末尾占位句必须唯一")
    prefix, suffix = prompt.split(PLACEHOLDER)
    require(prefix.rstrip().endswith(START) and suffix.lstrip().startswith(END)
            and suffix.lstrip()[len(END):].strip() == b"", "占位句不在末尾唯一待评区")
    appendix.decode("utf-8")
    items = []
    locations = freeze["first_location_requests"]
    require(len(locations) == 6 and [x["sequence"] for x in locations] == list(range(1, 7)),
            "冻结定位任务必须恰为有序六项")
    for judge_id, location in zip(IDS, locations):
        name = f"{location['sequence']:02d}_{location['image_id']}_{location['condition_id']}"
        directory = project_path(FREEZE_PATH).parent / "运行档案" / "定位" / name
        request_raw = (directory / "request.json").read_bytes()
        response_raw = (directory / "response.json").read_bytes()
        metadata = read_json(directory / "metadata.json")
        output_path = (directory / "output.txt").relative_to(PROJECT).as_posix()
        output_record = indexed[output_path]
        output = checked_source(output_record)
        require(sha(request_raw) == location["request_body_sha256"]
                == metadata["request_body_sha256"]
                and len(request_raw) == location["request_bytes"], "首次定位请求冻结哈希不符")
        require(sha(response_raw) == metadata["response_sha256"], "原始定位响应哈希不符")
        require(json.loads(response_raw)["choices"][0]["message"]["content"].encode("utf-8")
                == output, "首次定位完整正文与原响应不符")
        require(metadata["technical_status"] == "completed"
                and metadata["response_complete"] and metadata["http_status"] == 200
                and metadata["finish_reason"] == "stop"
                and not metadata.get("tool_call_detected"), "原定位技术状态不符")
        require(output.strip() and START not in output and END not in output
                and REF_START not in output and REF_END not in output,
                "完整正文为空或与材料边界冲突")
        # Raw byte replacement preserves all v0.3 instructions and examples.
        text = prefix + output + suffix + REF_START + appendix + REF_END
        body = encoded(payload(config, text))
        items.append({"judge_id": judge_id, "sequence": location["sequence"],
                      "source_task_id": location["task_id"], "image_id": location["image_id"],
                      "condition_id": location["condition_id"], "source_output": output_record,
                      "source_archive": directory.relative_to(PROJECT).as_posix(),
                      "frozen_location_request_sha256": sha(request_raw),
                      "location_response_sha256": sha(response_raw),
                      "request_file": f"请求/{judge_id}.txt", "request_text_sha256": sha(text),
                      "request_text_bytes": len(text), "api_body_sha256": sha(body),
                      "status_at_preparation": "尚未调用", "text": text})
    return config, prompt_record, appendix_record, items


def payload(config, text):
    # Only the assembled whitelisted text enters one independent user message.
    return {**config["settings"], "messages": [{"role": "user", "content": text.decode("utf-8")}]}


def public_item(item):
    return {k: v for k, v in item.items() if k != "text"}


def prepare(root=ROOT):
    root = Path(root).resolve()
    require(root.is_relative_to(ROOT), "产物必须位于本入口目录内")
    require(not (root / MAP).exists() and not (root / "请求").exists(), "准备产物已存在，拒绝覆盖")
    config, prompt, appendix, items = load_sources(root)
    mapping = {"created_at": now(), "status": "离线评审准备；没有API评审结果；不新增冻结决定",
               "batch_id": config["batch_id"], "freeze_sha256": FREEZE_SHA,
               "config_sha256": sha((root / CONFIG).read_bytes()),
               "runner_sha256": sha(Path(__file__).read_bytes()),
               "review_prompt": prompt, "appendix": appendix,
               "planned_review_requests": 6, "actual_review_calls_at_preparation": 0,
               "tasks": [public_item(item) for item in items]}
    (root / "请求").mkdir()
    for item in items:
        save(root / item["request_file"], item["text"])
    save_json(root / MAP, mapping)
    return mapping


def check(root=ROOT):
    root = Path(root)
    config, prompt, appendix, items = load_sources(root)
    mapping = read_json(root / MAP)
    require(mapping["config_sha256"] == sha((root / CONFIG).read_bytes())
            and mapping["runner_sha256"] == sha(Path(__file__).read_bytes()), "配置或程序已变化")
    require(mapping["freeze_sha256"] == FREEZE_SHA and mapping["batch_id"] == config["batch_id"]
            and mapping["review_prompt"] == prompt and mapping["appendix"] == appendix,
            "管理侧绑定不符")
    require(mapping["tasks"] == [public_item(item) for item in items]
            and mapping["planned_review_requests"] == 6
            and mapping["actual_review_calls_at_preparation"] == 0, "六项对应关系不符")
    require({p.name for p in (root / "请求").iterdir()} == {x + ".txt" for x in IDS},
            "请求文件重复、遗漏或存在额外材料")
    for item in items:
        raw = (root / item["request_file"]).read_bytes()
        require(raw == item["text"], "请求全文字节不符：" + item["judge_id"])
        require(sha(encoded(payload(config, raw))) == item["api_body_sha256"], "API载荷哈希不符")
    return config, mapping


def api_key():
    # Called only after an explicit execute flag and successful local preflight.
    import os
    key = os.environ.get("DEEPSEEK_API_KEY")
    require(isinstance(key, str) and key and key.isascii() and key == key.strip()
            and "\n" not in key and "\r" not in key,
            "未配置有效的DEEPSEEK_API_KEY环境变量")
    return key


def post_once(path, body, content_type, key, timeout, archive, meta):
    # stdlib http.client has no SDK retry, no redirect following, no proxy/env lookup.
    import http.client
    connection = http.client.HTTPSConnection("api.deepseek.com", timeout=timeout)
    try:
        meta["network_attempt_started"] = True
        connection.request("POST", path, body=body,
                           headers={"Authorization": "Bearer " + key,
                                    "Content-Type": content_type})
        response = connection.getresponse()
        meta["http_status"] = response.status
        meta["response_content_type"] = response.getheader("Content-Type")
        meta["response_complete"] = False
        with (archive / "response.json").open("xb") as handle:
            while True:
                try:
                    chunk = response.read(65536)
                except http.client.IncompleteRead as error:
                    handle.write(error.partial)
                    raise
                if not chunk:
                    break
                handle.write(chunk)
        meta["response_complete"] = True
        require(200 <= response.status < 300, "HTTP错误（详见原始响应）")
    finally:
        connection.close()


def classify_completion(result, archive, meta):
    require(isinstance(result, dict), "响应不是JSON对象")
    meta["returned_model"] = result.get("model")
    meta["usage"] = result.get("usage")
    choices = result.get("choices")
    require(isinstance(choices, list) and len(choices) == 1, "返回choices结构异常")
    choice = choices[0]
    message = choice.get("message", {})
    meta["finish_reason"] = choice.get("finish_reason")
    meta["refusal"] = message.get("refusal")
    meta["reasoning_content_present"] = bool(message.get("reasoning_content"))
    tools = bool(message.get("tool_calls") or message.get("function_call"))
    meta["tool_call_detected"] = tools
    text = message.get("content")
    if isinstance(text, str):
        save(archive / "output.txt", text.encode("utf-8"))
    require(not tools, "返回工具调用；停止后续请求")
    require(meta["finish_reason"] == "stop", "未正常结束或截断；停止后续请求")
    require(isinstance(text, str) and bool(text.strip()), "空正文；停止后续请求")
    require(isinstance(meta["returned_model"], str) and meta["returned_model"],
            "缺少实际返回模型标识")
    require(isinstance(meta["usage"], dict), "缺少用量记录")
    # Technical status only: do not parse, correct or adjudicate judgment status.


def attempt(directory, body, config, item, key, mapping):
    directory.mkdir()  # Durable first-attempt marker, also after interruption.
    start = time.monotonic()
    meta = {"judge_id": item["judge_id"], "started_at": now(), "technical_status": "failed",
            "settings": config["settings"], "requested_model": config["settings"]["model"],
            "returned_model": None, "finish_reason": None, "usage": None,
            "socket_timeout_seconds": config["socket_timeout_seconds"],
            "timeout_scope": "connect and each socket operation; not wall-clock deadline",
            "client_retries": 0, "network_attempt_started": False, "response_complete": False,
            "source_output_sha256": item["source_output"]["sha256"],
            "review_prompt_sha256": mapping["review_prompt"]["sha256"],
            "appendix_sha256": mapping["appendix"]["sha256"],
            "request_text_sha256": item["request_text_sha256"],
            "request_body_sha256": sha(body), "failure": None}
    save_json(directory / "attempt.json", {"started_at": meta["started_at"],
                                           "policy": "first attempt reserved; never resume or overwrite"})
    try:
        save(directory / "request.json", body)
        post_once(config["endpoint"], body, "application/json", key,
                  config["socket_timeout_seconds"], directory, meta)
        classify_completion(read_json(directory / "response.json"), directory, meta)
        meta["technical_status"] = "completed"
    except BaseException as error:
        meta["failure"] = {"type": type(error).__name__,
                           "reason": str(error) if isinstance(error, ValueError) else
                           "技术异常或中断；检查已保留档案；不重试"}
    finally:
        for name in ["request.json", "response.json", "output.txt"]:
            path = directory / name
            if path.is_file():
                meta[name.split(".")[0] + "_sha256"] = sha(path.read_bytes())
        meta["missing_artifacts"] = [n for n in ["request.json", "response.json", "output.txt"]
                                     if not (directory / n).is_file()]
        meta["ended_at"] = now()
        meta["elapsed_seconds"] = time.monotonic() - start
        save_json(directory / "metadata.json", meta)
    require(meta["technical_status"] == "completed", "技术失败；本轮停止且不可续跑")


def execute(authorized, root=ROOT):
    require(authorized, "实际评审须显式提供run --execute-review；本次不授权调用")
    root = Path(root).resolve()
    require(root.is_relative_to(ROOT), "档案必须位于本入口目录内")
    config, mapping = check(root)
    directory = root / "运行档案"
    require(not directory.exists(), "本轮已尝试或已有档案；拒绝重复执行或覆盖")
    key = api_key()
    directory.mkdir()
    summary = {"stage": "API_review", "started_at": now(), "status": "failed", "order": IDS,
               "attempted": [], "completed": [], "unexecuted": [], "failure": None,
               "settings": config["settings"], "config_sha256": mapping["config_sha256"],
               "runner_sha256": mapping["runner_sha256"], "freeze_sha256": FREEZE_SHA,
               "management_map_sha256": sha((root / MAP).read_bytes()), "client_retries": 0}
    save_json(directory / "stage_start.json", summary)
    try:
        for item in mapping["tasks"]:
            check(root)
            text = (root / item["request_file"]).read_bytes()
            body = encoded(payload(config, text))
            require(sha(body) == item["api_body_sha256"], "发送前正文哈希不符")
            summary["attempted"].append(item["judge_id"])
            print("即将执行一次评审请求：" + item["judge_id"], flush=True)
            attempt(directory / item["judge_id"], body, config, item, key, mapping)
            summary["completed"].append(item["judge_id"])
        summary["status"] = "completed"
    except BaseException as error:
        summary["failure"] = {"type": type(error).__name__,
                              "reason": str(error) if isinstance(error, ValueError) else
                              "技术异常或中断；阶段停止，不重试"}
    finally:
        summary["ended_at"] = now()
        summary["unexecuted"] = [name for name in IDS if name not in summary["attempted"]]
        save_json(directory / "summary.json", summary)
    require(summary["status"] == "completed", "阶段失败；保留首次档案，不可自动续跑")


def self_check():
    """Local tests only; credential reader and real transport are blocked."""
    import ast
    import tempfile
    import unittest
    from unittest.mock import patch
    import socket

    module = sys.modules[__name__]
    baseline = read_json(ROOT / CONFIG)["source_snapshot"]

    class OfflineChecks(unittest.TestCase):
        def setUp(self):
            self.temp = tempfile.TemporaryDirectory(prefix="_offline_check_", dir=ROOT)
            self.root = Path(self.temp.name).resolve()
            self.assertTrue(self.root.is_relative_to(ROOT))
            self.addCleanup(self.temp.cleanup)
            save(self.root / CONFIG, (ROOT / CONFIG).read_bytes())
            for target, name in [(module, "api_key"), (module, "post_once"), (socket, "create_connection")]:
                blocker = patch.object(target, name, side_effect=AssertionError("离线禁止凭据及网络"))
                blocker.start()
                self.addCleanup(blocker.stop)

        def test_six_full_texts_and_whitelist(self):
            prepare(self.root)
            config, mapping = check(self.root)
            self.assertEqual([t["judge_id"] for t in mapping["tasks"]], IDS)
            self.assertEqual(len({t["source_output"]["path"] for t in mapping["tasks"]}), 6)
            prompt = checked_source(mapping["review_prompt"])
            appendix = checked_source(mapping["appendix"])
            prefix, suffix = prompt.split(PLACEHOLDER)
            for item in mapping["tasks"]:
                output = checked_source(item["source_output"])
                text = (self.root / item["request_file"]).read_bytes()
                self.assertEqual(text, prefix + output + suffix + REF_START + appendix + REF_END)
                body = payload(config, text)
                self.assertEqual(set(body), set(config["settings"]) | {"messages"})
                self.assertEqual(body["messages"], [{"role": "user", "content": text.decode("utf-8")}])
                self.assertEqual(text[:len(prefix)] + PLACEHOLDER + suffix, prompt)
                self.assertNotIn(item["judge_id"].encode(), text)
                self.assertNotIn(item["source_output"]["path"].encode(), text)

        def test_hash_mismatch(self):
            cfg = read_json(self.root / CONFIG)
            cfg["source_snapshot"][0]["sha256"] = "0" * 64
            (self.root / CONFIG).write_bytes(encoded(cfg))
            with self.assertRaisesRegex(ValueError, "哈希不符"):
                prepare(self.root)
            self.assertFalse((self.root / "请求").exists())

        def test_no_overwrite(self):
            prepare(self.root)
            before = {p.name: sha(p.read_bytes()) for p in self.root.rglob("*") if p.is_file()}
            with self.assertRaisesRegex(ValueError, "拒绝覆盖"):
                prepare(self.root)
            self.assertEqual(before, {p.name: sha(p.read_bytes()) for p in self.root.rglob("*") if p.is_file()})

        def test_no_implicit_execution(self):
            prepare(self.root)
            with self.assertRaisesRegex(ValueError, "显式"):
                execute(False, self.root)
            self.assertFalse((self.root / "运行档案").exists())

        def test_existing_first_records(self):
            prepare(self.root)
            archive = self.root / "运行档案"
            archive.mkdir()
            save(archive / "preserve.txt", b"first-record-marker")
            with self.assertRaisesRegex(ValueError, "拒绝重复"):
                execute(True, self.root)
            self.assertEqual((archive / "preserve.txt").read_bytes(), b"first-record-marker")

        def test_request_tamper_rejected(self):
            prepare(self.root)
            with (self.root / "请求/J02.txt").open("ab") as handle:
                handle.write(b"EXTRA_MANAGEMENT_TEXT")
            with self.assertRaisesRegex(ValueError, "请求全文字节不符"):
                check(self.root)

        def test_local_failure_stops_first_attempt(self):
            # No server response or judgment is fabricated: inject one local I/O failure.
            prepare(self.root)
            with patch.object(module, "api_key", return_value="OFFLINE_NO_CREDENTIAL"), \
                    patch.object(module, "post_once", side_effect=OSError("offline injected failure")) as sender:
                with self.assertRaisesRegex(ValueError, "阶段失败"):
                    execute(True, self.root)
                self.assertEqual(sender.call_count, 1)
            summary = read_json(self.root / "运行档案/summary.json")
            self.assertEqual(summary["attempted"], ["J01"])
            self.assertEqual(summary["unexecuted"], IDS[1:])
            self.assertEqual(summary["completed"], [])
            first = self.root / "运行档案/J01"
            self.assertTrue((first / "request.json").is_file())
            self.assertFalse((first / "response.json").exists())
            self.assertFalse((first / "output.txt").exists())
            before = sha((first / "metadata.json").read_bytes())
            with self.assertRaisesRegex(ValueError, "拒绝重复"):
                execute(True, self.root)
            self.assertEqual(sha((first / "metadata.json").read_bytes()), before)

        def test_reused_transport_and_classifier(self):
            old = project_path("首批三图运行准备_v0.1/run_batch.py").read_text(encoding="utf-8")
            new = Path(__file__).read_text(encoding="utf-8")
            def definitions(source):
                return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(source).body
                        if isinstance(n, ast.FunctionDef)}
            old_defs, new_defs = definitions(old), definitions(new)
            for name in ["api_key", "post_once", "classify_completion", "encoded", "save", "save_json", "require"]:
                self.assertEqual(new_defs[name], old_defs[name])

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(OfflineChecks))
    for record in baseline:
        checked_source(record)
    require(result.wasSuccessful(), "离线检查未通过")
    require(not list(ROOT.glob("_offline_check_*")), "临时检查目录未清理")
    print(json.dumps({"checks_passed": result.testsRun, "source_files_unchanged": len(baseline),
                      "actual_api_calls": 0, "credentials_read": False, "checked_at": now()}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="prepare", choices=["prepare", "check", "self-check", "run"])
    parser.add_argument("--execute-review", action="store_true")
    args = parser.parse_args()
    try:
        require(not args.execute_review or args.action == "run", "执行开关仅用于run")
        if args.action == "prepare":
            prepare()
        elif args.action == "check":
            check()
        elif args.action == "self-check":
            self_check()
        else:
            execute(args.execute_review)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2
    print("完成；仅技术检查，不代表评审内容通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
