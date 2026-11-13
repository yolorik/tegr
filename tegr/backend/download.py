"""Скачивание кусков: очередь, приоритеты и финализация файла."""

import asyncio
import json
import os
import random
import time

from ..core import config
from ..system import telethon_boot
from .cache_math import (
    DownloadTracker, _open_read, _piece_ranges, index_at_end, piece_index_for)


class DownloadMixin:
    def _existing_partial_pieces(self, size, path, piece_count):
        """
        Быстрая проверка старого .part.
        Файл создаётся sparse/full-size, поэтому одного размера недостаточно.
        Для надёжного возобновления используем sidecar со списком готовых кусков.
        """
        meta_path = path + ".json"
        done = set()

        try:
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if (data.get("size") == size
                        and data.get("format") == 2
                        and data.get("piece_count") == piece_count):
                    done = {int(x) for x in data.get("done", [])}
        except Exception:
            pass

        return done

    def _save_partial_state(self, e):
        if e.get('complete'):
            return
        try:
            meta_path = e['path'] + ".json"
            tmp_path = meta_path + ".tmp"
            data = {
                "size": e['size'],
                "format": 2,
                "piece_count": e['piece_count'],
                "done": sorted(e.get('piece_done', set()))
            }
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, separators=(",", ":"))
            os.replace(tmp_path, meta_path)
        except Exception:
            pass

    async def ensure_cache(self, message, workers=None, stagger=config.RAMP_DELAY,
                           resume=False):
        """Готовит кеш ролика и поднимает воркеров.

        Принимает СООБЩЕНИЕ, а не документ. Причина серьёзная: ссылка на
        файл (file_reference) в Telegram живёт около получаса, и на
        большом файле истекает прямо посреди загрузки. Обновить её можно
        только заново прочитав сообщение — для этого нужны чат и id
        сообщения. И если передать Telethon документ, а не сообщение, он
        про это не знает и просто отдаёт ошибку FILE_REFERENCE_EXPIRED.
        Именно так плеер и залипал: 2934 ошибки на три журнала, ни одного
        докачанного файла.

        Документ можно передать и напрямую — тогда обновить ссылку не
        получится, но путь остаётся ради совместимости.
        """
        document = getattr(getattr(message, 'media', None), 'document', None)
        if document is None:
            document = message          # передан сам документ
            message = None
        doc_id = document.id
        e = self.cache.get(doc_id)
        if e is not None and message is not None:
            # Канал, из которого ролик: нужен вкладке загрузок, чтобы
            # было видно, откуда он. Запись могла создаться прелоадом
            # без сообщения — тогда дополняем.
            try:
                e.setdefault('peer_id', message.peer_id)
                e['peer_title'] = self._peer_title(message.peer_id)
            except Exception:
                pass
        if e is not None and message is not None and e.get('message') is None:
            # Запись могла быть создана без сообщения (прелоад по
            # документу). Дополняем: без него ссылку не обновить.
            e['message'] = message
        if e is not None:
            # Кеш уже существует (например, его начал прелоад по выделению
            # в списке). Докачиваем воркеры до нужного количества, чтобы
            # при открытии плеера загрузка шла на полной скорости.
            if e.get('complete') or e.get('queue') is None:
                return e
            if e.get('error'):
                # Фатальная ошибка (например, умершая ссылка) не лечится
                # новыми воркерами: они сразу выйдут.
                return e

            # Писательский дескриптор мог остаться закрытым (например,
            # после сбоя завершения файла). Воркер первым делом проверяет
            # fh и молча выходит, поэтому запись без него не оживить:
            # открываем заново, иначе загрузка так и будет стоять на нуле.
            if e.get('fh') is None and e.get('path'):
                try:
                    e['fh'] = open(e['path'], 'r+b', buffering=1024 * 1024)
                except Exception as ex:
                    # Не получилось — не беда: воркеры добавятся как обычно
                    # и сами решат, работать ли с этой записью.
                    print(f"[Кэш] Не удалось открыть файл заново: {ex}")

            # Считаем только ЖИВЫЕ задачи. Завершённые остаются в списке, и
            # раньше именно они «съедали» место: после переключения ролика
            # свободных воркеров не добавлялось, и загрузка не шла вовсе.
            tasks = [t for t in e.get('tasks', []) if not t.done()]
            e['tasks'] = tasks

            if e.get('paused') and not resume:
                # Загрузку приостановил сам плеер при переключении ролика.
                # Разбудить её вправе только play_video, а не запрос mpv.
                return e
            e['paused'] = False

            target = max(1, min(int(workers or config.PARALLEL_WORKERS),
                                e['piece_count']))
            for _ in range(target - len(tasks)):
                e['tasks'].append(
                    self.loop.create_task(
                        self._cache_worker(
                            document, e, e['queue'], len(e['tasks']), stagger
                        )
                    )
                )
            return e

        size = document.size
        mp4 = os.path.join(config.CACHE_DIR, f'{doc_id}.mp4')
        part = os.path.join(config.CACHE_DIR, f'{doc_id}.part')

        pieces = _piece_ranges(size)
        piece_count = len(pieces)
        tracker = DownloadTracker(size)

        # Полный файл — самый быстрый путь: Telegram вообще не участвует.
        if os.path.exists(mp4) and os.path.getsize(mp4) == size:
            tracker.add_range(0, size - 1)
            try:
                rfh = _open_read(mp4)
            except Exception:
                rfh = None

            e = {
                'fh': None,
                'rfh': rfh,
                'size': size,
                'complete': True,
                # Сообщение храним и у готового файла: если .mp4 потом
                # пересоздадут (очистка кеша), ссылку нужно будет брать
                # заново.
                'message': message,
                'tasks': [],
                'path': mp4,
                'tracker': tracker,
                'pieces': pieces,
                'piece_count': piece_count,
                'piece_done': set(range(piece_count)),
                'downloaded_bytes': size,
                'queue': None,
                'queued': set(),
                'active': set(),
                'data_event': asyncio.Event(),
            }
            self.cache[doc_id] = e
            return e

        # Важно: .part НЕ удаляем. Если пользователь вернулся к ролику,
        # продолжится именно предыдущая загрузка.
        try:
            fh = open(part, 'r+b', buffering=1024 * 1024)
            if os.path.getsize(part) != size:
                fh.truncate(size)
        except FileNotFoundError:
            fh = open(part, 'w+b', buffering=1024 * 1024)
            fh.truncate(size)
        except Exception as ex:
            print(f"[Кэш] Ошибка файла: {ex}")
            return None

        done = self._existing_partial_pieces(size, part, piece_count)
        done = {i for i in done if 0 <= i < piece_count}

        # Если sidecar отсутствует (или он от старого формата), старый .part
        # нельзя безопасно считать готовым: куски качаются заново — зато без
        # повреждения.
        tracker = DownloadTracker(size)
        for idx in done:
            offset, length = pieces[idx]
            tracker.add_range(offset, offset + length - 1)

        queue = asyncio.PriorityQueue()
        queued = set()
        active = set()

        # Сначала голова файла (мелкие куски), затем остальное по порядку.
        initial_order = list(range(piece_count))
        initial_order.sort(
            key=lambda i: (
                0 if pieces[i][0] < config.BOOTSTRAP_BYTES else 1,
                i
            )
        )

        for order, idx in enumerate(initial_order):
            if idx not in done:
                prio = -100000 + order if pieces[idx][0] < config.BOOTSTRAP_BYTES else idx
                queue.put_nowait((prio, idx))
                queued.add(idx)

        e = {
            'fh': fh,
            'rfh': None,
            'size': size,
            'complete': False,
            'error': False,
            'tasks': [],
            # Документ храним в самой записи: без него нечем поднять
            # загрузку заново, если воркеры погибли.
            'document': document,
            # И само сообщение: по нему берётся новая file_reference,
            # когда старая истекла (см. _refresh_document).
            'message': message,
            'path': part,
            'pieces': pieces,
            'piece_count': piece_count,
            'piece_done': done,
            'downloaded_bytes': sum(pieces[i][1] for i in done),
            'tracker': tracker,
            'queue': queue,
            'queued': queued,
            'active': active,
            'boosted': set(),
            'data_event': asyncio.Event(),
            # Скорость считаем по байтам за последний промежуток, а не
            # накопительно: накопительная сумма росла бы бесконечно и
            # показывала «133 МБ/с» вместо настоящей скорости.
            # speed_bytes — текущая скорость (байт/с) для вкладки загрузок.
            # speed_total — накопленные байты: по нему плеер считает
            # скорость за окно как разницу между двумя замерами. Это разные
            # величины, и раньше их путали: плеер брал разницу от скорости
            # и почти всегда получал ноль, а показывал половину средней.
            'speed_bytes': 0,
            'speed_total': 0,
            'first_byte_ts': 0.0,
            'speed_window_bytes': 0,
            'speed_window_ts': time.monotonic(),
            'speed_bps': 0.0,
            'last_meta_save': 0.0,
            'started_ts': time.monotonic(),
        }
        # Канал, откуда ролик: вкладка загрузок показывает его рядом с
        # названием, чтобы было понятно, что именно качается.
        if message is not None:
            # Храним помеченный id (-100...), а не объект PeerChannel:
            # по нему «Перейти к ролику на канале» сравнивает запись с
            # dialog.id, и объект с числом никогда не совпадал.
            e['peer_id'] = getattr(message, 'chat_id', None) or message.peer_id
            e['peer_title'] = self._peer_title(e['peer_id'])

        self.cache[doc_id] = e

        if len(done) >= piece_count:
            self._finalize_cache(e)
            return e

        if workers is None:
            workers = config.PARALLEL_WORKERS
        workers = max(1, min(int(workers), piece_count - len(done)))

        # Начало уже скачано (докачка) — сразу проверяем хвост.
        if 0 in done:
            self._maybe_prioritize_tail(e)

        for wid in range(workers):
            e['tasks'].append(
                self.loop.create_task(
                    self._cache_worker(document, e, queue, wid, stagger)
                )
            )

        return e

    @staticmethod
    def _wake_cache(e):
        """Будит HTTP-читателей, ждущих новые байты этого файла."""
        try:
            event = e.get('data_event') if e is not None else None
            if event is not None:
                event.set()
        except Exception:
            pass

    def _ensure_busy(self, e):
        """Держит воркеры занятыми, когда один завис на паузе.

        Замеры показали важную вещь: одна порция приходит за 0.11 с, но
        иногда поток замирает на десятки секунд. Если оба воркера в такой
        момент висят на паузе, скорость падает в ноль и стоит, пока они
        не очнутся.

        Лечим просто: если рабочий кусок есть в очереди, а свободных
        воркеров нет или их число меньше обычного (кто-то завис), берём
        и запускаем ещё одного. Лишний воркер живёт недолго: как только
        он упрётся в пустую очередь или файл докачается, он выйдет сам.

        Верхний предел есть: больше config.MAX_EXTRA_WORKERS сверх нормы
        не запускаем, иначе Telegram начнёт душить все соединения.
        """
        if e.get('complete') or e.get('error') or e.get('removed'):
            return
        queue = e.get('queue')
        if queue is None or queue.empty():
            return

        live = [t for t in e.get('tasks', []) if not t.done()]
        extra = e.get('extra_workers', 0)
        if len(live) >= config.PARALLEL_WORKERS + extra:
            return
        if extra >= config.MAX_EXTRA_WORKERS:
            return

        e['extra_workers'] = extra + 1
        print(f"[Кэш] добавляю воркер: живых {len(live)}, в очереди "
              f"{queue.qsize()} кусков")
        task = self.loop.create_task(
            self._cache_worker(e.get('document'), e, queue,
                               config.PARALLEL_WORKERS + extra, 0.0)
        )
        e['tasks'].append(task)

    @staticmethod
    def _index_at_end(path, size):
        """Оставлено для совместимости: логика в cache_math."""
        return index_at_end(path, size)

    def _maybe_prioritize_tail(self, e):
        """Один раз проверяет расположение индекса и поднимает хвост."""
        if e.get('moov_checked') or e.get('complete'):
            return
        e['moov_checked'] = True
        try:
            if self._index_at_end(e['path'], e['size']):
                print("[Кэш] индекс в конце файла — тяну хвост заранее")
                self.prioritize_position(e, e['size'] - 1)
        except Exception:
            pass

    def prioritize_position(self, e, byte_pos):
        """Поднимает приоритет кусков вокруг позиции читателя.

        ВАЖНО: очередь НЕ выгребаем и НЕ перестраиваем — просто кладём
        нужные куски с самым высоким приоритетом, а PriorityQueue сама
        поставит их вперёд. Прежняя версия выгребала очередь целиком, и
        когда у mpv оказывалось два читателя (текущая позиция и хвост с
        индексом), они по очереди перехватывали начало очереди каждые
        250 мс. Загрузка расползалась вразнобой, последовательное чтение
        упиралось в дыры, и воспроизведение стартовало только на 40+%
        загрузки.
        """
        if e.get('queue') is None or e.get('complete'):
            return

        byte_pos = max(0, min(byte_pos, e['size'] - 1))
        q = e['queue']

        # Старшинство задаёт поколение читателя: куски, поднятые после
        # свежей перемотки, обязаны обгонять всё, что поднималось раньше.
        # Иначе старый подъём с меньшим смещением перебивает новый, и
        # воркеры берут не тот кусок, которого ждёт читатель.
        gen = e.get('read_gen', 0)
        base = -200000 - gen * 1000
        if e.get('boost_gen') != gen:
            e['boost_gen'] = gen
            e['boosted'] = set()    # прошлое поколение больше не в счёт
        boosted = e['boosted']

        target_piece = piece_index_for(e['pieces'], byte_pos)

        # Назад поднимаем только когда читатель просит самый конец файла:
        # там лежит индекс moov, и mpv читает его с конца. Во всех прочих
        # случаях идём вперёд — иначе у середины файла порядок
        # переворачивается и воркеры берут куски ПОЗАДИ читателя.
        if target_piece >= e['piece_count'] - 1:
            order = [target_piece - i for i in range(config.PRIORITY_AHEAD_SEGS)]
        else:
            order = [target_piece + i for i in range(config.PRIORITY_AHEAD_SEGS)]

        # Запоминаем, какие куски прямо сейчас ждёт читатель. Воркер вправе
        # бросить только тот кусок, который не ждёт НИ ОДИН читатель.
        # Без этого два читателя (начало файла и хвост с индексом moov)
        # бесконечно бросали куски друг друга, и не докачивался ни один:
        # в логе это выглядело как «видео-дорожка так и не найдена» на
        # большом файле с индексом в конце.
        now = time.monotonic()
        wanted = e.setdefault('wanted', {})
        # Чистим старые отметки, чтобы словарь не рос весь сеанс.
        for old_idx, ts in list(wanted.items()):
            if now - ts >= config.WANTED_TTL:
                wanted.pop(old_idx, None)
        for idx in order:
            if 0 <= idx < e['piece_count']:
                wanted[idx] = now

        # rank 0 — самый срочный кусок, и приоритет у него самый высокий
        # (числа отрицательные, меньше — значит раньше).
        for rank, idx in enumerate(order):
            if idx < 0 or idx >= e['piece_count']:
                continue
            if idx in e['piece_done'] or idx in e['active']:
                continue
            if idx in boosted:
                continue            # уже поднят — дублей не плодим
            boosted.add(idx)
            # Поколение в самой записи очереди: воркер сможет отличить
            # старый подъём от свежего. Без этого после нескольких
            # перемоток старые «срочные» куски оставались впереди и
            # скачивались, хотя читатель их уже не ждёт.
            q.put_nowait((base + rank, idx, gen))
            e['queued'].add(idx)

    @staticmethod
    def _piece_wanted(e, idx, ttl=config.WANTED_TTL):
        """Ждёт ли этот кусок хоть один читатель прямо сейчас."""
        ts = e.get('wanted', {}).get(idx)
        return ts is not None and (time.monotonic() - ts) < ttl

    def _boost_stale(self, e, boost_gen, idx):
        """Старый подъём, который больше никому не нужен."""
        if boost_gen is None:
            return False
        if boost_gen == e.get('read_gen', 0):
            return False
        return not self._piece_wanted(e, idx)

    def _finalize_cache(self, e):
        if e.get('complete'):
            return

        e['complete'] = True
        self._wake_cache(e)
        for t in e.get('tasks', []):
            if not t.done():
                t.cancel()

        # Читателя закрываем сразу, писателя — перед самым переименованием.
        # Держим их порознь, чтобы вернуть запись в рабочее состояние, если
        # переименование сорвётся.
        rf = e.get('rfh')
        if rf:
            try:
                rf.close()
            except Exception:
                pass
            e['rfh'] = None
        wf = e.get('fh')
        if wf:
            try:
                wf.flush()
            except Exception:
                pass
            try:
                wf.close()
            except Exception:
                pass
            e['fh'] = None

        part = e['path']
        mp4 = part[:-5] + '.mp4' if part.endswith('.part') else part

        try:
            os.replace(part, mp4)
            e['path'] = mp4

            meta = part + ".json"
            try:
                if os.path.exists(meta):
                    os.remove(meta)
            except Exception:
                pass

            e['rfh'] = _open_read(mp4)
            print(f"[Кэш] Готово: {mp4}")
        except Exception as ex:
            # Переименование сорвалось: файл занят проверкой антивируса или
            # кем-то ещё. Раньше запись оставалась недокачанной с уже
            # ЗАКРЫТЫМ писательским дескриптором, а воркер первым делом
            # проверяет fh и молча выходит. Докачка вставала навсегда:
            # скорость замирала на нуле, читатель ждал данные, которых
            # никто не качал, и окно выглядело зависшим. Открываем файл
            # заново, чтобы загрузку можно было возобновить.
            e['complete'] = False
            try:
                e['fh'] = open(part, 'r+b', buffering=1024 * 1024)
            except Exception:
                e['fh'] = None
            print(f"[Кэш] Не удалось завершить файл: {ex}")

    async def _refresh_document(self, e, doc_id):
        """Читает сообщение заново и подменяет документ свежей ссылкой.

        Ссылка на файл (file_reference) в Telegram живёт около получаса и
        обновляется только вместе с сообщением. Когда она истекла,
        повторять запрос с тем же документом бессмысленно: Telegram
        отвечает FILE_REFERENCE_EXPIRED столько раз, сколько его ни
        спрашивай — это и был затык «серии перестали грузиться».

        Возвращает True, если документ обновился и кусок можно пытать
        снова; False — сообщение удалено или медиа заменено; None —
        сеть не дала обновить ссылку, стоит повторить позже.
        """
        message = e.get('message')
        if message is None:
            return False
        try:
            fresh = await self._request_with_reconnect(
                lambda: self.client.get_messages(
                    message.peer_id, ids=message.id),
                'Обновление ссылки на файл')
        except Exception as ex:
            print(f"[Кэш] Ссылку обновить не удалось ({doc_id}): {ex}")
            return None
        media = getattr(fresh, 'media', None)
        document = getattr(media, 'document', None)
        if document is None or document.id != doc_id:
            # Сообщение удалили или медиа заменили — качать нечего.
            print(f"[Кэш] Ссылка устарела, а сообщение изменилось "
                  f"({doc_id})")
            return False
        old_ref = getattr(e.get('document'), 'file_reference', None)
        new_ref = getattr(document, 'file_reference', None)
        # Документ подменяем в записи: следующие куски возьмут его оттуда.
        e['document'] = document
        e['message'] = fresh
        print(f"[Кэш] Ссылка на файл обновлена ({doc_id}): "
              f"{len(old_ref or b'')} -> {len(new_ref or b'')} байт")
        return True

    async def _cache_worker(self, document, e, queue, wid, stagger):
        if e.get('error') or e.get('fh') is None:
            return

        if stagger and wid > 0:
            try:
                await asyncio.sleep(wid * stagger)
            except asyncio.CancelledError:
                return

        while not e['complete']:
            # Ошибка могла случиться в другом воркере: файл больше не
            # доступен, и качать остальные куски незачем.
            if e.get('error'):
                return
            try:
                item = await queue.get()
            except asyncio.CancelledError:
                return
            if len(item) == 3:
                priority, idx, boost_gen = item
            else:
                priority, idx = item
                boost_gen = None

            e['queued'].discard(idx)
            # Снимаем отметку «поднят» только у записи своего поколения:
            # иначе старый дубль снимет её у свежего подъёма.
            if boost_gen is None or boost_gen == e.get('read_gen', 0):
                e['boosted'].discard(idx)

            if idx in e['piece_done'] or idx in e['active']:
                queue.task_done()
                continue

            # Старый подъём после перемотки: если читатель его больше не
            # ждёт, не тратим на него полосу. Обычная запись этого куска
            # остаётся в очереди и будет обработана в общем порядке.
            if self._boost_stale(e, boost_gen, idx):
                queue.task_done()
                continue

            e['active'].add(idx)
            start, length = e['pieces'][idx]

            # Поколение читателя на момент старта куска. Если он перемотает,
            # поколение сменится, и кусок надо бросить: иначе воркер
            # домалывает ненужные четыре мегабайта, пока читатель ждёт.
            my_gen = e.get('read_gen', 0)

            # Возобновляем кусок с уже скачанной позиции после сетевой ошибки.
            pos = e['tracker'].available_bytes_from(start)
            if pos >= length:
                pos = length
            ok = False
            aborted = False

            try:
                for attempt in range(config.SEG_RETRIES + 1):
                    stream = None
                    try:
                        request_offset = start + pos

                        # 1 МБ — оптимум по замеру 06.11.2026: 5.9–6.2 МБ/с
                        # против 2.8–3.0 у 512 КБ. 512 КБ остаётся запасным
                        # путём, если сервер или CDN не принял 1 МБ.
                        chunk_size = (512 * 1024 if e.get('use_small_chunks')
                                      else config.CHUNK_SIZE)

                        # Не пересекаем границу 1 МБ: такой запрос сервер
                        # отвергает (LimitInvalid). Даже если позиция после
                        # короткого ответа перестала быть ровной, режем
                        # запрос по ближайшей границе.
                        boundary = ((request_offset // config.CHUNK_SIZE) + 1) \
                                   * config.CHUNK_SIZE
                        max_len = boundary - request_offset
                        if chunk_size > max_len:
                            chunk_size = max_len

                        # И только степени двойки. Протокол разрешает любое
                        # кратное 4 КБ, но на живом тесте 06.11.2026 сервер
                        # отверг 872 КБ (LimitInvalid) и принял 512 КБ.
                        # Официальные клиенты тоже просят 1 МБ / 512 / 256 /
                        # 128 КБ. Остаток файла добирается сам: сервер
                        # отдаёт меньше запрошенного в конце.
                        step = 4096
                        while step * 2 <= chunk_size:
                            step *= 2
                        chunk_size = max(4096, step)

                        # Скачиваем по СООБЩЕНИЮ, а не по документу.
                        # Telethon внутри сам ловит FILE_REFERENCE_EXPIRED
                        # и перечитывает сообщение — но только когда у него
                        # есть чат и id (client/downloads.py: msg_data
                        # заполняется лишь для types.Message). С документом
                        # ошибка вылетает наружу, и загрузка встаёт навсегда.
                        source = e.get('message') or e.get('document') or document

                        # request_size передаём явно: у iter_download он по
                        # умолчанию 512 КБ, и без этого chunk_size=1 МБ
                        # доходил до сети только половиной.
                        stream = self.client.iter_download(
                            source,
                            offset=request_offset,
                            request_size=chunk_size,
                            chunk_size=chunk_size
                        ).__aiter__()

                        while True:
                            # Кусок, который ждёт плеер, и фоновый требуют
                            # разного терпения: за 45 секунд ожидания буфер
                            # у читателя опустеет. Проверяем на каждом
                            # чанке — нужность может появиться по ходу.
                            chunk_timeout = (
                                config.PIECE_STALL_URGENT_SECS
                                if self._piece_wanted(e, idx)
                                else config.PIECE_STALL_SECS)
                            try:
                                chunk = await asyncio.wait_for(
                                    stream.__anext__(), chunk_timeout)
                            except StopAsyncIteration:
                                break
                            except asyncio.CancelledError:
                                # wait_for по своему таймауту отменяет
                                # внутреннюю корутину, и наружу вместо
                                # TimeoutError приходит отмена. Это такой же
                                # зависший поток, как и молчание: считаем
                                # кусок брошенным и идём переподключаться.
                                # Настоящую остановку загрузки пропускаем
                                # дальше — её обрабатывает внешний блок.
                                if (e.get('paused') or e.get('complete')
                                        or e.get('removed')
                                        or self._is_closed()):
                                    raise
                                print(f"[Кэш] Кусок {idx}: поток не отдал "
                                      f"данные за {chunk_timeout:.0f} с — "
                                      "бросаю")
                                aborted = True
                                break
                            except asyncio.TimeoutError:
                                # Поток замолчал. Если кусок ждёт плеер,
                                # терпеть 45 секунд незачем — бросаем и
                                # переподключаемся; фоновый можно подождать.
                                print(f"[Кэш] Кусок {idx}: поток молчит "
                                      f"{chunk_timeout:.0f} с — бросаю")
                                aborted = True
                                break

                            if e['complete']:
                                return

                            # Читателя перемотали — этот кусок больше не
                            # нужен, бросаем и берём из очереди тот, что
                            # нужен сейчас. Уже скачанное не теряется:
                            # позиция возобновления берётся из трекера.
                            # Но кусок, который ждёт ДРУГОЙ читатель, не
                            # бросаем: у mpv их обычно два — начало файла и
                            # хвост с индексом moov. Взаимные броски
                            # оставляли mpv без индекса вовсе.
                            if (e.get('read_gen', 0) != my_gen
                                    and not self._piece_wanted(e, idx)):
                                aborted = True
                                break

                            fh = e['fh']
                            fh.seek(request_offset)
                            fh.write(chunk)
                            # Сразу отдаём данные на диск, чтобы HTTP-ридер
                            # видел их без задержки и не отдавал нули.
                            fh.flush()
                            # Читатели mpv спят на событии, а не опрашивают
                            # файл каждые 2 мс: будим их сразу после записи.
                            self._wake_cache(e)
                            # Скорость: копим байты в окне и раз в
                            # полсекунды пересчитываем. Накопительная
                            # сумма с прошлого раза выдавала «133 МБ/с»
                            # вместо настоящей скорости.
                            e['speed_window_bytes'] = (
                                e.get('speed_window_bytes', 0) + len(chunk))
                            e['speed_total'] = (
                                e.get('speed_total', 0) + len(chunk))
                            now_ts = time.monotonic()
                            if not e.get('first_byte_ts'):
                                e['first_byte_ts'] = now_ts
                            elapsed = now_ts - e.get('speed_window_ts', now_ts)
                            if elapsed >= 0.5:
                                e['speed_bps'] = (
                                    e['speed_window_bytes'] / elapsed)
                                e['speed_bytes'] = int(e['speed_bps'])
                                e['speed_window_bytes'] = 0
                                e['speed_window_ts'] = now_ts
                            # Данные пришли — связь жива.
                            self._bytes_from_telegram += len(chunk)
                            request_offset += len(chunk)
                            pos += len(chunk)
                            e['tracker'].add_range(
                                start, min(start + pos - 1, start + length - 1)
                            )

                            # Первые байты уже есть — самое время понять,
                            # лежит ли индекс moov в хвосте, и поднять его
                            # немедленно, не дожидаясь всего первого куска.
                            if idx == 0 and not e.get('moov_checked'):
                                self._maybe_prioritize_tail(e)

                            if pos >= length:
                                break

                        # Брошенный поток обязательно закрываем: иначе
                        # соединение остаётся висеть, а каждый новый запрос
                        # к Telegram и без того стоит около 0,6 с.
                        try:
                            await stream.aclose()
                        except Exception:
                            pass

                        if aborted:
                            break
                        if pos >= length:
                            ok = True
                            break

                    except asyncio.CancelledError:
                        raise
                    except Exception as ex:
                        # Неудачный поток мог оставить соединение
                        # открытым. Закрываем до переподключения, иначе
                        # каждый повтор копит висящие соединения.
                        try:
                            if stream is not None:
                                await stream.aclose()
                        except Exception:
                            pass
                        # FloodWait — это не обрыв связи. Переподключаться
                        # бессмысленно: Telegram всё равно велит ждать, а
                        # новый маршрут только добавляет шторм запросов.
                        # Просто спим ровно столько, сколько просят.
                        flood = (telethon_boot.FloodWaitError is not None
                                 and isinstance(ex, telethon_boot.FloodWaitError))
                        if not flood and 'FLOOD_WAIT' in str(ex).upper():
                            flood = True
                        if flood:
                            wait = float(getattr(ex, 'seconds', 10) or 10) + 1.0
                            print(f"[Кэш] Кусок {idx}: Telegram просит "
                                  f"подождать {wait:.0f} с — жду, не "
                                  "переподключаюсь")
                            try:
                                if stream is not None:
                                    await stream.aclose()
                            except Exception:
                                pass
                            await asyncio.sleep(wait)
                            # FloodWait — не ошибка куска. Не сжигаем
                            # оставшиеся попытки: вернём кусок в очередь,
                            # следующий заход начнёт с чистым счётчиком.
                            print(f"[Кэш] Кусок {idx}: после ожидания "
                                  "возвращаю в очередь")
                            aborted = True
                            break

                        # Сервер не принял диапазон: обычно это запрос,
                        # пересекающий границу 1 МБ (так бывает у хвоста,
                        # если его начало не выровнено). Переходим на
                        # 512 КБ для этого файла.
                        limit_invalid = (
                            (telethon_boot.LimitInvalidError is not None
                             and isinstance(ex, telethon_boot.LimitInvalidError))
                            or 'LIMIT_INVALID' in str(ex).upper())
                        if limit_invalid and not e.get('use_small_chunks'):
                            print(f"[Кэш] Кусок {idx}: сервер не принял "
                                  "1 МБ — перехожу на 512 КБ")
                            e['use_small_chunks'] = True
                            try:
                                if stream is not None:
                                    await stream.aclose()
                            except Exception:
                                pass
                            continue

                        # Ссылка на файл истекла. Это НЕ сетевая ошибка:
                        # переподключение и повтор с тем же документом дают
                        # ту же ошибку. Нужно заново прочитать сообщение,
                        # взять оттуда свежую ссылку и только потом
                        # повторять. Раньше здесь не было этой ветки, и
                        # плеер молотил ошибкой 2934 раза, не докачав ни
                        # одного файла: «серии перестают грузиться».
                        #
                        # Текст ошибки — «The file reference has expired…»,
                        # то есть с пробелом, поэтому ищем и подчёркивание,
                        # и пробел: имя класса в тексте не всегда то же.
                        text = str(ex).upper()
                        expired = ('FILE_REFERENCE' in text
                                   or 'FILE REFERENCE' in text)
                        if not expired and telethon_boot.FileReferenceExpiredError is not None:
                            expired = isinstance(ex, telethon_boot.FileReferenceExpiredError)
                        if expired:
                            refreshed = await self._refresh_document(
                                e, document.id)
                            if refreshed:
                                print(f"[Кэш] Кусок {idx}: ссылка обновлена, "
                                      "продолжаю")
                                continue
                            if refreshed is None:
                                # Сеть не дала прочитать сообщение. Это не
                                # повод навсегда помечать файл мёртвым:
                                # вернём кусок в очередь и попробуем позже.
                                print(f"[Кэш] Кусок {idx}: ссылку не "
                                      "обновить из-за сети — повторю")
                                await asyncio.sleep(1.0)
                                aborted = True
                                break
                            # Сообщение удалили или медиа заменили — вот
                            # теперь ссылка мертва навсегда.
                            # Помечаем запись сбойной и выходим из воркера
                            # совсем: иначе внешний while взял бы кусок из
                            # очереди снова, и это превращалось в вечный
                            # цикл из одинаковых ошибок (в журнале — 2934
                            # подряд, без единого докачанного файла).
                            print(f"[Кэш] Кусок {idx}: ссылка не "
                                  "обновляется, файл недоступен")
                            e['error'] = True
                            e['fatal'] = 'FILE_REFERENCE_EXPIRED'
                            try:
                                self.status_changed.emit(
                                    "Файл больше недоступен в Telegram")
                            except Exception:
                                pass
                            return
                        if attempt == config.SEG_RETRIES:
                            print(f"[Кэш] Кусок {idx}: ошибка: {ex}")
                            break
                        # Смена режима VPN рвёт маршрут: без нового перебора
                        # все повторы уйдут в тот же мёртвый адрес. Пауза
                        # растёт с каждой попыткой и разъезжается джиттером,
                        # иначе несколько воркеров бьют одновременно.
                        await self.reconnect(force=True)
                        await asyncio.sleep(min(4.0, 0.5 * (2 ** attempt))
                                            + random.uniform(0, 0.3))

                if ok:
                    e['piece_done'].add(idx)
                    e['downloaded_bytes'] += length

                    # Первый кусок готов — самое время понять, не лежит ли
                    # индекс moov в хвосте файла (иначе mpv будет ждать).
                    if idx == 0:
                        self._maybe_prioritize_tail(e)

                    # Не пишем JSON после каждого чанка.
                    now = time.monotonic()
                    if now - e.get('last_meta_save', 0) > 0.5:
                        self._save_partial_state(e)
                        e['last_meta_save'] = now

                    if len(e['piece_done']) >= e['piece_count']:
                        self._save_partial_state(e)
                        self._finalize_cache(e)
                        return
                elif aborted:
                    # Кусок брошен из-за перемотки или зависшего потока, но
                    # не потерян: возвращаем его в очередь. Отметку «поднят»
                    # снимаем обязательно — иначе кусок застрянет в общем
                    # порядке, и читатель его больше не дождётся.
                    if not e['complete']:
                        if e.get('boosted'):
                            e['boosted'].discard(idx)
                        queue.put_nowait((idx, idx))
                        e['queued'].add(idx)
                else:
                    # Возвращаем кусок в очередь, чтобы временный сбой не
                    # останавливал весь кеш. Пауза после этого нужна, чтобы
                    # при полностью мёртвой сети воркеры не крутили очередь
                    # на полной скорости: без неё они только и делали бы,
                    # что падали и ставили кусок обратно.
                    if not e['complete']:
                        queue.put_nowait((idx + 1000, idx))
                        e['queued'].add(idx)
                        await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                # Отмена приходит по двум совершенно разным причинам, и
                # раньше их не различали.
                #
                # Первая: плеер приостановил загрузку или программа
                # закрывается — воркер обязан выйти.
                #
                # Вторая: это отменил сам wait_for по своему таймауту.
                # Telethon не отдаёт управление мгновенно, и вместо
                # TimeoutError наружу выходит отмена. Выходить в этом случае
                # нельзя: воркер умирал навсегда, качать становилось некому,
                # а снаружи это выглядело как «связь есть, скорость стоит на
                # нуле». Оживить загрузку было нечем — только перезапуском.
                if (e.get('paused') or e.get('complete') or e.get('removed')
                        or e.get('fh') is None or self._is_closed()):
                    if not e['complete'] and idx not in e['piece_done']:
                        try:
                            e['active'].discard(idx)
                            queue.put_nowait((idx, idx))
                            e['queued'].add(idx)
                        except Exception:
                            pass
                    return

                print(f"[Кэш] Кусок {idx}: отмена на таймауте — "
                      "возвращаю в очередь, воркер продолжает")
                if not e['complete']:
                    try:
                        queue.put_nowait((idx, idx))
                        e['queued'].add(idx)
                    except Exception:
                        pass
                continue
            finally:
                e['active'].discard(idx)
                queue.task_done()
