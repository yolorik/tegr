"""Чистые помощники кэша: диапазоны, трекер и разбиение файла.

Здесь нет ни Telegram, ни Qt, ни сети. backend.py и виджеты используют
эти функции, а тесты могут проверять их отдельно.
"""
from ..core import config


class DownloadTracker:
    """Учёт скачанных участков файла.

    Хранит список непрерывных диапазонов (start, end) и умеет отвечать,
    сколько байт подряд доступно начиная с позиции — именно это нужно
    HTTP-серверу, чтобы отдать mpv ровно то, что уже лежит на диске.
    """
    def __init__(self, size):
        self.size = size
        self.ranges = []

    def add_range(self, start, end):
        """Добавляет участок и сливает его с соседними диапазонами."""
        if start >= self.size:
            return
        end = min(end, self.size - 1)
        if start > end:
            return

        new_ranges = []
        inserted = False
        cur_start, cur_end = start, end

        for r_start, r_end in self.ranges:
            if cur_end < r_start - 1:
                if not inserted:
                    new_ranges.append((cur_start, cur_end))
                    inserted = True
                new_ranges.append((r_start, r_end))
            elif cur_start > r_end + 1:
                new_ranges.append((r_start, r_end))
            else:
                cur_start = min(cur_start, r_start)
                cur_end = max(cur_end, r_end)

        if not inserted:
            new_ranges.append((cur_start, cur_end))
        self.ranges = new_ranges

    def available_bytes_from(self, start):
        """Сколько байт подряд доступно начиная с позиции start."""
        for r_start, r_end in self.ranges:
            if r_start <= start <= r_end:
                return r_end - start + 1
        return 0


def _open_read(path):
    """Открывает файл кеша на чтение БЕЗ буфера.

    Буферизованный читатель наполняет буфер целиком (1 МБ), а .part заранее
    заполнен нулями через truncate. Пока кусок не скачан, в буфер попадают
    нули, и последующий seek внутрь уже набранного буфера их НЕ перечитывает:
    mpv получал нули вместо видео. Без буфера каждое чтение идёт с диска.
    """
    return open(path, 'rb', buffering=0)


def _piece_ranges(size):
    """Разбивает файл на куски для скачивания.

    Начало файла (config.BOOTSTRAP_BYTES) нарезается мелкими кусками
    config.BOOTSTRAP_PIECE, чтобы все воркеры параллельно качали именно
    начало. Середина — крупными сегментами config.SEG_SIZE, ради
    пропускной способности. Хвост (config.TAIL_BYTES) снова нарезается
    мелко: там обычно лежит индекс moov, и mpv читает его с конца файла.
    При одном крупном последнем куске самые нужные байты приходили бы
    последними.
    """
    pieces = []
    # Начало хвоста выравниваем по размеру запроса: Telegram отвергает
    # диапазон, пересекающий границу 1 МБ, а размер файла на мегабайт
    # обычно не делится. Без выравнивания хвост не скачался бы вовсе.
    tail_start = max(
        config.BOOTSTRAP_BYTES,
        (size - config.TAIL_BYTES) // config.CHUNK_SIZE * config.CHUNK_SIZE,
    )
    start = 0
    while start < size:
        if start < config.BOOTSTRAP_BYTES:
            length = min(config.BOOTSTRAP_PIECE, size - start,
                         config.BOOTSTRAP_BYTES - start)
        elif start >= tail_start:
            length = min(config.TAIL_PIECE, size - start)
        else:
            length = min(config.SEG_SIZE, size - start, tail_start - start)
        pieces.append((start, length))
        start += length
    return pieces


def piece_index_for(pieces, byte_pos):
    """Бинарный поиск индекса куска, содержащего byte_pos."""
    lo, hi = 0, len(pieces) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if pieces[mid][0] <= byte_pos:
            lo = mid
        else:
            hi = mid - 1
    return lo


def index_at_end(path, size):
    """True, если индекс файла лежит в конце и нужен для старта.

    MP4: если mdat идёт раньше moov — индекс в хвосте; если moov
    впереди (faststart), хвост не нужен. MKV/WebM: боксов нет, а Cues
    почти всегда в конце — значит хвост нужен всегда.
    """
    try:
        with open(path, 'rb') as f:
            head = f.read(8)
            if len(head) < 8:
                return False

            # MKV/WebM — сигнатура EBML.
            if head[:4] == b'\x1a\x45\xdf\xa3':
                return True

            pos = 0
            for _ in range(12):
                if pos + 8 > size:
                    return False
                f.seek(pos)
                hdr = f.read(8)
                if len(hdr) < 8:
                    return False
                box_size = int.from_bytes(hdr[0:4], 'big')
                box_type = hdr[4:8]
                if box_type == b'moov':
                    return False        # moov в начале — всё хорошо
                if box_type == b'mdat':
                    return True         # mdat раньше moov -> индекс в хвосте
                if box_size < 8:
                    return False
                pos += box_size
    except Exception:
        return False
    return False
