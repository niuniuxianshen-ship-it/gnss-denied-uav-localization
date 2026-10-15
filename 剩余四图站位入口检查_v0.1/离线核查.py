"""Local-only archive verification. Never sends requests or reads credentials."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Check this local evidence archive only.")
    parser.add_argument("--report", help="New report path relative to this archive; no overwrite.")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    record = json.loads((base / "执行与访问记录.json").read_text(encoding="utf-8"))
    baseline = json.loads((base / "输入原件哈希_检查前.json").read_text(encoding="utf-8"))
    checks = []
    for item in baseline["inputs"]:
        p = Path(item["path"])
        ok = p.is_file() and digest(p) == item["sha256"] and p.stat().st_size == item["bytes"]
        checks.append({"check": "input_unchanged", "path": str(p), "passed": ok})
    cases = record["cases"]
    checks.append({"check": "four_subjects", "passed": sorted(c["id"] for c in cases) == ["001", "002", "007", "019"]})
    for case in cases:
        fmt = "%Y-%m-%d %H:%M:%S UTC"
        duration = (dt.datetime.strptime(case["end_utc"], fmt) - dt.datetime.strptime(case["start_utc"], fmt)).total_seconds()
        checks.append({"check": "subject_limit", "id": case["id"], "objects": len(case["objects"]), "seconds": duration, "passed": len(case["objects"]) <= 3 and 0 <= duration <= 480})
        texts = {}
        for key, relative in case["transcripts"].items():
            txt_path = base / relative
            json_path = txt_path.with_name(txt_path.name.replace("_转录.txt", ".json"))
            raw = json.loads(json_path.read_text(encoding="utf-8"))["tool_return"]
            text = txt_path.read_bytes().decode("utf-8")
            texts[key] = text
            checks.append({"check": "exact_tool_transcript", "path": str(txt_path), "passed": isinstance(raw, str) and text == raw})
        for quote in case["related_quotes"]:
            checks.append({"check": "quote_present", "id": case["id"], "quote": quote["text"], "passed": any(quote["text"] in t for t in texts.values())})
        checks.append({"check": "description_matches_original", "id": case["id"], "passed": case["old_description"] == case["returned_description"]})
    checks.append({"check": "tool_call_and_object_counts", "passed": len(record["tool_calls"]) == 8 and sum(len(c["objects"]) for c in cases) == 12})
    for name in ["四图材料提取表.md", "使用与核查说明.md"]:
        body = (base / name).read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", body):
            if target.startswith(("https://", "http://")):
                continue
            normalized = target[1:] if target.startswith("/") and len(target) > 2 and target[2] == ":" else target
            p = Path(normalized)
            if not p.is_absolute():
                p = base / p
            if args.report and p.resolve() == (base / args.report).resolve() and not p.exists():
                continue  # Generated report links are checked after this invocation.
            checks.append({"check": "local_link_exists", "document": name, "path": str(p), "passed": p.is_file()})
    checks.append({"check": "no_model_calls_recorded", "passed": record["scope"]["model_calls"] == 0})
    checks.append({"check": "no_temporary_files", "passed": not any(p.suffix in (".pyc", ".tmp", ".part") or p.name == "__pycache__" for p in base.rglob("*"))})
    result = {"status": "pass" if all(c["passed"] for c in checks) else "fail", "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "scope": "Local mechanical verification only. No evidence or qualification adjudication.", "checks": checks}
    if args.report:
        output = (base / args.report).resolve()
        if not output.is_relative_to(base):
            raise ValueError("Report must stay inside this evidence directory.")
        with output.open("xb") as stream:
            stream.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1

if __name__ == "__main__":
    raise SystemExit(main())
