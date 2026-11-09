"""История просмотров, позиции и настройки воспроизведения."""

import time

from ..core import config, history, settings

class PlaybackMixin:
    def is_watched(self, doc_id):
        return doc_id is not None and doc_id in self.watched

    def mark_watched(self, doc_id):
        """Отмечает ролик просмотренным и сразу пишет историю на диск."""
        if doc_id is None or doc_id in self.watched:
            return False
        self.watched.add(doc_id)
        history.save(self.watched, self.positions, self.history_entries)
        return True

    def set_setting(self, key, value):
        """Меняет настройку и сразу пишет её на диск.

        Пишем только при реальном изменении: ползунок громкости приходит
        десятками сигналов, и без этой проверки файл переписывался бы на
        каждое движение.
        """
        if self.settings.get(key) == value:
            return
        self.settings[key] = value
        settings.save(self.settings)

    def remember_position(self, doc_id, seconds):
        """Запоминает позицию просмотра ролика.

        Короткие позиции забываем: «продолжить» с двух секунд только
        раздражает, а ролик при этом считается непросмотренным.
        """
        if doc_id is None:
            return
        if seconds and seconds > 5:
            self.positions[doc_id] = float(seconds)
        else:
            self.positions.pop(doc_id, None)
        # Вкладке «История» позиция нужна и для коротких роликов: там она
        # показывается как «сколько просмотрено», а не как точка возврата.
        record = self.history_entries.get(doc_id)
        if record is not None:
            record['position'] = max(0.0, float(seconds or 0.0))

    def record_history(self, doc_id, title='', channel='', peer_id=None,
                       message_id=None, position=0.0, duration=0.0,
                       size=0):
        """Добавляет/обновляет запись о просмотре для вкладки «История».

        Пишется при каждом запуске ролика, а не только при первом
        просмотре: повторный запуск должен поднимать запись наверх, а не
        оставлять её в глубине списка.
        """
        if doc_id is None:
            return
        record = self.history_entries.get(doc_id) or {'doc_id': doc_id}
        if title:
            record['title'] = title
        if channel:
            record['channel'] = channel
        if peer_id is not None:
            try:
                record['peer_id'] = int(peer_id)
            except (TypeError, ValueError):
                pass
        if message_id is not None:
            try:
                record['message_id'] = int(message_id)
            except (TypeError, ValueError):
                pass
        if position:
            record['position'] = max(0.0, float(position))
        if duration:
            record['duration'] = max(0.0, float(duration))
        if size:
            record['size'] = int(size)
        record['doc_id'] = int(doc_id)
        record['watched_at'] = time.time()
        self.history_entries[doc_id] = record
        history.save(self.watched, self.positions, self.history_entries)

    def fetch_history_message(self, record):
        """Просит Telegram сообщение из истории и отдаёт его сигналом.

        Нужно для записей из других каналов: открыть ролик можно только по
        свежему сообщению, а не по одному id документа.
        """
        if not isinstance(record, dict):
            self.history_message.emit(record, None)
            return
        peer_id = record.get('peer_id')
        message_id = record.get('message_id')
        if (peer_id is None or message_id is None
                or self.loop is None or self.client is None):
            self.history_message.emit(record, None)
            return

        async def work():
            try:
                message = await self.client.get_messages(
                    peer_id, ids=message_id)
                self.history_message.emit(record, message)
            except Exception as ex:
                print(f"[История] Сообщение не получено: {ex}")
                self.history_message.emit(record, None)

        # Если цикл ещё не готов, submit_coroutine закроет корутину и
        # вернёт None. Тогда окно должно получить отказ, а не ждать вечно.
        if self.submit_coroutine(work()) is None:
            self.history_message.emit(record, None)

    def forget_history(self, doc_id):
        """Убирает одну запись из вкладки «История».

        Пометку «просмотрено» и позицию просмотра не трогаем: это
        отдельные вещи, и удаление записи из журнала не должно сбрасывать
        место, на котором человек остановился.
        """
        if doc_id is None:
            return
        if self.history_entries.pop(doc_id, None) is not None:
            history.save(self.watched, self.positions, self.history_entries)

    def clear_history(self):
        """Очищает все записи вкладки «История».

        «Просмотрено» и позиции остаются: очищается журнал просмотров,
        а не состояние роликов.
        """
        if not self.history_entries:
            return
        self.history_entries.clear()
        history.save(self.watched, self.positions, self.history_entries)

    def download_history_message(self, record):
        """Скачивает ролик из истории в кэш, не открывая плеер."""
        if not isinstance(record, dict):
            return

        async def work():
            doc_id = record.get('doc_id')
            message = None
            # Сначала ищем сообщение в уже загруженном канале: так не
            # нужен лишний запрос к Telegram.
            for candidate in list(self.current_messages or []):
                try:
                    if candidate.media.document.id == doc_id:
                        message = candidate
                        break
                except Exception:
                    continue
            if message is None:
                peer_id = record.get('peer_id')
                message_id = record.get('message_id')
                if peer_id is None or message_id is None:
                    self.status_changed.emit('Ролик больше недоступен')
                    return
                try:
                    message = await self.client.get_messages(
                        peer_id, ids=message_id)
                except Exception as ex:
                    print(f"[История] Ролик для загрузки не получен: {ex}")
                    self.status_changed.emit('Не удалось получить ролик')
                    return
            document = getattr(getattr(message, 'media', None),
                               'document', None)
            if document is None:
                self.status_changed.emit('Ролик больше недоступен')
                return
            self.status_changed.emit('Скачиваю в кэш...')
            await self.ensure_cache(message, workers=config.DOWNLOAD_WORKERS)

        if self.submit_coroutine(work()) is None:
            self.status_changed.emit('Не удалось начать загрузку')

    def flush_history(self):
        """Пишет историю просмотров и позиции на диск."""
        history.save(self.watched, self.positions, self.history_entries)
