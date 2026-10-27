"""Журнал tgplayer.log рядом с программой.

Раньше вся диагностика шла в консоль, а у собранного .exe консоли нет:
при сбое у пользователя не оставалось ничего. Этот модуль поднимается
первым, ещё до загрузки mpv, — чтобы в журнал попали и ошибки загрузки
библиотеки.

Файл ограничен по размеру и переписывается через старую версию: за
месяцы работы журнал иначе вырос бы до сотен мегабайт.
"""
import builtins
import os
import sys
import time

from . import paths

LOG_NAME = 'tgplayer.log'
LOG_MAX_BYTES = 1024 * 1024      # 1 МБ на файл
LOG_BACKUPS = 2                  # плюс два предыдущих

_log_ready = False


def log_dir():
    """Куда писать журнал: рядом с программой, иначе во временный каталог."""
    base = paths.app_dir()
    try:
        os.makedirs(base, exist_ok=True)
        probe = os.path.join(base, '.log_probe')
        with open(probe, 'w', encoding='utf-8') as f:
            f.write('x')
        os.remove(probe)
        return base
    except OSError:
        # Программу могли поставить в защищённый каталог: тогда пишем во
        # временный, лишь бы журнал был хоть где-то.
        return os.path.join(os.environ.get('TEMP')
                            or os.environ.get('TMP') or '.', 'TGPlayer')


def log_path():
    """Полный путь к файлу журнала."""
    return os.path.join(log_dir(), LOG_NAME)


def _rotate_log(path):
    """Сдвигает старые журналы: .2 -> .3, .1 -> .2, текущий -> .1."""
    try:
        for i in range(LOG_BACKUPS - 1, 0, -1):
            old = '%s.%d' % (path, i)
            new = '%s.%d' % (path, i + 1)
            if os.path.exists(old):
                if os.path.exists(new):
                    os.remove(new)
                os.replace(old, new)
        if os.path.exists(path):
            os.replace(path, path + '.1')
    except OSError:
        pass


def _open_log():
    """Открывает журнал на дозапись, при перерастании — прокручивает."""
    global _log_ready
    path = log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            _rotate_log(path)
        stream = open(path, 'a', encoding='utf-8', errors='replace')
        _log_ready = True
        return stream
    except OSError:
        _log_ready = False
        return None


def log(text, level='INFO'):
    """Пишет строку в журнал. Ошибки записи не мешают работе программы."""
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    line = '%s  %-5s  %s\n' % (stamp, level, text)
    stream = _open_log()
    if stream is None:
        return
    try:
        stream.write(line)
        stream.flush()
    except OSError:
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def log_error(text, exc=None):
    """Пишет ошибку вместе с трассировкой: без неё причина не видна."""
    if exc is not None:
        import traceback
        detail = ''.join(traceback.format_exception(
            type(exc), exc, exc.__traceback__))
        log('%s\n%s' % (text, detail.rstrip()), 'ERROR')
    else:
        log(text, 'ERROR')


def tee_prints():
    """Дублирует print в журнал.

    Делается сразу при импорте, до загрузки mpv: сообщения о том, что не
    поднялась библиотека или не хватило Vulkan, печатаются именно там, и
    без этого в журнал они бы не попали — а это первые сведения при
    разборе «не играет видео».
    """
    real_print = builtins.print
    if getattr(real_print, '_tee', False):
        return

    def tee_print(*args, **kwargs):
        level = kwargs.pop('level', 'INFO')
        try:
            real_print(*args, **kwargs)
        except Exception:
            pass
        try:
            log(' '.join(str(a) for a in args), level)
        except Exception:
            pass

    tee_print._tee = True
    builtins.print = tee_print


def log_ready():
    """Удалось ли открыть журнал: по этому видно, работает ли запись."""
    return _log_ready


def install_log_hooks():
    """Перехватывает необработанные исключения и пишет их в журнал.

    Иначе падение в потоке Qt или в асинхронной задаче проходит молча:
    в консоли его нет у .exe, а пользователь видит только «программа
    закрылась».
    """
    import threading
    import traceback

    def hook(exc_type, exc, tb):
        text = ''.join(traceback.format_exception(exc_type, exc, tb))
        log('Необработанное исключение:\n%s' % text.rstrip(), 'ERROR')
        # Не глотаем полностью: в консоли сообщение тоже нужно.
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook

    def thread_hook(args):
        text = ''.join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback))
        log('Исключение в потоке %r:\n%s'
            % (getattr(args.thread, 'name', '?'), text.rstrip()), 'ERROR')

    try:
        threading.excepthook = thread_hook
    except Exception:
        pass
