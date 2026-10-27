# -*- coding: utf-8 -*-
"""Распаковка PyInstaller onefile-архива: достаём player_deepseek_v2.pyc.

Структура CArchive (PyInstaller 5.x/6.x):
    [PKG: данные файлов][TOC][struct: magic 8 + TOTAL_LEN 4 + TOC_LEN 4 +
                            PYVER 4 + LICEVERSION 4]

Cookie лежит в САМОМ КОНЦЕ архива, поэтому TOC вычисляется от конца, а не
от начала. В прошлой попытке я считал от magic — отсюда ерунда.
"""
import struct
import zlib
import os
import sys

EXE = r'C:\tgplayer\_inst_probe\TGPlayerV2.exe'
OUT_DIR = r'C:\tgplayer\_extracted_1810'
MAGIC = b'MEI\x0c\x0b\x0a\x0b\x0e'

os.makedirs(OUT_DIR, exist_ok=True)
data = open(EXE, 'rb').read()

# Cookie ищем с конца: он последний в файле (иногда плюс выравнивание).
cookie_pos = data.rfind(MAGIC)
if cookie_pos < 0:
    sys.exit("cookie MEI не найден")
print("cookie на позиции", cookie_pos, "из", len(data))

magic, total_len, toc_off, toc_len, pyver = struct.unpack(
    '!8sIIII', data[cookie_pos:cookie_pos + 24])
print("total_len=%d toc_off=%d toc_len=%d pyver=%d"
      % (total_len, toc_off, toc_len, pyver))

# total_len — длина всей упаковки, cookie_end = начало пакета + total_len
pkg_start = cookie_pos + 24 - total_len
toc_start = cookie_pos - toc_len
toc_end = cookie_pos
print("пакет: %d..%d, TOC: %d..%d" % (pkg_start, cookie_pos, toc_start, toc_end))

toc = data[toc_start:toc_end]
pos = 0
entries = []
while pos < len(toc):
    (elen,) = struct.unpack('!i', toc[pos:pos + 4])
    if elen <= 0:
        break
    raw = toc[pos + 4:pos + 4 + elen]
    # structlen(4) offset(4) csize(4) usize(4) cflag(4) typ(1) name
    slen, off, csize, usize, cflag = struct.unpack('!IIIII', raw[:20])
    typ = raw[20:21]
    name = raw[21:].rstrip(b'\x00').decode('utf-8', 'replace')
    entries.append((name, off, csize, usize, cflag, typ))
    pos += 4 + elen

print("\nзаписей:", len(entries))
interesting = []
for name, off, csize, usize, cflag, typ in entries:
    tag = typ.decode('latin1')
    print("  %-38s off=%-10d csize=%-9d usize=%-9d %s"
          % (name, off, csize, usize, tag))
    if 'player' in name.lower() or name.endswith('.pyz'):
        interesting.append((name, off, csize, usize, cflag, typ))

for name, off, csize, usize, cflag, typ in interesting:
    blob = data[pkg_start + off:pkg_start + off + csize]
    if cflag:
        try:
            blob = zlib.decompress(blob)
        except Exception as e:
            print("  распаковка не удалась:", e)
    safe = name.replace('/', '_').replace('\\', '_')
    out = os.path.join(OUT_DIR, safe + ('.pyz' if name.endswith('.pyz') else '.pyc'))
    open(out, 'wb').write(blob)
    print("сохранён:", out, len(blob), "байт")
