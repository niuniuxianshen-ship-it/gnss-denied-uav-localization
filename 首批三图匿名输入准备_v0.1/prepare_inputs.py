"""Prepare this three-image batch locally; --verify-only never writes files."""

import argparse
from collections import Counter
from datetime import datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import sys
import zlib

import PIL
from PIL import Image, ImageCms, ImageOps, PngImagePlugin, features


BASE = Path(__file__).resolve().parent
PROJECT = BASE.parent
INPUT_DIR = BASE / "模型输入"
MAPPING = BASE / "管理" / "管理映射.json"
SOURCES = (
    ("IMG001", "027", "候选替换_v0.1/替换池材料_v0.1/027/027.jpg",
     "5f75ba522e50bf2a6ab7836653d1182b0eb848511edaf064c02d249c4c5716a8"),
    ("IMG002", "SCREEN01", "证据优先核验_SCREEN01_20261015/SCREEN01.jpg",
     "dc9bad81e137b56e9d1da4ecd580e6a4d0109a5f153db90072684634a9e1c412"),
    ("IMG003", "SCREEN03", "汉中汉江候选核验_20261015/SCREEN03.jpg",
     "ad836ae10a691869af5006a594c262bc9dd14e7390820992bdf1eccf0c087daf"),
)
DOCUMENTS = (
    "首批三图可行性批次设计修订_待审查_v0.1.md",
    "SCREEN03_工作组合评估_20261015.md",
    "替换候选资格核验_v0.1.md",
    "证据优先核验_SCREEN01_20261015/核验记录.md",
    "汉中汉江候选核验_20261015/核验记录.md",
)
# Audited generic IEC sRGB profile, not a photographer- or location-specific profile.
APPROVED_ICC_SHA256 = "2b3aa1645779a9e634744faf9b01e9102b0c9b88fd6deced7934df86b949af7e"
METHOD = (
    "Pillow decode; ImageOps.exif_transpose; fresh Image.frombytes RGB; "
    "lossless PNG compress_level=6; no resize, crop, enhancement or JPEG re-encode; "
    "retain only the hash-approved generic sRGB ICC when present, or add an sRGB "
    "chunk for an explicitly sRGB EXIF source; no other source metadata copied"
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def doc_hashes():
    return [{"path": p, "sha256": sha256((PROJECT / p).read_bytes())}
            for p in DOCUMENTS]


def reference(source):
    """Read only pinned originals. Return clean pixels plus explicit colour policy."""
    anon_id, work_id, relpath, expected_hash = source
    data = (PROJECT / relpath).read_bytes()
    if sha256(data) != expected_hash:
        raise ValueError(f"Original SHA-256 mismatch: {work_id}")
    with Image.open(BytesIO(data)) as original:
        original.load()
        if original.mode != "RGB" or getattr(original, "n_frames", 1) != 1:
            raise ValueError(f"Unsupported mode or multiple frames: {work_id}")
        exif = original.getexif()
        orientation = exif.get(274)
        if orientation not in (None, 1, 2, 3, 4, 5, 6, 7, 8):
            raise ValueError(f"Unrecognised orientation: {work_id}")
        sub = exif.get_ifd(34665) if 34665 in exif else {}
        icc = original.info.get("icc_profile")
        if icc and sha256(icc) != APPROVED_ICC_SHA256:
            raise ValueError(f"ICC requires a new management review: {work_id}")
        colour = "source_has_no_icc_or_explicit_srgb_tag; RGB values unchanged"
        srgb = not icc and sub.get(40961) == 1
        if icc:
            profile = ImageCms.ImageCmsProfile(BytesIO(icc))
            colour = "retain audited generic IEC sRGB ICC byte-for-byte"
            profile_info = {
                "name": ImageCms.getProfileName(profile).strip(),
                "manufacturer": ImageCms.getProfileManufacturer(profile).strip(),
                "copyright": ImageCms.getProfileCopyright(profile).strip(),
            }
        else:
            profile_info = None
        if srgb:
            colour = "source EXIF ColorSpace=1; RGB unchanged; anonymous sRGB chunk"
        with ImageOps.exif_transpose(original) as oriented:
            clean = Image.frombytes("RGB", oriented.size, oriented.tobytes())
        record = {
            "anonymous_id": anon_id, "original_work_id": work_id,
            "source": {"project_relative_path": relpath,
                       "absolute_path": str((PROJECT / relpath).resolve()),
                       "sha256": sha256(data), "bytes": len(data),
                       "size": list(original.size), "mode": original.mode,
                       "orientation_tag": orientation,
                       "source_info_keys": sorted(original.info),
                       "source_has_gps_ifd": 34853 in exif,
                       "exif_color_space": sub.get(40961),
                       "icc_sha256": sha256(icc) if icc else None,
                       "icc_bytes": len(icc) if icc else 0},
            "processing": {"method": METHOD, "colour_policy": colour,
                           "retained_icc_description": profile_info,
                           "orientation_applied": orientation not in (None, 1),
                           "pixel_sha256": sha256(clean.tobytes())},
        }
    return clean, icc, srgb, record


def png_chunks(data):
    """Inspect the actual container, including CRCs and trailing bytes."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a PNG file")
    pos, chunks = 8, []
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError("Truncated PNG chunk")
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise ValueError("Truncated PNG data")
        crc = struct.unpack(">I", data[end - 4:end])[0]
        if zlib.crc32(data[pos + 4:end - 4]) & 0xffffffff != crc:
            raise ValueError("PNG CRC mismatch")
        chunks.append(kind.decode("ascii"))
        pos = end
        if kind == b"IEND":
            if length != 0 or pos != len(data):
                raise ValueError("Unexpected IEND payload or trailing bytes")
            break
    if not chunks or chunks[0] != "IHDR" or chunks[-1] != "IEND":
        raise ValueError("Missing PNG header/end")
    return Counter(chunks)


def inspect_output(path, pixels, icc, srgb):
    data = path.read_bytes()
    counts = png_chunks(data)
    allowed = {"IHDR", "IDAT", "IEND"}
    if icc:
        allowed.add("iCCP")
    if srgb:
        allowed.add("sRGB")
    if set(counts) != allowed or counts["IHDR"] != 1 or counts["IEND"] != 1:
        raise ValueError(f"Unexpected PNG chunks: {path.name}: {dict(counts)}")
    if (icc and counts["iCCP"] != 1) or (srgb and counts["sRGB"] != 1):
        raise ValueError("Duplicate colour-profile chunks")
    with Image.open(BytesIO(data)) as output:
        output.load()
        if output.mode != pixels.mode or output.size != pixels.size:
            raise ValueError("Output mode or dimensions changed")
        if output.tobytes() != pixels.tobytes():
            raise ValueError("Decoded output pixels differ from oriented original")
        if len(output.getexif()) or output.text:
            raise ValueError("Output EXIF or text metadata remains")
        expected_keys = {"icc_profile"} if icc else ({"srgb"} if srgb else set())
        if set(output.info) != expected_keys:
            raise ValueError("Unexpected decoded metadata")
        if output.info.get("icc_profile") != icc:
            raise ValueError("Colour profile differs")
        if srgb and output.info.get("srgb") != 0:
            raise ValueError("Incorrect sRGB chunk")
    return {"project_relative_path": path.relative_to(PROJECT).as_posix(),
            "absolute_path": str(path.resolve()), "sha256": sha256(data),
            "bytes": len(data), "size": list(pixels.size), "mode": "RGB",
            "png_chunk_counts": dict(counts),
            "checks": {"decodable": True, "full_pixels_equal": True,
                       "dimensions_and_orientation_equal": True,
                       "colour_policy_preserved": True,
                       "no_exif_gps_xmp_or_text_chunks": True,
                       "crc_and_no_trailing_bytes": True}}


def execute(verify_only):
    ids = [s[0] for s in SOURCES]
    if len(ids) != len(set(ids)) or len(ids) != 3:
        raise ValueError("Anonymous IDs must be three distinct values")
    output_paths = [INPUT_DIR / f"{s[0]}.png" for s in SOURCES]
    if not verify_only and any(p.exists() for p in [MAPPING, *output_paths]):
        raise FileExistsError("Outputs already exist; use --verify-only; no overwrite")
    if INPUT_DIR.exists() and any(p not in output_paths for p in INPUT_DIR.iterdir()):
        raise ValueError("Model-input folder contains unexpected files")
    documents_before = doc_hashes()
    mapping = json.loads(MAPPING.read_text(encoding="utf-8")) if verify_only else None
    if mapping and (mapping["source_documents"] != documents_before or
                    mapping["script_sha256"] != sha256(Path(__file__).read_bytes())):
        raise ValueError("Recorded documents or preparation script changed")
    records = []
    for source, target in zip(SOURCES, output_paths):
        clean, icc, srgb, record = reference(source)
        try:
            if not verify_only:
                INPUT_DIR.mkdir(exist_ok=True)
                png_info = PngImagePlugin.PngInfo()
                if srgb:
                    png_info.add(b"sRGB", b"\x00")
                with target.open("xb") as stream:
                    clean.save(stream, format="PNG", pnginfo=png_info,
                               icc_profile=icc, compress_level=6, optimize=False)
            record["input"] = inspect_output(target, clean, icc, srgb)
        finally:
            clean.close()
        if sha256((PROJECT / source[2]).read_bytes()) != source[3]:
            raise ValueError("An original changed during preparation")
        if mapping and record != mapping["images"][len(records)]:
            raise ValueError("Output/mapping mismatch")
        records.append(record)
    if documents_before != doc_hashes():
        raise ValueError("Source documents changed during preparation")
    if mapping and len(mapping["images"]) != 3:
        raise ValueError("Mapping must contain exactly three images")
    if not verify_only:
        MAPPING.parent.mkdir(exist_ok=True)
        mapping = {"status": "anonymous inputs prepared; not submitted; batch not frozen",
                   "created_at": datetime.now().astimezone().isoformat(),
                   "python": sys.version, "pillow": PIL.__version__,
                   "jpeg_codec": features.version_codec("jpg"),
                   "zlib": zlib.ZLIB_VERSION,
                   "script_sha256": sha256(Path(__file__).read_bytes()),
                   "source_documents": documents_before,
                   "condition_reuse": "both conditions use this same PNG file per image",
                   "images": records,
                   "limitations": ["not a frozen or submitted batch",
                                   "cross-device/software colour rendering not verified",
                                   "IMG003 source has no ICC or explicit sRGB declaration",
                                   "visual landmarks/text intentionally retained",
                                   "no claim of eliminating prior knowledge of public photos"]}
        with MAPPING.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(mapping, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return {"operation": "verified_read_only" if verify_only else "prepared",
            "image_count": len(records),
            "images": [{"id": r["anonymous_id"], "size": r["input"]["size"],
                        "bytes": r["input"]["bytes"], "sha256": r["input"]["sha256"],
                        "chunks": r["input"]["png_chunk_counts"]} for r in records]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args.verify_only), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
