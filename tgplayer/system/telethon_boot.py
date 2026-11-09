"""Подтягивает Telethon при первом обращении.

Импорт Telethon — самая дорогая часть запуска: около трёх секунд на
слабой машине. Нужен он только для работы с Telegram, а окно, тема и
настройки к нему не относятся. Поэтому грузим его лениво: программа
открывается сразу, а библиотека подтягивается в фоновом потоке
(`preload`), к тому моменту, как понадобится подключение.

Имена классов лежат здесь, а не в backend: их подменяют тесты, и модуль
не должен зависеть от того, когда именно его импортировали.
"""
from threading import Lock as _Lock

_lock = _Lock()
_loaded = False

TelegramClient = None
telethon_connections = None
telethon_utils = None
MessageMediaDocument = None
DocumentAttributeVideo = None
InputMessagesFilterVideo = None
DialogFilter = None
Channel = None
Chat = None
GetDialogFiltersRequest = None
UpdateDialogFilterRequest = None
SearchContacts = None
JoinChannelRequest = None
LeaveChannelRequest = None
SessionPasswordNeededError = None
PhoneCodeInvalidError = None
PhoneCodeExpiredError = None
PhoneNumberInvalidError = None
PasswordHashInvalidError = None
# Ошибка «ссылка на файл истекла». До загрузки Telethon её нет, поэтому
# backend проверяет ещё и по тексту ошибки.
FileReferenceExpiredError = None
# Слишком много запросов: надо ждать, а не переподключаться. До загрузки
# Telethon класса нет, backend проверяет ещё и по тексту.
FloodWaitError = None
# Сервер не принял диапазон (например, запрос пересёк границу 1 МБ).
# Тогда переходим на 512 КБ вместо 1 МБ.
LimitInvalidError = None


def loaded():
    """Telethon уже подтянут."""
    return _loaded


def load():
    """Загружает Telethon один раз, безопасно для нескольких потоков."""
    global TelegramClient, telethon_connections, telethon_utils
    global MessageMediaDocument, DocumentAttributeVideo
    global InputMessagesFilterVideo, DialogFilter
    global Channel, Chat, GetDialogFiltersRequest, UpdateDialogFilterRequest
    global SearchContacts, JoinChannelRequest, LeaveChannelRequest
    global SessionPasswordNeededError, PhoneCodeInvalidError
    global PhoneCodeExpiredError, PhoneNumberInvalidError
    global PasswordHashInvalidError, FileReferenceExpiredError
    global FloodWaitError, LimitInvalidError
    global _loaded

    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        from telethon import TelegramClient as _TC
        from telethon.network import connection as _conn
        from telethon import utils as _utils
        from telethon.tl.types import (
            MessageMediaDocument as _MMD,
            DocumentAttributeVideo as _DAV,
            InputMessagesFilterVideo as _IMFV,
            DialogFilter as _DF, Channel as _Ch, Chat as _Chat)
        from telethon.tl.functions.messages import (
            GetDialogFiltersRequest as _GDF, UpdateDialogFilterRequest as _UDF)
        from telethon.tl.functions.contacts import SearchRequest as _SC
        from telethon.tl.functions.channels import (
            JoinChannelRequest as _JC, LeaveChannelRequest as _LC)
        from telethon.errors import (
            SessionPasswordNeededError as _SPN, PhoneCodeInvalidError as _PCI,
            PhoneCodeExpiredError as _PCE, PhoneNumberInvalidError as _PNI,
            PasswordHashInvalidError as _PHI,
            FileReferenceExpiredError as _FRE,
            FloodWaitError as _FWE)
        # LimitInvalidError появился позже остальных; без него плеер
        # должен продолжать работать, а не падать на импорте.
        try:
            from telethon.errors import LimitInvalidError as _LIE
        except ImportError:
            _LIE = None
        from telethon.client import downloads as _downloads

        TelegramClient = _TC
        telethon_connections = _conn
        telethon_utils = _utils
        MessageMediaDocument = _MMD
        DocumentAttributeVideo = _DAV
        InputMessagesFilterVideo = _IMFV
        DialogFilter = _DF
        Channel = _Ch
        Chat = _Chat
        GetDialogFiltersRequest = _GDF
        UpdateDialogFilterRequest = _UDF
        SearchContacts = _SC
        JoinChannelRequest = _JC
        LeaveChannelRequest = _LC
        SessionPasswordNeededError = _SPN
        PhoneCodeInvalidError = _PCI
        PhoneCodeExpiredError = _PCE
        PhoneNumberInvalidError = _PNI
        PasswordHashInvalidError = _PHI
        FileReferenceExpiredError = _FRE
        FloodWaitError = _FWE
        LimitInvalidError = _LIE

        # Telethon считает 512 КБ максимумом одного upload.getFile и молча
        # режет до него даже явно переданный размер. Замер 06.11.2026:
        # 1 МБ даёт 5.9–6.2 МБ/с против 2.8–3.0 у 512 КБ, поэтому предел
        # поднимаем. Запросы всё равно должны быть выровнены на 1 МБ —
        # сервер отвергает диапазон, пересекающий эту границу.
        #
        # Значение по умолчанию у iter_download (request_size) остаётся
        # прежним: оно вычислено при определении функции. Backend передаёт
        # размер явно.
        try:
            if hasattr(_downloads, 'MAX_CHUNK_SIZE'):
                _downloads.MAX_CHUNK_SIZE = 1024 * 1024
        except Exception as ex:
            print(f"[Старт] Не удалось поднять размер запроса Telethon: {ex}")

        # Признак «загружено» держим отдельно: сами имена может
        # подменить вызывающий код (например, тесты подставляют свою
        # заглушку вместо TelegramClient), и по ним повторную загрузку
        # не отличить от первой.
        _loaded = True


def preload():
    """Готовит Telethon заранее, в отдельном потоке.

    Импорт идёт в фоне, поэтому окно уже видно, а к моменту входа
    библиотека обычно на месте. Ошибку не роняем: если импорт не удался,
    её покажет обычный путь подключения.
    """
    try:
        load()
    except Exception as ex:
        print(f"[Старт] Telethon не подтянулся: {ex}")
