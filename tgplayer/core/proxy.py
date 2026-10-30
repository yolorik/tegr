"""Поиск и выбор способа подключения к Telegram.

Прокси — самая частая причина «плеер не работает». Telegram в России без
прокси недоступен, а способов ходить к нему несколько, и угадать нужный
заранее нельзя: VPN бывает в режиме TUN (тогда годится прямое
подключение) и в режиме proxy (тогда нужен локальный прокси клиента),
плюс бывает свой MTProto.

Здесь — весь перебор:

  * проверка, доступен ли Telegram напрямую;
  * системный прокси Windows;
  * локальные порты, которые слушает VPN-клиент;
  * свой MTProto из настроек;
  * файл proxy.txt как ручной обход автопоиска.

Модуль ничего не решает сам: он отдаёт список кандидатов, а порядок
выбора и подключение — дело backend.
"""
import base64
import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, paths, utils


def mode_slug(port, ptype):
    """Строка-режим для прокси: 'proxy:http:10809'."""
    return f"proxy:{ptype}:{port}"


def parse_mode(mode):
    """'proxy:http:10809' -> ('http', 10809), иначе None."""
    if not isinstance(mode, str) or not mode.startswith('proxy:'):
        return None
    try:
        _, ptype, port = mode.split(':')
        return ptype, int(port)
    except ValueError:
        return None


def mode_label(mode):
    """Человекочитаемое название способа подключения для логов."""
    parsed = parse_mode(mode)
    if parsed is None:
        return {'direct': 'напрямую', 'mtproxy': 'MTProxy'}.get(mode, str(mode))
    ptype, port = parsed
    return f"{ptype.upper()}-прокси VPN ({config.VPN_PROXY_HOST}:{port})"


# Адреса серверов Telegram для быстрой проверки доступности напрямую.
TELEGRAM_PROBE_HOSTS = (
    ('149.154.167.51', 443),
    ('91.108.56.130', 443),
)


