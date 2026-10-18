"""关系记录新材料对照专用入口：默认只读check；真实执行另需本批冻结和显式授权。"""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
CONFIG = "配置.json"
MAP = "请求关联_管理用.json"
BATCH_ID = "RELATION_NEW_MATERIAL_DIAGNOSTIC_001"
PLAN_PATH = "关系记录新材料对照准备_v0.1/管理计划与参照表.md"
CODING_PATH = "API评审关系记录草案_v0.4/识别编码口径_待审查.md"
ORDER = [("N01", "v0.3"), ("N01", "v0.4"), ("N02", "v0.4"), ("N02", "v0.3"),
         ("N03", "v0.3"), ("N03", "v0.4"), ("N04", "v0.4"), ("N04", "v0.3")]
IDS = [material + "_" + version.replace("v0.", "V0") for material, version in ORDER]

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

def payload(config, text):
    # Only the assembled whitelisted text enters one independent user message.
    return {**config["settings"], "messages": [{"role": "user", "content": text.decode("utf-8")}]}

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


def local_root(root):
    root = Path(root).resolve()
    require(root.is_relative_to(ROOT), "检查或档案目录必须位于本入口目录内")
    return root


def plan_rows(text):
    rows = []
    for line in text.splitlines():
        if not re.match(r"^\| [1-8] \| v0\.[34] \|", line):
            continue
        cells = [s.strip() for s in line.split("|")[1:-1]]
        require(len(cells) == 7 and cells[6] == "尚未调用", "管理计划任务行格式不符")
        material = re.search(r"N0[1-4]", cells[2]).group()
        request_path = re.search(r"\]\(([^)]+)\)", cells[3]).group(1)
        rows.append({"sequence": int(cells[0]), "material_id": material,
                     "prompt_version": cells[1],
                     "path": "关系记录新材料对照准备_v0.1/" + request_path,
                     "bytes": int(cells[4]), "sha256": cells[5].strip(chr(96)),
                     "task_id": material + "_" + cells[1].replace("v0.", "V0")})
    require([(r["material_id"], r["prompt_version"]) for r in rows] == ORDER
            and [r["sequence"] for r in rows] == list(range(1, 9)), "八项固定顺序不符")
    return rows


def check(root=ROOT):
    """Read only. Never reads credentials, imports the old runner or uses transport."""
    root = local_root(root)
    config_raw = (root / CONFIG).read_bytes()
    config = json.loads(config_raw)
    mapping = read_json(root / MAP)
    require(config["schema_version"] == mapping["schema_version"] == 1
            and config["batch_id"] == mapping["batch_id"] == BATCH_ID, "本批编号或版本不符")
    require(mapping["config_sha256"] == sha(config_raw)
            and mapping["runner_sha256"] == sha(Path(__file__).read_bytes()), "配置或入口程序哈希不符")
    require(config["management_plan_path"] == PLAN_PATH
            and config["recognition_coding_path"] == CODING_PATH, "管理依据路径不符")
    snapshots = config["source_snapshot"]
    indexed = {r["path"]: r for r in snapshots}
    require(len(indexed) == len(snapshots), "来源快照路径重复")
    raw_sources = {name: checked_source(rec) for name, rec in indexed.items()}
    plan = raw_sources[PLAN_PATH].decode("utf-8")
    fence = chr(96) * 3
    planned = json.loads(re.search(fence + r"json\n(.*?)\n" + fence, plan, re.S).group(1))
    keys = ["model", "thinking", "temperature", "max_tokens", "stream", "response_format", "tool_choice"]
    require(config["settings"] == {k: planned[k] for k in keys}, "设置与管理计划不符")
    require(config["base_url"] == planned["base_url"] == "https://api.deepseek.com"
            and config["endpoint"] == planned["endpoint"] == "/chat/completions"
            and config["api_key_env"] == "DEEPSEEK_API_KEY", "服务地址或凭据机制不符")
    for key in ["socket_timeout_seconds", "timeout_scope", "client_retries", "system_message"]:
        require(config[key] == planned[key], "拟用字段与管理计划不符：" + key)
    require(config["client_retries"] == 0 and config["system_message"] is None, "禁止重试或系统消息")
    require(config["freeze_record_file"] == "本批冻结登记.json"
            and config["execution_archive_directory"] == "运行档案", "本批档案路径不符")
    rows = plan_rows(plan)
    require(config["requests"] == rows and config["execution_order"] == IDS, "请求关联或顺序不符")
    require(config["planned_first_requests"] == mapping["planned_first_requests"] == 8
            and config["actual_api_calls_at_preparation"] == mapping["actual_api_calls_at_preparation"] == 0,
            "计划数量或准备状态不符")
    require(mapping["management_plan_sha256"] == sha(raw_sources[PLAN_PATH])
            and mapping["recognition_coding_sha256"] == sha(raw_sources[CODING_PATH]), "管理依据哈希不符")
    tasks, texts = [], {}
    for row in rows:
        require(indexed[row["path"]]["sha256"] == row["sha256"]
                and indexed[row["path"]]["bytes"] == row["bytes"], "请求快照与计划不符")
        text = raw_sources[row["path"]]
        body = encoded(payload(config, text))
        texts[row["task_id"]] = text
        tasks.append({**row, "request_text_sha256": sha(text), "api_body_sha256": sha(body),
                      "status_at_preparation": "尚未调用"})
    require(mapping["tasks"] == tasks, "请求全文或API载荷关联哈希不符")
    return config, mapping, texts


