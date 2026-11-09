"""Telegram: точка сборки бэкенда.

Подключение, вход, каталог, видео, кэш, HTTP-сервер и история живут в
отдельных mixin-модулях. Здесь остаются сигналы Qt, начальное состояние
и запуск цикла.
"""
import asyncio
import os
import secrets
import time

from PyQt6.QtCore import QObject, pyqtSignal

from ..core import config, history, proxy, settings
from .cache import CacheMixin
from .catalog import CatalogMixin
from .common import (  # noqa: F401  (реэкспорт для tgplayer_runtime)
    _error_text, _is_session_error, _normalize_phone, _remove_session_files,
    _session_file_ok)
from .connection import ConnectionMixin
from .download import DownloadMixin
from .login import LoginMixin
from .playback import PlaybackMixin
from .stream import StreamMixin
from .videos import VideoMixin

__all__ = [
    'TelegramBackend', '_normalize_phone', '_remove_session_files',
    '_is_session_error', '_error_text', '_session_file_ok',
]


class TelegramBackend(QObject, ConnectionMixin, LoginMixin, CatalogMixin,
                      VideoMixin, CacheMixin, DownloadMixin, StreamMixin,
                      PlaybackMixin):
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
