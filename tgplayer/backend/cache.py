"""Записи кэша, их остановка/удаление и наблюдение за связью."""

import asyncio
import os
import time

from ..core import config


class CacheMixin:
    def _close_entry_handles(self, e):
        """Закрывает файловые дескрипторы записи и чтения записи кеша.

        На Windows открытый файл нельзя удалить, поэтому перед чисткой
        кеша дескрипторы обязательно закрываем.
        """
        for key in ("fh", "rfh"):
            f = e.get(key)
            if f:
                try:
                    f.flush()
                except Exception:
                    pass
                try:
                    f.close()
                except Exception:
                    pass
                e[key] = None

    def cleanup_cache(self):
        """Останавливает фоновые задачи и вычищает кэш с диска.

        Раньше .part здесь сохранялся ради докачки после перезапуска, но
        тогда недокачанный ролик (сотни мегабайт) висел на диске, пока его
        не переоткроют, и кэш рос от запуска к запуску. Теперь при выходе
        удаляется всё.
        """
        for e in list(self.cache.values()):
            # Метка до отмены: воркер проверяет её, чтобы отличить удаление
            # записи от отмены по сетевому таймауту.
            e['removed'] = True
            for t in e.get('tasks', []):
                if not t.done():
                    t.cancel()
            self._close_entry_handles(e)
        self.cache.clear()
        self._wipe_cache_dir()

    @staticmethod
    def _wipe_cache_dir():
        """Удаляет все файлы кэша.

        Ошибки глотаем: на Windows файл может остаться залоченным чужим
        дескриптором, и падать из-за этого на выходе незачем.
        """
        try:
            names = os.listdir(config.CACHE_DIR)
        except Exception:
            return
        for name in names:
            p = os.path.join(config.CACHE_DIR, name)
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass

    def cancel_user_download(self, doc_id):
        """Отменяет загрузку и удаляет её файлы из кэша (Qt-поток).

        Это именно отмена, а не пауза: недокачанный файл стирается, и
        при следующем «скачать» загрузка пойдёт с начала. Пауза — это
        pause_user_download, у неё файл остаётся.

        Порядок важен: сначала закрываем писательский дескриптор, потом
        удаляем файл — иначе на Windows удалить нечего, файл занят.
        """
        if self.loop is None or self.loop.is_closed():
            self._remove_cache_files(doc_id)
            return
        self.loop.call_soon_threadsafe(
            self._cancel_user_download_on_loop, doc_id)

    def _cancel_user_download_on_loop(self, doc_id):
        self._discard_download_on_loop(doc_id)

    def pause_user_download(self, doc_id):
        """Останавливает загрузку, сохраняя файл и прогресс (Qt-поток).

        Из диалога загрузок это кнопка паузы: скачанное остаётся на
        диске, и нажатие «продолжить» докачает с того же места, а не
        начнёт заново. Дескриптор закрываем — иначе файл остаётся
        занятым и кэш нечем чистить.
        """
        if self.loop is None or self.loop.is_closed():
            return
        self.loop.call_soon_threadsafe(
            self._pause_user_download_on_loop, doc_id)

    def _pause_user_download_on_loop(self, doc_id):
        e = self.cache.get(doc_id)
        if e is None or e.get('complete'):
            print(f"[Кэш] Останавливать нечего: {doc_id}")
            return
        self._pause_download_on_loop(doc_id)
        self._wake_cache(e)
        # Дескриптор закрываем отдельно от записи: ensure_cache умеет
        # открыть его заново, когда загрузку попросят снова.
        fh = e.get('fh')
        if fh is not None:
            try:
                fh.flush()
            except Exception:
                pass
            try:
                fh.close()
            except Exception:
                pass
            e['fh'] = None

    def forget_cache_entry(self, doc_id):
        """Забывает готовую запись кэша после удаления её файла (Qt-поток).

        Нужно после «удалить из кэша»: запись о готовом файле держит
        читающий дескриптор, и на Windows файл из-за него нельзя
        удалить. Закрываем дескриптор и выбрасываем запись — следующий
        запрос прочитает файл заново.
        """
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._forget_on_loop, doc_id)
        else:
            self._forget_on_loop(doc_id)

    def is_cached_file(self, doc_id):
        """Ролик целиком лежит файлом .mp4 в кэше.

        Смотрим именно файл, а не запись self.cache: после «удалить из
        кэша» запись отбрасывается, а файл от прошлого запуска мог
        остаться и на диске.
        """
        path = os.path.join(config.CACHE_DIR, f'{doc_id}.mp4')
        try:
            return os.path.isfile(path)
        except Exception:
            return False

    def _forget_on_loop(self, doc_id):
        e = self.cache.get(doc_id)
        if e is not None and e.get('complete'):
            self._close_entry_handles(e)
            self.cache.pop(doc_id, None)

    def delete_cached_file(self, doc_id):
        """Удаляет скачанный ролик из кэша целиком (Qt-поток).

        Раньше удаление было только общим («Очистить кэш» в настройках),
        и один ролик убрать было нельзя. Порядок важен: сначала в
        потоке бэкенда закрываем дескрипторы записи, потом удаляем файл
        — занятый на Windows файл не удаляется.
        """
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._delete_cached_on_loop, doc_id)
        else:
            self._delete_cached_on_loop(doc_id)

    def _delete_cached_on_loop(self, doc_id):
        e = self.cache.get(doc_id)
        if e is not None:
            # Запись убираем и из кэша: иначе mpv продолжал бы играть
            # из файла, который мы только что удалили.
            e['removed'] = True
            self._wake_cache(e)
            for t in e.get('tasks', []):
                if not t.done():
                    t.cancel()
            self._close_entry_handles(e)
            self.cache.pop(doc_id, None)
        self._remove_cache_files(doc_id)

    def discard_download(self, doc_id):
        """Отменить загрузку документа и удалить его файлы кэша (Qt-поток)."""
        if self.loop is None or self.loop.is_closed():
            self._remove_cache_files(doc_id)
            return
        self.loop.call_soon_threadsafe(self._discard_download_on_loop, doc_id)

    def _discard_download_on_loop(self, doc_id):
        e = self.cache.get(doc_id)
        if e is not None:
            e['removed'] = True
            self._wake_cache(e)
            for t in e.get('tasks', []):
                if not t.done():
                    t.cancel()
            self._close_entry_handles(e)
            self.cache.pop(doc_id, None)
        self._remove_cache_files(doc_id)

    @staticmethod
    def _remove_cache_files(doc_id):
        for suffix in ('.part', '.part.json', '.mp4'):
            p = os.path.join(config.CACHE_DIR, f'{doc_id}{suffix}')
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

    def pause_download(self, doc_id):
        """Останавливает загрузку, сохраняя файл и прогресс (Qt-поток).

        Отличие от discard_download: файлы остаются на диске. Если
        пользователь вернётся к этому ролику, докачка пойдёт с того же
        места, а не с нуля — на файлах в сотни мегабайт это разница в
        минуты ожидания.
        """
        if self.loop is None or self.loop.is_closed():
            return
        self.loop.call_soon_threadsafe(self._pause_download_on_loop, doc_id)

    def _pause_download_on_loop(self, doc_id):
        e = self.cache.get(doc_id)
        if e is None or e.get('complete'):
            return
        # Флаг ставим ДО отмены: отменённый воркер проверяет его, чтобы
        # отличить остановку плеером от отмены по сетевому таймауту.
        e['paused'] = True
        for t in list(e.get('tasks', [])):
            if not t.done():
                t.cancel()
        e['tasks'] = []
        # Скорость обнуляем: иначе на вкладке загрузок у остановленной
        # загрузки продолжала бы висеть последняя цифра.
        e['speed_bytes'] = 0
        e['speed_bps'] = 0.0
        e['speed_window_bytes'] = 0
        e['speed_window_ts'] = time.monotonic()
        print(f"[Кэш] Загрузка остановлена: {doc_id}")

    def discard_other_downloads(self, keep_ids):
        """Удаляет кэш всех роликов, кроме перечисленных (Qt-поток).

        Нужно, чтобы приостановленные загрузки не копились на диске: на
        каждом переключении остаётся один приостановленный ролик.
        """
        if self.loop is None or self.loop.is_closed():
            return
        self.loop.call_soon_threadsafe(
            self._discard_others_on_loop, set(keep_ids))

    def _discard_others_on_loop(self, keep):
        for doc_id in list(self.cache.keys()):
            if doc_id not in keep:
                self._discard_download_on_loop(doc_id)

    def clear_cache(self, keep_ids=()):
        """Стирает весь кэш скачанных роликов (Qt-поток).

        keep_ids — ролики, которые сейчас играют: их файлы трогать
        нельзя, иначе воспроизведение оборвётся. Остальное удаляется, в
        том числе остатки прошлых запусков, которых в self.cache уже нет.
        """
        keep = set(keep_ids)
        for doc_id in list(self.cache.keys()):
            if doc_id not in keep:
                self.discard_download(doc_id)
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._clear_cache_dir, keep)
        else:
            self._clear_cache_dir(keep)

    @staticmethod
    def _clear_cache_dir(keep=()):
        """Удаляет файлы кэша с диска. Занятые файлы пропускает."""
        keep = set(keep)
        try:
            names = os.listdir(config.CACHE_DIR)
        except Exception:
            return
        for name in names:
            stem = name.split('.')[0]
            if stem.isdigit() and int(stem) in keep:
                continue
            path = os.path.join(config.CACHE_DIR, name)
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except Exception:
                # Файл занят плеером — Windows не даёт его удалить, и
                # это нормально: он уйдёт при следующей очистке.
                pass

    def _has_active_downloads(self):
        """Качается ли сейчас хоть один кусок."""
        for e in self.cache.values():
            if e.get('active') and not (e.get('complete')
                                        or e.get('removed')
                                        or e.get('error')):
                return True
        return False

    def _has_waiting_readers(self):
        """Ждёт ли читатель данные, которых никто не качает.

        Это второй, не менее важный признак обрыва. Счёт по «активным»
        кускам ловил только тот случай, когда воркер уже висит на мёртвом
        соединении. Но связь может умереть в момент, когда очередь пуста и
        воркеры спят: тогда active пуст, наблюдатель считал, что качать
        нечего, сбрасывал отсчёт и НИКОГДА не переподключался. Снаружи это
        выглядело как «плеер пытается, но скорость стоит на нуле».
        """
        for e in self.cache.values():
            if (e.get('complete') or e.get('removed')
                    or e.get('error')):
                continue
            # wanted заполняет обработчик потока, когда mpv упёрся в
            # непрокачанный участок и просит именно его. Записи старше
            # TTL уже не считаются: иначе давно закрытый ролик навсегда
            # оставлял «читателя», и наблюдатель зря переподключался.
            for idx in list((e.get('wanted') or {})):
                if self._piece_wanted(e, idx):
                    return True
        return False

    async def _revive_workers(self):
        """Поднимает загрузку там, где воркеры погибли.

        Воркер может умереть, если отмена из wait_for совпала с обрывом
        связи. Связь при этом восстанавливается сама, а качать становится
        некому: снаружи это «скорость стоит на нуле» до перезапуска плеера.
        Поэтому раз в такт наблюдателя проверяем, что у незавершённых
        записей есть живые воркеры, и поднимаем их заново.
        """
        for doc_id, e in list(self.cache.items()):
            if (e.get('complete') or e.get('removed') or e.get('paused')
                    or e.get('error')):
                continue
            if e.get('fh') is None or e.get('queue') is None:
                continue
            if [t for t in e.get('tasks', []) if not t.done()]:
                continue
            document = e.get('document')
            if document is None:
                continue
            print(f"[Кэш] Воркеры пропали — поднимаю загрузку заново "
                  f"({doc_id})")
            self.status_changed.emit("Возобновляю загрузку...")
            # Сообщение из записи: по нему обновится истёкшая ссылка.
            await self.ensure_cache(e.get('message') or document, resume=True)

    def _download_stalled(self):
        """Качаем, но новые данные не приходят — маршрут мёртв.

        Это главный признак обрыва. Telethon при потере связи не падает, а
        уходит в свой цикл переподключения тем же маршрутом: пока он там
        висит, is_connected() возвращает True, запросы не отвечают и не
        падают. Ждать ошибку бесполезно — ждать нужно данные.

        Считаем именно байты, а не время запроса: mpv при отсутствии
        картинки перезапускает файл по кругу, и любой отсчёт «от запроса»
        сбрасывался бы каждые полторы секунды, так и не дойдя до порога.
        """
        if not self._has_active_downloads() and not self._has_waiting_readers():
            # Качать нечего — застоя быть не может, отсчёт сбрасываем.
            self._progress_bytes = self._bytes_from_telegram
            self._progress_ts = time.monotonic()
            return False

        if self._bytes_from_telegram != self._progress_bytes:
            self._progress_bytes = self._bytes_from_telegram
            self._progress_ts = time.monotonic()
            return False

        return (time.monotonic() - self._progress_ts) > config.STALL_TIMEOUT

    async def _connection_watchdog(self):
        """Следит за связью и за тем, что данные реально идут.

        Проверка по данным важнее проверки по is_connected(): при смене
        режима VPN соединение не «моргает», а залипает, и без счётчика
        молчания обрыв остался бы незамеченным.
        """
        while True:
            await asyncio.sleep(config.WATCHDOG_INTERVAL)
            try:
                # Сначала проверяем, что качать вообще есть кому: гибель
                # воркеров связь не рвёт, поэтому по данным её не видно.
                await self._revive_workers()

                # И что воркеров хватает: если кто-то завис на паузе,
                # остальные не должны стоять. Держим загрузку занятой.
                for _e in list(self.cache.values()):
                    try:
                        self._ensure_busy(_e)
                    except Exception:
                        pass

                # Значок связи обновляем на каждом такте: обрыв мог
                # случиться между проверками, а состояние должно быть
                # видно сразу. Пока идёт перебор способов, не вмешиваемся:
                # иначе крутящееся кольцо сменялось бы крестиком.
                if not self._is_connecting():
                    self._set_connection()

                if self._download_stalled():
                    silence = int(time.monotonic() - self._progress_ts)
                    print(f"[Бэкенд] Данные не приходят {silence} с — "
                          "перебираю способы заново...")
                    self.status_changed.emit(
                        "Нет данных от Telegram — восстанавливаю связь...")
                    # Сдвигаем отсчёт, иначе следующий перебор запустится
                    # сразу же, пока этот ещё не закончился.
                    self._progress_ts = time.monotonic()
                    self._progress_bytes = self._bytes_from_telegram
                    await self.reconnect(force=True)
                    continue

                if self._is_connected():
                    continue

                # Маршрут жив, но связи нет — даём Telethon шанс подняться
                # самому и только потом перебираем способы.
                await asyncio.sleep(config.WATCHDOG_GRACE)
                if self._is_connected():
                    continue
                await self.reconnect()
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                print(f"[Бэкенд] Наблюдатель за связью: {ex}")
