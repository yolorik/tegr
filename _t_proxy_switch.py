"""Мгновенное переключение режима прокси.

Проверяем то, на что жаловался человек: стоит «свой прокси», идёт попытка
подключения по недоступному адресу; переключаем на другой режим — старая
попытка должна прекратиться СРАЗУ, а не после её таймаута.

Смотрим на поколение подключения: после смены режима всё, что ждёт сеть
по прежнему маршруту, видит другое поколение и бросает попытку.

Отчёт — _proxy_switch.txt, код возврата 0 — переключение срабатывает.
"""
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


def main():
    app = QApplication(sys.argv)
    backend = P.TelegramBackend()

    # Начинаем со «своего прокси» на заведомо мёртвый адрес: попытка
    # будет висеть, пока не истечёт таймаут.
    backend.set_proxy('custom', host='192.0.2.1', port=443,
                      secret='0123456789abcdef0123456789abcdef')
    time.sleep(0.2)
    say('режим выставлен: %s' % backend._proxy_mode)

    thread = P.WorkerThread(backend)
    thread.start()

    # Дадим попытке начаться и повисеть.
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)

    gen_before = backend._connect_gen
    say('поколение до переключения: %d' % gen_before)
    say('цикл подключения крутится: %s' % (not backend.ready))

    # Главная проверка: переключаем режим и смотрим, что поколение
    # сменилось сразу же — значит, старая попытка обречена.
    t0 = time.monotonic()
    backend.set_proxy('off')
    # Ждём, пока поколение изменится.
    changed = False
    while time.monotonic() - t0 < 3:
        app.processEvents()
        time.sleep(0.02)
        if backend._connect_gen != gen_before:
            changed = True
            break
    elapsed = time.monotonic() - t0
    say('поколение сменилось: %s за %.2f с' % (changed, elapsed))
    say('режим после переключения: %s' % backend._proxy_mode)
    say('старая попытка устарела: %s'
        % backend._connect_stale(gen_before))

    ok = changed and elapsed < 1.0 and backend._connect_stale(gen_before)
    if not changed:
        say('ПРОВАЛ: переключение не сработало')

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
    return ok


if __name__ == '__main__':
    ok = False
    try:
        ok = main()
    except Exception as ex:
        import traceback
        say('СБОЙ: %s' % ex)
        say(traceback.format_exc())
    text = '\n'.join(OUT)
    with io.open(r'C:\tgplayer\_proxy_switch.txt', 'w', encoding='utf-8') as f:
        f.write(text + '\n')
    sys.exit(0 if ok else 1)
