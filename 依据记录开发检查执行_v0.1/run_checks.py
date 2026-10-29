"""Four first requests for the evidence-record development check; default is read-only."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
BATCH = "EVIDENCE_RECORD_DEVELOPMENT_001"
PREP = "依据记录开发检查准备_v0.1/"
PLAN = PREP + "管理计划与参照表.md"
CONFIG_SOURCE = "关系记录新材料执行入口_v0.1/配置.json"
HELPER_SOURCE = "关系记录新材料执行入口_v0.1/run_comparison.py"
HELPER_SHA = "461330743d3f179a7fa2491c7018c61a89390567ad370aa82bf8ae8820666359"
ORDER = ["甲_V04", "甲_V05", "乙_V05", "乙_V04"]


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def source_path(name):
    path = (PROJECT / name).resolve()
    require(path.is_relative_to(PROJECT), "来源路径越出项目")
    return path


require(sha(source_path(HELPER_SOURCE).read_bytes()) == HELPER_SHA, "复用函数原件哈希变化")
spec = importlib.util.spec_from_file_location("preserved_review_helpers", source_path(HELPER_SOURCE))
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)  # Definitions only; never calls the old batch main/check/run.


def check():
    raw = (ROOT / "执行关联.json").read_bytes()
    manifest = json.loads(raw)
    require(manifest["schema_version"] == 1 and manifest["batch_id"] == BATCH,
            "不是本批执行关联")
    require(manifest["maximum_first_requests"] == 4 and manifest["execution_order"] == ORDER,
            "四项数量或顺序不符")
    sources = {}
    for record in manifest["source_snapshot"]:
        name = record["path"]
        require(name not in sources, "来源快照重复")
        data = source_path(name).read_bytes()
        require(sha(data) == record["sha256"] and len(data) == record["bytes"], "来源变化：" + name)
        sources[name] = data
    original = json.loads(sources[CONFIG_SOURCE])
    config = manifest["config"]
    for name in ["base_url", "endpoint", "settings", "socket_timeout_seconds", "timeout_scope",
                 "client_retries", "system_message"]:
        require(config[name] == original[name], "设置与已审阅来源不同：" + name)
    require(config["base_url"] == "https://api.deepseek.com" and config["endpoint"] == "/chat/completions"
            and config["client_retries"] == 0 and config["system_message"] is None,
            "禁止变更地址、重试或额外系统消息")
    require(config["batch_id"] == BATCH and config["settings"] == {
        "model": "deepseek-flash", "thinking": {"type": "disabled"}, "temperature": 0,
        "max_tokens": 8192, "stream": False, "response_format": {"type": "text"}, "tool_choice": "none"
    }, "请求设置不符")
    require([task["task_id"] for task in manifest["tasks"]] == ORDER, "任务顺序不符")
    appendix = sources["开发输入/参考附录正文.md"]
    suffix = "\n【共同参考附录开始（参考材料，不属于待评正文）】\n".encode("utf-8")
    suffix += appendix + "【共同参考附录结束】\n".encode("utf-8")
    bodies = {}
    for task in manifest["tasks"]:
        task_id = task["task_id"]
        material, version = task_id.split("_")
        prompt_path = ("API评审关系记录草案_v0.4/完整评审提示词草案.txt" if version == "V04"
                       else "API评审依据关系草案_v0.5/完整评审提示词草案.txt")
        expected_path = PREP + "请求_" + task_id + ".txt"
        require(task["path"] == expected_path, "任务请求路径不符")
        text = sources[expected_path]
        placeholder = "在此放入完整待评文本".encode("utf-8")
        require(sources[prompt_path].count(placeholder) == 1, "占位句不唯一")
        expected = sources[prompt_path].replace(placeholder, sources[PREP + "待评正文_" + material + ".txt"]) + suffix
        require(text == expected and sha(text) == task["request_text_sha256"], "请求组装不符")
        body = helpers.encoded(helpers.payload(config, text))
        require(sha(body) == task["api_body_sha256"], "API载荷哈希不符")
        bodies[task_id] = body
    bindings = {"runner_sha256": sha(Path(__file__).read_bytes()), "manifest_sha256": sha(raw),
                "management_plan_sha256": sha(sources[PLAN]), "execution_order": ORDER,
                "request_text_sha256": {t["task_id"]: t["request_text_sha256"] for t in manifest["tasks"]},
                "api_body_sha256": {t["task_id"]: t["api_body_sha256"] for t in manifest["tasks"]}}
    return manifest, bindings, bodies


def verify_freeze(bindings, expected_sha):
    require(isinstance(expected_sha, str) and re.fullmatch(r"[a-f0-9]{64}", expected_sha),
            "须指定真实冻结SHA-256")
    raw = (ROOT / "本批冻结登记.json").read_bytes()
    require(sha(raw) == expected_sha, "冻结登记哈希不符")
    record = json.loads(raw)
    require(record.get("batch_id") == BATCH and record.get("status") == "frozen"
            and record.get("execution_authorized") is True and record.get("maximum_first_requests") == 4
            and record.get("bindings") == bindings, "本批冻结或绑定不符")
    require(all(record.get(k) for k in ["approved_by", "approved_at", "approval_reference"]), "缺少授权依据")


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


def execute(authorized, freeze_sha):
    require(authorized, "真实执行必须提供--execute-review")
    manifest, bindings, _ = check()
    verify_freeze(bindings, freeze_sha)
    directory = ROOT / "运行档案"
    require(not directory.exists() and not directory.is_symlink(), "已有首次档案；禁止重复执行或续跑")
    key = existing_key()
    directory.mkdir()  # Exclusive reservation; concurrent starts cannot both reach HTTP.
    summary = {"record_kind": "first_api_attempt", "batch_id": BATCH, "status": "failed",
               "started_at": helpers.now(), "execution_order": ORDER, "attempted": [], "completed": [],
               "unexecuted": [], "failure": None, "settings": manifest["config"]["settings"],
               "client_retries": 0, "freeze_sha256": freeze_sha, "bindings": bindings}
    helpers.save_json(directory / "stage_start.json", summary)
    try:
        for task in manifest["tasks"]:
            current, current_bindings, bodies = check()
            verify_freeze(current_bindings, freeze_sha)
            require(current == manifest and current_bindings == bindings, "发送前本批材料变化")
            summary["attempted"].append(task["task_id"])
            print("一次首次请求：" + task["task_id"], flush=True)
            helpers.attempt(directory / task["task_id"], bodies[task["task_id"]], manifest["config"],
                            task, key, {"management_plan_sha256": bindings["management_plan_sha256"]},
                            "first_api_attempt")
            summary["completed"].append(task["task_id"])
        summary["status"] = "completed"
    except BaseException as error:
        summary["failure"] = {"type": type(error).__name__,
                              "reason": str(error) if isinstance(error, ValueError) else "技术异常；停止，不重试"}
    finally:
        summary["ended_at"] = helpers.now()
        summary["unexecuted"] = [task for task in ORDER if task not in summary["attempted"]]
        helpers.save_json(directory / "summary.json", summary)
    require(summary["status"] == "completed", "本轮技术失败；首次档案保留，禁止续跑")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="check", choices=["check", "run"])
    parser.add_argument("--execute-review", action="store_true")
    parser.add_argument("--freeze-sha256")
    args = parser.parse_args()
    try:
        if args.action == "run":
            execute(args.execute_review, args.freeze_sha256)
            print(json.dumps({"technical_status": "completed", "content_adjudicated": False}))
        else:
            require(not args.execute_review, "执行开关不能用于check")
            manifest, bindings, _ = check()
            if args.freeze_sha256:
                verify_freeze(bindings, args.freeze_sha256)
            print(json.dumps({"status": "local_check_passed", "batch_id": BATCH,
                              "planned_requests": 4, "api_calls_in_this_check": 0,
                              "freeze_verified": bool(args.freeze_sha256), "bindings": bindings}, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print("ERROR: " + str(error))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
