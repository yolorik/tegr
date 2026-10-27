# -*- coding: utf-8 -*-
"""Декомпиляция player_deepseek_v2.pyc через API decompyle3."""
import sys
import marshal
import importlib.util

PYC = r'C:\tgplayer\_inst_probe\TGPlayerV2.exe_extracted\player_deepseek_v2.pyc'
OUT = r'C:\tgplayer\_src_1810.py'

data = open(PYC, 'rb').read()
# заголовок pyc для 3.11: magic(4) + flags(4) + timestamp(4) + size(4)
code = marshal.loads(data[16:])
print("модуль:", code.co_filename, "имена:", len(code.co_names))

from decompyle3.main import decompile
import decompyle3.main as M

with open(OUT, 'w', encoding='utf-8') as out:
    decompile(code, (3, 11, 0), out=out)
print("записано:", OUT)
