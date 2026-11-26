# -*- coding: utf-8 -*-
"""生成扩展图标（extension/icons/icon{16,48,128}.png）：不依赖 PIL，手写 PNG。

图形：深色圆角方块 + 蓝色圆环（定位）+ 中心的红色录制点。
用法：python extension/tools/make_icons.py
"""
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BG = (26, 26, 30)
BLUE = (79, 140, 255)
RED = (255, 92, 92)
SIZE = 128


def inside_rounded_square(x, y, n, radius):
    cx = min(max(x, radius), n - 1 - radius)
    cy = min(max(y, radius), n - 1 - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def render(n):
    """n×n 的 RGBA 像素：4×4 超采样抗锯齿。"""
    px = []
    c = (n - 1) / 2
    ring_outer, ring_inner, dot = n * 0.40, n * 0.28, n * 0.15
    for y in range(n):
        row = []
        for x in range(n):
            r = g = b = a = 0
            for sy in (0.25, 0.75):
                for sx in (0.25, 0.75):
                    px_, py_ = x + sx, y + sy
                    col = None
                    if inside_rounded_square(px_, py_, n, n * 0.22):
                        d = ((px_ - c - 0.5) ** 2 + (py_ - c - 0.5) ** 2) ** 0.5
                        if d <= dot:
                            col = RED
                        elif ring_inner <= d <= ring_outer:
                            col = BLUE
                        else:
                            col = BG
                    if col:
                        r, g, b, a = r + col[0], g + col[1], b + col[2], a + 255
            row.append((r // 4, g // 4, b // 4, a // 4))
        px.append(row)
    return px


def png_bytes(px):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)

    raw = b''.join(b'\x00' + bytes(v for p in row for v in p) for row in px)
    ihdr = struct.pack('>IIBBBBB', len(px[0]), len(px), 8, 6, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')


if __name__ == '__main__':
    out = ROOT / 'icons'
    out.mkdir(exist_ok=True)
    for n in (16, 48, 128):
        (out / f'icon{n}.png').write_bytes(png_bytes(render(n)))
        print('wrote', out / f'icon{n}.png')
