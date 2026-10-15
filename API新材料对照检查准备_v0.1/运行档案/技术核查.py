"""Local-only technical checker; never imports or calls the API worker.

Input: --spec-file JSON or --spec-base64 UTF-8 JSON encoded as base64.
It reads original archives, checks them, and appends to the running register.
It neither evaluates assessment content nor modifies original API archives.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(spec):
    task = spec["request"]
    execution = spec["exec_result"]
    notices = []
    for line in execution.get("output", "").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict) and "directory" in value:
                notices.append(value)
        except (ValueError, TypeError):
            pass
    notice = notices[-1] if notices else {}
    directory = Path(notice["directory"]) if notice.get("directory") else None
    root = Path(spec["runs_path"]).resolve()
    if directory is not None and root not in directory.resolve().parents:
        raise ValueError("Archive outside registered root")
    required = ["request.json", "response.json", "output.txt", "metadata.json"]
    present = {name: bool(directory and (directory / name).is_file()) for name in required}
    missing = [name for name, exists in present.items() if not exists]
    files = [
        {"name": name, "path": str(directory / name), "sha256": sha256(directory / name)}
        for name, exists in present.items() if exists
    ]
    errors = ["应有档案缺失"] if missing else []
    objects = {}
    for name in ["metadata.json", "request.json", "response.json"]:
        objects[name] = {}
        if present[name]:
            try:
                objects[name] = json.loads((directory / name).read_text(encoding="utf-8-sig"))
            except (ValueError, OSError):
                errors.append(name + "无法解析")
    meta = objects["metadata.json"]
    request = objects["request.json"]
    response = objects["response.json"]
    text = Path(task["path"]).read_text(encoding="utf-8-sig")
    expected = {
        "model": "deepseek-flash",
        "messages": [
            {"role": "system", "content": spec["system"]},
            {"role": "user", "content": text},
        ],
        "thinking": {"type": "disabled"},
        "max_tokens": 2048,
        "stream": False,
        "tool_choice": "none",
    }
    messages = request.get("messages", [])
    checks = {
        "user_body_exact": len(messages) == 2 and messages[1] == expected["messages"][1],
        "system_exact": len(messages) == 2 and messages[0] == expected["messages"][0],
        "payload_exact": request == expected,
        "input_files_empty": meta.get("input_files") == [],
        "response_output_matches": False,
        "response_metadata_matches": False,
        "archives_unchanged": False,
        "inputs_unchanged": False,
        "registration_precedes_call": False,
    }
    output = (directory / "output.txt").read_text(encoding="utf-8-sig") if present["output.txt"] else None
    finish = None
    tools = None
    content = None
    returned_model = response.get("model")
    usage = response.get("usage")
    try:
        choice = response["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
        if not isinstance(content, str):
            raise ValueError("Non-text response")
        finish = choice.get("finish_reason")
        tools = bool(message.get("tool_calls") or message.get("function_call"))
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        checks["response_output_matches"] = output is not None and normalized == output
        checks["response_metadata_matches"] = (
            meta.get("returned_model") == returned_model
            and meta.get("finish_reason") == finish
            and meta.get("tool_calls") == tools
            and meta.get("usage") == usage
        )
    except (KeyError, IndexError, TypeError, ValueError):
        errors.append("原始响应结构无法核对")
    if execution.get("exit_code") != 0:
        errors.append("工作器退出码非0或执行未完成")
    if meta.get("status") != "completed":
        errors.append("工作器技术状态非completed")
    if not isinstance(content, str) or not content.strip():
        errors.append("空输出或无文本响应")
    if finish != "stop":
        errors.append("非stop结束：截断或其他不完整结束")
    if tools:
        errors.append("工具调用")
    if meta.get("attempts") != 1 or meta.get("automatic_retries") != 0:
        errors.append("调用次数或重试元数据异常")
    try:
        checks["registration_precedes_call"] = (
            dt.datetime.fromisoformat(spec["registered_at"])
            < dt.datetime.fromisoformat(meta["started_at"])
            <= dt.datetime.fromisoformat(meta["ended_at"])
        )
    except (KeyError, TypeError, ValueError):
        errors.append("起止时间无法核对")
    inputs = [
        {"path": item["path"], "sha256": sha256(item["path"]),
         "unchanged": sha256(item["path"]) == item["sha256"]}
        for item in spec["sources"]
    ]
    checks["inputs_unchanged"] = all(item["unchanged"] for item in inputs)
    checks["archives_unchanged"] = all(sha256(item["path"]) == item["sha256"] for item in files)
    for name, passed in checks.items():
        if not passed:
            errors.append("核对未通过：" + name)
    return {
        "order": task["order"], "tag": task["tag"], "request_path": task["path"],
        "request_sha256": task["sha256"],
        "archive_directory": str(directory) if directory else None,
        "started_at": meta.get("started_at", "未取得"),
        "ended_at": meta.get("ended_at", "未取得"),
        "requested_model": meta.get("requested_model", request.get("model", "未取得")),
        "returned_model": returned_model, "worker_status": meta.get("status", "未取得"),
        "http_status": meta.get("http_status"), "finish_reason": finish,
        "usage": usage, "elapsed_seconds": meta.get("elapsed_seconds"),
        "tool_calls": tools,
        "output_nonempty": isinstance(content, str) and bool(content.strip()),
        "exit_code": execution.get("exit_code"),
        "attempts": meta.get("attempts"), "automatic_retries": meta.get("automatic_retries"),
        "missing_files": missing, "files": files, "checks": checks,
        "safe_to_continue": not errors, "errors": errors,
    }


def link(label, path):
    return "[" + label + "](" + str(path).replace("\\", "/") + ")"


def quoted(value):
    return chr(96) + str(value) + chr(96)


def entry(result):
    lines = [
        "",
        "### 第" + str(result["order"]) + "次：" + result["tag"],
        "",
        "- 原请求：" + link(Path(result["request_path"]).name, result["request_path"])
        + "；SHA256：" + quoted(result["request_sha256"]) + "。",
        "- 实际运行目录：" + quoted(result["archive_directory"] or "未创建或未取得") + "。",
        "- 实际开始／结束（UTC）：" + quoted(result["started_at"]) + "／"
        + quoted(result["ended_at"]) + "；耗时（秒）：" + quoted(result["elapsed_seconds"]) + "。",
        "- 请求模型／返回模型：" + quoted(result["requested_model"]) + "／"
        + quoted(result["returned_model"] or "未取得") + "。",
        "- 技术状态：" + quoted(result["worker_status"]) + "；HTTP："
        + quoted(result["http_status"]) + "；结束原因：" + quoted(result["finish_reason"])
        + "；退出码：" + quoted(result["exit_code"]) + "。",
        "- 工具调用：" + quoted(result["tool_calls"]) + "；非空文本响应："
        + quoted(result["output_nonempty"]) + "；attempts：" + quoted(result["attempts"])
        + "；automatic_retries：" + quoted(result["automatic_retries"]) + "。",
        "- 原始用量：" + quoted(json.dumps(result["usage"], ensure_ascii=False, separators=(",", ":"))) + "。",
        "",
        "|首次档案|SHA256|",
        "|---|---|",
    ]
    for item in result["files"]:
        lines.append("|" + link(item["name"], item["path"]) + "|" + quoted(item["sha256"]) + "|")
    lines.extend([
        "",
        "- 请求正文、system及完整载荷与登记核对："
        + str(all(result["checks"][k] for k in ["user_body_exact", "system_exact", "payload_exact"]))
        + "；input_files为空：" + str(result["checks"]["input_files_empty"]) + "。",
        "- 原始响应content与output对应：" + str(result["checks"]["response_output_matches"])
        + "（文本读取时统一换行，未改写文件）；响应模型、结束原因、工具与用量和元数据一致："
        + str(result["checks"]["response_metadata_matches"]) + "。",
        "- 首次调用前登记核对：" + str(result["checks"]["registration_precedes_call"])
        + "；九份输入哈希不变：" + str(result["checks"]["inputs_unchanged"])
        + "；首次档案检查前后哈希不变：" + str(result["checks"]["archives_unchanged"]) + "。",
        "- 本次缺失档案：" + ("无" if not result["missing_files"] else "、".join(result["missing_files"])) + "。",
        "- 后续执行技术判断：" + (
            "本次技术核对通过，可按登记顺序继续；不代表内容通过。"
            if result["safe_to_continue"] else
            "停止后续请求；原因：" + "；".join(result["errors"]) + "。"
        ),
        "",
    ])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--spec-base64")
    inputs.add_argument("--spec-file", help="Saved local technical-check specification")
    parser.add_argument("--no-append", action="store_true",
                        help="Recheck archived data without changing the register")
    args = parser.parse_args()
    spec = (json.loads(Path(args.spec_file).read_text(encoding="utf-8-sig"))
            if args.spec_file else
            json.loads(base64.b64decode(args.spec_base64).decode("utf-8")))
    result = check(spec)
    if not args.no_append:
        register = Path(spec["register_path"])
        heading = "### 第" + str(result["order"]) + "次：" + result["tag"]
        if heading in register.read_text(encoding="utf-8-sig"):
            raise ValueError("This check is already registered; refusing duplicate append")
        with register.open("a", encoding="utf-8", newline="") as out:
            out.write(entry(result))
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()

