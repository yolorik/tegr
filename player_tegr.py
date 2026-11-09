"""Tegr — рабочий вариант с оформлением Paper × Studio.

Точка входа. Использует тот же TelegramBackend и PlayerWindow, что и
остальные окна, но главное окно собрано по-новому: каналы
слева, видео в центре, загрузки и история справа, темы и масштаб — в
настройках.

Запуск:

    python player_tegr.py
"""
import asyncio
import sys

# Локальный фасад готовит окружение: каталог данных, журнал, libmpv и
# пути.
import tgplayer_runtime as base

from PyQt6.QtWidgets import QApplication, QMessageBox

from tgplayer.windows.tegr_window import TegrMainWindow


if __name__ == '__main__':
    base._setup_logging()

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    app = QApplication(sys.argv)

    guard = base._single_instance_guard()
    if guard is None:
        QMessageBox.information(
            None, 'Уже запущено',
            'Плеер уже работает.\n\nВторой экземпляр испортил бы файл '
            'сессии Telegram и занял бы тот же локальный порт.')
        sys.exit(0)

    if base.mpv is None:
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle('Не запустилось')
        box.setText('Не удалось загрузить видеодвижок (libmpv-2.dll).')
        box.setInformativeText(
            'Подробный отчёт — по кнопке ниже. Он же записан в файл '
            'tgplayer.log рядом с программой.')
        box.setDetailedText(base._mpv_diagnosis())
        box.exec()
        sys.exit(1)

    backend = base.TelegramBackend()
    main_win = TegrMainWindow(backend)
    main_win.start_login()

    def _login_cancelled():
        if getattr(main_win, '_shown_after_login', False):
            return
        print('[Вход] отменён — закрываю программу')
        QApplication.instance().quit()

    backend.login_cancelled.connect(_login_cancelled)

    preload = base._Thread(target=base.preload_telethon, daemon=True,
                           name='telethon-preload')
    preload.start()

    thread = base.WorkerThread(backend)
    thread.start()

    def stop_all():
        backend._closed = True
        if backend.loop and backend.loop.is_running():
            backend.loop.call_soon_threadsafe(backend.loop.stop)

    app.aboutToQuit.connect(stop_all)
    code = app.exec()
    stop_all()
    thread.wait(2000)
    sys.exit(code)
