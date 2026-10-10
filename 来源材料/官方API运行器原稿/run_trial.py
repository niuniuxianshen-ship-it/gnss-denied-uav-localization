"""One-image A/B/C development trial; default mode is offline."""
import argparse
import atexit
import getpass
import hashlib
import json
import os
from pathlib import Path
import struct
import time
from datetime import datetime, timezone, timedelta
from urllib import request, error
import zlib
import uuid

ROOT = Path(__file__).resolve().parent
ENDPOINT = "https://api.openai.com/v1/responses"
MODEL = "gpt-6.1-sol"
SHANGHAI = timezone(timedelta(hours=8))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def check_png(data):
    """Accept only metadata-free PNG chunks; also check lengths and CRCs."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Input must be a PNG.")
    offset, chunks, size = 8, [], None
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("Truncated PNG chunk.")
        kind = data[offset + 4:offset + 8]
        content = data[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", data[end - 4:end])[0]
        if zlib.crc32(kind + content) & 0xffffffff != crc:
            raise ValueError("PNG CRC mismatch.")
        if kind not in (b"IHDR", b"IDAT", b"IEND"):
            raise ValueError("PNG contains an unsupported or metadata chunk.")
        if kind == b"IHDR":
            if chunks or length != 13:
                raise ValueError("Invalid IHDR.")
            size = struct.unpack(">II", content[:8])
        chunks.append(kind)
        offset = end
        if kind == b"IEND":
            if length != 0 or offset != len(data):
                raise ValueError("Invalid IEND or trailing data.")
            break
    if not size or chunks[-1:] != [b"IEND"] or b"IDAT" not in chunks:
        raise ValueError("Incomplete PNG.")
    return {"width": size[0], "height": size[1], "sha256": sha(data),
            "bytes": len(data), "chunk_types": sorted({x.decode() for x in chunks})}


def build_payload(prompt, appendix, file_id):
    return {
        "model": MODEL,
        "reasoning": {"effort": "low"},
        "max_output_tokens": 4096,
        "store": False,
        "tools": [],
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": prompt + "\n\n" + appendix},
            {"type": "input_image", "detail": "high",
             "file_id": file_id}
        ]}]
    }


def upload_image(image, key):
    boundary = "trial_" + uuid.uuid4().hex
    prefix = ("--" + boundary + '\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nvision\r\n'
        + "--" + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="001.png"\r\n'
        + 'Content-Type: image/png\r\n\r\n').encode("ascii")
    body = prefix + image + ("\r\n--" + boundary + "--\r\n").encode("ascii")
    req = request.Request("https://api.openai.com/v1/files", data=body, method="POST", headers={
        "Authorization": "Bearer " + key, "Content-Type": "multipart/form-data; boundary=" + boundary})
    with request.urlopen(req, timeout=180) as reply:
        result = json.loads(reply.read())
    file_id = result.get("id", "")
    if not file_id.startswith("file-") or not all(c.isalnum() or c in "-_" for c in file_id):
        raise ValueError("Invalid upload file ID.")
    return file_id


def delete_upload(file_id, key, run_dir):
    req = request.Request("https://api.openai.com/v1/files/" + file_id, method="DELETE",
                          headers={"Authorization": "Bearer " + key})
    result = {"file_id": file_id, "deleted": False}
    try:
        with request.urlopen(req, timeout=30) as reply:
            result["deleted"] = json.loads(reply.read()).get("deleted") is True
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    save_json(run_dir / "upload_cleanup.json", result)
    if not result["deleted"]:
        print("Upload cleanup not confirmed. Review upload_cleanup.json and remove this file in your API account.")


def extract_text(response):
    return "\n".join(part.get("text", "")
        for item in response.get("output", []) if item.get("type") == "message"
        for part in item.get("content", []) if part.get("type") == "output_text")


def response_checks(response, text, variant):
    fields = {
        "A": ["省级行政区：", "置信分数："],
        "B": ["可见线索：", "不可读或无法辨认的内容：", "支持判断的线索：",
              "冲突线索：", "省级行政区：", "置信分数："],
        "C": ["可见线索：", "不可读或无法辨认的内容：", "候选排序及支持依据：",
              "区分候选所需的证据：", "冲突线索：", "最终回答：", "第一候选置信分数："]
    }
    output_types = [item.get("type") for item in response.get("output", [])]
    return {"response_status": response.get("status"),
            "incomplete_details": response.get("incomplete_details"),
            "empty_text": not bool(text.strip()),
            "missing_field_labels": [field for field in fields[variant] if field not in text],
            "unexpected_output_types": [kind for kind in output_types if kind not in ("message", "reasoning")],
            "note": "Field-label checks only; manually review values, candidates and evidence. No automatic scoring."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Send up to three paid API requests.")
    args = parser.parse_args()
    image = (ROOT / "inputs" / "001.png").read_bytes()
    info = check_png(image)
    appendix_bytes = (ROOT / "inputs" / "appendix.md").read_bytes()
    appendix = appendix_bytes.decode("utf-8-sig")
    payloads, prompts = {}, {}
    for variant in "ABC":
        prompts[variant] = (ROOT / "prompts" / (variant + ".txt")).read_text(encoding="utf-8-sig")
        payloads[variant] = build_payload(prompts[variant], appendix, "[file ID assigned only during --run]")
    if not args.run:
        print(json.dumps({"mode": "offline_check", "network_requests": 0,
            "image": info, "appendix_sha256": sha(appendix_bytes),
            "model": MODEL, "reasoning_effort": "low", "max_output_tokens": 4096,
            "image_detail": "high", "tools": [], "variants": list(payloads),
            "prompt_sha256": {v: sha(p.encode("utf-8")) for v, p in prompts.items()}},
            ensure_ascii=False, indent=2))
        return 0
    print("This sends 3 independent image requests to OpenAI and incurs API usage.")
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        if not os.isatty(0):
            raise ValueError("Use an interactive terminal or set OPENAI_API_KEY locally.")
        key = getpass.getpass("OpenAI API key (hidden; not saved): ").strip()
    if not key:
        raise ValueError("Missing API key.")
    run_dir = ROOT / "runs" / datetime.now(SHANGHAI).strftime("%Y%m%d_%H%M%S_%f")
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "appendix.md").write_bytes(appendix_bytes)
    save_json(run_dir / "manifest.json", {
        "created_at": datetime.now(SHANGHAI).isoformat(), "endpoint": ENDPOINT,
        "image": info, "appendix_sha256": sha(appendix_bytes),
        "script_sha256": sha(Path(__file__).read_bytes()), "order": list(payloads),
        "automatic_retries": 0, "mode": "development_trial", "no_ground_truth_loaded": True,
        "note": "Same settings and bytes; API inference is not guaranteed deterministic."})
    try:
        file_id = upload_image(image, key)
    except Exception as exc:
        save_json(run_dir / "upload_error.json", {"error_type": type(exc).__name__,
            "http_status": getattr(exc, "code", None), "automatic_retries": 0,
            "note": "Upload failed or outcome unknown. Check API Files before retrying; no model calls sent."})
        print("Upload failed; no model requests sent. Records: " + str(run_dir))
        return 1
    save_json(run_dir / "upload.json", {"file_id": file_id, "purpose": "vision", "image_sha256": sha(image)})
    atexit.register(delete_upload, file_id, key, run_dir)
    summaries = []
    for variant, payload in payloads.items():
        payload["input"][0]["content"][1]["file_id"] = file_id
        save_json(run_dir / (variant + "_request.json"), payload)
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(ENDPOINT, data=encoded, method="POST", headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json"})
        print("Request " + variant + " started.", flush=True)
        started = time.perf_counter()
        summary = {"variant": variant, "started_at": datetime.now(SHANGHAI).isoformat(),
                   "request_body_sha256": sha(encoded)}
        try:
            with request.urlopen(req, timeout=180) as reply:
                raw = reply.read()
                summary["request_id"] = reply.headers.get("x-request-id")
                summary["http_status"] = reply.status
            (run_dir / (variant + "_response.json")).write_bytes(raw)
            response = json.loads(raw)
            text = extract_text(response)
            (run_dir / (variant + "_output.txt")).write_text(text, encoding="utf-8")
            summary.update({"returned_model": response.get("model"),
                            "usage": response.get("usage"),
                            "checks": response_checks(response, text, variant)})
        except error.HTTPError as exc:
            summary.update({"error_type": "HTTPError", "http_status": exc.code,
                            "request_id": exc.headers.get("x-request-id")})
            # Never save authorization headers or echo the key in errors.
            body = exc.read().decode("utf-8", errors="replace").replace(key, "[REDACTED]")
            (run_dir / (variant + "_error.txt")).write_text(body, encoding="utf-8")
        except Exception as exc:
            summary.update({"error_type": type(exc).__name__,
                "error_message": "Request or response processing failed; no automatic retry. A timeout may still be billed."})
        summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        summaries.append(summary)
        save_json(run_dir / "summary.json", summaries)
        print("Request " + variant + " recorded.", flush=True)
        # Preserve evidence and stop on failure/truncation rather than spend more.
        checks = summary.get("checks", {})
        if "error_type" in summary or checks.get("response_status") != "completed" or checks.get("empty_text") or checks.get("unexpected_output_types"):
            print("Stopped after an error/incomplete response. Review saved records before a new attempt.")
            print(str(run_dir))
            return 1
    print("Trial saved: " + str(run_dir))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("Setup error: " + type(exc).__name__ + ". Check input files and local key setup.")
        raise SystemExit(1)
