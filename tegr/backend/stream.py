"""Локальный HTTP-сервер для mpv: Range, 206/416 и отдача кэша."""

import asyncio
import secrets
import time

from aiohttp import web

from ..core import config
from .cache_math import _open_read, piece_index_for
from .http_range import parse_range_header

class StreamMixin:
    async def start_stream_server(self):
        app = web.Application(client_max_size=0)
        app.router.add_get('/stream', self.handle_stream)

        runner = web.AppRunner(app, access_log=None)
        await runner.setup()

        # Порт 8081 может быть занят чужой программой или вторым экземпляром
        # плеера. Раньше это была фатальная ошибка, а SO_REUSEADDR на Windows
        # позволял двум процессам «сесть» на один порт — и запросы mpv уходили
        # чужому серверу. Теперь при занятом порте берём свободный у системы.
        last_error = None
        for port in (config.STREAM_PORT, 0):
            site = web.TCPSite(runner, '127.0.0.1', port)
            try:
                await site.start()
            except OSError as ex:
                last_error = ex
                print(f"[Сервер] Порт {port or 'любой'} не подошёл: {ex}")
                continue
            self.stream_port = self._bound_port(site)
            self._server_started = True
            if self.stream_port != config.STREAM_PORT:
                print(f"[Сервер] Порт {config.STREAM_PORT} занят — "
                      f"слушаю {self.stream_port}")
            else:
                print(f"[Сервер] Слушаю 127.0.0.1:{self.stream_port}")
            return

        self.error_occurred.emit(f"Ошибка сервера: {last_error}")
        raise RuntimeError(f"Не удалось поднять сервер: {last_error}")

    @staticmethod
    def _bound_port(site):
        """Реальный порт, который занял сервер (0 = «любой» от системы)."""
        try:
            return int(site._server.sockets[0].getsockname()[1])
        except Exception:
            return config.STREAM_PORT

    async def handle_stream(self, request):
        try:
            # Локальный порт доступен всем программам на машине.
            # Токен отделяет запросы плеера от чужих. Пустое значение
            # оставляем для тестов и совместимости со старыми
            # экземплярами, у которых поле ещё не было создано.
            expected = getattr(self, 'stream_token', '') or ''
            if expected and not secrets.compare_digest(
                    str(request.query.get('t') or ''), expected):
                return web.Response(status=403)
            msg_idx = int(request.query.get('idx', -1))
            # Плейлист берём по токену окна плеера: смена канала в главном
            # окне не должна подменять файл под уже играющим роликом.
            token = request.query.get('pl')
            playlist = self._playlists.get(token) if token else None
            if playlist is None:
                playlist = self.current_messages
            # Индекс должен быть строго в диапазоне. При idx=-5 проверка
            # «== -1» пропускала запрос, и playlist[-5] отдавал предпоследний
            # ролик вместо честной ошибки.
            if msg_idx < 0 or msg_idx >= len(playlist):
                return web.Response(status=404)

            document = playlist[msg_idx].media.document
            file_size = document.size

            parsed = parse_range_header(
                request.headers.get('Range'), file_size)
            if parsed is None:
                # RFC 9110: у 416 должен быть Content-Range с полным
                # размером файла, иначе клиент не знает, какой диапазон
                # допустим.
                return web.Response(status=416, headers={
                    'Content-Range': f'bytes */{file_size}',
                    'Accept-Ranges': 'bytes',
                })
            start, end, status = parsed

            print(f"[HTTP] idx={msg_idx} запрос диапазона {start}-{end} ({status})")

            content_length = end - start + 1
            headers = {
                'Content-Type': document.mime_type or 'video/mp4',
                'Content-Length': str(content_length),
                'Accept-Ranges': 'bytes',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
            }

            if status == 206:
                headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'

            # Передаём СООБЩЕНИЕ, а не документ: только по нему можно
            # заново прочитать файловую ссылку, когда она истечёт.
            e = await self.ensure_cache(playlist[msg_idx])
            if e is None:
                return web.Response(status=500)

            self.prioritize_position(e, start)

            return await self._serve_from_cache(
                request, e, document, start, end, status, headers
            )

        except (ConnectionResetError, ConnectionAbortedError):
            # mpv закрывает соединение при перемотке и перезапуске файла —
            # это нормальный ход событий, а не ошибка.
            return web.Response(status=499)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            text = str(ex)
            if 'Connection lost' in text or 'Connection reset' in text:
                return web.Response(status=499)
            print(f"[HTTP] Ошибка потока: {ex}")
            return web.Response(status=500)

    def _available_from(self, e, start):
        if start >= e['size']:
            return 0
        return e['tracker'].available_bytes_from(start)

    async def _serve_from_cache(self, request, e, document, start, end, status,
                                headers):
        response = web.StreamResponse(status=status, headers=headers)
        await response.prepare(request)

        # Дескриптор чтения берём из записи на КАЖДОЙ итерации, а не один
        # раз в начале. Пока идёт отдача, _finalize_cache успевает закрыть
        # старый дескриптор и открыть новый: закешированная локальная
        # ссылка оставалась закрытой, seek падал с ValueError, data выходил
        # пустым, а ветка «complete» обрывала ответ раньше Content-Length.
        # mpv видел преждевременный конец файла и уходил на следующий ролик.
        if e.get('rfh') is None and e.get('complete'):
            try:
                e['rfh'] = _open_read(e['path'])
            except Exception:
                pass

        pos = start
        remaining = end - start + 1
        last_prio_ts = 0.0
        last_prio_piece = -1

        try:
            while remaining > 0:
                # Клиент мог уйти (mpv закрывает соединение при перемотке
                # и перезапуске файла), а запись кеша — исчезнуть после
                # discard_download. Без этих проверок цикл ждал бы данные
                # вечно: 500 пробуждений в секунду на брошенное соединение.
                if request.transport is None or request.transport.is_closing():
                    break
                if e.get('removed'):
                    break
                if e.get('error') and not e.get('complete'):
                    break

                # Событие сбрасываем ДО проверки наличия байт: если данные
                # придут между проверкой и ожиданием, event уже будет
                # выставлен и разбудит читателя без задержки.
                event = e.get('data_event')
                if event is None:
                    event = asyncio.Event()
                    e['data_event'] = event
                event.clear()
                avail = self._available_from(e, pos)

                if avail <= 0:
                    if e.get('complete'):
                        break

                    # Читатель упёрся в непрокачанный участок — поднимаем
                    # приоритет именно этого места. Без этого длинное
                    # последовательное чтение (например, индекса в хвосте)
                    # ждало бы куски в общем порядке, то есть после всей
                    # остальной загрузки.
                    piece = piece_index_for(e['pieces'], pos)
                    now = time.monotonic()
                    if piece != last_prio_piece or now - last_prio_ts > 1.0:
                        self.prioritize_position(e, pos)
                        # Читатель упёрся в кусок, который никто не качает.
                        # Поднимаем поколение: воркеры бросят то, что качают
                        # зря, и возьмут нужное. Без этого приходилось ждать,
                        # пока воркер домучает свой четырёхмегабайтный кусок.
                        if (piece not in e['active']
                                and piece not in e['piece_done']):
                            e['read_gen'] = e.get('read_gen', 0) + 1
                            # Читатель ждёт кусок, которого никто не качает,
                            # и живых воркеров у этой записи нет. Так бывает
                            # после переключения ролика: предыдущую запись
                            # приостановили, а разбудить её было вправе
                            # только play_video — и mpv, вернувшись к этому
                            # ролику, стоял на нуле навсегда. Будим сами:
                            # раз mpv читает этот файл, он и есть текущий.
                            if not [t for t in e.get('tasks', [])
                                    if not t.done()]:
                                # Здесь документ уже не важен: запись
                                # кеша помнит сообщение с прошлого раунда.
                                await self.ensure_cache(
                                    e.get('message') or document, resume=True)
                        last_prio_piece = piece
                        last_prio_ts = now

                    # Ждём сигнал о новых байтах. 100 мс — только
                    # страховка от потерянного сигнала, обычно событие
                    # срабатывает сразу после записи куска.
                    try:
                        await asyncio.wait_for(
                            event.wait(), config.CACHE_WAIT_SECS)
                    except asyncio.TimeoutError:
                        pass
                    continue

                n = min(remaining, avail, config.READ_CAP)

                data = b''
                for _attempt in (0, 1):
                    try:
                        rfh = e.get('rfh')
                        if rfh is None:
                            rfh = _open_read(e['path'])
                            e['rfh'] = rfh

                        rfh.seek(pos)
                        data = rfh.read(n)
                        break
                    except Exception:
                        # Дескриптор мог устареть: _finalize_cache закрывает
                        # старый и открывает новый. Сбрасываем и пробуем ещё
                        # раз свежим — иначе ответ обрывался бы раньше
                        # Content-Length, и mpv видел конец файла.
                        e['rfh'] = None
                        data = b''

                if not data:
                    if e.get('complete'):
                        break
                    try:
                        await asyncio.wait_for(
                            event.wait(), config.CACHE_WAIT_SECS)
                    except asyncio.TimeoutError:
                        pass
                    continue

                await response.write(data)
                pos += len(data)
                remaining -= len(data)

        except (ConnectionResetError, asyncio.CancelledError):
            raise

        # rfh здесь НЕ закрываем: он переиспользуется следующими
        # Range-запросами, а закрывают его _finalize_cache и discard.
        try:
            await response.write_eof()
        except (ConnectionResetError, asyncio.CancelledError):
            pass

        return response
