"""Generate two synthetic PNG teaching images; they are not research samples."""
import argparse
from pathlib import Path
import struct
import zlib


def chunk(kind, data):
    checksum = zlib.crc32(kind + data) & 0xffffffff
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def make_png(identifier):
    width, height = 96, 64
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            color = (235, 240, 245)
            if identifier == "DEMO01":
                if 14 <= x < 46 and 14 <= y < 46:
                    color = (190, 55, 65)
                elif 60 <= x < 82 and 8 <= y < 56:
                    color = (45, 105, 195)
            else:
                if (x - 68) ** 2 + (y - 30) ** 2 <= 17 ** 2:
                    color = (225, 165, 35)
                elif 10 <= x <= 46 and 50 - (x - 28) // 2 >= y >= 14 + abs(x - 28):
                    color = (45, 145, 100)
            rows.extend(color)
    label = (identifier + "; teaching only; not a research sample.").encode("ascii")
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"tEXt", b"Description\x00" + label)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    folder = args.output_dir.resolve()
    if not folder.is_dir():
        parser.error("output directory must already exist")
    paths = [folder / (identifier + ".png") for identifier in ("DEMO01", "DEMO02")]
    if any(path.exists() for path in paths):
        parser.error("a demo image already exists; refusing overwrite")
    for identifier, path in zip(("DEMO01", "DEMO02"), paths):
        with path.open("xb") as handle:
            handle.write(make_png(identifier))
    print("Generated two teaching PNG images; no research samples or model calls.")


if __name__ == "__main__":
    main()
