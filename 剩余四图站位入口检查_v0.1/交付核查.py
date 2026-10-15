"""Offline delivery check. Does not access the network or run model workers."""
import argparse
import ast
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--report")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    baseline = json.loads((base / "输入原件哈希_检查前.json").read_text(encoding="utf-8"))
    record = json.loads((base / "执行与访问记录.json").read_text(encoding="utf-8"))
    checks = []
    for item in baseline["inputs"]:
        p = Path(item["path"])
        data = p.read_bytes()
        checks.append({"check": "input_unchanged", "path": str(p), "before": item["sha256"], "after": hashlib.sha256(data).hexdigest(), "passed": hashlib.sha256(data).hexdigest() == item["sha256"] and len(data) == item["bytes"]})
    link_count = 0
    output = (base / args.report).resolve() if args.report else None
    if output is not None and not output.is_relative_to(base):
        raise ValueError("Report must stay in this new directory.")
    for name in ["四图材料提取表.md", "使用与核查说明.md"]:
        for target in re.findall(r"\]\(([^)]+)\)", (base / name).read_text(encoding="utf-8")):
            if target.startswith(("https://", "http://")):
                continue
            path = target[1:] if target.startswith("/") and len(target) > 2 and target[2] == ":" else target
            p = Path(path)
            if not p.is_absolute():
                p = base / p
            if output is not None and p.resolve() == output and not p.exists():
                continue
            checks.append({"check": "local_link_exists", "document": name, "path": str(p), "passed": p.is_file()})
            link_count += 1
    source = (base / "离线核查.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module)
    allowed = {"argparse", "datetime", "hashlib", "json", "pathlib", "re", "sys"}
    checks.append({"check": "checker_standard_library_imports", "imports": imports, "passed": set(imports).issubset(allowed)})
    identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    disallowed = {"urllib", "requests", "socket", "subprocess", "environ", "getenv", "__import__", "eval", "exec"}
    checks.append({"check": "no_network_or_credentials_identifiers_in_executable_syntax", "found": sorted(disallowed & (identifiers | attributes)), "passed": not disallowed & (identifiers | attributes)})
    constants = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    checks.append({"check": "no_legacy_runner_path_in_checker", "passed": "worker.py" not in constants and "run_trial.py" not in constants})
    for call in record["tool_calls"]:
        p = Path(call["archive_path"])
        archive = json.loads(p.read_text(encoding="utf-8"))
        txt = p.with_name(p.stem + "_转录.txt")
        checks.append({"check": "exact_tool_transcript", "path": str(p), "passed": archive["tool_return"] == txt.read_bytes().decode("utf-8")})
    for case in record["cases"]:
        texts = [(base / relative).read_text(encoding="utf-8") for relative in case["transcripts"].values()]
        for quote in case["related_quotes"]:
            checks.append({"check": "quote_present_verbatim", "id": case["id"], "quote": quote["text"], "passed": any(quote["text"] in text for text in texts)})
        fmt = "%Y-%m-%d %H:%M:%S UTC"
        duration = (dt.datetime.strptime(case["end_utc"], fmt) - dt.datetime.strptime(case["start_utc"], fmt)).total_seconds()
        checks.append({"check": "subject_limits", "id": case["id"], "seconds": duration, "objects": len(case["objects"]), "passed": 0 <= duration <= 480 and len(case["objects"]) <= 3})
    checks.append({"check": "subjects_and_object_counts", "passed": [c["id"] for c in record["cases"]] == ["001", "002", "007", "019"] and sum(len(c["objects"]) for c in record["cases"]) == 12 and len(record["tool_calls"]) == 8})
    checks.append({"check": "zero_model_calls_recorded", "passed": record["scope"]["model_calls"] == 0 and record["scope"]["credentials_read"] == 0})
    checks.append({"check": "no_one_off_temp_files", "passed": not any(p.suffix in [".tmp", ".part", ".pyc"] or p.name == "__pycache__" for p in base.rglob("*"))})
    manifest = [{"path": str(p.relative_to(base)), "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(base.rglob("*")) if p.is_file()]
    result = {"status": "pass" if all(c["passed"] for c in checks) else "fail", "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "python_version": sys.version.split()[0], "checks": checks, "local_links_checked": link_count, "manifest": manifest, "manifest_note": "Excludes the newly generated report itself.", "previous_false_positive": {"report": "交付核查结果.json", "reason": "Substring requests occurred only in the checker docstring Never sends requests. AST imports and executable identifiers are checked here; old report retained."}, "scope": "Local mechanical checks only; no qualification adjudication, source refresh or model calls."}
    if output is not None:
        with output.open("xb") as f:
            f.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"status": result["status"], "checks": len(checks), "local_links_checked": link_count, "failures": [c for c in checks if not c["passed"]]}, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1

if __name__ == "__main__":
    raise SystemExit(main())
