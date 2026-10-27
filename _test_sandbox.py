"""Тесты не должны трогать настоящие настройки и сессию.

Проверки создают настоящие окна (PlayerWindow, MainWindow), а те при
создании и закрытии сохраняют геометрию и настройки в settings.json
рядом с программой. Из-за этого боевой файл настроек уже дважды
обнулялся до одной строки — терялись громкость, тема, прокси и скрытые
каналы.

Импортируйте этот модуль ПЕРВЫМ в любом тесте, который создаёт окна:
он уводит каталог данных во временный, и всё пишется туда.

    from _test_sandbox import use_sandbox
    use_sandbox()
"""
import os
import shutil
import sys
import tempfile

_ACTIVE = False


def use_sandbox():
    """Уводит данные (настройки, историю, кеш) во временный каталог.

    Возвращает путь к нему: по нему видно, куда именно писали.
    """
    global _ACTIVE
    if _ACTIVE:
        return os.environ.get('TGPLAYER_TEST_DIR')
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)

    tmp = tempfile.mkdtemp(prefix='tgplayer_test_')
    os.environ['TGPLAYER_TEST_DIR'] = tmp

    # Настройки копируем, чтобы окна видели привычные значения, но
    # записывали их обратно в копию, а не в боевой файл.
    from tgplayer.core import paths
    paths.set_launch_dir(root)
    paths.set_base_dir(tmp)
    try:
        src = os.path.join(root, 'settings.json')
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(tmp, 'settings.json'))
        src_proxy = os.path.join(root, 'proxy.txt')
        if os.path.exists(src_proxy):
            shutil.copy2(src_proxy, os.path.join(tmp, 'proxy.txt'))
    except Exception:
        pass

    _ACTIVE = True
    return tmp


def cleanup(path=None):
    """Убирает временный каталог (звать в конце теста)."""
    global _ACTIVE
    target = path or os.environ.get('TGPLAYER_TEST_DIR')
    if not target or not _ACTIVE:
        return
    try:
        shutil.rmtree(target, ignore_errors=True)
    except Exception:
        pass
    _ACTIVE = False


if __name__ == '__main__':
    print('песочница:', use_sandbox())
    from tgplayer.core import settings
    print('настройки читаются из песочницы, ключей:',
          len(settings.load()))
    print('файл настроек:', settings.settings_path())
