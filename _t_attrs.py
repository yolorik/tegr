# -*- coding: utf-8 -*-
"""Ищем обращения к несуществующим атрибутам модулей после распила.

Распил породил ошибки вида `theme.set_theme.ui_scale(...)` — модуль
`theme` заменял бывший класс. Проверяем статически: все `X.y.z`, где X —
модуль проекта, а y у него нет.
"""
import ast
import os
import sys

sys.path.insert(0, r'C:\tgplayer')

MODULES = {
    'config': 'tgplayer.core.config',
    'theme': 'tgplayer.core.theme',
    'history': 'tgplayer.core.history',
    'utils': 'tgplayer.core.utils',
    'paths': 'tgplayer.core.paths',
    'proxy': 'tgplayer.core.proxy',
    'log': 'tgplayer.core.log',
    'version': 'tgplayer.core.version',
}

real = {}
for alias, modpath in MODULES.items():
    try:
        mod = __import__(modpath, fromlist=['x'])
        real[alias] = set(dir(mod))
    except Exception as e:
        print("не импортирован %s: %s" % (modpath, e))

problems = []
roots = [r'C:\tgplayer\player_deepseek.py']
for root, dirs, files in os.walk(r'C:\tgplayer\tgplayer'):
    for f in files:
        if f.endswith('.py'):
            roots.append(os.path.join(root, f))

for path in roots:
    try:
        tree = ast.parse(open(path, encoding='utf-8').read())
    except SyntaxError as e:
        print("СИНТАКСИС: %s: %s" % (path, e))
        continue
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)):
            continue
        inner = node.value
        if not isinstance(inner.value, ast.Name):
            continue
        alias = inner.value.id
        if alias not in real:
            continue
        attr = inner.attr
        if attr not in real[alias]:
            problems.append((path, node.lineno, "%s.%s.%s" % (alias, attr, node.attr)))

if problems:
    print("НАЙДЕНЫ обращения к несуществующим атрибутам:")
    for p, ln, text in problems:
        print("  %s:%d  %s" % (os.path.basename(p), ln, text))
else:
    print("Таких ошибок нет.")
