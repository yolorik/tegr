"""Подключение к Telegram: клиент, прокси и переподключение."""

import asyncio
import time

from ..core import config, proxy, utils
from ..system import telethon_boot
from .common import (
    _error_text, _is_session_error, _remove_session_files, _session_file_ok)


class ConnectionMixin:
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
