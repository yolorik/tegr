# -*- coding: utf-8 -*-
"""Сравниваем логику 18.10 и текущего кода по именам методов и строкам.

Параметры совпали, значит различия — в коде. Ищем, каких механизмов
в 18.10 НЕ было (их и добавили после 18.10), и наоборот.
"""
import marshal
import types
import os
import re

PYC = r'C:\tgplayer\_inst_probe\TGPlayerV2.exe_extracted\player_deepseek_v2.pyc'
code = marshal.loads(open(PYC, 'rb').read()[16:])


def collect(co, names, strs, depth=0):
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            if c.co_name and not c.co_name.startswith('<'):
                names.add(c.co_name)
            collect(c, names, strs, depth + 1)
        elif isinstance(c, str):
            strs.add(c)


names_old, strs_old = set(), set()
collect(code, names_old, strs_old)

# текущий код
files = []
for root, dirs, fs in os.walk(r'C:\tgplayer\tgplayer'):
    for f in fs:
        if f.endswith('.py'):
            files.append(os.path.join(root, f))
files.append(r'C:\tgplayer\player_deepseek.py')

names_new, strs_new = set(), set()
for p in files:
    src = open(p, encoding='utf-8').read()
    for m in re.finditer(r'def\s+(\w+)', src):
        names_new.add(m.group(1))
    for m in re.finditer(r'"([^"\n]{6,})"|\'([^\'\n]{6,})\'', src):
        s = m.group(1) or m.group(2)
        if s:
            strs_new.add(s)

print("методов в 18.10:", len(names_old), " в текущем:", len(names_new))
print()
print("=== методы, которых НЕ БЫЛО в 18.10 (добавлены после) ===")
for n in sorted(names_new - names_old):
    if not n.startswith('__'):
        print("  ", n)

print()
print("=== методы, которые БЫЛИ в 18.10, но пропали сейчас ===")
for n in sorted(names_old - names_new):
    if not n.startswith('__'):
        print("  ", n)

print()
print("=== сообщения журнала, которых не было в 18.10 (добавлены после) ===")
for s in sorted(strs_new - strs_old):
    if s.startswith('[') and len(s) < 90:
        print("  ", s)
