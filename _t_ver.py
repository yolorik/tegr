# -*- coding: utf-8 -*-
"""Проверка сравнения версий и разбора тегов."""
import sys
sys.path.insert(0, r'C:\tgplayer')
from tgplayer.core import updates as U
from tgplayer.core import version

print("=== parse_version ===")
for s in ('v2.5.0', '2.5.0d', 'v2.4.0c', '2.5.1', 'v3.0', 'мусор', '2.5.0-beta.1'):
    print("  %-14s -> %s" % (s, U.parse_version(s)))

print()
print("=== is_newer (наша версия %s) ===" % version.VERSION)
cases = [
    ('v2.5.0', False, 'тот же номер, буква у нас есть -> не новее'),
    ('v2.5.0d', False, 'ровно наша'),
    ('v2.6.0', True,  'новее по числам'),
    ('v2.4.0', False, 'старее'),
    ('v2.5.1', True,  'новее патч'),
    ('v3.0.0', True,  'новее мажор'),
    ('v2.5.0e', True,  'та же версия, буква новее'),
]
ok = True
for tag, want, note in cases:
    got = U.is_newer(tag)
    mark = 'OK  ' if got == want else 'FAIL'
    if got != want: ok = False
    print("  %s %-10s -> %-5s (%s)" % (mark, tag, got, note))

print()
print("=== канал stable ===")
tests = [
    ('описание\nSTABLE', 'stable', True),
    ('описание\nSTABLE\n', 'stable', True),
    ('описание\n  stable  \n', 'stable', True),
    ('описание без метки', 'stable', False),
    ('описание без метки', 'beta', True),
]
for body, ch, want in tests:
    got = U._channel_ok(body, ch)
    mark = 'OK  ' if got == want else 'FAIL'
    if got != want: ok = False
    print("  %s канал=%-7s %-28r -> %s" % (mark, ch, body[:26], got))

print()
print("ИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
