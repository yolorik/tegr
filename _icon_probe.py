# -*- coding: utf-8 -*-
"""Сравниваем создание иконки в исходниках и в собранном .exe.

Если при запуске из Python значок есть, а из .exe нет — разница может быть
в том, ЧТО именно передаётся в QSystemTrayIcon: путь, QPixmap или иконка
из ресурсов. Проверяем, какая иконка реально доходит в обоих случаях.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):
    APP_HOME = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_HOME = HERE

print(f"frozen        : {getattr(sys, 'frozen', False)}")
print(f"папка программ: {APP_HOME}")
print(f"_MEIPASS      : {getattr(sys, '_MEIPASS', 'нет')}")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon, QPixmap
app = QApplication([])

candidates = []
if getattr(sys, '_MEIPASS', None):
    candidates.append(os.path.join(sys._MEIPASS, 'tgplayer.ico'))
candidates.append(os.path.join(APP_HOME, 'tgplayer.ico'))
candidates.append(os.path.join(HERE, 'tgplayer.ico'))

for p in candidates:
    exists = os.path.exists(p)
    print(f"\nпуть: {p}")
    print(f"  существует: {exists}")
    if not exists:
        continue
    icon = QIcon(p)
    print(f"  isNull: {icon.isNull()}")
    sizes = [(s.width(), s.height()) for s in icon.availableSizes()]
    print(f"  размеры: {sizes}")
    pix = QPixmap(p)
    print(f"  QPixmap пустой: {pix.isNull()}, размер: {pix.width()}x{pix.height()}")
    if sizes:
        big = icon.pixmap(32, 32)
        print(f"  pixmap(32) пустой: {big.isNull()}, {big.width()}x{big.height()}")
