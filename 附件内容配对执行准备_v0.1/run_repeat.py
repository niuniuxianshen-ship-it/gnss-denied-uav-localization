"""附件内容配对 v0.4检查；默认仅离线check，真实调用须另行授权和冻结。"""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
BATCH = "ATTACHMENT_CONTENT_PAIR_V04_001"
ORDER = [f"REP_{material}_V04_{repeat:02d}"
         for repeat in range(1, 4) for material in ("A", "B")]
SOURCE_CONFIG = '评审重复检查准备_v0.1/配置.json'
SOURCE_REQUESTS = {'A': '附件内容配对离线组装_v0.1/甲版完整请求.txt', 'B': '附件内容配对离线组装_v0.1/乙版完整请求.txt'}
EXTRA_SOURCES = ('附件内容配对离线组装_v0.1/管理说明.md', '附件内容配对离线组装_v0.1/结果判读卡.md', '评审重复检查准备_v0.1/run_repeat.py', '评审重复检查准备_v0.1/offline_checks.py')
API_FIELDS = ("base_url", "endpoint", "settings", "socket_timeout_seconds",
              "timeout_scope", "client_retries", "system_message")

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


def payload(config, text):
    # Only the assembled whitelisted text enters one independent user message.
    return {**config["settings"], "messages": [{"role": "user", "content": text.decode("utf-8")}]}


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


def existing_key():
    # Called only after explicit execution, frozen checks and first-archive checks.
    names = ["DEEPSEEK_API_KEY", "WHALE_DEEPSEEK_BALANCE_KEY"]
    def valid(value):
        return isinstance(value, str) and bool(value) and all(33 <= ord(c) <= 126 for c in value)
    for name in names:
        value = os.environ.get(name)
        if valid(value):
            return value
    if os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as environment:
            for name in names:
                try:
                    value = winreg.QueryValueEx(environment, name)[0]
                except FileNotFoundError:
                    continue
                if valid(value):
                    return value
    raise ValueError("未取得有效的既有命名凭据；没有发起请求")


def load_json(path):
    return json.loads(Path(path).read_bytes())


def local_root(root):
    root = Path(root).resolve()
    require(root.is_relative_to(ROOT), "检查和档案目录必须在本入口内")
    return root


def checked_project_source(record):
    path = (PROJECT / record["path"]).resolve()
    require(path.is_relative_to(PROJECT), "来源路径越出项目")
    raw = path.read_bytes()
    require(sha(raw) == record["sha256"] and len(raw) == record["bytes"],
            "历史来源哈希或字节数变化：" + record["path"])
    return raw


def check(root=ROOT):
    """Only read local files. No credentials, transport or old-runner imports."""
    root = local_root(root)
    config_raw = (root / "配置.json").read_bytes()
    config = json.loads(config_raw)
    mapping = load_json(root / "六项任务关联_管理用.json")
    require(config["schema_version"] == mapping["schema_version"] == 1
            and config["batch_id"] == mapping["batch_id"] == BATCH, "批次不符")
    require(mapping["config_sha256"] == sha(config_raw)
            and mapping["runner_sha256"] == sha(Path(__file__).read_bytes()),
            "配置或程序哈希不符")
    require(config["execution_order"] == ORDER
            and config["planned_first_requests"] == 6
            and config["actual_api_calls_at_preparation"] == 0
            and config["preparation_status"] == "未冻结；尚未调用", "准备状态或顺序不符")
    records = config["source_snapshot"]
    indexed = {record["path"]: record for record in records}
    require(len(records) == len(indexed) == 7
            and set(indexed) == {SOURCE_CONFIG, *SOURCE_REQUESTS.values(), *EXTRA_SOURCES}, "来源白名单不符")
    sources = {name: checked_project_source(record) for name, record in indexed.items()}
    original = json.loads(sources[SOURCE_CONFIG])
    require(all(config[name] == original[name] for name in API_FIELDS),
            "拟用设置与指定来源不同")
    require(config["base_url"] == "https://api.deepseek.com"
            and config["endpoint"] == "/chat/completions"
            and config["system_message"] is None and config["client_retries"] == 0,
            "禁止变更服务、系统消息或重试")
    require(config["settings"] == {
        "model": "deepseek-flash", "thinking": {"type": "disabled"},
        "temperature": 0, "max_tokens": 8192, "stream": False,
        "response_format": {"type": "text"}, "tool_choice": "none"
    }, "固定请求设置不符")
    require(config["execution_archive_directory"] == "运行档案"
            and config["freeze_record_file"] == "本批冻结登记.json", "档案路径不符")
    texts = {}
    for material in ("A", "B"):
        copy = root / "请求副本" / f"请求_{material}_V04.txt"
        raw = copy.read_bytes()
        require(raw == sources[SOURCE_REQUESTS[material]], "请求副本与历史原件不同：" + material)
        texts[material] = raw
    expected = []
    for sequence, task_id in enumerate(ORDER, 1):
        material = task_id.split("_")[1]
        body = encoded(payload(config, texts[material]))
        expected.append({
            "sequence": sequence, "task_id": task_id, "material_id": material,
            "repeat_index": (sequence + 1) // 2, "prompt_version": "v0.4",
            "request_path": f"请求副本/请求_{material}_V04.txt",
            "original_request_path": SOURCE_REQUESTS[material],
            "request_bytes": len(texts[material]), "request_text_sha256": sha(texts[material]),
            "api_body_sha256": sha(body), "status_at_preparation": "尚未调用"
        })
    require(mapping["planned_first_requests"] == 6
            and mapping["actual_api_calls_at_preparation"] == 0
            and mapping["tasks"] == expected, "六项编号、顺序或请求关联不符")
    bodies = {task["task_id"]: encoded(payload(config, texts[task["material_id"]]))
              for task in expected}
    return config, mapping, bodies


