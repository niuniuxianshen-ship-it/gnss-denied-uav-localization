"""Three-image draft batch. Default: offline preparation; no credentials/network."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import time


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
ORDER = [("IMG001", "BASE"), ("IMG001", "ENHANCED"),
         ("IMG002", "ENHANCED"), ("IMG002", "BASE"),
         ("IMG003", "BASE"), ("IMG003", "ENHANCED")]
SETTINGS = {"model": "deepseek-flash", "thinking": {"type": "disabled"},
            "temperature": 0, "max_tokens": 8192, "stream": False,
            "response_format": {"type": "text"}, "tool_choice": "none"}
EXPIRY = {"anchor": "created_at", "seconds": 604800}
PLAN = "上传计划与任务关联.json"


def now():
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def save(path, raw):
    with Path(path).open("xb") as handle:
        handle.write(raw)


def save_json(path, value):
    save(path, encoded(value))


def read_json(path):
    return json.loads(Path(path).read_bytes().decode("utf-8"))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def source(record):
    path = (PROJECT / record["path"]).resolve()
    require(path.is_relative_to(PROJECT), "输入路径超出项目")
    raw = path.read_bytes()
    require(sha(raw) == record["sha256"] and len(raw) == record["bytes"],
            "输入哈希或字节数不符: " + record["path"])
    return raw


def inputs(config):
    require(config["settings"] == SETTINGS, "模型设置不符合本批约束")
    require(config["expires_after"] == EXPIRY, "文件有效期必须为7天")
    require(config["base_url"] == "https://api.deepseek.com", "只允许官方服务")
    require(config["api_key_env"] == "DEEPSEEK_API_KEY", "仅允许既定环境变量")
    require(re.fullmatch(r"[A-Za-z0-9_-]+", config["batch_id"]), "批次编号无效")
    for field in ("upload_timeout_seconds", "model_timeout_seconds"):
        require(type(config[field]) in (int, float) and config[field] > 0,
                "超时必须为正数")
    images = config["images"]
    require([x["image_id"] for x in images] == ["IMG001", "IMG002", "IMG003"],
            "图片编号、顺序或数量不符合本批约束")
    image_bytes = {}
    for item in images:
        raw = source(item)
        require(raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) > 24,
                "图片不是PNG")
        require(list(struct.unpack(">II", raw[16:24])) == item["size"], "尺寸不符")
        require(len(raw) <= 64 * 1024 * 1024 and max(item["size"]) <= 8192,
                "图片超过本次查阅的接口上限")
        image_bytes[item["image_id"]] = raw
    require(set(config["conditions"]) == {"BASE", "ENHANCED"}, "条件必须恰为两项")
    appendix = source(config["appendix"])
    appendix.decode("utf-8")
    texts = {}
    for condition, record in config["conditions"].items():
        prompt = source(record)
        prompt.decode("utf-8")
        texts[condition] = prompt + b"\n\n" + appendix
    return image_bytes, texts


def payload(text, file_id):
    # Nothing except the explicit text and a single file reference enters messages.
    return {**SETTINGS, "messages": [{"role": "user", "content": [
        {"type": "text", "text": text.decode("utf-8")},
        {"type": "file", "file_id": file_id}]}]}


def prepare(config_path, root=ROOT):
    config_path, root = Path(config_path).resolve(), Path(root).resolve()
    require(root.is_relative_to(ROOT), "输出必须位于本入口目录内")
    require(not (root / PLAN).exists() and not (root / "请求模板").exists(),
            "准备产物已存在，拒绝覆盖")
    config = read_json(config_path)
    images, texts = inputs(config)
    uploads = [{"batch_id": config["batch_id"], **item,
                "upload_filename": item["image_id"] + ".png",
                "purpose": "user_data", "expires_after": EXPIRY,
                "file_id": None, "status": "待上传"} for item in config["images"]]
    tasks = []
    bodies = []
    for sequence, (image_id, condition) in enumerate(ORDER, 1):
        filename = f"请求模板/{sequence:02d}_{image_id}_{condition}.json"
        body = encoded(payload(texts[condition], None))
        bodies.append((filename, body))
        tasks.append({"sequence": sequence,
                      "task_id": f"{config['batch_id']}:{image_id}:{condition}",
                      "image_id": image_id, "condition_id": condition,
                      "image_sha256": next(x["sha256"] for x in config["images"]
                                           if x["image_id"] == image_id),
                      "prompt_sha256": config["conditions"][condition]["sha256"],
                      "appendix_sha256": config["appendix"]["sha256"],
                      "template": filename, "template_sha256": sha(body),
                      "text_utf8_sha256": sha(texts[condition]),
                      "text_utf8_bytes": len(texts[condition]), "status": "尚未调用"})
    plan = {"status": "离线准备；配置待审阅，未冻结",
            "created_at": now(), "batch_id": config["batch_id"],
            "config_sha256": sha(config_path.read_bytes()),
            "runner_sha256": sha(Path(__file__).read_bytes()),
            "planned_uploads": 3, "planned_model_requests": 6,
            "actual_uploads_at_preparation": 0, "actual_model_calls_at_preparation": 0,
            "assembly": "prompt bytes + LF LF + appendix bytes, without normalization",
            "uploads": uploads, "tasks": tasks}
    (root / "请求模板").mkdir()
    for name, body in bodies:
        save(root / name, body)
    save_json(root / PLAN, plan)
    return plan


def check(config_path, root=ROOT):
    root = Path(root)
    config = read_json(config_path)
    _, texts = inputs(config)
    plan = read_json(root / PLAN)
    require(plan["config_sha256"] == sha(Path(config_path).read_bytes()), "配置已变化")
    require(plan["runner_sha256"] == sha(Path(__file__).read_bytes()), "运行程序已变化")
    require(plan["batch_id"] == config["batch_id"], "批次编号不符")
    expected_uploads = [{"batch_id": config["batch_id"], **item,
                         "upload_filename": item["image_id"] + ".png",
                         "purpose": "user_data", "expires_after": EXPIRY,
                         "file_id": None, "status": "待上传"} for item in config["images"]]
    require(plan["uploads"] == expected_uploads, "上传计划不符")
    require(len(plan["tasks"]) == 6 and plan["planned_uploads"] == 3
            and plan["planned_model_requests"] == 6, "计划数量不符")
    seen = set()
    for seq, (task, pair) in enumerate(zip(plan["tasks"], ORDER), 1):
        require((task["image_id"], task["condition_id"]) == pair
                and task["sequence"] == seq, "任务顺序不符")
        require(task["task_id"] == f"{config['batch_id']}:{pair[0]}:{pair[1]}"
                and task["task_id"] not in seen, "任务编号不符或重复")
        seen.add(task["task_id"])
        expected_path = f"请求模板/{seq:02d}_{pair[0]}_{pair[1]}.json"
        require(task["template"] == expected_path, "模板路径不符")
        body = (root / expected_path).read_bytes()
        require(sha(body) == task["template_sha256"]
                and body == encoded(payload(texts[pair[1]], None)), "模板字节不符")
        require(task["text_utf8_sha256"] == sha(texts[pair[1]])
                and task["text_utf8_bytes"] == len(texts[pair[1]]), "文本记录不符")
        require(task["image_sha256"] == next(x["sha256"] for x in config["images"]
                                               if x["image_id"] == pair[0])
                and task["prompt_sha256"] == config["conditions"][pair[1]]["sha256"]
                and task["appendix_sha256"] == config["appendix"]["sha256"], "任务输入哈希不符")
    return config, plan


def validate_upload(result, item):
    require(isinstance(result, dict), "上传返回不是对象")
    require(isinstance(result.get("id"), str)
            and re.fullmatch(r"file-api-[A-Za-z0-9_-]+", result["id"]), "真实文件ID缺失或无效")
    require(result.get("object") == "file" and result.get("purpose") == "user_data"
            and result.get("bytes") == item["bytes"]
            and result.get("filename") == item["upload_filename"], "上传返回与图片不符")
    created, expiry = result.get("created_at"), result.get("expires_at")
    require(type(created) is int and type(expiry) is int
            and expiry - created == EXPIRY["seconds"], "未确认返回的7天有效期")
    require(expiry > time.time(), "文件已过期")
    return result["id"]


def file_ids(root, plan):
    directory = Path(root) / "运行档案" / "上传"
    require(directory.is_dir(), "缺少真实上传记录和file_id，禁止提交定位请求")
    summary = read_json(directory / "summary.json")
    require(summary["status"] == "completed" and len(summary["completed"]) == 3,
            "上传未全部完成")
    ids = {}
    for item in plan["uploads"]:
        archive = directory / item["image_id"]
        meta = read_json(archive / "metadata.json")
        raw = (archive / "response.json").read_bytes()
        require(meta["technical_status"] == "completed"
                and meta["response_sha256"] == sha(raw)
                and meta["input_sha256"] == item["sha256"], "上传归档核对失败")
        require(read_json(archive / "request.json") == upload_description(item),
                "上传请求记录不符")
        ids[item["image_id"]] = validate_upload(json.loads(raw), item)
    require(len(set(ids.values())) == 3, "不同图片返回相同文件ID，停止")
    return ids


def real_payload(template, file_id):
    require(isinstance(file_id, str) and re.fullmatch(r"file-api-[A-Za-z0-9_-]+", file_id),
            "未取得真实file_id，禁止提交定位请求")
    result = json.loads(json.dumps(template))
    require(result["messages"][0]["content"][1]["file_id"] is None, "模板已填ID")
    result["messages"][0]["content"][1]["file_id"] = file_id
    return result


def upload_description(item):
    return {"method": "POST", "url": "https://api.deepseek.com/files",
            "fields": {"purpose": "user_data", "expires_after[anchor]": "created_at",
                       "expires_after[seconds]": 604800},
            "file": {"filename": item["upload_filename"], "sha256": item["sha256"],
                     "bytes": item["bytes"], "content_type": "image/png"}}


def multipart(item, raw):
    # Fixed ASCII boundary, checked against content; no source path is transmitted.
    boundary = "----ThreeImageBoundary" + item["sha256"][:24]
    require(boundary.encode("ascii") not in raw, "表单边界与图片内容冲突")
    parts = []
    for name, value in upload_description(item)["fields"].items():
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                      f'\r\n\r\n{value}\r\n').encode("ascii"))
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                  f'filename="{item["upload_filename"]}"\r\n'
                  'Content-Type: image/png\r\n\r\n').encode("ascii"))
    parts.extend([raw, f"\r\n--{boundary}--\r\n".encode("ascii")])
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


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
    # No assessment of geography, correctness, abstention or output format here.


def attempt(archive, request, body, content_type, endpoint, key, timeout, item, upload):
    archive.mkdir()  # An exclusive, durable attempted-item marker, even after a crash.
    started = time.monotonic()
    meta = {"started_at": now(), "technical_status": "failed",
            "network_attempt_started": False, "requested_model": None if upload else SETTINGS["model"],
            "returned_model": None, "finish_reason": None, "usage": None,
            "socket_timeout_seconds": timeout, "timeout_scope": "connect and each socket operation; not wall-clock deadline",
            "input_sha256": item.get("sha256", item.get("image_sha256")),
            "prompt_sha256": item.get("prompt_sha256"),
            "appendix_sha256": item.get("appendix_sha256"),
            "text_utf8_sha256": item.get("text_utf8_sha256"), "response_complete": False,
            "request_body_sha256": sha(body), "failure": None}
    save_json(archive / "attempt.json", {"started_at": meta["started_at"],
                                         "policy": "first attempt reserved; never resume or overwrite"})
    try:
        save_json(archive / "request.json", request)
        if upload:
            save(archive / "request.multipart.bin", body)
        post_once(endpoint, body, content_type, key, timeout, archive, meta)
        result = read_json(archive / "response.json")
        if upload:
            meta["file_id"] = validate_upload(result, item)
        else:
            classify_completion(result, archive, meta)
        meta["technical_status"] = "completed"
    except BaseException as error:
        # Never serialize exception repr/locals, authentication or credentials.
        meta["failure"] = {"type": type(error).__name__,
                           "reason": str(error) if isinstance(error, ValueError) else
                           "技术异常或中断；检查已保留档案；不重试"}
    finally:
        response_path = archive / "response.json"
        if response_path.exists():
            meta["response_sha256"] = sha(response_path.read_bytes())
        meta["ended_at"] = now()
        meta["elapsed_seconds"] = time.monotonic() - started
        meta["missing_artifacts"] = [name for name in ("request.json", "response.json")
                                     if not (archive / name).is_file()]
        if not upload and not (archive / "output.txt").is_file():
            meta["missing_artifacts"].append("output.txt")
        save_json(archive / "metadata.json", meta)
    require(meta["technical_status"] == "completed", "技术失败；本阶段停止且不可续跑")
    return meta


def execute(config_path, stage, authorized, root=ROOT):
    require(authorized, "仅离线检查；实际执行必须提供对应的显式执行选项")
    root = Path(root)
    config, plan = check(config_path, root)
    directory = root / "运行档案" / ("上传" if stage == "upload" else "定位")
    require(not directory.exists(), "本阶段已尝试或已有档案；拒绝再次执行或覆盖")
    ids = file_ids(root, plan) if stage == "run" else None
    key = api_key()
    directory.parent.mkdir(exist_ok=True)
    directory.mkdir()  # The whole stage is first-attempt-only; no automatic resume.
    items = plan["uploads"] if stage == "upload" else plan["tasks"]
    names = [x["image_id"] if stage == "upload" else x["template"].split("/")[-1][:-5]
             for x in items]
    summary = {"started_at": now(), "stage": stage, "status": "failed",
               "order": names, "completed": [], "attempted": [], "unexecuted": [],
               "config_sha256": plan["config_sha256"], "runner_sha256": plan["runner_sha256"],
               "failure": None}
    save_json(directory / "stage_start.json", summary)
    try:
        for item, name in zip(items, names):
            check(config_path, root)  # Reject any change between first attempts.
            if stage == "upload":
                body, content_type = multipart(item, source(item))
                request = upload_description(item)
                endpoint, timeout = "/files", config["upload_timeout_seconds"]
            else:
                ids = file_ids(root, plan)  # Also reject an expired file before each model call.
                request = real_payload(read_json(root / item["template"]), ids[item["image_id"]])
                body, content_type = encoded(request), "application/json"
                endpoint, timeout = "/chat/completions", config["model_timeout_seconds"]
            summary["attempted"].append(name)
            print("即将执行一次" + ("上传: " if stage == "upload" else "定位请求: ") + name, flush=True)
            attempt(directory / name, request, body, content_type, endpoint, key, timeout,
                    item, stage == "upload")
            summary["completed"].append(name)
        summary["status"] = "completed"
    except BaseException as error:
        summary["failure"] = {"type": type(error).__name__,
                              "reason": str(error) if isinstance(error, ValueError) else
                              "技术异常或中断；阶段停止，不重试"}
    finally:
        summary["ended_at"] = now()
        summary["unexecuted"] = [name for name in names if name not in summary["attempted"]]
        save_json(directory / "summary.json", summary)
    require(summary["status"] == "completed", "阶段失败；未执行项见summary.json，不可自动续跑")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="prepare",
                        choices=["prepare", "check", "upload", "run"])
    parser.add_argument("--execute-upload", action="store_true")
    parser.add_argument("--execute-run", action="store_true")
    parser.add_argument("--submission-ready", action="store_true")
    args = parser.parse_args()
    config_path = ROOT / "配置草案.json"
    try:
        require(not (args.execute_upload and args.execute_run), "执行阶段不能合并")
        require(not args.execute_upload or args.action == "upload", "上传开关只用于upload")
        require(not args.execute_run or args.action == "run", "定位开关只用于run")
        require(not args.submission_ready or args.action == "check", "ID检查只用于check")
        if args.action == "prepare":
            prepare(config_path)
        elif args.action == "check":
            _, plan = check(config_path)
            if args.submission_ready:
                file_ids(ROOT, plan)
        elif args.action == "upload":
            execute(config_path, "upload", args.execute_upload)
        else:
            execute(config_path, "run", args.execute_run)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2
    print("完成；仅技术状态，不代表内容正确或批次冻结")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
