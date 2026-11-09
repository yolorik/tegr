"""Каналы, папки, поиск и проверка каналов на видео."""

import asyncio
import json
import os
import time

from ..core import config, paths
from ..system import telethon_boot
from .common import _error_text


CHANNEL_MARKS_FILE = 'channel_videos.json'
# «Есть видео» можно не перепроверять месяц, «нет видео» — сутки:
# канал мог опубликовать ролик после проверки.
CHANNEL_MARK_TTL_TRUE = 30 * 24 * 60 * 60
CHANNEL_MARK_TTL_FALSE = 24 * 60 * 60
# Версия способа проверки. Пометки «нет видео», полученные старым
# перебором последних сообщений, могли быть ложными: сервер теперь ищет
# по всей истории. Такие пометки не загружаем — канал перепроверится.
CHANNEL_MARK_VERSION = 2


class CatalogMixin:
    async def reload_folders(self):
        """Пересобирает папки из уже прочитанных каналов, без сети.

        Нужно после скрытия канала: папка «Скрытые» и счётчики в ней
        должны обновиться сразу, а полная перезагрузка каталогов — это
        запросы в Telegram на каждое «скрыть».
        """
        # Список настроек нужен здесь же: скрытие сначала пишет его, а
        # потом пересобирает папки. Копия в self.settings к этому моменту
        # уже обновлена, но читать её через save_settings не надо —
        # берём ровно то, что сейчас лежит в настройках.
        hidden_ids = {str(x) for x in (self.settings.get('hidden') or [])}
        folders_data = []
        for f in self.folders or []:
            if f.get('hidden'):
                # Старый блок «Скрытых» не тащим: он собирается заново.
                continue
            # Папки, целиком собранные по флагам (broadcasts/groups),
            # держат копию общего списка. Исключаем скрытое и из неё.
            item = dict(f)
            item['dialogs'] = [d for d in (f.get('dialogs') or [])
                               if str(getattr(d, 'id', '')) not in hidden_ids]
            folders_data.append(item)
        if hidden_ids:
            hidden = [d for d in (self.dialogs or [])
                      if str(getattr(d, 'id', '')) in hidden_ids]
            if hidden:
                folders_data.append({
                    "title": f"Скрытые ({len(hidden)})",
                    "dialogs": hidden,
                    "filter_id": None,
                    "hidden": True,
                })
        self.folders = folders_data
        self.folders_loaded.emit(folders_data)
        return folders_data

    async def refresh_catalogs(self):
        """Сбрасывает кеш списков и перечитывает папки и каналы из Telegram.

        Папки в Telegram меняются редко, но если пользователь их
        переименовал или пересобрал, без этого пришлось бы перезапускать
        программу целиком.
        """
        self._videos_by_dialog.clear()
        self._video_qualities.clear()
        self._quality_groups.clear()
        # Пометки не сбрасываем: они лежат в channel_videos.json и
        # перепроверяются по сроку, а не при каждом обновлении.
        self._video_more.clear()
        self._video_offset.clear()
        await self.load_data()

    async def search_channels(self, query):
        """Ищет каналы по всему Telegram, а не только среди подписок.

        Обычный список каналов — это то, на что пользователь уже
        подписан. Найти что-то новое так нельзя, поэтому здесь идёт
        настоящий поиск по Telegram.
        """
        query = (query or '').strip()
        if not query:
            return
        try:
            res = await self.client(telethon_boot.SearchContacts(q=query, limit=config.SEARCH_LIMIT))
        except Exception as ex:
            text = _error_text(ex)
            print(f"[Поиск] «{query}»: не удалось — {text}")
            self.status_changed.emit(f"Поиск не удался: {text}")
            return

        found = []
        for chat in getattr(res, 'chats', None) or []:
            # И каналы, и группы: и то и другое можно добавить к себе.
            if not isinstance(chat, (telethon_boot.Channel, telethon_boot.Chat)):
                continue
            # Заблокированные показывать незачем. А вот left=True означает
            # всего лишь «вы не подписаны» — это как раз то, что ищут,
            # поэтому такие каналы оставляем.
            if getattr(chat, 'kicked', False):
                continue
            found.append(chat)
        print(f"[Поиск] «{query}»: найдено {len(found)}")
        self.search_results.emit(found)

    async def join_channel(self, chat):
        """Подписывается на канал или вступает в группу."""
        title = getattr(chat, 'title', '') or getattr(chat, 'name', '') or 'чат'
        if not isinstance(chat, telethon_boot.Channel):
            # Обычная группа: подписки у неё нет, и telethon_boot.JoinChannelRequest её
            # не примет. Вступить можно только по ссылке-приглашению.
            self.error_occurred.emit(
                f"«{title}» — обычная группа. Вступить в неё можно только "
                "по ссылке-приглашению.")
            return
        try:
            await self.client(telethon_boot.JoinChannelRequest(chat))
        except Exception as ex:
            text = _error_text(ex)
            print(f"[Подписка] «{title}»: не удалось — {text}")
            self.error_occurred.emit(
                f"Не удалось подписаться на «{title}»: {text}")
            return
        print(f"[Подписка] «{title}»: готово")
        self.status_changed.emit(f"Подписка на «{title}» оформлена")
        # Канал появился в подписках — список надо перечитать, иначе
        # подписка останется невидимой до перезапуска.
        await self.refresh_catalogs()

    async def leave_channel(self, chat):
        """Отписывается от канала или выходит из группы."""
        title = getattr(chat, 'title', '') or getattr(chat, 'name', '') or 'чат'
        try:
            await self.client(telethon_boot.LeaveChannelRequest(chat))
        except Exception as ex:
            text = _error_text(ex)
            print(f"[Отписка] «{title}»: не удалось — {text}")
            self.error_occurred.emit(
                f"Не удалось отписаться от «{title}»: {text}")
            return
        print(f"[Отписка] «{title}»: готово")
        self.status_changed.emit(f"Вы отписались от «{title}»")
        await self.refresh_catalogs()

    async def add_to_folder(self, filter_id, chat):
        """Кладёт канал или группу в папку Telegram.

        Папки живут на стороне Telegram, поэтому правка сразу видна во
        всех клиентах, а не только здесь.
        """
        title = (getattr(chat, 'title', '') or getattr(chat, 'name', '')
                 or 'чат')
        raw = (getattr(self, '_folder_filters', None) or {}).get(filter_id)
        if raw is None:
            self.error_occurred.emit('Папка не найдена. Обновите список.')
            return

        # В папку попадает только то, на что вы подписаны. На неподписанный
        # канал Telegram правку принимает без ошибки, но молча не
        # применяет — проверено. Поэтому сначала подписываемся.
        joined = False
        if isinstance(chat, telethon_boot.Channel) and getattr(chat, 'left', None) is True:
            try:
                await self.client(telethon_boot.JoinChannelRequest(chat))
                joined = True
                print(f"[Папки] «{title}»: подписка перед добавлением")
            except Exception as ex:
                self.error_occurred.emit(
                    f"Сначала нужно подписаться на «{title}»: "
                    f"{_error_text(ex)}")
                return

        try:
            peer = telethon_boot.telethon_utils.get_input_peer(chat)
        except Exception as ex:
            self.error_occurred.emit(
                f"Не удалось определить «{title}»: {_error_text(ex)}")
            return

        include = list(getattr(raw, 'include_peers', None) or [])
        key = self._input_peer_id(peer)
        if any(self._input_peer_id(p) == key for p in include):
            self.status_changed.emit(f"«{title}» уже в этой папке")
            return
        include.append(peer)
        raw.include_peers = include

        try:
            await self.client(telethon_boot.UpdateDialogFilterRequest(
                id=getattr(raw, 'id', filter_id), filter=raw))
        except Exception as ex:
            text = _error_text(ex)
            print(f"[Папки] «{title}»: не удалось — {text}")
            self.error_occurred.emit(f"Не удалось добавить в папку: {text}")
            return
        print(f"[Папки] «{title}»: добавлен в папку {filter_id}")
        message = f"«{title}» добавлен в папку"
        if joined:
            message += " (и вы подписаны)"
        self.status_changed.emit(message)
        await self.refresh_catalogs()

    @staticmethod
    def _input_peer_id(peer):
        """Id чата внутри папки: без учёта типа объекта.

        Не путать с _peer_key: тот читает id из сообщения, а этот — из
        элемента папки (InputPeerChannel/InputPeerChat).
        """
        return (getattr(peer, 'channel_id', None)
                or getattr(peer, 'chat_id', None)
                or getattr(peer, 'user_id', None))

    def _dialog_title_for_peer(self, peer_id, resolve=True):
        """Название канала по id, который пришёл в сообщении.

        Причина, по которой приходилось писать «канал неизвестен»:
        в сообщении лежит PeerChannel с «сырым» channel_id (999), а у
        диалога и у самой сущности id — совсем другой, помеченный
        (-1000000000999). Сравнивать их напрямую нельзя, поэтому
        переводим сообщение в тот же id, что у диалога, и уж потом
        ищем название. resolve оставлен ради совместимости: сеть здесь
        не трогаем никогда, для запроса есть peer_title_async.
        """
        peer = peer_id
        if peer is None:
            return ''
        try:
            dialog_id = self.client.get_peer_id(peer) if self.client else None
        except Exception:
            dialog_id = None
        # Ключи, с которыми сравниваем: помеченный вид из get_peer_id и
        # «сырые» поля канала/чата. Из сырых делаем помеченный вручную —
        # get_peer_id на них не всегда срабатывает.
        variants = set()
        for key in (dialog_id, getattr(peer, 'channel_id', None),
                    getattr(peer, 'chat_id', None)):
            if key is None:
                continue
            try:
                key = int(key)
            except (TypeError, ValueError):
                continue
            variants.add(key)
            # Из «сырого» id делаем помеченный: -100 + id. Отрицательное
            # или уже помеченное число не трогаем — иначе получается
            # «-100-1000000000999».
            if key > 0:
                variants.add(-1000000000000 - key)
                variants.add(-key)
        try:
            for dialog in self.dialogs:
                entity = getattr(dialog, 'entity', None)
                cands = set()
                for cand in (getattr(dialog, 'id', None),
                             getattr(entity, 'id', None)):
                    try:
                        cands.add(int(cand))
                    except (TypeError, ValueError):
                        continue
                ent_id = getattr(entity, 'id', None)
                try:
                    ent_id = int(ent_id) if ent_id is not None else None
                except (TypeError, ValueError):
                    ent_id = None
                if ent_id is not None and ent_id > 0:
                    cands.add(-1000000000000 - ent_id)
                if cands & variants:
                    name = getattr(dialog, 'name', '') or ''
                    if name:
                        return name
        except Exception:
            pass
        return ''

    def peer_title_async(self, peer_id, callback):
        """Название канала с запросом к Telegram, ответ через сигнал.

        Вызывать из Qt-потока: сам запрос уходит в цикл бэкенда, а
        результат возвращается в Qt-поток сигналом — иначе окно бы
        замирало на время запроса.
        """
        if peer_id is None:
            return
        title = self._dialog_title_for_peer(peer_id, resolve=False)
        if title:
            callback(title)
            return
        if self.loop is None or self.loop.is_closed():
            return

        async def _lookup():
            try:
                entity = await self.client.get_entity(peer_id)
            except Exception as ex:
                print(f"[Кэш] Канал {peer_id} не найден: {ex}")
                return
            name = getattr(entity, 'title', '') or ''
            if name:
                callback(name)

        self.submit_coroutine(_lookup())

    def _peer_title(self, peer_id):
        """Название канала по его id, без обращения к сети."""
        return self._dialog_title_for_peer(peer_id, resolve=False)

    async def load_data(self):
        self.dialogs = []
        dialog_map = {}

        # Папки Telegram ссылаются на каналы их «сырыми» id (channel_id),
        # а dialog.id — это id со знаком (-100...). Раньше карта строилась
        # только по dialog.id, поэтому ни один канал в папку не попадал и
        # все папки, кроме «Все каналы», пропадали целиком.
        def _remember(dialog):
            for key in (getattr(dialog, 'id', None),
                        getattr(getattr(dialog, 'entity', None), 'id', None)):
                if key is not None:
                    dialog_map.setdefault(key, dialog)

        seen_dialogs = set()

        # Архивные каналы тоже нужны: папки могут на них ссылаться.
        for archived in (False, True):
            async def _fetch(archived=archived):
                collected = []
                async for dialog in self.client.iter_dialogs(
                        archived=archived):
                    collected.append(dialog)
                return collected

            try:
                dialogs = await self._request_with_reconnect(
                    _fetch, 'Чтение диалогов')
            except Exception as ex:
                print(f"[Папки] Не удалось прочитать диалоги: {ex}")
                dialogs = []

            for dialog in dialogs:
                if not (dialog.is_channel or dialog.is_group):
                    continue
                key = getattr(dialog, 'id', None)
                if key in seen_dialogs:
                    continue
                seen_dialogs.add(key)
                self.dialogs.append(dialog)
                _remember(dialog)

        folders_data = [{"title": "Все каналы", "dialogs": self.dialogs}]
        # Исходные объекты папок: чтобы положить канал в папку, её надо
        # отправить обратно в Telegram целиком, а не только названием.
        self._folder_filters = {}

        try:
            raw_filters = await self.client(telethon_boot.GetDialogFiltersRequest())
            filters_list = getattr(raw_filters, 'filters', raw_filters)
            if isinstance(filters_list, list):
                for f in filters_list:
                    # Папки бывают разных типов: обычная (telethon_boot.DialogFilter) и
                    # chatlist (DialogFilterChatlist). У обеих есть
                    # include_peers, поэтому проверяем и по полю — иначе
                    # chatlist-папки терялись.
                    if (not isinstance(f, telethon_boot.DialogFilter)
                            and not hasattr(f, 'include_peers')):
                        continue

                    title = getattr(getattr(f, 'title', None), 'text', None)
                    if not title:
                        title = str(getattr(f, 'title', '') or '')
                    if not title:
                        continue

                    peers = list(getattr(f, 'include_peers', None) or [])
                    peers += list(getattr(f, 'pinned_peers', None) or [])

                    folder_dialogs = []
                    added = set()
                    for peer in peers:
                        peer_id = (
                            getattr(peer, 'channel_id', None)
                            or getattr(peer, 'chat_id', None)
                            or getattr(peer, 'user_id', None)
                        )
                        if not peer_id or peer_id in added:
                            continue
                        d = dialog_map.get(peer_id)
                        if d is not None:
                            added.add(peer_id)
                            folder_dialogs.append(d)

                    # Папка может задаваться не списком чатов, а флагами
                    # (broadcasts/groups): тогда include_peers пуст, и папка
                    # пропадала из списка целиком. Собираем такие по флагам.
                    if not folder_dialogs:
                        want_broadcast = bool(getattr(f, 'broadcasts', False))
                        want_groups = bool(getattr(f, 'groups', False))
                        if want_broadcast or want_groups:
                            for d in self.dialogs:
                                if (want_broadcast and d.is_channel
                                        and not d.is_group):
                                    folder_dialogs.append(d)
                                elif want_groups and d.is_group:
                                    folder_dialogs.append(d)

                    if folder_dialogs:
                        filter_id = getattr(f, 'id', None)
                        if filter_id is not None:
                            self._folder_filters[filter_id] = f
                        # Скрытые каналы в папки не попадают: их место —
                        # «Скрытые». Иначе после каждого запуска списки
                        # строились заново и скрытое снова оказывалось
                        # среди остальных.
                        folder_dialogs = self._without_hidden(folder_dialogs)
                        if not folder_dialogs:
                            continue
                        folders_data.append({
                            "title": title,
                            "dialogs": folder_dialogs,
                            "filter_id": filter_id,
                        })
        except Exception as ex:
            print(f"[Папки] Не удалось загрузить папки: {ex}")

        # Папка «Скрытые» — не папка Telegram, а место для каналов,
        # убранных из списка. Без неё спрятанный канал было бы не вернуть:
        # он исчезал бы из «Все каналы» без следа.
        hidden_ids = {str(x) for x in (self.settings.get('hidden') or [])}
        if hidden_ids:
            hidden = [d for d in self.dialogs
                      if str(getattr(d, 'id', '')) in hidden_ids]
            if hidden:
                # Название не зашиваем: в нём число, и окно ищет папку по
                # отметке hidden, а не по тексту.
                folders_data.append({
                    "title": f"Скрытые ({len(hidden)})",
                    "dialogs": hidden,
                    "filter_id": None,
                    "hidden": True,
                })
        # «Все каналы» собираются первыми и тоже должны быть без скрытых.
        # Делаем это последним шагом: список скрытых нужен целиком.
        folders_data[0]["dialogs"] = self._without_hidden(
            folders_data[0]["dialogs"])

        self.folders = folders_data
        # Печатаем: по логу видно, что вход и загрузка каналов дошли до
        # конца — без этого строка «Подключено» ничего не доказывала.
        videos_total = sum(len(f.get('dialogs') or []) for f in folders_data)
        print(f"[Данные] Загружено каналов: {len(folders_data)}, "
              f"видео в них: {videos_total}")
        self.folders_loaded.emit(folders_data)
        # Проверка «есть ли в канале видео» запускается сама после каждой
        # перезагрузки списка: пометки нужны сразу, а не по первому клику.
        self._start_channel_check(self.dialogs)

    def _hidden_ids(self):
        """Id скрытых каналов строками — так они лежат в настройках."""
        try:
            return {str(x) for x in (self.settings.get('hidden') or [])}
        except Exception:
            return set()

    def _without_hidden(self, dialogs):
        """Убирает скрытые каналы из списка папки."""
        hidden = self._hidden_ids()
        if not hidden:
            return list(dialogs or [])
        return [d for d in (dialogs or [])
                if str(getattr(d, 'id', '')) not in hidden]

    @staticmethod
    def _dialog_peer_id(dialog):
        """Id диалога в том виде, в каком он приходит в сообщениях.

        У диалога id со знаком (-100...), а у сообщения из канала —
        «сырой» channel_id. Без этого перевода пометки бы не находились.
        Принимает и dialog, и объект, у которого есть только id.
        """
        return (getattr(dialog, 'id', None)
                or getattr(getattr(dialog, 'entity', None), 'id', None))

    @staticmethod
    def _message_has_video(msg):
        """Есть ли в сообщении видео — по тем же полям, что у _video_info.

        Отдельная проверка, а не вызов _video_info: там же собирается
        подпись, и на тысяче сообщений это лишняя работа.
        """
        media = getattr(msg, 'media', None)
        if not isinstance(media, telethon_boot.MessageMediaDocument):
            return False
        attrs = getattr(getattr(media, 'document', None), 'attributes', None)
        if not attrs:
            return False
        return any(isinstance(a, telethon_boot.DocumentAttributeVideo) for a in attrs)

    def _start_channel_check(self, dialogs):
        """Запускает фоновую проверку каналов на наличие видео.

        Запуск идёт через loop.call_soon_threadsafe, а не submit_coroutine:
        тот отказывается работать до конца подключения, а здесь нужно
        ровно чтобы задача встала в очередь loop ещё до ready.
        """
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(
                    self._check_channel_videos(dialogs)))
        except Exception as ex:
            print(f"[Пометки] Не удалось запустить проверку: {ex}")

    async def _check_channel_videos(self, dialogs):
        """Проверяет каналы на видео, не открывая их.

        Порядок проверки — от дешёвого к дорогому:
          1. последнее сообщение канала (оно уже пришло со списком);
          2. только если там нет видео — поиск по видео на сервере.

        Второй шаг — отдельный запрос на канал, поэтому их число
        ограничено CHANNEL_SCAN_LIMIT. Остаток каналов остаётся без пометки:
        лучше не пометить канал, чем получить FloodWait на весь аккаунт.
        """
        found = {}
        pending = []
        now = time.time()
        for dialog in dialogs or []:
            key = getattr(dialog, 'id', None)
            if key is None:
                continue
            last = getattr(dialog, 'message', None)
            if last is not None and self._message_has_video(last):
                found[key] = True
                continue
            # Уже проверяли — не тратим запрос. «Есть видео» живёт
            # дольше, «нет видео» перепроверяем чаще.
            cached = self._dialog_has_video.get(key)
            ts = self._channel_marks_ts.get(key, 0)
            ttl = (CHANNEL_MARK_TTL_TRUE if cached
                   else CHANNEL_MARK_TTL_FALSE)
            if cached is not None and now - ts < ttl:
                found[key] = cached
                continue
            pending.append(dialog)

        checked = 0
        unknown = []
        for dialog in pending:
            if checked >= config.CHANNEL_SCAN_LIMIT or self._closed:
                break
            key = getattr(dialog, 'id', None)
            result = await self._has_video_further(dialog)
            checked += 1
            if result is None:
                # Проверка не удалась (сеть, FloodWait, закрытая история).
                # Такой канал нельзя считать пустым и прятать из списка:
                # лучше оставить его видимым, чем ошибиться.
                unknown.append(dialog)
            else:
                found[key] = result
            # Пауза между запросами: пачка подряд — прямая дорога к
            # FloodWait, а проверка не настолько срочная.
            await asyncio.sleep(config.VIDEO_CHECK_DELAY)
        # Без пометки остались каналы, до которых очередь не дошла, и те,
        # что проверить не вышло. Им пометку не ставим: «нет видео» — это
        # был бы не вывод, а отговорка.
        unknown_ids = {getattr(d, 'id', None) for d in unknown}
        done = {getattr(d, 'id', None) for d in pending[:checked]} - unknown_ids
        self._channel_unchecked = [
            d for d in pending if getattr(d, 'id', None) not in done]
        print(f"[Пометки] Без видео в последнем сообщении: {len(pending)}, "
              f"проверено: {checked}, не вышло: {len(unknown)}, "
              f"осталось на потом: {len(self._channel_unchecked)}")
        # Даже если ни одна проверка не удалась, пометки не теряются, а
        # второй заход получит эти каналы через _store_marks.
        self._store_marks(found, done)

    def _load_channel_marks(self):
        """Читает channel_videos.json: какие каналы уже проверены."""
        path = os.path.join(paths.app_dir(), CHANNEL_MARKS_FILE)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        channels = data.get('channels')
        if not isinstance(channels, dict):
            return
        for key, value in channels.items():
            if not isinstance(value, dict):
                continue
            try:
                peer_id = int(key)
                has = value.get('has')
                ts = float(value.get('ts') or 0)
                version = int(value.get('v') or 0)
            except (TypeError, ValueError):
                continue
            if not isinstance(has, bool):
                continue
            # «Нет видео» от старого способа проверки могло быть ложным:
            # такой канал не скрываем, а отдаём на перепроверку.
            if has is False and version < CHANNEL_MARK_VERSION:
                continue
            self._dialog_has_video[peer_id] = has
            self._channel_marks_ts[peer_id] = ts

    def _save_channel_marks(self):
        """Пишет проверенные каналы на диск, чтобы не сканировать снова."""
        path = os.path.join(paths.app_dir(), CHANNEL_MARKS_FILE)
        data = {'channels': {}}
        for peer_id, has in self._dialog_has_video.items():
            data['channels'][str(peer_id)] = {
                'has': bool(has),
                'ts': self._channel_marks_ts.get(peer_id, 0),
                'v': CHANNEL_MARK_VERSION,
            }
        tmp = path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
            os.replace(tmp, path)
        except Exception as ex:
            print(f"[Пометки] Не сохранились: {ex}")

    def _store_marks(self, found, scanned=None):
        """Кладёт пометки в кеш и сообщает окну.

        Пометки только дополняются: канал, где видео находили раньше,
        остаётся «с видео», даже если последнее сообщение — картинка.
        «Нет видео» ставится лишь тем, чью историю действительно читали.
        """
        now = time.time()
        scanned = set(scanned or ())
        changed = False
        for peer_id, has in found.items():
            old = self._dialog_has_video.get(peer_id)
            if has:
                if old is not True:
                    self._dialog_has_video[peer_id] = True
                    self._channel_marks_ts[peer_id] = now
                    changed = True
                elif peer_id in scanned:
                    self._channel_marks_ts[peer_id] = now
                    changed = True
            else:
                if old is None:
                    self._dialog_has_video[peer_id] = False
                    self._channel_marks_ts[peer_id] = now
                    changed = True
                elif old is False and peer_id in scanned:
                    self._channel_marks_ts[peer_id] = now
                    changed = True
                elif old is True and peer_id in scanned:
                    # Проверили заново и видео не нашли, но раньше оно
                    # было: не понижаем пометку, только продлеваем срок.
                    self._channel_marks_ts[peer_id] = now
                    changed = True
        if changed:
            self._save_channel_marks()
        print(f"[Пометки] Проверено каналов: {len(found)}, "
              f"без видео: {sum(1 for v in found.values() if not v)}")
        self.channels_checked.emit(found)
        # Остались непроверенные — назначаем второй заход. Один раз: если
        # и после него что-то не проверилось, хватит — проверка не должна
        # молотить запросы в фоне сама по себе.
        if self._channel_unchecked and not self._check_retry_done:
            self._check_retry_done = True
            try:
                self.loop.call_later(
                    config.CHECK_RETRY_SEC,
                    lambda: asyncio.ensure_future(
                        self._check_channel_videos(
                            list(self._channel_unchecked))))
            except Exception as ex:
                print(f"[Пометки] Второй заход не назначен: {ex}")

    async def _has_video_further(self, dialog):
        """Спрашивает у Telegram, есть ли в канале хоть один ролик.

        Перебор последних сообщений давал ложные «нет видео»: ролик в
        канале идёт раз в десяток постов и мог не попасть в прочитанный
        кусок. Здесь фильтр по видео делает сервер — он видит всю
        историю канала и отвечает на нужный вопрос одним запросом.

        Возвращает True/False, а None — «проверить не удалось». Ошибку
        нельзя превращать в «нет видео»: такой канал исчез бы из списка
        насовсем, хотя ролики в нём есть.
        """
        entity = getattr(dialog, 'entity', None)
        peer_id = self._dialog_peer_id(dialog)
        if entity is None or peer_id is None:
            return None
        try:
            async for message in self.client.iter_messages(
                    entity, limit=10,
                    filter=telethon_boot.InputMessagesFilterVideo()):
                # Проверяем и на этой стороне: фильтр сервера надёжен, но
                # «видео» здесь — ровно то, что попадает в список роликов.
                if self._message_has_video(message):
                    return True
            return False
        except Exception as ex:
            print(f"[Пометки] «{getattr(dialog, 'name', '')}»: {ex}")
            return None

    def _note_channel_videos(self, dialog_id, messages):
        """Запоминает по загруженному списку, что канал с видео.

        Заодно снимает пометку «нет видео», если проверка по последним
        сообщениям ошиблась: настоящий список роликов важнее догадки.
        """
        peer_id = self._dialog_peer_id({'id': dialog_id})
        if peer_id is None:
            return
        has = any(self._message_has_video(m) for m in (messages or []))
        if has and self._dialog_has_video.get(peer_id) is not True:
            self._dialog_has_video[peer_id] = True
            self._channel_marks_ts[peer_id] = time.time()
            self._save_channel_marks()
            self.channels_checked.emit({peer_id: True})
