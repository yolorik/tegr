"""Настоящая скорость загрузки из Telegram через текущий прокси.

Раньше в конфиге стояли цифры «0.38 МБ/с через прокси, 2.1 напрямую» —
они получены при разработке и по MTProto. Здесь меряем то, что есть
сейчас: качаем реальный кусок ролика через Telethon тем же путём, что
и плеер, и считаем скорость.

Пишет отчёт в _real_speed.txt.
"""
import asyncio
import io
import sys
import time

sys.path.insert(0, r"C:\tgplayer")

from PyQt6.QtWidgets import QApplication

import player_deepseek as P

OUT = []


def say(text=''):
    OUT.append(str(text))
    print(text)


async def measure(backend, app):
    # Ждём подключения.
    for _ in range(120):
        await asyncio.sleep(0.5)
        if getattr(backend, 'ready', False):
            break
    if not getattr(backend, 'ready', False):
        say('ПРОВАЛ: бэкенд не подключился')
        return

    say('подключено через: %s' % P._mode_label(backend._proxy_mode))

    # Берём первое видео из списка каналов.
    marks = dict(getattr(backend, '_dialog_has_video', {}) or {})
    good = {str(k) for k, v in marks.items() if v}
    say('каналов с видео по пометкам: %d' % len(good))
    dialogs = list(getattr(backend, 'dialogs', []) or [])
    say('диалогов: %d' % len(dialogs))

    # Перебираем каналы, пока не найдём видео: у части диалогов роликов
    # в последних сообщениях нет, и список выйдет пустым.
    msgs = []
    order = []
    for d in dialogs:
        try:
            peer = backend._dialog_peer_id(d)
        except Exception:
            peer = None
        if peer is not None and str(peer) in good:
            order.insert(0, d)
        else:
            order.append(d)

    for dialog in order[:12]:
        backend.active_dialog_id = getattr(dialog, 'id', None)
        try:
            await backend.load_videos_from_dialog(dialog)
        except Exception as ex:
            say('  канал %s: %s' % (getattr(dialog, 'id', '?'), ex))
            continue
        msgs = list(getattr(backend, 'current_messages', []) or [])
        if msgs:
            say('нашли канал с видео: %s' % (getattr(dialog, 'name', '') or getattr(dialog, 'id', '')))
            break
    if not msgs:
        say('ПРОВАЛ: список видео пуст во всех проверенных')
        return

    msg = msgs[0]
    doc = msg.media.document
    say('файл: %.1f МБ' % (doc.size / 1024 / 1024))

    # Меряем: качаем 4 МБ с начала файла замером времени.
    need = 4 * 1024 * 1024
    t0 = time.monotonic()
    got = 0
    chunk_size = 1024 * 1024
    try:
        async for chunk in backend.client.iter_download(
                msg, offset=0, chunk_size=chunk_size):
            got += len(chunk)
            if got >= need:
                break
    except Exception as ex:
        say('ошибка загрузки: %s' % ex)
        return
    dt = time.monotonic() - t0
    mbps = (got / 1024 / 1024) / dt if dt > 0 else 0
    say('скачано %.1f МБ за %.2f с' % (got / 1024 / 1024, dt))
    say('СКОРОСТЬ: %.2f МБ/с' % mbps)
    say('')
    say('сколько займут 6 МБ (старт ролика) при такой скорости: %.1f с'
        % (6 / mbps if mbps > 0 else 0))


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(P.build_style_scaled())
    backend = P.TelegramBackend()
    thread = P.WorkerThread(backend)
    thread.start()

    # Ждём, пока рабочий поток поднимет цикл событий.
    loop = None
    for _ in range(200):
        app.processEvents()
        time.sleep(0.05)
        loop = getattr(backend, 'loop', None)
        if loop is not None and loop.is_running():
            break
    if loop is None or not loop.is_running():
        say('ПРОВАЛ: цикл бэкенда не поднялся')
        return

    # Мост Qt <-> asyncio: гоняем замер в потоке бэкенда.
    fut = asyncio.run_coroutine_threadsafe(measure(backend, app), loop)

    # Крутим цикл Qt, пока замер идёт.
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)
        if fut.done():
            break
    try:
        fut.result(timeout=5)
    except Exception as ex:
        say('СБОЙ замера: %s' % ex)

    try:
        backend._closed = True
        if backend.loop and backend.loop.is_running():
            backend.loop.call_soon_threadsafe(backend.loop.stop)
    except Exception:
        pass
    try:
        thread.wait(2000)
    except Exception:
        pass


if __name__ == '__main__':
    try:
        main()
    except Exception as ex:
        import traceback
        say('СБОЙ: %s' % ex)
        say(traceback.format_exc())
    text = '\n'.join(OUT)
    with io.open(r'C:\tgplayer\_real_speed.txt', 'w', encoding='utf-8') as f:
        f.write(text + '\n')
