"""Telegram: подключение, кеш роликов и локальный HTTP-сервер.

Сердце программы. Здесь три вещи, между которыми много общего:

  * подключение к Telegram через Telethon — с перебором способов
    (напрямую, MTProxy, локальный прокси), переподключением при обрыве
    и наблюдением за связью;
  * кеш роликов: файл на диске, который качается кусками в том порядке,
    в каком его просит mpv, и общий на все запросы;
  * локальный HTTP-сервер на 127.0.0.1: mpv не умеет говорить с
    Telegram, поэтому байты ему отдаёт этот сервер, поддержкой Range.

Единственное, что здесь от интерфейса, — сигналы QObject: окно
подписывается на них и не знает, как устроена загрузка.
"""
import asyncio
import base64
import json
import os
import random
import re
import secrets
import socket
import subprocess
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from aiohttp import web

from PyQt6.QtCore import QObject, Qt, pyqtSignal

from ..core import config, history, log, paths, proxy, settings, utils
from ..system import telethon_boot
from .cache_math import (
    DownloadTracker, _open_read, _piece_ranges, piece_index_for)
from .http_range import parse_range_header

CHANNEL_MARKS_FILE = 'channel_videos.json'
# «Есть видео» можно не перепроверять месяц, «нет видео» — сутки:
# канал мог опубликовать ролик после проверки.
CHANNEL_MARK_TTL_TRUE = 30 * 24 * 60 * 60
CHANNEL_MARK_TTL_FALSE = 24 * 60 * 60
# Версия способа проверки. Пометки «нет видео», полученные старым
# перебором последних сообщений, могли быть ложными: сервер теперь ищет
# по всей истории. Такие пометки не загружаем — канал перепроверится.
CHANNEL_MARK_VERSION = 2


def _normalize_phone(text):
    """Приводит номер к виду, который понимает Telegram: плюс и цифры.

    Пользователь вводит номер как привык — с пробелами, скобками и
    дефисами; Telegram такие не принимает.
    """
    digits = ''.join(ch for ch in (text or '') if ch.isdigit())
    return ('+' + digits) if digits else ''


def _remove_session_files():
    """Удаляет файл сессии Telethon вместе со служебными файлами.

    Нужно, когда сессия повреждена: без этого вход невозможен, а
    предлагать пользователю удалить файл руками — плохой выход.
    """
    for suffix in ('', '.session', '.session-journal', '.session-wal',
                   '.session-shm', '-journal'):
        path = config.SESSION_NAME + suffix
        try:
            if os.path.exists(path):
                os.remove(path)
                print(f"[Вход] удалён файл сессии: {path}")
        except Exception as ex:
            print(f"[Вход] не удалось удалить {path}: {ex}")


def _is_session_error(ex):
    """Похожа ли ошибка на повреждённый файл сессии, а не на сеть."""
    text = str(ex).lower()
    return any(word in text for word in (
        'database', 'sqlite', 'malformed', 'disk i/o error',
        'file is encrypted', 'not a database',
    ))


def _error_text(ex):
    """Понятное описание ошибки подключения.

    У asyncio.TimeoutError пустой str(), и в окне появлялось
    «Не удалось подключиться: » — без единого слова о причине.
    """
    if isinstance(ex, (asyncio.TimeoutError, TimeoutError)):
        return "истекло время ожидания"
    text = str(ex).strip()
    return text or ex.__class__.__name__


def _session_file_ok():
    """Читается ли файл сессии как база SQLite.

    Telethon открывает сессию прямо в конструкторе клиента, поэтому
    повреждённый файл валит СОЗДАНИЕ клиента, а не подключение — и
    поймать это можно только здесь. Проверяем заранее: тогда файл можно
    удалить, пока его никто не держит. Иначе sqlite успевает открыть
    файл, удаление не проходит («файл занят»), и лечение не срабатывает.
    """
    path = config.SESSION_NAME + '.session'
    if not os.path.exists(path):
        return True
    try:
        import sqlite3
        con = sqlite3.connect(path)
        try:
            con.execute('select name from sqlite_master limit 1').fetchone()
        finally:
            con.close()
        return True
    except Exception as ex:
        print(f"[Вход] файл сессии нечитаем: {ex}")
        return False


