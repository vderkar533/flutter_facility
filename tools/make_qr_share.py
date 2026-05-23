from __future__ import annotations

import http.server
import os
import shutil
import socketserver
import sys
from pathlib import Path


VERSION = 4
SIZE = VERSION * 4 + 17
DATA_CODEWORDS = 80
ECC_CODEWORDS = 20
MASK = 0


def gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(8):
        if (y >> i) & 1:
            z ^= x << i
    for i in range(14, 7, -1):
        if (z >> i) & 1:
            z ^= 0x11D << (i - 8)
    return z


def rs_generator(degree: int) -> list[int]:
    result = [1]
    root = 1
    for _ in range(degree):
        result = [gf_mul(coef, root) for coef in result] + [0]
        for i in range(len(result) - 1):
            result[i + 1] ^= result[i]
        root = gf_mul(root, 2)
    return result


def rs_remainder(data: list[int], degree: int) -> list[int]:
    divisor = rs_generator(degree)
    result = [0] * degree
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor[1:]):
            result[i] ^= gf_mul(coef, factor)
    return result


def bits_to_codewords(bits: list[int]) -> list[int]:
    while len(bits) % 8:
        bits.append(0)
    return [
        sum(bits[i + j] << (7 - j) for j in range(8))
        for i in range(0, len(bits), 8)
    ]


def encode_data(text: str) -> list[int]:
    payload = text.encode("utf-8")
    if len(payload) > 78:
        raise ValueError("URL is too long for this small QR generator")

    bits: list[int] = []
    bits += [0, 1, 0, 0]  # Byte mode.
    bits += [(len(payload) >> i) & 1 for i in range(7, -1, -1)]
    for b in payload:
        bits += [(b >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, DATA_CODEWORDS * 8 - len(bits))

    data = bits_to_codewords(bits)
    pads = [0xEC, 0x11]
    while len(data) < DATA_CODEWORDS:
        data.append(pads[len(data) % 2])
    return data + rs_remainder(data, ECC_CODEWORDS)


def draw_finder(modules: list[list[bool | None]], reserved: list[list[bool]], x: int, y: int) -> None:
    for dy in range(-1, 8):
        for dx in range(-1, 8):
            xx, yy = x + dx, y + dy
            if 0 <= xx < SIZE and 0 <= yy < SIZE:
                reserved[yy][xx] = True
                modules[yy][xx] = (
                    0 <= dx <= 6
                    and 0 <= dy <= 6
                    and (dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4))
                )


def draw_alignment(modules: list[list[bool | None]], reserved: list[list[bool]], cx: int, cy: int) -> None:
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            xx, yy = cx + dx, cy + dy
            reserved[yy][xx] = True
            modules[yy][xx] = max(abs(dx), abs(dy)) != 1


def format_bits() -> int:
    data = (0b01 << 3) | MASK  # Low error correction, mask 0.
    rem = data << 10
    for i in range(14, 9, -1):
        if (rem >> i) & 1:
            rem ^= 0x537 << (i - 10)
    return ((data << 10) | rem) ^ 0x5412


def mask_bit(x: int, y: int) -> bool:
    return (x + y) % 2 == 0


def make_qr_svg(text: str) -> str:
    codewords = encode_data(text)
    bits = [(b >> i) & 1 for b in codewords for i in range(7, -1, -1)]
    modules: list[list[bool | None]] = [[None] * SIZE for _ in range(SIZE)]
    reserved = [[False] * SIZE for _ in range(SIZE)]

    draw_finder(modules, reserved, 0, 0)
    draw_finder(modules, reserved, SIZE - 7, 0)
    draw_finder(modules, reserved, 0, SIZE - 7)
    draw_alignment(modules, reserved, 26, 26)

    for i in range(8, SIZE - 8):
        modules[6][i] = modules[i][6] = i % 2 == 0
        reserved[6][i] = reserved[i][6] = True
    modules[VERSION * 4 + 9][8] = True
    reserved[VERSION * 4 + 9][8] = True

    for i in range(9):
        reserved[8][i] = reserved[i][8] = True
    for i in range(8):
        reserved[8][SIZE - 1 - i] = reserved[SIZE - 1 - i][8] = True

    bit_index = 0
    direction = -1
    x = SIZE - 1
    while x > 0:
        if x == 6:
            x -= 1
        y = SIZE - 1 if direction == -1 else 0
        while 0 <= y < SIZE:
            for xx in (x, x - 1):
                if not reserved[y][xx]:
                    value = bit_index < len(bits) and bits[bit_index] == 1
                    modules[y][xx] = value ^ mask_bit(xx, y)
                    bit_index += 1
            y += direction
        direction *= -1
        x -= 2

    fb = format_bits()
    coords1 = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(14 - i, 8) for i in range(9, 15)]
    coords2 = [(SIZE - 1 - i, 8) for i in range(8)] + [(8, SIZE - 15 + i) for i in range(8, 15)]
    for i, (x, y) in enumerate(coords1):
        modules[y][x] = ((fb >> i) & 1) == 1
    for i, (x, y) in enumerate(coords2):
        modules[y][x] = ((fb >> i) & 1) == 1

    scale = 12
    quiet = 4
    rects = []
    for y, row in enumerate(modules):
        for x, value in enumerate(row):
            if value:
                rects.append(f'<rect x="{(x + quiet) * scale}" y="{(y + quiet) * scale}" width="{scale}" height="{scale}"/>')

    total = (SIZE + quiet * 2) * scale
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" width="{total}" height="{total}">',
            '<rect width="100%" height="100%" fill="#fff"/>',
            '<g fill="#000">',
            *rects,
            "</g>",
            "</svg>",
        ]
    )


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("Usage: make_qr_share.py <url> <apk-path> [share-dir]")
    url = sys.argv[1]
    apk = Path(sys.argv[2])
    share_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("qr_download")
    share_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(apk, share_dir / "facility-service-management.apk")
    (share_dir / "qr.svg").write_text(make_qr_svg(url), encoding="utf-8")
    (share_dir / "index.html").write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Facility Service Management APK</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #1f2933; }}
    img {{ width: min(72vw, 360px); height: auto; }}
    a {{ display: inline-block; margin-top: 16px; font-size: 18px; }}
  </style>
</head>
<body>
  <h1>Facility Service Management APK</h1>
  <p>Scan this QR code or tap the link below from an Android phone on the same Wi-Fi network.</p>
  <img src="qr.svg" alt="APK download QR code">
  <p><a href="{url}">Download APK</a></p>
</body>
</html>
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
