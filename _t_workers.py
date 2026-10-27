"""Сколько воркеров реально быстрее: замер параллельной загрузки.

Качаем один и тот же файл через 1, 2, 4 и 6 параллельных запросов и
считаем скорость. Цифры в конфиге («шесть медленнее двух») получены на
MTProto при разработке — их надо перепроверить на текущем прокси.

Пишет отчёт в _workers_test.txt.
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


async def find_video(backend):
    """Первый ролик из любого канала."""
    order = list(getattr(backend, 'dialogs', []) or [])
    for dialog in order[:12]:
        backend.active_dialog_id = getattr(dialog, 'id', None)
        try:
            await backend.load_videos_from_dialog(dialog)
        except Exception:
            continue
        msgs = list(getattr(backend, 'current_messages', []) or [])
        if msgs:
            # Берём файл покрупнее: на мелком замер не показателен.
            for m in msgs:
                try:
                    if m.media.document.size > 30 * 1024 * 1024:
                        return m
                except Exception:
                    continue
            return msgs[0]
    return None


async def download_part(backend, msg, workers, need_bytes):
    """Качаем need_bytes с начала файла в workers параллельных потоков."""
    chunk = 1024 * 1024
    total = need_bytes
    per = max(chunk, total // workers)
    got = 0
    lock = asyncio.Lock()

    async def worker(offset):
        nonlocal got
        done = 0
        try:
            async for data in backend.client.iter_download(
                    msg, offset=offset, chunk_size=chunk):
                done += len(data)
                async with lock:
                    got += len(data)
                if done >= per:
                    break
        except Exception:
            pass

    t0 = time.monotonic()
    tasks = [asyncio.ensure_future(worker(i * per)) for i in range(workers)]
    await asyncio.gather(*tasks, return_exceptions=True)
    dt = time.monotonic() - t0
    return (got / 1024 / 1024), dt


async def measure(backend):
    for _ in range(120):
        await asyncio.sleep(0.5)
        if getattr(backend, 'ready', False):
            break
    if not getattr(backend, 'ready', False):
        say('ПРОВАЛ: бэкенд не подключился')
        return

    say('способ: %s' % P._mode_label(backend._proxy_mode))
    msg = await find_video(backend)
    if msg is None:
        say('ПРОВАЛ: ролик не найден')
        return
    size = msg.media.document.size
    say('файл: %.1f МБ' % (size / 1024 / 1024))
    say('')

    need = 8 * 1024 * 1024
    for workers in (1, 2, 4, 6):
        mb, dt = await download_part(backend, msg, workers, need)
        speed = mb / dt if dt > 0 else 0
        say('%d воркер(ов): %.1f МБ за %.2f с -> %.2f МБ/с'
            % (workers, mb, dt, speed))
        await asyncio.sleep(1.0)

    say('')
    say('для сравнения: 6 МБ на старт ролика при лучшей скорости:')
    say('  см. строку с максимальной скоростью выше')


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(P.build_style_scaled())
    backend = P.TelegramBackend()
    thread = P.WorkerThread(backend)
    thread.start()

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

    fut = asyncio.run_coroutine_threadsafe(measure(backend), loop)
    deadline = time.monotonic() + 240
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
        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
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
    with io.open(r'C:\tgplayer\_workers_test.txt', 'w', encoding='utf-8') as f:
        f.write(text + '\n')