class TelegramBackend(QObject):
    folders_loaded = pyqtSignal(list)
    videos_loaded = pyqtSignal(object, list)
    # Итог проверки каналов на видео: {id диалога: bool}. Идёт отдельно
    # от videos_loaded: пометки каналов нужны и для каналов, которые
    # пользователь не открывал.
    channels_checked = pyqtSignal(object)
    # Ответ на запрос сообщения из истории: (запись, сообщение или None).
    # Сигнал доставляет результат из цикла backend в поток окна.
    history_message = pyqtSignal(object, object)
    error_occurred = pyqtSignal(str)
    status_changed = pyqtSignal(str)
    # Состояние связи с Telegram: 'ok', 'off' или 'connecting'. По нему
    # красятся значок в трее и кнопка в углу окна. Сигнал шлётся только
    # при смене состояния, поэтому подписчику не приходится сравнивать.
    connection_changed = pyqtSignal(str)
    # Шаг входа в аккаунт: (вид шага, подсказка). Виды: 'phone', 'code',
    # 'password', 'error'. Ответ возвращается через answer_login().
    login_step = pyqtSignal(str, str)
    # Вход отменён человеком. По этому сигналу программа закрывается:
    # без аккаунта ей нечего показывать, а пустое окно только мешает.
    login_cancelled = pyqtSignal()
    # Человек в окне кода нажал «Войти по QR»: вход по номеру надо
    # прервать и переключиться на код с экрана.
    login_switch_qr = pyqtSignal()
    # Найденные по всему Telegram каналы (список объектов Telethon).
    search_results = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.loop = None
        self.dialogs = []
        self.folders = []
        self.current_messages = []
        # Какой канал сейчас выбран в главном окне. Нужен, чтобы результат
        # загрузки устаревшего канала не подменял current_messages.
        self.active_dialog_id = None
        # Название канала, из которого играет ролик: попадает в заголовок
        # окна плеера, иначе по одному названию видео непонятно, откуда оно.
        self.active_dialog_title = ""
        # Просмотренные ролики (id документов). Читается с диска при старте
        # и переживает перезапуск программы.
        self.watched = history.load_watched()
        # Позиции просмотра: id документа -> секунды. Нужны, чтобы вернуться
        # к ролику с того же места, а не с нуля.
        self.positions = history.load_positions()
        # Записи для вкладки «История»: название, канал, когда смотрели.
        self.history_entries = history.load_entries()
        # Настройки, переживающие перезапуск: громкость, режим декодера,
        # автовыбор качества, скорость, геометрия окон.
        self.settings = settings.load()
        # Порт локального HTTP-сервера. Заполняется при старте: если 8081
        # занят чужой программой, берём свободный у системы.
        self.stream_port = config.STREAM_PORT
        # Токен локального HTTP-сервера. Без него любой процесс на
        # машине мог бы читать видео через 127.0.0.1. mpv получает
        # токен вместе с адресом потока.
        self.stream_token = secrets.token_urlsafe(24)
        # Закреплённые плейлисты плеера: токен -> список сообщений. Запросы
        # mpv идут с ?pl=<токен>, поэтому смена канала в главном окне больше
        # не подменяет файл под уже играющим роликом.
        self._playlists = {}
        self._playlist_seq = 0
        self._server_started = False
        self.ready = False

        # Переподключение: замок не даёт нескольким воркерам одновременно
        # перебирать способы, счётчик поколений — не повторять перебор,
        # если сосед только что всё починил.
        self._reconnect_lock = None
        self._reconnect_gen = 0
        self._last_reconnect = 0.0
        self._reconnect_count = 0
        self._watchdog_task = None
        # Сколько байт пришло от Telegram и на каком значении мы в прошлый
        # раз убедились, что данные идут. Считаем именно байты: перезапуск
        # файла и повторный ensure_cache не должны выглядеть как «данные
        # пошли», иначе залипшую связь не поймать.
        self._bytes_from_telegram = 0
        self._progress_bytes = 0
        self._progress_ts = time.monotonic()

        # doc_id -> состояние кеша
        self.cache = {}

        # Кеш списков видео по dialog id: переключение между каналами
        # в рамках одной сессии больше не вызывает повторный запрос.
        self._videos_by_dialog = {}

        # Проверенным каналам верим на слово: список роликов у канала
        # меняется медленно, а перепроверка при каждом нажатии на канал
        # стоила бы отдельного запроса в Telegram.
        self._dialog_has_video = {}
        self._channel_marks_ts = {}
        # Проверка каналов на видео идёт в фоне и может быть не запущена:
        # без неё кнопки «Догрузить» просто нет, а список работает как
        # раньше.
        self._channel_check_task = None
        # Каналы, до которых проверка на видео не дошла. Их проверяем
        # позже: пачка запросов подряд на всю подписку — это FloodWait.
        self._channel_unchecked = []
        # Второй заход за проверку назначен: больше одного раза не надо.
        self._check_retry_done = False
        # Задачи фоновой догрузки: по ним видно, идёт ли она сейчас.
        self._loading_more = set()
        # Есть ли в канале ещё не прочитанная история: по этому признаку
        # в конце списка роликов показывается кнопка догрузки.
        self._video_more = {}
        # Самое старое прочитанное сообщение канала: граница для
        # следующей догрузки. Нужна и каналу без роликов в первой
        # странице — иначе старые ролики было бы не найти.
        self._video_offset = {}
        self._load_channel_marks()

        # Готовые группы качеств строятся одновременно со списком видео.
        self._video_qualities = {}
        self._quality_groups = {}

        os.makedirs(config.CACHE_DIR, exist_ok=True)

        # Способ подключения берём из настроек: его выбирает пользователь,
        # а не догадки автопоиска.
        cfg = self.settings
        self._proxy_mode = str(cfg.get('proxy_mode') or config.DEFAULT_PROXY_MODE)
        if self._proxy_mode not in config.PROXY_MODES:
            self._proxy_mode = config.DEFAULT_PROXY_MODE
        self._mtproxy = self._mtproxy_from(cfg)
        # Будит цикл подключения, когда способ сменили в настройках.
        self._connect_wake = None
        # Плеер закрывают. У окна плеера есть своё поле с тем же именем,
        # но у бэкенда его не было — обращение к нему роняло цикл
        # подключения на первом же шаге.
        self._closed = False
        # Пользователь отказался входить в аккаунт: перебирать дальше
        # бессмысленно, цикл подключения должен остановиться.
        self._stop_connect = False
        # Поколение подключения. Смена режима прокси увеличивает его, и
        # этим обрывает попытку по прежнему маршруту: без этого
        # переключение ждало, пока старый режим откажет сам (до 15 с на
        # попытку, и таких попыток до трёх).
        self._connect_gen = 0

        self._use_proxy = (self._proxy_mode == 'custom')
        preferred = self._preferred_mode()
        print(f"[Бэкенд] Способ подключения: {proxy.mode_label(preferred)}")
        self.client = self._build_client(preferred, self._mtproxy)
        # Запоминаем, для какого способа построен клиент: перебор способов
        # обязан пересоздавать его, иначе попытка уйдёт не тем путём.
        self._client_mode = preferred
        # Ожидание ответа пользователя на текущем шаге входа.
        self._login_future = None

    @staticmethod
    def _mtproxy_from(cfg):
        """Собирает тройку (хост, порт, секрет) своего MTProto-прокси."""
        host = str(cfg.get('mtproxy_host') or config.MT_PROXY_DEFAULT[0]).strip()
        secret = str(cfg.get('mtproxy_secret') or config.MT_PROXY_DEFAULT[2]).strip()
        try:
            port = int(cfg.get('mtproxy_port') or config.MT_PROXY_DEFAULT[1])
        except (TypeError, ValueError):
            port = config.MT_PROXY_DEFAULT[1]
        return (host or config.MT_PROXY_DEFAULT[0], port, secret)

    def _preferred_mode(self):
        """Первый способ подключения по выбору пользователя."""
        if self._proxy_mode == 'custom':
            return 'mtproxy'
        if self._proxy_mode == 'system':
            found = proxy.system_proxy_modes()
            if found:
                return found[0]
        return 'direct'

    def _proxy_failure_hint(self):
        """Почему в режиме системного прокси не вышло и что сделать.

        Проверки дают три равные картины отказа, и у каждой своё лечение:

        * в настройках Windows нет адреса прокси — его не включил
          VPN-клиент, а в режиме «системный прокси» идём только туда;
        * адрес есть и порт открыт, но прокси не пропускает до Telegram —
          чаще всего это выключенный VPN или заблокированный дата-центр;
        * прокси требует логин и пароль, а в proxy.txt их нет.

        Возвращает готовое сообщение или '' если сказать нечего.
        """
        try:
            servers = proxy.system_proxy_modes()
            raw = proxy.system_proxy_raw()
        except Exception:
            return ''
        if not raw:
            return ("В Windows прокси не включён (или не указан адрес). "
                    "Включите системный прокси в VPN-клиенте либо "
                    "выберите другой способ подключения.")
        if not servers:
            return ("Прокси (%s) прописан в Windows, но не отвечает — "
                    "VPN-клиент выключен?" % raw)
        # Адрес есть и прокси отвечает, но подключиться через него не
        # вышло. Две частые причины: VPN не пускает (сервер Telegram
        # закрыт) или прокси требует логин и пароль, а их нет в proxy.txt.
        return ("Прокси (%s) отвечает, но до Telegram через него не "
                "пройти. Проверьте, что VPN включён; если у прокси есть "
                "логин и пароль — укажите их в proxy.txt." % raw)

    @staticmethod
    def _build_client(mode, mtproxy=None):
        """Создаёт клиент Telethon для выбранного способа подключения.

        direct          — напрямую: работает при VPN в режиме TUN;
        mtproxy         — через MTProxy (TgWsProxy);
        proxy:<тип>:<порт> — через локальный прокси VPN-клиента (режим
                          «proxy»): тип http/socks5, порт на localhost.

        mtproxy — своя тройка (хост, порт, секрет) из настроек. Без неё
        берётся встроенная, поэтому вызов _build_client('mtproxy') без
        аргументов продолжает работать.

        Возвращает None, если способ сейчас недоступен — например, VPN в
        режиме «proxy» не запущен и такого порта на localhost нет.
        """
        # Повторов ровно один. Telethon при обрыве не падает, а уходит в
        # свой цикл переподключения ТЕМ ЖЕ маршрутом и висит там минутами:
        # пока он висит, нет ни ошибки, ни данных, и заметить обрыв нечем.
        # Пусть лучше падает быстро — перебор способов выше сам найдёт
        # новый маршрут.
        kwargs = {'connection_retries': 1}

        if mode == 'mtproxy':
            # Класс соединения лежит в Telethon: подтягиваем библиотеку,
            # если её ещё нет (окно могло открыться раньше неё).
            telethon_boot.load()
            kwargs['connection'] = (
                telethon_boot.telethon_connections.ConnectionTcpMTProxyRandomizedIntermediate
            )
            kwargs['proxy'] = tuple(mtproxy) if mtproxy else config.PROXY
        else:
            parsed = proxy.parse_mode(mode)
            if parsed is not None:
                ptype, port = parsed
                if not utils.port_open(config.VPN_PROXY_HOST, port):
                    return None
                # Формат ровно такой, какого ждёт Telethon: он разворачивает
                # словарь в _parse_proxy(proxy_type, addr, port, rdns,
                # username, password).
                kwargs['proxy'] = {
                    'proxy_type': ptype,
                    'addr': config.VPN_PROXY_HOST,
                    'port': port,
                    'rdns': True,
                    'username': proxy.PROXY_AUTH.get('user') or None,
                    'password': proxy.PROXY_AUTH.get('pass') or None,
                }

        # Проверяем сессию ДО создания клиента: Telethon открывает файл
        # сессии в конструкторе, и битый файл валит именно создание.
        if not _session_file_ok():
            _remove_session_files()

        # Клиент тоже из Telethon: к этому моменту библиотека нужна
        # обязательно, даже если фоновый импорт ещё не закончился.
        telethon_boot.load()

        try:
            return telethon_boot.TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH, **kwargs)
        except Exception as ex:
            # Страховка на случай, если файл испортился между проверкой и
            # созданием клиента: повреждённый файл валит СОЗДАНИЕ клиента,
            # а не подключение. Раньше это роняло программу ещё до
            # появления окна, и вылечить можно было только удалением
            # файла руками.
            if not _is_session_error(ex):
                raise
            print(f"[Вход] файл сессии повреждён ({ex}) — "
                  f"удаляю и создаю заново")
            _remove_session_files()
            return telethon_boot.TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH, **kwargs)

    # ---------- вход в аккаунт прямо в программе ----------

    async def _ask_login(self, kind, message):
        """Спрашивает данные входа у пользователя и ждёт ответа.

        Выполняется в потоке backend'а: сигнал уходит в Qt-поток, там
        показывается диалог, а ответ возвращается сюда через
        answer_login(). None означает отказ пользователя.
        """
        fut = self.loop.create_future()
        self._login_future = fut
        self.login_step.emit(kind, message)
        try:
            return await fut
        finally:
            if self._login_future is fut:
                self._login_future = None

    def answer_login(self, value):
        """Ответ пользователя на шаг входа (вызывается из Qt-потока)."""
        fut = self._login_future
        if fut is None or fut.done() or self.loop is None:
            return
        try:
            self.loop.call_soon_threadsafe(fut.set_result, value)
        except RuntimeError:
            pass

    async def qr_login_flow(self, on_url, on_state):
        """Вход по QR-коду: телефон сканирует код с экрана.

        Нужен потому, что Telegram не всегда присылает код по SMS: если у
        номера уже есть активная сессия, он отправляет код В ПРИЛОЖЕНИЕ —
        в служебный чат «Telegram». Человек ждёт SMS, ничего не приходит,
        и войти невозможно. QR снимает вопрос вовсе: код не нужен.

        on_url(str) — отдать ссылку tg://login?token=... (нарисовать QR);
        on_state(str) — сообщить состояние: 'scan' (ждём сканирования),
        'confirm' (отсканировали, подтвердите на телефоне), 'expired'
        (код устарел, обновляем).

        Возвращает 'ok', 'cancel' или 'error' — как _interactive_login.
        """
        client = self.client
        if client is None:
            return 'error'

        if await client.is_user_authorized():
            return 'ok'

        # Сбрасываем флаг «человек отказался». Окно входа закрывается
        # кнопкой «Войти по QR» как обычное «принято», но backend до этого
        # мог уже пометить вход отменённым — и тогда цикл ниже не сделал
        # бы ни одного шага, а программа закрылась бы.
        self._stop_connect = False

        try:
            qr = await client.qr_login()
        except Exception as ex:
            print(f"[Вход] QR не получен: {ex}")
            return 'error'

        on_url(qr.url)
        on_state('scan')

        # Ждём подтверждения. Каждый wait — это один цикл ожидания;
        # когда код устаревает, Telethon бросает TimeoutError, и код
        # нужно перерисовать (recreate). Так повторяем, пока человек
        # не отменит вход.
        while not self._stop_connect and not self._is_closed():
            try:
                await qr.wait(timeout=30)
                print("[Вход] QR подтверждён")
                return 'ok'
            except asyncio.TimeoutError:
                # Код устарел — рисуем новый. Это нормальный ход событий:
                # QR живёт около минуты.
                if self._stop_connect or self._is_closed():
                    break
                try:
                    await qr.recreate()
                    on_url(qr.url)
                    on_state('expired')
                    print("[Вход] QR обновлён")
                except Exception as ex:
                    print(f"[Вход] QR не обновлён: {ex}")
                    return 'error'
            except telethon_boot.SessionPasswordNeededError:
                # Включена двухфакторная защита: после сканирования нужен
                # облачный пароль.
                password = await self._ask_login(
                    'password', "Включена двухфакторная защита. Введите пароль")
                if not password:
                    return 'cancel'
                try:
                    await client.sign_in(password=password)
                    return 'ok'
                except Exception as ex:
                    print(f"[Вход] пароль не принят: {ex}")
                    return 'error'
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                print(f"[Вход] QR: {type(ex).__name__}: {ex}")
                return 'error'

        return 'cancel'

    async def _interactive_login(self):
        """Вход в аккаунт: номер, код, при необходимости пароль 2FA.

        Возвращает 'ok', 'cancel' (пользователь отказался или данные
        неверны) либо 'error' (сеть или Telegram не дали войти — есть
        смысл попробовать другой способ подключения).
        """
        client = self.client
        phone = await self._ask_login(
            'phone', "Введите номер телефона в международном формате, "
                     "например +79991234567")
        if not phone:
            return 'cancel'
        phone = _normalize_phone(phone)
        if len(phone) < 8:
            await self._ask_login('error', "Номер выглядит неполным.")
            return 'cancel'

        print(f"[Вход] запрашиваю код для {phone}")
        try:
            sent = await client.send_code_request(phone)
        except telethon_boot.PhoneNumberInvalidError:
            await self._ask_login('error', "Telegram не принял этот номер.")
            return 'cancel'
        except Exception as ex:
            print(f"[Вход] код не отправлен: {ex}")
            await self._ask_login('error', f"Не удалось отправить код: {ex}")
            return 'error'

        code = await self._ask_login(
            'code', "Код отправлен в Telegram. Введите его сюда")
        if not code:
            return 'cancel'
        code = ''.join(ch for ch in code if ch.isdigit()) or code

        try:
            await client.sign_in(phone=phone, code=code,
                                 phone_code_hash=sent.phone_code_hash)
        except telethon_boot.SessionPasswordNeededError:
            password = await self._ask_login(
                'password', "Включена двухфакторная защита. Введите пароль")
            if not password:
                return 'cancel'
            try:
                await client.sign_in(password=password)
            except telethon_boot.PasswordHashInvalidError:
                await self._ask_login('error', "Неверный пароль.")
                return 'cancel'
            except Exception as ex:
                print(f"[Вход] пароль не принят: {ex}")
                await self._ask_login('error', f"Вход не выполнен: {ex}")
                return 'error'
        except (telethon_boot.PhoneCodeInvalidError, telethon_boot.PhoneCodeExpiredError):
            await self._ask_login(
                'error', "Код неверный или устарел. Запустите программу "
                         "заново и запросите новый код.")
            return 'cancel'
        except Exception as ex:
            print(f"[Вход] код не принят: {ex}")
            await self._ask_login('error', f"Вход не выполнен: {ex}")
            return 'error'

        who = ''
        try:
            me = await client.get_me()
            if me is not None:
                who = (f" (@{me.username})" if me.username
                       else f" (id {me.id})")
        except Exception:
            pass
        print(f"[Вход] выполнен{who}")
        self.status_changed.emit("Вход выполнен.")
        return 'ok'

    def start_loop(self):
        """Работает в отдельном потоке: свой event loop на всё время работы.

        Сначала поднимает подключение и локальный HTTP-сервер, и только
        если это удалось — уходит в run_forever. При выходе закрывает
        дескрипторы кеша, отключает клиент и гасит loop.
        """
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            backend_ok = self.loop.run_until_complete(self.run_backend())
            if backend_ok and self._server_started:
                self.loop.run_forever()
        except Exception as e:
            self.error_occurred.emit(f"Фатальная ошибка бэкенда: {e}")
        finally:
            # Наблюдателя гасим до закрытия loop, иначе он останется
            # висеть незавершённой задачей.
            try:
                if self._watchdog_task is not None:
                    self._watchdog_task.cancel()
                    self.loop.run_until_complete(
                        asyncio.gather(self._watchdog_task,
                                       return_exceptions=True))
                    self._watchdog_task = None
            except Exception:
                pass
            self.cleanup_cache()
            try:
                if self.client.is_connected():
                    self.loop.run_until_complete(self.client.disconnect())
            except Exception:
                pass
            try:
                self.loop.close()
            except Exception:
                pass

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
        """Забывает готовую запись кэша, не трогая файл (Qt-поток).

        Удаление самого файла — это delete_cached_file; здесь только
        сама запись (нужно, когда файл уже удалён кем-то снаружи).
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
            # Метка для уже работающих HTTP-обработчиков: запись удалена, и
            # ждать по ней данные больше нечего — иначе они крутились бы
            # вечно, потому что файл уже удалён с диска.
            e['removed'] = True
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

    def submit_coroutine(self, coro):
        """Безопасно отправляет coroutine в backend loop из Qt-потока."""
        if self.loop is None or self.loop.is_closed() or not self.ready:
            try:
                coro.close()
            except Exception:
                pass
            return None
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.loop)
        except (RuntimeError, RuntimeWarning):
            try:
                coro.close()
            except Exception:
                pass
            return None

    def _is_connected(self):
        """Есть ли живое соединение с Telegram по мнению Telethon."""
        client = self.client
        if client is None:
            return False
        try:
            return bool(client.is_connected())
        except Exception:
            return False

    def _is_connecting(self):
        """Идёт ли сейчас перебор способов подключения.

        Через try, а не getattr с запасным значением: у наследника
        QObject чтение НЕсуществующего поля само бросает RuntimeError,
        если объект собран через __new__ без __init__. Из-за этого
        наблюдатель за связью падал на первом же такте.
        """
        try:
            return self._conn_state == 'connecting'
        except Exception:
            return False

    def _set_connection(self, state=None):
        """Сообщает окну о состоянии связи, если оно изменилось.

        state — 'ok', 'off' или 'connecting'. Сигнал шлём только на
        смену: наблюдатель зовёт этот метод на каждом такте, а дёргать
        перерисовку значка впустую незачем.

        Всё тело в перехвате не для красоты: у QObject-наследника даже
        запись поля падает, если объект создан через __new__ без __init__
        (так его собирают проверки). Ронять из-за этого подключение
        нельзя — состояние связи всего лишь красит значок.
        """
        try:
            if state is None:
                state = 'ok' if self._is_connected() else 'off'
            if state not in ('ok', 'off', 'connecting'):
                state = 'off'
            if state == getattr(self, '_conn_state', None):
                return state
            self._conn_state = state
            self.connection_changed.emit(state)
            return state
        except Exception:
            return state

    async def reconnect(self, force=False):
        """Ищет рабочий способ подключения заново.

        Режим VPN может смениться на ходу: плеер стартовал при TUN и ходил
        напрямую, пользователь переключился в «proxy» — прямой дороги
        больше нет. Telethon переподключается сам, но только тем же
        маршрутом, поэтому нужен полный перебор способов, как при запуске.

        force=True — вызов после сетевой ошибки: маршрут уже доказал свою
        негодность, и is_connected() ему верить нельзя.
        """
        if self._reconnect_lock is None:
            self._reconnect_lock = asyncio.Lock()
        seen = self._reconnect_gen

        async with self._reconnect_lock:
            # Пока ждали очередь, соседний воркер уже всё починил.
            if self._reconnect_gen != seen and self._is_connected():
                return True
            now = time.monotonic()
            if now - self._last_reconnect < config.RECONNECT_MIN_GAP:
                # Перебор только что был — второй раз подряд не повторяем.
                return self._is_connected()
            if not force and self._is_connected():
                return True

            self._last_reconnect = now
            self._reconnect_gen += 1
            self._reconnect_count += 1
            print(f"[Бэкенд] Связь потеряна — перебираю способы заново "
                  f"(#{self._reconnect_count})...")
            self.status_changed.emit("Переподключение...")
            # Старый способ больше не годится — заставляем выбрать заново.
            self._client_mode = None
            ok = await self._connect_with_fallback(notify=False)
            self._set_connection('ok' if ok else 'off')
            self.status_changed.emit(
                "Связь восстановлена." if ok
                else "Связь не восстановлена — проверьте VPN.")
            return ok

    def _is_closed(self):
        """Плеер закрывают — подключение и повторы больше не нужны.

        Через getattr, а не напрямую: часть проверок создаёт бэкенд через
        __new__, не заходя в __init__.
        """
        return bool(getattr(self, '_closed', False))

    def set_proxy(self, mode, host=None, port=None, secret=None):
        """Меняет способ подключения на ходу (вызывается из потока окна).

        Клиент построен под прежний способ, поэтому сбрасываем _client_mode:
        ближайшее подключение пересоберёт его заново. Если связь уже есть —
        переподключаемся сразу, не дожидаясь обрыва.

        Смена режима ОБРЫВАЕТ идущую попытку подключения. Без этого было
        так: выбрали «свой прокси», он висит на недоступном адресе до
        полного таймаута (15 с на попытку, а их три), и всё это время
        переключение на «системный прокси» не действовало — человек жал
        настройку, а плеер продолжал долбиться по старому маршруту. Дело
        в том, что connect() внутри _connect_with_fallback ничем не
        прерывается: смена режима только сбрасывала _client_mode, но
        текущий await ждал своего конца.
        """
        if mode not in config.PROXY_MODES:
            return
        self._proxy_mode = mode
        if host is not None:
            self._mtproxy = self._mtproxy_from({
                'mtproxy_host': host,
                'mtproxy_port': port,
                'mtproxy_secret': secret,
            })
        self._use_proxy = (mode == 'custom')
        self._client_mode = None
        # Прежний отказ от входа больше не в счёт: пользователь сменил
        # настройки и явно хочет попробовать снова.
        self._stop_connect = False
        if self.loop is None or self.loop.is_closed():
            return
        self.loop.call_soon_threadsafe(self._restart_connect)

    def _restart_connect(self):
        """Будит подключение после смены настроек (в потоке бэкенда).

        Гоняет поколение подключения: всё, что сейчас ждёт сеть по
        прежнему режиму, увидит смену поколения и прекратит попытки.
        Без этого смена способа ждала ошибки старого — до пятнадцати
        секунд на попытку, и несколько раз подряд.
        """
        # Новое поколение — прежние попытки теперь недействительны.
        self._connect_gen = getattr(self, '_connect_gen', 0) + 1
        # Будим цикл подключения: он может ждать паузы между попытками.
        wake = self._connect_wake
        if wake is not None:
            wake.set()
        if self.ready:
            # Связь есть — переподключаемся по новому режиму сразу.
            asyncio.ensure_future(self.reconnect(force=True))
            return
        # Связи нет: цикл подключения уже крутится и сейчас прервёт
        # ожидание по старому режиму (см. _connect_with_fallback).

    def _connect_stale(self, gen):
        """Поколение подключения сменилось — текущую попытку пора бросить."""
        return getattr(self, '_connect_gen', gen) != gen

    async def _request_with_reconnect(self, factory, label):
        """Выполняет запрос; при сетевой ошибке переподключается и повторяет.

        factory вызывается без аргументов и возвращает НОВУЮ coroutine:
        повтор должен собирать запрос заново, а не переиспользовать уже
        сгоревший объект.
        """
        try:
            return await factory()
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            print(f"[Бэкенд] {label}: ошибка: {ex}")
            if not await self.reconnect(force=True):
                raise
            return await factory()

    def _has_active_downloads(self):
        """Качается ли сейчас хоть один кусок."""
        return any(e.get('active') for e in self.cache.values())

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
            if e.get('complete') or e.get('removed'):
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

    async def run_backend(self):
        try:
            self.status_changed.emit("Подключение...")

            # Подключение пробуем до победного. Раньше единственная неудача
            # (VPN ещё не поднялся, прокси выключен) оставляла плеер
            # мёртвым до перезапуска — даже если прокси включали через
            # минуту. Теперь ждём и пробуем снова, а смена способа в
            # настройках будит цикл немедленно.
            self._connect_wake = asyncio.Event()
            attempt = 0
            while not self._is_closed() and not self._stop_connect:
                attempt += 1
                if await self._connect_with_fallback(notify=(attempt == 1)):
                    break
                if self._is_closed() or self._stop_connect:
                    return False
                self.status_changed.emit(
                    "Не удалось подключиться — пробую снова...")
                try:
                    await asyncio.wait_for(
                        self._connect_wake.wait(), config.CONNECT_RETRY_PAUSE)
                except asyncio.TimeoutError:
                    pass
                self._connect_wake.clear()

            if self._is_closed() or self._stop_connect:
                return False

            await self.load_data()
            await self.start_stream_server()
            self.ready = True
            # Наблюдатель за связью живёт до выхода: он замечает обрыв,
            # когда пользователь ничего не делает.
            self._watchdog_task = asyncio.ensure_future(
                self._connection_watchdog())
            self._set_connection('ok')
            self.status_changed.emit("Готово.")
            return True
        except Exception as e:
            self.ready = False
            self.error_occurred.emit(f"Ошибка бэкенда: {e}")
            return False

    async def _connect_with_fallback(self, notify=True):
        """Перебирает способы подключения, пока один не сработает.

        notify=False используется при переподключении на ходу: там ошибка
        уходит в строку состояния, а не модальным окном поверх видео.

        Порядок зависит от того, как поднят VPN:

        Порядок задаёт выбранный способ, и только он:

        * без прокси — напрямую;
        * свой прокси — свой MTProto;
        * системный прокси — адрес из настроек Windows.

        Резервных маршрутов нет. Раньше к системному прокси подмешивались
        локальные порты, MTProxy и прямое подключение, и режим подключался
        даже при выключенном прокси — со стороны это выглядело так, будто
        прокси не работает, а связь всё равно есть.
        """
        # Список способов задаёт выбор пользователя, а не догадки. Внутри
        # системного прокси адресов может быть несколько (HTTP и SOCKS5 на
        # одном порту) — их и перебираем.
        if self._proxy_mode == 'off':
            # «Без прокси» — идём только напрямую. Никаких резервных
            # прокси: пользователь их явно отключил.
            modes = ['direct']
        elif self._proxy_mode == 'custom':
            # Свой MTProto. Тоже ровно один способ: остальное — чужие
            # маршруты, которых пользователь не выбирал.
            modes = ['mtproxy']
        else:
            # «Системный прокси» — идём только туда, куда указывает Windows.
            #
            # Раньше здесь к системному прокси подмешивались локальные
            # порты, MTProxy и прямое подключение. Из-за этого при
            # выключенном прокси режим всё равно подключался в обход
            # него, и было непонятно, что вообще работает. Теперь
            # источник ровно один: настройка Windows.
            modes = proxy.system_proxy_modes()
            if modes:
                print("[Бэкенд] Системный прокси Windows: "
                      + ", ".join(proxy.mode_label(m) for m in modes))
            else:
                print("[Бэкенд] Системный прокси Windows выключен.")

        # Системный прокси может оказаться выключенным, и тогда идти
        # некуда: подменять его другим маршрутом нельзя — это и было
        # причиной подключения «в обход прокси».
        #
        # Это не сбой программы, а состояние настройки Windows, поэтому
        # сообщаем строкой состояния снизу, без модального окна: окно
        # пришлось бы закрывать руками каждый раз, когда VPN выключен.
        if not modes:
            self._set_connection('off')
            msg = ("Системный прокси в Windows выключен. Включите его в "
                   "параметрах прокси Windows либо выберите другой способ "
                   "подключения.")
            print("[Бэкенд] " + msg)
            self.status_changed.emit(msg)
            return False

        if not config.AUTO_FALLBACK:
            modes = modes[:1]
        preferred = modes[0]

        # Пока перебираем способы, значок крутит кольцо: снаружи видно,
        # что программа работает, а не просто «связи нет».
        self._set_connection('connecting')

        last_error = None
        # Общий предел на весь перебор. Без него ожидание складывалось в
        # несколько минут молчания: попыток много, и каждая висела.
        deadline = time.monotonic() + config.CONNECT_BUDGET
        # Поколение подключения на входе: если человек сменит режим прокси,
        # поколение сменится, и попытки по старому маршруту прекратятся.
        gen = getattr(self, '_connect_gen', 0)
        for mode in modes:
            label = proxy.mode_label(mode)
            if self._connect_stale(gen):
                print("[Бэкенд] Режим прокси сменился — бросаю старую попытку")
                last_error = None
                break
            if time.monotonic() > deadline:
                print("[Бэкенд] Время на подключение вышло")
                if last_error is None:
                    last_error = RuntimeError("истекло время ожидания")
                break
            # Клиент пересоздаём, когда он построен НЕ для этого способа, —
            # включая первый в списке. Раньше первый способ считался уже
            # готовым, хотя клиент в __init__ создан для «напрямую»: в
            # итоге плеер писал «пробую прокси», а на деле подключался
            # напрямую и висел на заблокированном адресе Telegram.
            if getattr(self, '_client_mode', None) != mode:
                print(f"[Бэкенд] Пробую способ: {label}...")
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                try:
                    self.client.session.close()
                except Exception:
                    pass
                try:
                    self.client = self._build_client(mode, self._mtproxy)
                except Exception as ex:
                    print(f"[Бэкенд] Не удалось создать клиент: {ex}")
                    self.client = None
                self._client_mode = mode

            if self.client is None:
                print(f"[Бэкенд] Способ недоступен: {label}")
                continue

            self._use_proxy = (mode == 'mtproxy')

            # Локальный прокси либо пускает, либо нет: повторять его
            # бессмысленно, куда полезнее сразу перейти к следующему
            # кандидату. Сетевые способы (напрямую, MTProxy) пробуем
            # настойчивее — там сбой чаще оказывается временным.
            # Резервные способы проверяем по разу: их задача быстро
            # выяснить, есть ли дорога, а не удержать связь.
            if mode.startswith('proxy:'):
                # Локальный прокси пробуем ещё раз, но только с новым
                # соединением: без этого единственная попытка уходила на
                # разогрев недавно поднятого VPN-клиента, и режим
                # «системный прокси» выглядел нерабочим.
                tries = 1
            else:
                tries = 3 if mode == preferred else 1
            # Показываем, что происходит: при переборе способов ожидание
            # может затянуться, и молчащее окно выглядит как зависание.
            if notify:
                self.status_changed.emit(f"Подключение ({label})...")
            for attempt in range(1, tries + 1):
                # Перед каждой попыткой сверяемся с поколением: пока
                # ждали сеть, человек мог сменить режим прокси, и тогда
                # продолжать этот маршрут бессмысленно.
                if self._connect_stale(gen):
                    print("[Бэкенд] Режим прокси сменился — бросаю попытку")
                    last_error = None
                    break
                try:
                    if not self.client.is_connected():
                        # Подключение ждём через wait_for: он прерывается
                        # по таймауту. Проверку поколения внутри connect()
                        # сделать нечем — Telethon не умеет отменять
                        # отдельную попытку, поэтому после неё сверяемся.
                        #
                        # Прямо перед подключением ВНОВЬ читаем логин и
                        # пароль из proxy.txt: раньше они читались только
                        # при старте, и вписанные в файл на работающей
                        # программе не подхватывались — прокси с
                        # авторизацией так и отвечал отказом.
                        try:
                            proxy.load_proxy_config()
                            if mode.startswith('proxy:'):
                                self.client = self._build_client(
                                    mode, self._mtproxy)
                        except Exception as ex:
                            print(f"[Бэкенд] логин/пароль прокси: {ex}")
                        if self.client is None:
                            print(f"[Бэкенд] Способ недоступен: {label}")
                            break
                        await asyncio.wait_for(self.client.connect(),
                                               timeout=config.CONNECT_TIMEOUT)
                    if self._connect_stale(gen):
                        print("[Бэкенд] Режим прокси сменился — бросаю попытку")
                        try:
                            await self.client.disconnect()
                        except Exception:
                            pass
                        last_error = None
                        break
                    if await self.client.is_user_authorized():
                        print(f"[Бэкенд] Подключено: {label}")
                        return True
                    # Связь есть, но аккаунт не авторизован. Раньше здесь
                    # отправляли запускать отдельный auth.py — теперь
                    # спрашиваем данные прямо в окне программы.
                    print(f"[Бэкенд] Связь есть ({label}), вход не выполнен")
                    status = await self._interactive_login()
                    if status == 'ok':
                        print(f"[Бэкенд] Подключено: {label}")
                        return True
                    if status == 'error':
                        # Данные могли не дойти из-за сети — пусть цикл
                        # попробует следующий способ подключения.
                        last_error = RuntimeError("вход в аккаунт не выполнен")
                        break
                    # Отказ пользователя — не сбой сети: перебирать способы
                    # и повторять попытки бессмысленно, иначе плеер будет
                    # бесконечно спрашивать одно и то же.
                    self._stop_connect = True
                    # Сигнал шлём ВСЕГДА, а не только при первой попытке:
                    # по нему программа понимает, что вход отменили, и
                    # закрывается. Без аккаунта работать нечем, а держать
                    # пустое окно — хуже, чем выйти.
                    self.login_cancelled.emit()
                    return False
                except Exception as ex:
                    # Смена режима прокси — не «ошибка способа», а
                    # сознательное действие человека. Не считаем это
                    # провалом маршрута и не продолжаем его: связь будет
                    # чинить соседняя попытка по новому режиму.
                    if self._connect_stale(gen):
                        print("[Бэкенд] Режим прокси сменился — бросаю попытку")
                        try:
                            await self.client.disconnect()
                        except Exception:
                            pass
                        last_error = None
                        break
                    last_error = ex
                    # У ожидания пишется «истекло время ожидания» —
                    # без этого в журнале оставалась пустая строка и
                    # понять, почему режим не подключился, было нечем.
                    reason = (repr(ex) if not str(ex)
                              else f"{type(ex).__name__}: {ex}")
                    print(f"[Бэкенд] Ошибка ({label}), "
                          f"попытка {attempt}/{tries}: {reason}")
                    # Битая сессия: файл есть, но открыть его нельзя.
                    # Без этого плеер навсегда застревал на ошибке, и
                    # вылечить это можно было только удалением файла
                    # руками. Сначала отпускаем дескрипторы: на Windows
                    # занятый файл не удалить, и лечение не срабатывало.
                    if _is_session_error(ex):
                        try:
                            await self.client.disconnect()
                        except Exception:
                            pass
                        try:
                            self.client.session.close()
                        except Exception:
                            pass
                        _remove_session_files()
                        self._client_mode = None
                        last_error = None
                        continue
                    # Маршрут в «чёрную дыру»: попытка не отказала, а
                    # выждала весь предел. Повторять бессмысленно —
                    # каждая следующая снова будет ждать столько же.
                    if isinstance(ex, (asyncio.TimeoutError, TimeoutError)):
                        print(f"[Бэкенд] {label}: время подключения вышло")
                        break
                    try:
                        await self.client.disconnect()
                    except Exception:
                        pass
                    if attempt < tries:
                        await asyncio.sleep(1.5 * attempt)

        if last_error and notify:
            # В режиме «системный прокси» общая фраза «проверьте VPN»
            # ничего не объясняет: причин три, и лечение у каждой своё.
            # Подсказка называет ту, что видна по настройкам Windows.
            hint = ''
            if self._proxy_mode == 'system':
                hint = self._proxy_failure_hint()
            self.error_occurred.emit(
                "Не удалось подключиться: " + _error_text(last_error)
                + (". " + hint if hint else ". Проверьте VPN или прокси."))
            if hint:
                self.status_changed.emit(hint)
        return False

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

    # ----------------------------------------------------------
    # ИСТОРИЯ ПРОСМОТРОВ
    # ----------------------------------------------------------
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

    # ----------------------------------------------------------
    # КЭШИРОВАНИЕ И СТРИМИНГ
    # ----------------------------------------------------------
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
        """True, если индекс файла лежит в конце и нужен для старта.

        MP4: если mdat идёт раньше moov — индекс в хвосте; если moov
        впереди (faststart), хвост не нужен.
        MKV/WebM: боксов нет, а Cues почти всегда в конце — значит хвост
        нужен всегда.
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