def freeze_bindings(root, mapping):
    root = local_root(root)
    return {
        "runner_sha256": sha(Path(__file__).read_bytes()),
        "config_sha256": sha((root / "配置.json").read_bytes()),
        "mapping_sha256": sha((root / "六项任务关联_管理用.json").read_bytes()),
        "usage_and_design_sha256": sha((root / "使用与核查说明.md").read_bytes()),
        "execution_order": ORDER,
        "request_text_sha256": {t["task_id"]: t["request_text_sha256"] for t in mapping["tasks"]},
        "api_body_sha256": {t["task_id"]: t["api_body_sha256"] for t in mapping["tasks"]}
    }


def verify_freeze(root, mapping, expected_sha):
    require(isinstance(expected_sha, str) and re.fullmatch(r"[a-f0-9]{64}", expected_sha),
            "须显式指定本批真实冻结SHA-256；本轮未冻结")
    raw = (local_root(root) / "本批冻结登记.json").read_bytes()
    require(sha(raw) == expected_sha, "冻结登记哈希不符")
    record = json.loads(raw)
    require(record.get("schema_version") == 1 and record.get("batch_id") == BATCH
            and record.get("status") == "frozen"
            and record.get("execution_authorized") is True
            and record.get("maximum_first_requests") == 6
            and record.get("bindings") == freeze_bindings(root, mapping), "不是本批有效冻结")
    for key in ("approved_by", "approved_at", "approval_reference"):
        require(isinstance(record.get(key), str) and record[key].strip(), "缺少真实审批依据")
    require(datetime.fromisoformat(record["approved_at"]).utcoffset() is not None,
            "审批时间必须带时区")
    return {"sha256": sha(raw)}


def attempt(directory, body, config, item, key, kind):
    directory.mkdir()  # Exclusive first-attempt marker, including interrupted attempts.
    started = time.monotonic()
    meta = {
        "record_kind": kind, "batch_id": BATCH, "task_id": item["task_id"],
        "material_id": item["material_id"], "repeat_index": item["repeat_index"],
        "started_at": now(), "technical_status": "failed",
        "requested_model": config["settings"]["model"], "settings": config["settings"],
        "returned_model": None, "finish_reason": None, "usage": None,
        "network_attempt_started": False, "response_complete": False,
        "socket_timeout_seconds": config["socket_timeout_seconds"],
        "timeout_scope": config["timeout_scope"], "client_retries": 0,
        "request_text_sha256": item["request_text_sha256"],
        "request_body_sha256": sha(body), "failure": None
    }
    if kind == "offline_check":
        meta.update(actual_api_calls=0, notice="内存离线桩；不是API结果")
    save_json(directory / "attempt.json", {
        "record_kind": kind, "task_id": item["task_id"], "started_at": meta["started_at"],
        "policy": "one attempt; never resume or overwrite"
    })
    try:
        save(directory / "request.json", body)
        post_once(config["endpoint"], body, "application/json", key,
                  config["socket_timeout_seconds"], directory, meta)
        classify_completion(load_json(directory / "response.json"), directory, meta)
        meta["technical_status"] = "completed"
    except BaseException as error:
        meta["failure"] = {
            "type": type(error).__name__,
            "reason": str(error) if isinstance(error, ValueError) else "技术异常或中断；不重试"
        }
    finally:
        for name in ("request.json", "response.json", "output.txt"):
            path = directory / name
            if path.is_file():
                meta[name.split(".")[0] + "_sha256"] = sha(path.read_bytes())
        meta["missing_artifacts"] = [
            name for name in ("request.json", "response.json", "output.txt")
            if not (directory / name).is_file()
        ]
        meta["ended_at"] = now()
        meta["elapsed_seconds"] = time.monotonic() - started
        save_json(directory / "metadata.json", meta)
    require(meta["technical_status"] == "completed" and not meta["missing_artifacts"],
            "技术失败或必要档案缺失；停止后续，不重试")