def freeze_bindings(root, config, mapping):
    root = local_root(root)
    return {"runner_sha256": sha(Path(__file__).read_bytes()),
            "config_sha256": sha((root / CONFIG).read_bytes()),
            "request_map_sha256": sha((root / MAP).read_bytes()),
            "management_plan_sha256": mapping["management_plan_sha256"],
            "recognition_coding_sha256": mapping["recognition_coding_sha256"],
            "execution_order": IDS,
            "request_text_sha256": {t["task_id"]: t["request_text_sha256"] for t in mapping["tasks"]},
            "api_body_sha256": {t["task_id"]: t["api_body_sha256"] for t in mapping["tasks"]}}


def verify_freeze(root, config, mapping, expected_sha256):
    """Validate a future, separately approved local record; never create or fix it."""
    root = local_root(root)
    path = root / config["freeze_record_file"]
    require(path.is_file() and path.resolve().is_relative_to(root), "缺少本批冻结登记；拒绝调用")
    require(isinstance(expected_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", expected_sha256),
            "须显式提供本批冻结登记的--freeze-sha256")
    raw = path.read_bytes()
    require(sha(raw) == expected_sha256, "本批冻结登记哈希不符")
    record = json.loads(raw)
    require(record.get("schema_version") == 1 and record.get("batch_id") == BATCH_ID,
            "冻结登记不是本批")
    require(record.get("status") == "frozen" and record.get("execution_authorized") is True,
            "本批未冻结或未授权执行")
    for key in ["approved_by", "approved_at", "approval_reference"]:
        require(isinstance(record.get(key), str) and record[key].strip(), "冻结登记缺少审批字段：" + key)
    approved_at = datetime.fromisoformat(record["approved_at"])
    require(approved_at.utcoffset() is not None, "冻结审批时间须包含时区")
    require(record.get("maximum_first_requests") == 8
            and record.get("bindings") == freeze_bindings(root, config, mapping), "本批冻结绑定不符")
    return {"sha256": sha(raw), "approval_reference": record["approval_reference"]}


def archive_kind(root):
    # Only the test script supplies a temporary child root; CLI always uses ROOT.
    return "offline_check" if Path(root).resolve() != ROOT else "first_api_attempt"


def attempt(directory, body, config, item, key, mapping, kind):
    directory.mkdir()  # Exclusive first-attempt marker, retained even after interruption.
    started = time.monotonic()
    meta = {"record_kind": kind, "batch_id": config["batch_id"], "task_id": item["task_id"],
            "started_at": now(), "technical_status": "failed", "settings": config["settings"],
            "requested_model": config["settings"]["model"], "returned_model": None,
            "finish_reason": None, "usage": None, "socket_timeout_seconds": config["socket_timeout_seconds"],
            "timeout_scope": config["timeout_scope"], "client_retries": 0,
            "network_attempt_started": False, "response_complete": False,
            "request_text_sha256": item["request_text_sha256"], "request_body_sha256": sha(body),
            "management_plan_sha256": mapping["management_plan_sha256"], "failure": None}
    if kind == "offline_check":
        meta["notice"] = "离线检查桩；不是API结果；没有读取真实凭据或访问网络"
        meta["actual_api_calls"] = 0
    save_json(directory / "attempt.json", {"record_kind": kind, "started_at": meta["started_at"],
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
                           "技术异常或中断；检查首次档案；不重试"}
    finally:
        for name in ["request.json", "response.json", "output.txt"]:
            path = directory / name
            if path.is_file():
                meta[name.split(".")[0] + "_sha256"] = sha(path.read_bytes())
        meta["missing_artifacts"] = [n for n in ["request.json", "response.json", "output.txt"]
                                     if not (directory / n).is_file()]
        meta["ended_at"] = now()
        meta["elapsed_seconds"] = time.monotonic() - started
        save_json(directory / "metadata.json", meta)
    require(meta["technical_status"] == "completed", "技术失败；本轮停止且不可续跑")


def execute(authorized, expected_freeze_sha256=None, root=ROOT):
    require(authorized, "真实执行须显式提供run --execute-review；拒绝隐式调用")
    root = local_root(root)
    config, mapping, _ = check(root)
    directory = root / config["execution_archive_directory"]
    require(not directory.exists() and not directory.is_symlink(), "已有首次档案；拒绝重复执行或续跑")
    freeze = verify_freeze(root, config, mapping, expected_freeze_sha256)
    key = api_key()  # Only after flag, all local checks, frozen bindings and absence of archives.
    directory.mkdir()  # Atomic reservation closes concurrent-start races before any HTTP call.
    kind = archive_kind(root)
    summary = {"record_kind": kind, "batch_id": config["batch_id"], "started_at": now(),
               "status": "failed", "execution_order": IDS, "attempted": [], "completed": [],
               "unexecuted": [], "failure": None, "settings": config["settings"],
               "client_retries": 0, "freeze_sha256": freeze["sha256"],
               **{k: v for k, v in freeze_bindings(root, config, mapping).items()
                  if k in ["runner_sha256", "config_sha256", "request_map_sha256"]}}
    if kind == "offline_check":
        summary["notice"] = "离线检查：冻结校验和凭据为内存桩；不是冻结登记或API结果"
        summary["actual_api_calls"] = 0
    save_json(directory / "stage_start.json", summary)
    try:
        for item in mapping["tasks"]:
            current_config, current_mapping, texts = check(root)
            verify_freeze(root, current_config, current_mapping, expected_freeze_sha256)
            require(current_config == config and current_mapping == mapping, "发送前本批绑定已变化")
            body = encoded(payload(config, texts[item["task_id"]]))
            require(sha(body) == item["api_body_sha256"], "发送前请求载荷哈希不符")
            summary["attempted"].append(item["task_id"])
            print("一次首次请求：" + item["task_id"], flush=True)
            attempt(directory / item["task_id"], body, config, item, key, mapping, kind)
            summary["completed"].append(item["task_id"])
        summary["status"] = "completed"
    except BaseException as error:
        summary["failure"] = {"type": type(error).__name__,
                              "reason": str(error) if isinstance(error, ValueError) else
                              "技术异常或中断；阶段停止，不重试"}
    finally:
        summary["ended_at"] = now()
        summary["unexecuted"] = [task for task in IDS if task not in summary["attempted"]]
        save_json(directory / "summary.json", summary)
    require(summary["status"] == "completed", "阶段失败；保留首次档案，禁止续跑")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="check", choices=["check", "run"])
    parser.add_argument("--execute-review", action="store_true")
    parser.add_argument("--freeze-sha256")
    parser.add_argument("--require-frozen", action="store_true", help="check时额外只读核验本批冻结")
    args = parser.parse_args()
    try:
        require(not args.execute_review or args.action == "run", "执行开关仅用于run")
        require(not args.require_frozen or args.action == "check", "额外冻结检查仅用于check")
        if args.action == "run":
            execute(args.execute_review, args.freeze_sha256)
            result = {"status": "completed", "notice": "仅技术状态，不代表内容检查通过"}
        else:
            config, mapping, _ = check()
            if args.require_frozen:
                verify_freeze(ROOT, config, mapping, args.freeze_sha256)
            result = {"status": "offline_check_passed", "batch_id": config["batch_id"],
                      "planned_first_requests": 8, "execution_order": IDS,
                      "request_status": "尚未调用（准备记录）", "api_calls_in_this_check": 0,
                      "freeze_verified": args.require_frozen,
                      "freeze_bindings_for_future_review_only": freeze_bindings(ROOT, config, mapping)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
