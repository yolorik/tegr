# -*- coding: utf-8 -*-
"""Проверка полноты извлечения: сколько кода реально доступно из 18.10."""
import marshal
import types
import dis
import os

PYC = r'C:\tgplayer\_inst_probe\TGPlayerV2.exe_extracted\player_deepseek_v2.pyc'
code = marshal.loads(open(PYC, 'rb').read()[16:])

classes = []
functions = []
total_instr = 0


def walk(co, depth=0):
    global total_instr
    total_instr += len(list(dis.get_instructions(co)))
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            if c.co_name and not c.co_name.startswith('<'):
                if depth == 1:
                    classes.append(c.co_name)
                elif depth == 2:
                    functions.append(c.co_name)
            walk(c, depth + 1)


walk(code)

print("=== объём модуля 18.10 ===")
print("  строк исходника (co_firstlineno максимум):", end=" ")
mx = [0]
def maxline(co):
    mx[0] = max(mx[0], co.co_firstlineno)
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            maxline(c)
maxline(code)
print(mx[0])
print("  байткода (инструкций):", total_instr)
print("  классов:", len(classes))
print("  методов:", len(functions))
print()
print("=== все классы 18.10 ===")
for c in classes:
    print("  ", c)
