"""Build an offline image-by-condition plan. No model calls are implemented."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


def checked_id(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError(label + " must be a 1-64 character ASCII identifier")
    return value


def checked_inputs(config, section, id_key, base):
    items = config.get(section)
    if not isinstance(items, list) or not items:
        raise ValueError(section + " must be a non-empty list")
    seen = set()
    checked = []
    for index, item in enumerate(items):
        label = section + "[" + str(index) + "]"
        if not isinstance(item, dict):
            raise ValueError(label + " must be an object")
        identifier = checked_id(item.get(id_key), label + "." + id_key)
        if identifier in seen:
            raise ValueError("duplicate " + id_key + ": " + identifier)
        seen.add(identifier)
        filename = item.get("path")
        expected = item.get("sha256")
        notice = item.get("material_notice")
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError(label + ".path must be explicit")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise ValueError(label + ".sha256 must contain 64 hexadecimal characters")
        if not isinstance(notice, str) or not notice.strip():
            raise ValueError(label + ".material_notice is required")
        path = Path(filename)
        if not path.is_absolute():
            path = base / path
        path = path.resolve()
        if not path.is_file():
            raise ValueError("input missing or not a file: " + str(path))
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected.lower():
            raise ValueError("SHA-256 mismatch: " + str(path))
        checked.append({id_key: identifier, "path": path.as_posix(),
                        "sha256": actual, "material_notice": notice})
    return checked


def prepare(config_path, output_path):
    config_path = config_path.resolve()
    output_path = output_path.resolve()
    if output_path.exists():
        raise FileExistsError("output already exists; refusing overwrite: " + str(output_path))
    if not output_path.parent.is_dir():
        raise ValueError("output parent directory does not exist")
    raw = config_path.read_bytes()
    config = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(config, dict) or type(config.get("schema_version")) is not int or config["schema_version"] != 1:
        raise ValueError("configuration must be an object with schema_version 1")
    batch_id = checked_id(config.get("batch_id"), "batch_id")
    notice = config.get("material_notice")
    if not isinstance(notice, str) or not notice.strip():
        raise ValueError("material_notice is required")
    images = checked_inputs(config, "images", "image_id", config_path.parent)
    conditions = checked_inputs(config, "conditions", "condition_id", config_path.parent)
    tasks = []
    for image in images:
        for condition in conditions:
            tasks.append({
                "batch_id": batch_id,
                "image_id": image["image_id"],
                "condition_id": condition["condition_id"],
                "task_id": batch_id + ":" + image["image_id"] + ":" + condition["condition_id"],
                "image_input": dict(image),
                "condition_input": dict(condition),
                "status": "\u5c1a\u672a\u8c03\u7528"
            })
    plan = {
        "schema_version": 1,
        "plan_type": "offline_image_condition_plan",
        "batch_id": batch_id,
        "material_notice": notice,
        "config_input": {
            "path": config_path.as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest()
        },
        "image_count": len(images),
        "condition_count": len(conditions),
        "planned_task_count": len(tasks),
        "model_call_count": 0,
        "tasks": tasks
    }
    data = (json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with output_path.open("xb") as handle:
        handle.write(data)
    return plan


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        plan = prepare(args.config, args.output)
    except (OSError, ValueError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps({
        "mode": "offline",
        "planned_tasks": plan["planned_task_count"],
        "model_calls": 0,
        "output": args.output.resolve().as_posix()
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
