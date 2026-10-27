# -*- coding: utf-8 -*-
"""Разбор TOC PyInstaller с автоподбором схемы записи.

Формат записи различается между версиями PyInstaller. Перебираем варианты
положения имени и берём тот, где имена выглядят осмысленно.
"""
import struct
import zlib
import os

EXE = r'C:\tgplayer\_inst_probe\TGPlayerV2.exe'
OUT_DIR = r'C:\tgplayer\_extracted_1810'
MAGIC = b'MEI\x0c\x0b\x0a\x0b\x0e'

os.makedirs(OUT_DIR, exist_ok=True)
data = open(EXE, 'rb').read()
cookie_pos = data.rfind(MAGIC)
magic, total_len, toc_off, toc_len, pyver = struct.unpack(
    '!8sIIII', data[cookie_pos:cookie_pos + 24])
pkg_start = cookie_pos + 24 - total_len
toc = data[cookie_pos - toc_len:cookie_pos]
print("TOC %d байт, пакет с %d, pyver %d" % (len(toc), pkg_start, pyver))


def try_scheme(name_pos):
    out = []
    pos = 0
    while pos < len(toc):
        if pos + 4 > len(toc):
            return None
        (elen,) = struct.unpack('!i', toc[pos:pos + 4])
        if elen <= 0 or pos + 4 + elen > len(toc):
            return None
        raw = toc[pos + 4:pos + 4 + elen]
        if len(raw) < name_pos:
            return None
        off, csize, usize = struct.unpack('!III', raw[4:16])
        name = raw[name_pos:].rstrip(b'\x00')
        try:
            name_s = name.decode('utf-8')
        except UnicodeDecodeError:
            return None
        if not name_s or any(ord(ch) < 32 for ch in name_s):
            return None
        out.append((name_s, off, csize, usize))
        pos += 4 + elen
    return out or None


for name_pos, desc in ((21, 'с cflag'), (17, 'без cflag')):
    res = try_scheme(name_pos)
    if not res:
        continue
    print("\nсхема подошла: %s (имя с байта %d), записей %d"
          % (desc, name_pos, len(res)))
    for n, off, cs, us in res:
        print("  %-38s off=%-10d csize=%-9d usize=%d" % (n, off, cs, us))
    for n, off, cs, us in res:
        if 'player' not in n.lower() and not n.endswith('.pyz'):
            continue
        blob = data[pkg_start + off:pkg_start + off + cs]
        try:
            blob = zlib.decompress(blob)
        except Exception:
            pass
        safe = n.replace('/', '_').replace('\\', '_')
        out = os.path.join(OUT_DIR, safe + ('.pyz' if n.endswith('.pyz') else '.pyc'))
        open(out, 'wb').write(blob)
        print("  сохранён:", out, len(blob), "байт")
    break
else:
    print("ни одна схема не подошла")
