"""Каталоги программы: где лежат данные и где ресурсы.

Это самый нижний слой: он не знает ни про Qt, ни про Telethon, ни про
настройки. Всё остальное в программе спрашивает дорогу отсюда.

Разделение важное: данных два вида.
  * Рядом с программой (`app_dir`) живёт то, с чем работает человек:
    сессия Telegram, кеш видео, настройки, история, журнал. В собранном
    .exe это папка с .exe.
  * Внутри сборки (`bundle_dir`) живёт то, что положил туда PyInstaller:
    библиотеки mpv, значок. В onefile это временная папка распаковки,
    которая удаляется при выходе.

Где «рядом с программой», задаёт точка входа (app.py) через
`set_base_dir`: у модуля нет своего файла-ориентира, потому что в сборке
он лежит в `_internal`, а данные должны лежать рядом с .exe.
"""
import os
import sys

from PyQt6.QtGui import QIcon

# Каталог данных: назначается из точки входа сразу после её старта.
# Пока не назначен — берётся каталог запуска, это же и есть «рядом».
_base_dir = None

# Каталог запуска программы (там, где лежит точка входа или .exe).
# От него считается всё, что не назначено явно.
_launch_dir = None


def set_base_dir(path):
    """Назначает каталог данных: сессия, кеш, настройки, журнал."""
    global _base_dir
    _base_dir = os.path.abspath(path) if path else None
    return _base_dir


def app_dir():
    """Каталог рядом с .exe (или с исходником) — туда можно писать.

    В собранном PyInstaller-приложении __file__ модуля указывает во
    временную папку распаковки, которая удаляется при выходе. Сессия
    Telegram и кеш видео должны лежать рядом с .exe, иначе они теряются.
    Поэтому ориентир — не файл модуля, а каталог запуска.
    """
    if _base_dir:
        return _base_dir
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    if _launch_dir:
        return _launch_dir
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir():
    """Каталог с ресурсами: в onefile это временная распаковка PyInstaller."""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', app_dir())
    if _launch_dir:
        return _launch_dir
    return app_dir()


def set_launch_dir(path):
    """Запоминает каталог запуска — ориентир для данных и ресурсов."""
    global _launch_dir
    _launch_dir = os.path.abspath(path) if path else None
    return _launch_dir


# Каталог, где лежит сама программа (.exe или исходники). Отличается от
# каталога данных, когда данные ушли в профиль из-за прав доступа.
_program_dir = None


def set_program_dir(path):
    """Назначает каталог программы — там ресурсы вроде tgplayer.ico."""
    global _program_dir
    _program_dir = os.path.abspath(path) if path else None
    return _program_dir


def program_dir():
    """Каталог самой программы (не данных)."""
    if _program_dir:
        return _program_dir
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    if _launch_dir:
        return _launch_dir
    return os.path.dirname(os.path.abspath(__file__))


def load_app_icon():
    """Иконка приложения из tgplayer.ico, иначе None.

    Ищем в трёх местах по порядку:

    * во внутреннем каталоге сборки — PyInstaller кладёт --add-data в
      _internal, а не в корень;
    * рядом с .exe — там файл лежит в переносимой сборке;
    * в каталоге данных (профиле) — на случай, если программу поставили
      туда, куда писать нельзя (Program Files), и данные ушли в профиль.

    Последний пункт появился из-за реальной ошибки: после установки в
    Program Files данные переехали в профиль, иконки там не было, и в
    трее показывался запасной значок вместо настоящего.
    """
    for base in (bundle_dir(), app_dir(), program_dir()):
        if not base:
            continue
        path = os.path.join(base, 'tgplayer.ico')
        if os.path.exists(path):
            icon = QIcon(path)
            if not icon.isNull():
                return icon
    return None
