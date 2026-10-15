"""Create a local teaching upload plan; no API operations are implemented."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent
NOTICE = "教学准备，不是研究样本或实验结果；未上传、未调用模型、未冻结"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("批次、图片及条件编号必须为1至64位ASCII字母、数字、下划线或连字符")
    return value


def prepare(manifest_path, output_path):
    manifest_path = Path(manifest_path).resolve()
    output_path = Path(output_path).resolve()
    if not output_path.is_relative_to(ROOT) or not output_path.parent.is_dir():
        raise ValueError("输出必须位于本程序目录内，且父目录已经存在")
    if output_path.exists():
        raise FileExistsError("输出已存在，拒绝覆盖: " + str(output_path))

    raw_manifest = manifest_path.read_bytes()
    manifest = json.loads(raw_manifest.decode("utf-8-sig"))
    tasks = manifest["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks必须为非空列表")
    if manifest["text_draft_count"] != len(tasks):
        raise ValueError("文本任务数量与登记不一致")
    if manifest["model_call_count"] != 0:
        raise ValueError("本轮只接受尚未调用的教学任务")

    sources = {manifest_path: {"path": manifest_path.as_posix(),
                              "sha256": sha(raw_manifest), "bytes": len(raw_manifest)}}
    cache = {}

    def read_input(record, text=False):
        path = Path(record["path"])
        if not path.is_absolute():
            path = manifest_path.parent / path
        path = path.resolve()
        expected = record["sha256"]
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise ValueError("预期SHA-256格式错误: " + str(path))
        if path not in cache:
            raw = path.read_bytes()
            cache[path] = raw
            sources[path] = {"path": path.as_posix(), "sha256": sha(raw), "bytes": len(raw)}
        if sources[path]["sha256"] != expected.lower():
            raise ValueError("输入哈希不符: " + str(path))
        if text:
            cache[path].decode("utf-8-sig")
        return dict(sources[path])

    uploads = {}
    links = []
    seen_tasks = set()
    seen_combinations = set()
    for task in tasks:
        batch = identifier(task["batch_id"])
        image_id = identifier(task["image_id"])
        condition = identifier(task["condition_id"])
        task_id = task["task_id"]
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("任务编号必须为非空字符串")
        combination = (batch, image_id, condition)
        if task_id in seen_tasks or combination in seen_combinations:
            raise ValueError("任务编号或图片条件组合重复")
        if task["status"] != "尚未调用":
            raise ValueError("输入任务并非尚未调用: " + task_id)
        seen_tasks.add(task_id)
        seen_combinations.add(combination)

        image = read_input(task["image_input"])
        text = read_input(task["text_draft"], text=True)
        upload_key = (batch, image_id)
        if upload_key in uploads:
            previous = uploads[upload_key]
            if (previous["local_path"], previous["sha256"]) != (image["path"], image["sha256"]):
                raise ValueError("同批图片编号对应不同路径或哈希: " + batch + ":" + image_id)
        else:
            suffix = Path(image["path"]).suffix.lower()
            if not suffix:
                raise ValueError("图片文件缺少扩展名: " + image["path"])
            uploads[upload_key] = {
                "batch_id": batch, "image_id": image_id,
                "local_path": image["path"], "sha256": image["sha256"],
                "bytes": image["bytes"], "upload_filename": image_id + suffix,
                "file_id": None, "status": "待上传"
            }
        links.append({
            "task_id": task_id, "batch_id": batch, "image_id": image_id,
            "condition_id": condition, "text_path": text["path"],
            "text_sha256": text["sha256"], "status": "尚未调用"
        })

    for path, record in sources.items():
        if sha(path.read_bytes()) != record["sha256"]:
            raise ValueError("输入在准备期间发生变化: " + str(path))

    plan = {
        "schema_version": 1, "artifact_type": "offline_teaching_files_upload_plan",
        "material_notice": NOTICE, "generated_at_UTC": datetime.now(timezone.utc).isoformat(),
        "source_manifest": sources[manifest_path], "source_inputs": list(sources.values()),
        "association_key": ["batch_id", "image_id"],
        "planned_upload_file_count": len(uploads), "associated_location_task_count": len(links),
        "actual_upload_count": 0, "actual_model_call_count": 0,
        "uploads": list(uploads.values()), "tasks": links
    }
    with output_path.open("xb") as handle:
        handle.write((json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return plan


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        plan = prepare(args.manifest, args.output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps({key: plan[key] for key in (
        "planned_upload_file_count", "associated_location_task_count",
        "actual_upload_count", "actual_model_call_count")}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
