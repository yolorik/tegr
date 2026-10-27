# -*- coding: utf-8 -*-
"""Вытаскиваем из pyc 18.10 все строка и числа — по ним видно настройки.

Декомпилятора под 3.11 нет, но байткод маршалится без проблем: константы
и имена модуля читаются напрямую. Нас интересует загрузчик: сколько было
воркеров, какой таймаут, как поднимался хвост.
"""
import marshal
import types
import sys

PYC = r'C:\tgplayer\_inst_probe\TGPlayerV2.exe_extracted\player_deepseek_v2.pyc'
data = open(PYC, 'rb').read()
code = marshal.loads(data[16:])

strings = set()
numbers = set()


def walk(co):
    for c in co.co_consts:
        if isinstance(c, str):
            strings.add(c)
        elif isinstance(c, (int, float)) and not isinstance(c, bool):
            numbers.add(c)
        elif isinstance(c, types.CodeType):
            walk(c)
        elif isinstance(c, tuple):
            for x in c:
                if isinstance(x, str):
                    strings.add(x)


walk(code)
print("строк:", len(strings), " чисел:", len(numbers))
print()

print("=== строки про загрузчик ===")
keys = ['Кэш', 'воркер', 'хвост', 'индекс', 'moov', 'поток', 'кусок',
        'молчит', 'бросаю', 'ссылка', 'file_reference', 'приоритет']
for s in sorted(strings):
    if any(k.lower() in s.lower() for k in keys):
        print("  ", s)

print()
print("=== подозрительные числа конфигурации (1..200) ===")
interesting = sorted(n for n in numbers if isinstance(n, int) and 1 <= n <= 200)
print("  ", interesting)
