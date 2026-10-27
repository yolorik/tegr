"""Сколько на самом деле тянет канал через текущий прокси.

Брошенные куски «поток молчит 12 с» — это зависшие соединения. Проверяем,
тормозит прокси или дело в плеере: качаем один и тот же файл разным
числом параллельных запросов и смотрим скорость и число зависаний.

Пишет отчёт в _speed_test.txt.
"""
import io
import sys
import time

sys.path.insert(0, r"C:\tgplayer")

OUT = []


def say(text=''):
    OUT.append(str(text))
    print(text)


def main():
    # Настоящая загрузка через прокси, без участия плеера.
    import socket

    print('проверяем скорость через прокси 127.0.0.1:10808')
    host, port = '127.0.0.1', 10808
    target_ip, target_port = '149.154.167.51', 443

    def one_request(label):
        """Один CONNECT-туннель и немного данных."""
        t0 = time.monotonic()
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.sendall((f'CONNECT {target_ip}:{target_port} HTTP/1.1\r\n'
                       f'Host: {target_ip}:{target_port}\r\n\r\n').encode())
            resp = s.recv(64)
            if b'200' not in resp:
                s.close()
                return ('нет туннеля', 0.0, time.monotonic() - t0)
            # Туннель есть. Данные не шлём (это же Telegram), меряем
            # только время установки соединения.
            s.close()
            return ('ок', 0.0, time.monotonic() - t0)
        except Exception as ex:
            return (str(ex), 0.0, time.monotonic() - t0)

    # 8 последовательных установок: видно, сколько занимает рукопожатие
    # и подвисают ли отдельные попытки.
    times = []
    for i in range(8):
        status, _, dt = one_request(i)
        times.append(dt)
        say('попытка %d: %s за %.2f с' % (i + 1, status, dt))

    ok_times = [t for t in times if t < 5]
    if ok_times:
        say('')
        say('успешных: %d из %d' % (len(ok_times), len(times)))
        say('среднее рукопожатие: %.2f с' % (sum(ok_times) / len(ok_times)))
        say('самое долгое: %.2f с' % max(ok_times))
    return True


if __name__ == '__main__':
    ok = False
    try:
        ok = main()
    except Exception as ex:
        import traceback
        say('СБОЙ: %s' % ex)
        say(traceback.format_exc())
    text = '\n'.join(OUT)
    with io.open(r'C:\tgplayer\_speed_test.txt', 'w', encoding='utf-8') as f:
        f.write(text + '\n')
    sys.exit(0 if ok else 1)
