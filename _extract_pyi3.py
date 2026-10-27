# -*- coding: utf-8 -*-
"""Распаковка PyInstaller-архива: достаём скомпилированный player_deepseek_v2.

Структура записи TOC (PyInstaller 6.x, little-endian):
    [длина_записи:i][смещение:I][csize:I][usize:I][cflag:I][тип:c][имя][pad]\0

Имя начинается со смещения 17 от начала данных записи (после cflag 16 байт
и одного байта типа). Никакого магического выравнивания: длина записи уже
включает хвостовой выравнивающий байт.
"""
import struct
import zlib
import os

EXE = r'C:\tgplayer\_inst_probe\TGPlayerV2.exe'
OUT_DIR = r'C:\tgplayer\_extracted_1810'
MAGIC = b'MEI\x0c\x0b\x0a\x0b\x0e'

os.makedirs(OUT_DIR, exist_ok=True)
data = open(EXE, 'rb').read()
cp = data.rfind(MAGIC)
_, total_len, toc_off, toc_len, pyver = struct.unpack('!8sIIII', data[cp:cp + 24])
pkg_start = cp + 24 - total_len
toc = data[cp - toc_len:cp]
print("пакет с %d, TOC %d байт, pyver %d" % (pkg_start, len(toc), pyver))


def parse(name_pos):
    out, pos = [], 0
    while pos + 4 <= len(toc):
        (elen,) = struct.unpack('!i', toc[pos:pos + 4])
        if elen <= 0 or pos + 4 + elen > len(toc):
            return None
        raw = toc[pos + 4:pos + 4 + elen]
        if len(raw) < name_pos + 1:
            return None
        off, csize, usize, cflag = struct.unpack('!IIII', raw[:16])
        try:
            name = raw[name_pos:].rstrip(b'\x00').decode('utf-8')
        except UnicodeDecodeError:
            return None
        if not name or any(ord(c) < 32 for c in name):
            return None
        out.append((name, off, csize, usize, cflag))
        pos += 4 + elen
    return out or None


entries = None
for name_pos in (17, 18, 21):
    entries = parse(name_pos)
    if entries:
        print("имя с байта %d, записей %d" % (name_pos, len(entries)))
        break

if not entries:
    raise SystemExit("TOC не разобран")

for name, off, csize, usize, cflag in entries:
    print("  %-38s off=%-10d csize=%-9d usize=%-9d cflag=%d"
          % (name, off, csize, usize, cflag))

for name, off, csize, usize, cflag in entries:
    if 'player_deepseek' not in name and not name.endswith('.pyz'):
        continue
    blob = data[pkg_start + off:pkg_start + off + csize]
    if cflag:
        blob = zlib.decompress(blob)
    ext = '.pyz' if name.endswith('.pyz') else '.pyc'
    out = os.path.join(OUT_DIR, name.strip('_ ').replace('/', '_') + ext)
    open(out, 'wb').write(blob)
    print("сохранён:", out, len(blob), "байт")
