"""Offline teaching text assembly; no model requests or image conversion."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


NOTICE = "教学准备，不是研究样本或实验结果；尚未调用模型"
SEPARATOR = b"\n\n"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def assemble(manifest_path, appendix_path, output_dir):
    manifest_path = Path(manifest_path).resolve()
    appendix_path = Path(appendix_path).resolve()
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise ValueError("输出目录已存在，拒绝覆盖: " + str(output_dir))
    if not output_dir.parent.is_dir():
        raise ValueError("输出父目录不存在: " + str(output_dir.parent))

    sources = {}
    cache = {}

    def read_source(path, expected=None):
        path = Path(path).resolve()
        if path not in cache:
            raw = path.read_bytes()
            cache[path] = raw
            sources[path] = {"path": path.as_posix(), "sha256": digest(raw)}
        actual = sources[path]["sha256"]
        if expected is not None:
            if (not isinstance(expected, str) or len(expected) != 64
                    or any(c not in "0123456789abcdefABCDEF" for c in expected)):
                raise ValueError("预期SHA-256无效: " + str(path))
            if actual != expected.lower():
                raise ValueError("输入哈希不符: " + str(path))
        return cache[path], dict(sources[path])

    raw_manifest, manifest_record = read_source(manifest_path)
    plan = json.loads(raw_manifest.decode("utf-8"))
    appendix, appendix_record = read_source(appendix_path)
    appendix.decode("utf-8")  # Validate encoding without normalizing newlines.
    tasks = plan["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("任务清单必须包含非空tasks列表")
    if plan["planned_task_count"] != len(tasks):
        raise ValueError("任务数量与清单登记不一致")

    prepared = []
    task_ids = set()
    combinations = set()
    for index, task in enumerate(tasks, 1):
        identifiers = [task[k] for k in
                       ("batch_id", "image_id", "condition_id", "task_id")]
        if any(not isinstance(v, str) or not v for v in identifiers):
            raise ValueError("任务编号必须为非空字符串")
        combination = tuple(identifiers[:3])
        if task["task_id"] in task_ids or combination in combinations:
            raise ValueError("任务编号或图片条件组合重复")
        task_ids.add(task["task_id"])
        combinations.add(combination)

        def resolve_input(record):
            path = Path(record["path"])
            if not path.is_absolute():
                path = manifest_path.parent / path
            return read_source(path, record["sha256"])

        image, image_record = resolve_input(task["image_input"])
        prompt, prompt_record = resolve_input(task["condition_input"])
        prompt.decode("utf-8")
        body = prompt + SEPARATOR + appendix
        filename = "教学文本_{:02d}.txt".format(index)
        prepared.append((filename, body, {
            "batch_id": task["batch_id"],
            "image_id": task["image_id"],
            "condition_id": task["condition_id"],
            "task_id": task["task_id"],
            "image_input": image_record,
            "condition_input": prompt_record,
            "appendix_input": appendix_record,
            "text_draft": {
                "path": (output_dir / filename).as_posix(),
                "sha256": digest(body),
                "bytes": len(body)
            },
            "status": "尚未调用",
            "material_notice": NOTICE
        }))

    # Recheck the exact explicit inputs before writing any output.
    for path, record in sources.items():
        if digest(path.read_bytes()) != record["sha256"]:
            raise ValueError("输入在组装期间发生变化: " + str(path))

    management = {
        "schema_version": 1,
        "artifact_type": "offline_teaching_text_drafts",
        "material_notice": NOTICE,
        "assembly_rule": "提示词原始字节 + 两个LF换行字节 + 附录原始字节；不裁剪原有换行",
        "source_manifest": manifest_record,
        "appendix_input": appendix_record,
        "source_inputs": list(sources.values()),
        "text_draft_count": len(prepared),
        "model_call_count": 0,
        "tasks": [item[2] for item in prepared]
    }
    output_dir.mkdir()  # Exclusive: even an empty existing directory is refused.
    for filename, body, _ in prepared:
        with (output_dir / filename).open("xb") as handle:
            handle.write(body)
    with (output_dir / "组装清单_管理用.json").open("xb") as handle:
        handle.write((json.dumps(management, ensure_ascii=False, indent=2)
                      + "\n").encode("utf-8"))
    return management


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="仅离线组装教学文本草案")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--appendix", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        management = assemble(args.manifest, args.appendix, args.output_dir)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps({
        "text_draft_count": management["text_draft_count"],
        "model_call_count": 0,
        "output_dir": str(Path(args.output_dir).resolve())
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
