"""Списки видео канала, качества и плейлисты."""

from ..core import config
from ..system import telethon_boot


class VideoMixin:
    @staticmethod
    def _video_info(message):
        if not message.media or not isinstance(message.media, telethon_boot.MessageMediaDocument):
            return None
        doc = message.media.document
        attr = next(
            (a for a in doc.attributes if isinstance(a, telethon_boot.DocumentAttributeVideo)),
            None
        )
        if not attr:
            return None

        text = message.text or ""
        title = text[:60].replace("\n", " ").strip()
        if not title:
            title = f"Видео #{message.id}"

        return doc, attr, title

    async def load_videos_from_dialog(self, dialog):
        dialog_id = getattr(dialog, "id", None)

        # Внутрисессионный кеш — самый дешёвый способ ускорить возврат в канал.
        cached = self._videos_by_dialog.get(dialog_id)
        if cached is not None:
            # Список мог устареть, пока шла загрузка: канал уже переключили.
            if dialog_id == self.active_dialog_id:
                self.current_messages, video_titles = cached
                self.videos_loaded.emit(dialog_id, video_titles)
            self.status_changed.emit("Готово")
            return

        try:
            entity = getattr(dialog, "entity", None) or dialog_id
            self.status_changed.emit("Загрузка видео...")

            async def _fetch():
                collected = []
                seen = 0
                oldest = None
                # Читаем на одно сообщение больше страницы: так видно,
                # есть ли вообще история ниже, не делая отдельный запрос.
                async for message in self.client.iter_messages(
                        entity, limit=config.VIDEO_PAGE + 1):
                    seen += 1
                    oldest = (message.id if oldest is None
                              else min(oldest, message.id))
                    if self._video_info(message):
                        collected.append(message)
                return collected, seen, oldest

            messages, seen, oldest = await self._request_with_reconnect(
                _fetch, 'Загрузка списка видео')

            messages.reverse()
            if oldest is not None:
                self._video_offset[dialog_id] = oldest

            video_titles = [self._index_video(m) or '' for m in messages]

            # Кнопка догрузки нужна только если в истории есть сообщения
            # ниже прочитанной страницы. bool(messages) здесь врал:
            # канал ровно на 200 сообщений навсегда получал «догрузить».
            self._video_more[dialog_id] = seen > config.VIDEO_PAGE
            self._videos_by_dialog[dialog_id] = (messages, video_titles)
            # current_messages — общее состояние, из него играет плеер.
            # Присваиваем только активному каналу: при быстром переключении
            # A->B список показывал бы B, а плеер открывал ролик из A.
            if dialog_id == self.active_dialog_id:
                self.current_messages = messages
                self.videos_loaded.emit(dialog_id, video_titles)
            # Список пришёл целиком — в канале видео точно есть.
            self._note_channel_videos(dialog_id, messages)
            self.status_changed.emit("Готово")
        except Exception as e:
            self.error_occurred.emit(f"Ошибка видео: {e}")
            self.videos_loaded.emit(dialog_id, [])

    def _index_video(self, message):
        """Кладёт ролик в группу качеств и возвращает его название.

        Общее для первой загрузки канала и для догрузки: группы качеств
        должны собираться одинаково, иначе меню качества у догруженных
        роликов было бы пустым.
        """
        info = self._video_info(message)
        if not info:
            return None
        _, attr, title = info
        group_key = self._quality_key(message)
        self._quality_groups.setdefault(group_key, []).append({
            'message': message,
            'quality': f"{attr.h}p" if getattr(attr, 'h', None) else f"{getattr(attr, 'w', 0)}p",
            'size': message.media.document.size / (1024 * 1024),
            'width': getattr(attr, 'w', 0),
            'height': getattr(attr, 'h', 0),
            'duration': getattr(attr, 'duration', 0),
            'doc_id': message.media.document.id
        })
        return title

    async def load_more_videos(self, dialog):
        """Догружает следующий кусок истории канала в конец списка.

        Первая загрузка читает config.VIDEO_PAGE последних сообщений. Если в
        канале ролики идут редко, этого мало: на одно видео приходится
        десяток постов, и список обрывается без намёка на то, что он
        неполный. Здесь читается ещё столько же сообщений НИЖЕ самого
        старого загруженного (offset_id идёт от новых к старым), поэтому
        уже прочитанное не перечитывается.
        """
        dialog_id = getattr(dialog, "id", None)
        if dialog_id is None or dialog_id in self._loading_more:
            return
        cached = self._videos_by_dialog.get(dialog_id)
        # Догружать можно только поверх уже загруженного списка.
        if cached is None:
            return
        old_messages, old_titles = cached[0], cached[1]
        offset_id = self._video_offset.get(dialog_id)
        if offset_id is None and old_messages:
            offset_id = min(m.id for m in old_messages)
        if offset_id is None:
            self._video_more[dialog_id] = False
            self.status_changed.emit("Больше роликов нет")
            return
        self._loading_more.add(dialog_id)
        try:
            # offset_id — исключающая граница: Telegram вернёт сообщения
            # строго старше него.
            offset_id = self._video_offset.get(dialog_id, offset_id)
            entity = getattr(dialog, "entity", None) or dialog_id
            self.status_changed.emit("Догрузка видео...")

            async def _fetch():
                collected = []
                seen = 0
                oldest = None
                async for message in self.client.iter_messages(
                        entity, limit=config.VIDEO_PAGE + 1,
                        offset_id=offset_id):
                    seen += 1
                    oldest = (message.id if oldest is None
                              else min(oldest, message.id))
                    if self._video_info(message):
                        collected.append(message)
                return collected, seen, oldest

            fresh, seen, oldest = await self._request_with_reconnect(
                _fetch, 'Догрузка списка видео')
            if oldest is not None:
                self._video_offset[dialog_id] = oldest
            # Дубли отсекаем по id: если канал перечитали между запросами,
            # ролик стоял бы в списке дважды.
            known = {m.id for m in old_messages}
            fresh = [m for m in fresh if m.id not in known]
            # История канала идёт от новых к старым, а список роликов —
            # наоборот, поэтому переворачиваем. Просто reverse() здесь
            # нельзя: ролики разбросаны по истории, и после отсева по
            # соседним элементам порядок восстанавливается только полной
            # сортировкой.
            fresh.sort(key=lambda m: m.id)

            messages = fresh + list(old_messages)
            titles = [self._index_video(m) or '' for m in fresh]
            titles += list(old_titles)
            # Кнопка остаётся, только если ниже прочитанного реально
            # есть ещё сообщения. Наличие роликов в текущей странице
            # ничего не говорит о том, что история не кончилась.
            self._video_more[dialog_id] = seen > config.VIDEO_PAGE
            # Кеш канала кладём ПОСЛЕ догруженного списка: иначе при
            # возврате в канал догрузка пропадала бы.
            self._videos_by_dialog[dialog_id] = (messages, titles)
            if dialog_id == self.active_dialog_id:
                self.current_messages = messages
                self.videos_loaded.emit(dialog_id, titles)
            print(f"[Догрузка] Канал {dialog_id}: +{len(fresh)}, "
                  f"всего {len(messages)}")
            if fresh:
                self.status_changed.emit(f"Догружено: {len(fresh)}")
            elif seen > config.VIDEO_PAGE:
                self.status_changed.emit("Пока без новых роликов")
            else:
                self.status_changed.emit("Больше роликов нет")
        except Exception as e:
            self.error_occurred.emit(f"Ошибка догрузки: {e}")
        finally:
            self._loading_more.discard(dialog_id)

    def pin_playlist(self, messages):
        """Закрепляет список роликов за окном плеера и возвращает токен.

        Плейлист живёт отдельно от current_messages: пока ролик играет,
        пользователь может переключить канал в главном окне, и следующий
        Range-запрос mpv (перемотка, переподключение) ушёл бы за чужим
        файлом. Держим только последние плейлисты — старые окна закрыты.
        """
        self._playlist_seq += 1
        token = str(self._playlist_seq)
        self._playlists[token] = list(messages)
        while len(self._playlists) > 4:
            self._playlists.pop(next(iter(self._playlists)))
        return token

    @staticmethod
    def _peer_key(message):
        """«Сырой» id чата: channel_id/chat_id, а не Dialog.id.

        У Dialog.id знак -100..., из-за чего ключи записи и чтения не
        совпадали, и автовыбор качества не находил ни одной группы.
        """
        return (getattr(message.peer_id, "channel_id", None)
                or getattr(message.peer_id, "chat_id", None))

    @classmethod
    def _quality_key(cls, message):
        """Ключ группы качеств: один ролик в разных качествах."""
        return (cls._peer_key(message), (message.text or "").strip())

    @classmethod
    def _msg_key(cls, message):
        """Ключ сообщения, уникальный между каналами.

        id сообщения уникален только внутри чата: в двух каналах сообщение
        №5 — это разные ролики. Кеш качеств по одному id давал коллизию, и
        меню качества могло показать варианты чужого канала.
        """
        return (cls._peer_key(message), message.id)

    @staticmethod
    def _same_video(variant, duration):
        """Отсеивает чужие ролики, попавшие в группу по общей подписи.

        Группировка идёт по тексту подписи, а под ней в канале лежат разные
        серии. У вариантов одного ролика длительность совпадает, у разных —
        отличается, поэтому она и разделяет. Нет данных о длительности —
        не отсеиваем: лучше показать лишнее, чем потерять вариант.
        """
        if duration <= 0:
            return True
        vdur = variant.get('duration') or 0
        if vdur <= 0:
            return True
        return abs(vdur - duration) <= max(2, duration * 0.02)

    async def get_video_qualities(self, message):
        key = self._msg_key(message)
        if key in self._video_qualities:
            return self._video_qualities[key]

        info = self._video_info(message)
        duration = getattr(info[1], 'duration', 0) if info else 0

        group_key = self._quality_key(message)
        qualities = [v for v in self._quality_groups.get(group_key, [])
                     if self._same_video(v, duration)]

        # Fallback для сообщений, загруженных не через load_videos_from_dialog.
        if not qualities:
            try:
                entity = message.peer_id

                async def _fetch():
                    collected = []
                    async for msg in self.client.iter_messages(
                            entity, limit=50):
                        collected.append(msg)
                    return collected

                candidates = await self._request_with_reconnect(
                    _fetch, 'Поиск вариантов качества')
                for msg in candidates:
                    info = self._video_info(msg)
                    if not info or msg.text != message.text:
                        continue
                    doc, attr, _ = info
                    if not self._same_video(
                            {'duration': getattr(attr, 'duration', 0)},
                            duration):
                        continue
                    qualities.append({
                            'message': msg,
                            'quality': f"{attr.h}p" if getattr(attr, 'h', None) else f"{getattr(attr, 'w', 0)}p",
                            'size': doc.size / (1024 * 1024),
                            'width': getattr(attr, 'w', 0),
                            'height': getattr(attr, 'h', 0),
                            'duration': getattr(attr, 'duration', 0),
                            'doc_id': doc.id
                        })
            except Exception as e:
                print(f"[Качество] Ошибка: {e}")

        qualities.sort(key=lambda x: x['height'] or 0, reverse=True)
        self._video_qualities[key] = qualities
        return qualities

    def pick_quality_for_speed(self, message, max_mbps):
        """Самый качественный вариант, который влезает в max_mbps.

        Работает синхронно по уже собранным группам качеств — без сети.
        Возвращает None, если подходящего варианта нет.
        """
        if max_mbps <= 0:
            return None
        info = self._video_info(message)
        duration = getattr(info[1], 'duration', 0) if info else 0
        variants = [v for v in (self._quality_groups.get(
            self._quality_key(message)) or [])
            if self._same_video(v, duration)]

        best = None
        for v in variants:
            dur = v.get('duration') or 0
            if dur <= 0:
                continue
            bitrate = v['size'] * 8 / dur      # size в МБ -> Мбит/с
            if bitrate <= max_mbps:
                if best is None or (v['height'] or 0) > (best['height'] or 0):
                    best = v
        return best
