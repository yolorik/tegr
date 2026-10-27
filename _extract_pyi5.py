# -*- coding: utf-8 -*-
"""Распаковка player_deepseek_v2.pyc из установщика 18.10.

Факт из hexdump: записи TOC идут блоком по 48 байт:
    [4] offset  [4] csize  [4] usize  [4] cflag  [1] тип  [31] имя+pad
Никакой внешней длины записи нет — первая четвёрка это offset.
Разбираем именно так, длина блока 48 = 16 (поля) + 1 (тип) + 31 (имя).
"""
import struct
import zlib
import os

EXE = r'C:\tgplayer\_inst_probe\TGPlayerV2.exe'
OUT_DIR = r'C:\tgplayer\_extracted_1810'
MAGIC = b'MEI\x0c\x0b\x0a\x0b\x0e'
BLOCK = 48

os.makedirs(OUT_DIR, exist_ok=True)
data = open(EXE, 'rb').read()
cp = data.rfind(MAGIC)
_, total_len, _o, toc_len, pyver = struct.unpack('!8sIIII', data[cp:cp + 24])
pkg_start = cp + 24 - total_len
toc = data[cp - toc_len:cp]
print("пакет с %d, TOC %d байт = %d блоков по %d\n"
      % (pkg_start, len(toc), len(toc) // BLOCK, BLOCK))

entries = []
for i in range(len(toc) // BLOCK):
    b = toc[i * BLOCK:(i + 1) * BLOCK]
    off, csize, usize, cflag = struct.unpack('!IIII', b[:16])
    typ = b[16:17].decode('latin1')
    name = b[17:].rstrip(b'\x00').decode('utf-8', 'replace')
    entries.append((name, off, csize, usize, cflag, typ))
    print("  %-36s off=%-10d csize=%-9d usize=%-9d cflag=%d тип=%r"
          % (name, off, csize, usize, cflag, typ))

for name, off, csize, usize, cflag, typ in entries:
    if 'player_deepseek' not in name:
        continue
    blob = data[pkg_start + off:pkg_start + off + csize]
    if cflag:
        blob = zlib.decompress(blob)
    out = os.path.join(OUT_DIR, name.strip() + '.pyc')
    open(out, 'wb').write(blob)
    print("\nсохранён:", out, len(blob), "байт (usize=%d)" % usize)

for name, off, csize, usize, cflag, typ in entries:
    if not name.endswith('.pyz'):
        continue
    blob = data[pkg_start + off:pkg_start + off + csize]
    if cflag:
        blob = zlib.decompress(blob)
    out = os.path.join(OUT_DIR, 'PYZ.pyz')
    open(out, 'wb').write(blob)
    print("сохранён:", out, len(blob), "байт")
