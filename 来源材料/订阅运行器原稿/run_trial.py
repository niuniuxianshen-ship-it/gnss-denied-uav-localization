"""A/B/C development trial using Codex CLI ChatGPT subscription login."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
MODEL = "gpt-6.1-sol"
ZONE = timezone(timedelta(hours=8))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_environment():
    env = os.environ.copy()
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_BASE_URL"):
        env.pop(name, None)
    return env


def disabled_skills():
    # Discover folder names only, never read skill content or authentication files.
    home = Path.home()
    roots = [home / ".agents" / "skills", home / ".codex" / "skills",
             home / ".codex" / "plugins" / "cache"]
    return [{"path": str(path.parent), "enabled": False}
            for root in roots if root.exists() for path in sorted(root.rglob("SKILL.md"))]


def command(executable, image, workspace, output, skills):
    overrides = [
        'model_provider="openai"', 'forced_login_method="chatgpt"',
        'model_reasoning_effort="low"', 'web_search="disabled"',
        'features.shell_tool=false', 'features.unified_exec=false',
        'features.multi_agent=false', 'features.plugins=false',
        'memories.use_memories=false', 'memories.generate_memories=false',
        'project_doc_max_bytes=0', 'history.persistence="none"',
        'skills.config=[' + ','.join('{path=' + json.dumps(s["path"], ensure_ascii=False)
                                    + ',enabled=false}' for s in skills) + ']'
    ]
    args = [executable, "exec", "--ignore-user-config", "--ephemeral", "--json",
            "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
            "--model", MODEL, "--image", str(image), "--cd", str(workspace),
            "--output-last-message", str(output)]
    for value in overrides:
        args.extend(["--config", value])
    args.append("-")
    # Windows CreateProcess has a command-line size limit.
    if len(subprocess.list2cmdline(args)) > 30000:
        raise ValueError("Skill override list is too long; do not run until config isolation is adapted.")
    return args


def inspect_events(raw):
    events, malformed = [], 0
    for line in raw.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            malformed += 1
    tools = []
    allowed = {"agent_message", "reasoning", "error"}
    for event in events:
        item = event.get("item", {})
        kind = item.get("type")
        if kind and kind not in allowed:
            tools.append(kind)
    return {"completed": any(e.get("type") == "turn.completed" for e in events),
            "usage_events": [e for e in events if e.get("type") == "turn.completed"],
            "failure_events": [e for e in events if e.get("type") == "turn.failed"],
            "transport_or_error_events": [e for e in events if e.get("type") == "error"],
            "item_warning_events": [e for e in events if e.get("item", {}).get("type") == "error"],
            "unexpected_item_types": sorted(set(tools)), "non_json_lines": malformed,
            "note": "A completed turn can have recovered transport errors. Error items are not tool calls. Manually inspect all warnings; output completeness and tool use remain separate checks."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Use subscription quota for up to three CLI runs.")
    args = parser.parse_args()
    executable = shutil.which("codex")
    if not executable:
        raise ValueError("Codex CLI not found.")
    env = clean_environment()
    version = subprocess.run([executable, "--version"], capture_output=True, encoding="utf-8", errors="replace", env=env)
    login = subprocess.run([executable, "login", "status"], capture_output=True, encoding="utf-8", errors="replace", env=env)
    status = login.stdout + login.stderr
    if login.returncode != 0 or "Logged in using ChatGPT" not in status:
        raise ValueError("ChatGPT login not confirmed. Run codex login locally; no API-key fallback.")
    image = ROOT / "inputs" / "001.png"
    image_bytes = image.read_bytes()
    appendix_bytes = (ROOT / "inputs" / "appendix.md").read_bytes()
    appendix = appendix_bytes.decode("utf-8-sig")
    prompts = {v: (ROOT / "prompts" / (v + ".txt")).read_text(encoding="utf-8-sig") for v in "ABC"}
    skills = disabled_skills()
    manifest = {"mode": "subscription_development", "model_requested": MODEL,
        "cli_version": version.stdout.strip(), "login": "ChatGPT", "reasoning_effort": "low",
        "image_sha256": digest(image_bytes), "appendix_sha256": digest(appendix_bytes),
        "prompt_sha256": {v: digest(p.encode("utf-8")) for v, p in prompts.items()},
        "disabled_skill_count": len(skills), "history": "fresh ephemeral sessions",
        "output_token_limit": "CLI default; not asserted equal to API 4096",
        "retry_policy": "no runner retries; CLI transport may retry internally",
        "limitations": "CLI base system prompt and managed policy remain; tool isolation must be inspected."}
    command(executable, image, ROOT, ROOT / "unused.txt", skills)
    if not args.run:
        print(json.dumps({**manifest, "model_runs": 0}, ensure_ascii=False, indent=2))
        return 0
    run = ROOT / "runs" / datetime.now(ZONE).strftime("%Y%m%d_%H%M%S_%f")
    run.mkdir(parents=True, exist_ok=False)
    write_json(run / "manifest.json", manifest)
    (run / "appendix.md").write_bytes(appendix_bytes)
    summary = []
    with tempfile.TemporaryDirectory(prefix="trial_", dir=run) as td:
        workspace = Path(td)
        # Only an anonymous image is placed in the agent working directory.
        local_image = workspace / "001.png"
        local_image.write_bytes(image_bytes)
        for variant, prompt in prompts.items():
            output = run / (variant + "_output.txt")
            cmd = command(executable, local_image, workspace, output, skills)
            submitted = prompt + "\n\n" + appendix
            (run / (variant + "_input.txt")).write_text(submitted, encoding="utf-8")
            write_json(run / (variant + "_command.json"), cmd)
            print("Starting " + variant + " using subscription login.", flush=True)
            started = time.perf_counter()
            record = {"variant": variant, "started_at": datetime.now(ZONE).isoformat()}
            with (run / (variant + "_events.jsonl")).open("w", encoding="utf-8") as stdout, (run / (variant + "_stderr.txt")).open("w", encoding="utf-8") as stderr:
                try:
                    result = subprocess.run(cmd, input=submitted, encoding="utf-8", env=env,
                                            stdout=stdout, stderr=stderr, timeout=300)
                    record["exit_code"] = result.returncode
                except subprocess.TimeoutExpired:
                    record["error"] = "timeout; may have consumed quota"
            record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            raw = (run / (variant + "_events.jsonl")).read_text(encoding="utf-8", errors="replace")
            record["checks"] = inspect_events(raw)
            record["has_output"] = output.exists() and bool(output.read_text(encoding="utf-8").strip())
            summary.append(record)
            write_json(run / "summary.json", summary)
            checks = record["checks"]
            if record.get("exit_code") != 0 or not record["has_output"] or not checks["completed"] or checks["failure_events"] or checks["unexpected_item_types"]:
                print("Stopped: review first-run events before another attempt.")
                print(str(run))
                return 1
    print("Saved: " + str(run))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("Setup failed: " + str(exc))
        raise SystemExit(1)
