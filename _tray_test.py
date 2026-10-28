# -*- coding: utf-8 -*-
"""Проверяем, доходит ли иконка до трея на самом деле.

isSystemTrayAvailable() и создание QSystemTrayIcon ещё не значат, что
значок появился: Windows может его скрыть или не принять. Смотрим, что
сообщает сама Windows через Shell_NotifyIcon.
"""
import sys
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import QTimer

app = QApplication(sys.argv)

print("isSystemTrayAvailable:", QSystemTrayIcon.isSystemTrayAvailable())
print("supportsMessages     :", QSystemTrayIcon.supportsMessages())

# иконку берём прямо из файла, как делает плеер
icon = QIcon(r'C:\tgplayer\dist\TGPlayer\tgplayer.ico')
print("иконка не пустая     :", not icon.isNull())
print("размеры              :", [(s.width(), s.height()) for s in icon.availableSizes()])

tray = QSystemTrayIcon(icon)
tray.setToolTip("TGPlayer — проверка трея")
menu = QMenu()
menu.addAction("Тест")
tray.setContextMenu(menu)
tray.show()

print("visible после show() :", tray.isVisible())
print("geometry             :", tray.geometry())

# Даём Windows время и проверяем ещё раз
def check():
    print("через 2 с, visible   :", tray.isVisible())
    print("geometry             :", tray.geometry())
    # Пробуем показать уведомление — если трей работает, оно появится
    tray.showMessage("TGPlayer", "Если вы это видите, трей работает",
                     QSystemTrayIcon.MessageIcon.Information, 3000)
    QTimer.singleShot(4000, app.quit)

QTimer.singleShot(2000, check)
app.exec()
print("конец")
