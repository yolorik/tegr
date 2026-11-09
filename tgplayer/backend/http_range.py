"""Разбор HTTP Range для локального видеострима.

Вынесено из backend.py отдельной чистой функцией: здесь нет ни Telegram,
ни файлов, ни сети. Это позволяет проверять поведение диапазонов тестами
без запуска всего приложения.
"""


def parse_range_header(value, file_size):
    """Разбирает Range и возвращает (start, end, status).

    status — 200 для запроса всего файла и 206 для диапазона.
    None — диапазон невыполним (нужен ответ 416).

    Поддерживается один диапазон, как и сам сервер mpv: остальные после
    запятой игнорируются. Некорректный заголовок трактуется как его
    отсутствие — отдаём файл целиком.
    """
    start, end, status = 0, file_size - 1, 200
    if value:
        try:
            spec = value.strip()
            if spec.lower().startswith('bytes='):
                spec = spec[6:]
            # Мультидиапазоны не поддерживаем — берём первый.
            spec = spec.split(',')[0].strip()
            first, _, last = spec.partition('-')
            first, last = first.strip(), last.strip()

            if not first:
                # "bytes=-N" — ПОСЛЕДНИЕ N байт, а не первые.
                n = int(last) if last else 0
                if n > 0:
                    start = max(0, file_size - n)
                    end = file_size - 1
                    status = 206
            else:
                start = int(first)
                end = int(last) if last else file_size - 1
                end = min(end, file_size - 1)
                status = 206
        except (ValueError, IndexError):
            start, end, status = 0, file_size - 1, 200

    if start > end or start >= file_size:
        return None
    return start, end, status
