# -*- coding: utf-8 -*-
"""Распаковка PyInstaller-архива по фактическим смещениям имён.

Из hexdump видно: записи идут с шагом 48 байт, имя начинается со смещения
16 от начала данных записи (после offset/csize/usize/cflag), тип — прямо
перед именем. Поэтому разбираем адресно, а не «на глаз».
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
_, total_len, _toc_off, toc_len, pyver = struct.unpack('!8sIIII', data[cp:cp + 24])
pkg_start = cp + 24 - total_len
toc = data[cp - toc_len:cp]
print("пакет с %d, TOC %d байт, pyver %d" % (pkg_start, len(toc), pyver))
print(toc[:200].hex())
print()

entries = []
pos = 0
while pos + 4 <= len(toc):
    (elen,) = struct.unpack('!i', toc[pos:pos + 4])
    if elen <= 0:
        break
    raw = toc[pos + 4:pos + 4 + elen]
    print("запись len=%d raw=%s" % (elen, raw.hex()))
    entries.append(raw)
    pos += 4 + elen
print("\nвсего записей:", len(entries))