def execute(authorized, freeze_sha=None, root=ROOT):
    require(authorized, "真实执行须显式run --execute-review")
    root = local_root(root)
    config, mapping, bodies = check(root)
    directory = root / "运行档案"
    require(not directory.exists() and not directory.is_symlink(),
            "已有首次档案；禁止重复执行或续跑")
    freeze = verify_freeze(root, mapping, freeze_sha)
    directory.mkdir()  # Reserve before credentials or transport; concurrent starts cannot both run.
    kind = "first_api_attempt" if root == ROOT else "offline_check"
    summary = {
        "record_kind": kind, "batch_id": BATCH, "started_at": now(), "status": "failed",
        "execution_order": ORDER, "attempted": [], "completed": [], "unexecuted": [],
        "failure": None, "freeze_sha256": freeze["sha256"],
        "settings": config["settings"], "client_retries": 0
    }
    if kind == "offline_check":
        summary.update(actual_api_calls=0, notice="内存离线桩；无实际冻结或API调用")
    save_json(directory / "stage_start.json", summary)
    try:
        key = existing_key()  # Only after explicit flag, local checks, freeze and archive reservation.
        for item in mapping["tasks"]:
            current_config, current_mapping, current_bodies = check(root)
            require(current_config == config and current_mapping == mapping,
                    "发送前输入或设置变化")
            verify_freeze(root, mapping, freeze_sha)
            body = current_bodies[item["task_id"]]
            require(sha(body) == item["api_body_sha256"], "发送前载荷变化")
            summary["attempted"].append(item["task_id"])
            attempt(directory / item["task_id"], body, config, item, key, kind)
            summary["completed"].append(item["task_id"])
        summary["status"] = "completed"
    except BaseException as error:
        summary["failure"] = {
            "type": type(error).__name__,
            "reason": str(error) if isinstance(error, ValueError) else "技术异常或中断；本批停止"
        }
    finally:
        summary["ended_at"] = now()
        summary["unexecuted"] = [task for task in ORDER if task not in summary["attempted"]]
        summary["task_status"] = {
            task: ("completed" if task in summary["completed"] else
                   "failed" if task in summary["attempted"] else "未执行")
            for task in ORDER
        }
        save_json(directory / "summary.json", summary)
    require(summary["status"] == "completed", "本批技术失败；档案保留，禁止续跑")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="check", choices=("check", "run"))
    parser.add_argument("--execute-review", action="store_true")
    parser.add_argument("--freeze-sha256")
    args = parser.parse_args()
    try:
        require(not args.execute_review or args.action == "run", "执行开关仅用于run")
        if args.action == "run":
            execute(args.execute_review, args.freeze_sha256)
            result = {"technical_status": "completed", "content_adjudicated": False}
        else:
            config, mapping, _ = check()
            if args.freeze_sha256:
                verify_freeze(ROOT, mapping, args.freeze_sha256)
            result = {
                "status": "offline_check_passed", "planned_requests": 6,
                "execution_order": ORDER, "actual_api_calls_in_this_check": 0,
                "freeze_verified": bool(args.freeze_sha256),
                "existing_archive": (ROOT / "运行档案").exists() or (ROOT / "运行档案").is_symlink(),
                "bindings_for_future_review_only": freeze_bindings(ROOT, mapping)
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