def telegram_direct_ok(timeout=2.5):
    """Быстро проверяет, доступны ли серверы Telegram напрямую.

    Нужна, чтобы не тратить десятки секунд на заведомо провальное прямое
    подключение, когда VPN работает в режиме «proxy»: там трафик не
    маршрутизируется, и дороги до Telegram просто нет. При рабочем
    прямом подключении проверка завершается мгновенно.

    Оба адреса проверяются параллельно: последовательная проверка в
    режиме «proxy» упиралась в таймаут дважды и отнимала до пяти секунд
    на самом старте.
    """
    def probe(host, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False
        finally:
            s.close()

    pool = ThreadPoolExecutor(max_workers=len(TELEGRAM_PROBE_HOSTS))
    try:
        futures = [pool.submit(probe, host, port)
                   for host, port in TELEGRAM_PROBE_HOSTS]
        # Возвращаемся, как только ответил первый удачный. Раньше выход
        # через `with` всё равно ждал второй зонд: shutdown(wait=True)
        # блокировал поток до его таймаута, и экономии не было.
        for future in as_completed(futures):
            try:
                if future.result():
                    return True
            except Exception:
                pass
        return False
    finally:
        # Оставшийся зонд не ждём: он уже ничего не решает.
        pool.shutdown(wait=False, cancel_futures=True)


# Порты, которые заведомо не являются прокси VPN: наш собственный
# HTTP-сервер и всё системное до 1024. Сканировать их бессмысленно.
IGNORED_LOCAL_PORTS = frozenset({config.STREAM_PORT})

# Логин и пароль локального прокси. Заполняются из proxy.txt при чтении
# настроек (см. load_proxy_config).
PROXY_AUTH = {'user': '', 'pass': ''}


def list_listening_ports():
    """Порты, которые слушают на localhost, по данным netstat.

    Перебирать диапазон сокетами слишком долго, а netstat показывает
    ровно то, что реально открыто.
    """
    ports = set()
    try:
        out = subprocess.run(
            ['netstat', '-ano', '-p', 'TCP'],
            capture_output=True, text=True, timeout=6,
            creationflags=0x08000000,   # без мелькающего окна консоли
            stdin=subprocess.DEVNULL,   # в .exe без консоли stdin невалиден
        ).stdout
    except Exception as ex:
        # Молчать нельзя: без этого списка автопоиск слепнет, и понять
        # причину по логу было бы невозможно.
        print(f"[Бэкенд] Список портов не получен: {ex}")
        return []

    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != 'TCP':
            continue
        if parts[3].upper() != 'LISTENING':
            continue
        host, _, port = parts[1].rpartition(':')
        if host not in ('127.0.0.1', '0.0.0.0', '[::]', '[::1]', '::'):
            continue
        try:
            value = int(port)
        except ValueError:
            continue
        if 1024 <= value <= 65535 and value not in IGNORED_LOCAL_PORTS:
            ports.add(value)
    return sorted(ports)


def detect_proxy_protocol(port, host=config.VPN_PROXY_HOST, timeout=0.7):
    """Определяет протокол локального прокси коротким рукопожатием.

    Возвращает 'socks5', 'http' или None. Проверять обязательно: у incy
    порты 10808 и 10813 открыты, но прокси на них не отвечает — то есть
    сам факт «порт открыт» ещё ничего не значит.

    Если в proxy.txt заданы логин и пароль, рукопожатие выполняется с
    ними: иначе прокси с авторизацией так и остался бы незамеченным.
    """
    user = PROXY_AUTH.get('user') or ''
    pwd = PROXY_AUTH.get('pass') or ''

    # SOCKS5. Предлагаем оба метода — «без пароля» и «логин/пароль», —
    # чтобы работать и с прокси, который авторизацию не требует.
    methods = b'\x00\x02' if user else b'\x00'
    try:
        with socket.create_connection((host, port), timeout) as s:
            s.sendall(b'\x05\x01' + bytes([len(methods)]) + methods)
            data = s.recv(2)
            if len(data) == 2 and data[0] == 0x05:
                if data[1] == 0x00:
                    return 'socks5'
                if data[1] == 0x02 and user:
                    # Под-переговоры логина и пароля по RFC 1929.
                    u = user.encode('utf-8')
                    w = pwd.encode('utf-8')
                    if len(u) > 255 or len(w) > 255:
                        print(f"[Бэкенд] Порт {port}: логин или пароль "
                              "слишком длинные для SOCKS5")
                        return None
                    s.sendall(b'\x01' + bytes([len(u)]) + u
                              + bytes([len(w)]) + w)
                    reply = s.recv(2)
                    if len(reply) == 2 and reply[1] == 0x00:
                        return 'socks5'
                    print(f"[Бэкенд] Порт {port}: SOCKS5 — неверный "
                          "логин или пароль")
                    return None
                if data[1] == 0xFF:
                    print(f"[Бэкенд] Порт {port}: SOCKS5 требует логин и "
                          "пароль (укажите их в proxy.txt)")
    except OSError:
        pass

    # HTTP: запрос CONNECT до сервера Telegram. Успехом считаем только
    # код 200: обычные веб-серверы (Windows HTTP.sys, Steam) тоже
    # отвечают по протоколу HTTP, но прокси они не заменяют.
    auth = b''
    if user:
        token = base64.b64encode(f"{user}:{pwd}".encode('utf-8'))
        auth = b'Proxy-Authorization: Basic ' + token + b'\r\n'
    try:
        with socket.create_connection((host, port), timeout) as s:
            s.sendall(
                b'CONNECT 149.154.167.51:443 HTTP/1.1\r\n'
                b'Host: 149.154.167.51:443\r\n'
                + auth +
                b'\r\n'
            )
            data = s.recv(64)
        if data.startswith(b'HTTP/'):
            status = data.split(b'\r\n', 1)[0]
            if b' 200' in status:
                return 'http'
            if b' 407' in status:
                print(f"[Бэкенд] Порт {port}: прокси требует логин и "
                      "пароль (укажите их в proxy.txt)")
    except OSError:
        pass
    return None


def discover_local_proxies(max_workers=16):
    """Ищет локальные прокси VPN-клиента и определяет их протокол.

    Список портов берём у системы, а протокол — рукопожатием. Поэтому
    это работает с любым VPN-клиентом, а не только с теми, чьи порты мы
    угадали заранее. Порты проверяются параллельно: по очереди это
    занимает под двадцать секунд, а вместе — доли секунды.
    """
    ports = list_listening_ports()
    if not ports:
        return []

    found = []
    workers = min(max_workers, len(ports))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for port, ptype in zip(ports, pool.map(detect_proxy_protocol, ports)):
            if ptype:
                found.append((port, ptype))
    return found


def modes_from_proxy_string(server):
    """Разбирает строку системного прокси в список режимов.

    Формат бывает двух видов: «127.0.0.1:10808» — один прокси на всё,
    либо «http=127.0.0.1:10809;socks=127.0.0.1:10808» — разные порты по
    протоколам. Второй вид тоже встречается, поэтому поддерживаем оба.
    """
    server = (server or '').strip()
    if not server:
        return []

    if '=' not in server:
        _, _, port = server.rpartition(':')
        if not port.isdigit():
            return []
        # Один порт обычно принимает и HTTP, и SOCKS5 — пробуем оба.
        return [mode_slug(int(port), 'http'),
                mode_slug(int(port), 'socks5')]

    found = []
    for item in server.split(';'):
        item = item.strip()
        if not item:
            continue
        if '=' not in item:
            # Смешанный формат: часть адресов идёт без имени протокола.
            _, _, port = item.rpartition(':')
            if port.isdigit():
                for ptype in ('http', 'socks5'):
                    mode = mode_slug(int(port), ptype)
                    if mode not in found:
                        found.append(mode)
            continue
        name, _, value = item.partition('=')
        name = name.strip().lower()
        if name in ('http', 'https'):
            ptype = 'http'
        elif name == 'socks':
            ptype = 'socks5'
        else:
            continue
        _, _, port = value.strip().rpartition(':')
        if port.isdigit():
            mode = mode_slug(int(port), ptype)
            if mode not in found:
                found.append(mode)
    return found


def proxy_port_alive(port, host='127.0.0.1', timeout=0.5):
    """Отвечает ли кто-нибудь на этом порту.

    Флаг ProxyEnable в реестре не значит, что прокси работает: VPN-клиент
    прописывает адрес при запуске и не всегда убирает при выходе. Если
    порт никто не слушает, прокси фактически выключен, и честнее сказать
    об этом сразу, чем стучаться в мёртвый адрес.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def system_proxy_raw():
    """Что прописано в Windows как прокси — даже если никуда не идёт.

    Одна строка для сообщений об ошибках: по ней понятно, включён ли
    системный прокси и какой адрес в нём стоит.
    """
    try:
        import winreg
    except ImportError:
        return ''
    path = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            enabled, _ = winreg.QueryValueEx(key, 'ProxyEnable')
            server, _ = winreg.QueryValueEx(key, 'ProxyServer')
    except OSError:
        return ''
    if not enabled:
        return ''
    return str(server or '')


def system_proxy_modes():
    """Прокси, который VPN-клиент прописал в настройки Windows.

    В режиме «proxy» клиент включает системный прокси и указывает там
    свой адрес — это самый надёжный источник: он не зависит ни от
    netstat, ни от угадывания портов. Читаем из реестра стандартным
    модулем, без запуска внешних программ (что важно для собранного
    .exe, где внешние процессы могут быть недоступны).
    """
    try:
        import winreg
    except ImportError:
        return []

    path = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            enabled, _ = winreg.QueryValueEx(key, 'ProxyEnable')
            server, _ = winreg.QueryValueEx(key, 'ProxyServer')
    except OSError:
        return []

    if not enabled:
        return []

    modes = modes_from_proxy_string(str(server))
    # Прокси прописан, но галочка могла остаться от прошлого запуска VPN.
    # Проверяем, что на порту действительно кто-то есть.
    alive = []
    for m in modes:
        parsed = parse_mode(m)
        if parsed and proxy_port_alive(parsed[1]):
            alive.append(m)
    if modes and not alive:
        print("[Бэкенд] Прокси в Windows прописан (%s), но порт не "
              "отвечает — считаем системный прокси выключенным." % server)
        return []
    return alive


def load_proxy_config():
    """Читает proxy.txt рядом с программой.

    Поддерживаются строки «port = 10808», «type = socks5», «user = логин»
    и «pass = пароль». Строки с # игнорируются, отсутствие файла — это
    нормальная ситуация, а не ошибка.
    """
    path = os.path.join(paths.app_dir(), config.PROXY_CONFIG_NAME)
    cfg = {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                cfg[key.strip().lower()] = value.strip()
    except OSError:
        pass

    PROXY_AUTH['user'] = cfg.get('user', '')
    PROXY_AUTH['pass'] = cfg.get('pass', '')
    return cfg


def proxy_config_modes():
    """Способ подключения, заданный вручную в proxy.txt.

    Если он указан — пробуем его первым. Это и ручной обход автопоиска,
    и способ жёстко задать порт, когда автоматика не справляется.
    """
    cfg = load_proxy_config()
    port, ptype = cfg.get('port', ''), cfg.get('type', '')
    if not port or not ptype:
        return []
    try:
        return [mode_slug(int(port), ptype.lower())]
    except ValueError:
        print(f"[Бэкенд] В {config.PROXY_CONFIG_NAME} некорректный port={port!r}")
        return []


