# -*- coding: utf-8 -*-
"""Проверка обновлений и скачивание новой версии с GitHub.

Репозиторий публичный, поэтому токен НЕ нужен: чтение релизов доступно
без авторизации. Токен в .exe встраивать нельзя ни в коем случае — он
извлекается из бинарника, и тогда кто угодно сможет писать в репозиторий.

Формат релиза, на который рассчитан модуль:

    тег:   v2.5.0            (числа, без буквы — так принято в semver)
    имя:   Tegr 3.1.0d      (буква — схема загрузчика, в имени)
    файлы: Tegr_Setup.exe
           TGPlayer_Setup.exe (прежнее имя, нужно старым сборкам)
           SHA256.txt        (одна строка: хеш установщика)

Канал обновлений:
    stable — только релизы, где в теле описания есть строка STABLE
    beta   — любой релиз

Зачем канал: за время работы были сборки, ломавшие загрузку. Выкладывать
такую сразу всем нельзя — сначала на себя (beta), потом, убедившись, —
в stable.
"""
import hashlib
import json
import os
import re
import urllib.error
import urllib.request

from . import version

# Адрес репозитория. Меняется здесь и только здесь.
#
# Благодаря этой строке и текущему имени программы адрес совпадал с
# репозиторием: yolorik/tgplayer. Переименовывать репозиторий не нужно —
# имя репозитория и имя программы независимы.
REPO = 'yolorik/tgplayer'
API_LATEST = f'https://api.github.com/repos/{REPO}/releases'
RELEASES_PAGE = f'https://github.com/{REPO}/releases'

# Имя файла установщика в релизе.
#
# Список, а не одно имя: программа искала установщик под именем
# «TGPlayer_Setup.exe», а после переименования в Tegr он стал
# «Tegr_Setup.exe». Старые релизы в репозитории остались со старым именем
# файла, поэтому оба принимаются: так проверка не спотыкается о них.
SETUP_ASSETS = ('Tegr_Setup.exe', 'TGPlayer_Setup.exe')
HASH_ASSET = 'SHA256.txt'

# Сколько ждать ответа GitHub. Проверка идёт в фоне и не должна висеть:
# на медленной сети человек ждёт запуска, а не обновления.
TIMEOUT = 12.0

USER_AGENT = f'{version.APP_NAME}/{version.VERSION}'


class UpdateInfo:
    """Сведения о доступном обновлении."""

    def __init__(self, tag, title, notes, setup_url, hash_url, size, page):
        self.tag = tag
        self.title = title
        self.notes = notes
        self.setup_url = setup_url
        self.hash_url = hash_url
        self.size = size
        self.page = page

    def __repr__(self):
        return f'<UpdateInfo {self.tag} {self.size} байт>'


def parse_version(text):
    """Разбирает строку версии в (числа, буква).

    «2.5.0d» -> ((2, 5, 0), 'd')
    «v2.5.0»  -> ((2, 5, 0), '')
    Ошибки не роняют: непонятное считаем нулевой версией, чтобы проверка
    обновлений никогда не мешала запуску плеера.
    """
    if not text:
        return (0, 0, 0), ''
    text = str(text).strip().lstrip('vV')
    nums = re.findall(r'\d+', text.split('-')[0].split('+')[0])
    parts = tuple(int(x) for x in nums[:3])
    while len(parts) < 3:
        parts += (0,)
    tail = ''.join(ch for ch in text if ch.isalpha())
    return parts, tail.lower()


def is_newer(remote_tag, local_version=None):
    """Есть ли в remote_tag версия новее нашей."""
    local = local_version or version.VERSION
    r_nums, r_letter = parse_version(remote_tag)
    l_nums, l_letter = parse_version(local)
    if r_nums != l_nums:
        return r_nums > l_nums
    # Числа равны — смотрим букву схемы загрузчика: «d» новее «c».
    return r_letter > l_letter


def _get(url):
    """GET с нашим User-Agent. Возвращает (код, данные или None)."""
    req = urllib.request.Request(url, headers={
        'User-Agent': USER_AGENT,
        'Accept': 'application/vnd.github+json',
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as ex:
        # 404 — нет релизов вовсе, это не ошибка для нас.
        return ex.code, None
    except Exception:
        return 0, None


def _channel_ok(body, channel):
    """Подходит ли релиз под канал.

    stable — в описании есть отдельная строка STABLE (латиницей, в верхнем
    регистре). Такой пометки достаточно: не нужны ни draft, ни prerelease,
    а значит релиз можно опубликовать обычным путём.
    """
    if channel != 'stable':
        return True
    return bool(re.search(r'(?m)^\s*STABLE\s*$', body or '', re.IGNORECASE))


def check(channel='stable'):
    """Ищет доступное обновление.

    Возвращает (UpdateInfo или None, причина). Причину печатаем в журнал:
    без неё непонятно, «нет обновлений» или «GitHub не ответил».
    """
    code, raw = _get(API_LATEST)
    if code != 200 or raw is None:
        if code == 404:
            return None, 'в репозитории нет ни одного релиза'
        return None, f'GitHub не ответил (код {code})'

    try:
        releases = json.loads(raw.decode('utf-8'))
    except Exception:
        return None, 'ответ GitHub не разобрать'

    if not isinstance(releases, list):
        return None, 'неожиданный формат ответа GitHub'

    for rel in releases:
        if rel.get('draft'):
            continue
        tag = rel.get('tag_name') or ''
        body = rel.get('body') or ''
        if not _channel_ok(body, channel):
            continue
        if not is_newer(tag):
            continue

        # Есть обновление — ищем в нём установщик и хеш.
        setup_url = hash_url = None
        setup_name = ''
        size = 0
        for asset in rel.get('assets') or []:
            name = asset.get('name') or ''
            if name in SETUP_ASSETS:
                # Нужно имя первого попавшегося файла: раньше оно
                # совпадало с SETUP_ASSET, но теперь имён два, и брать
                # надо то, которое реально есть в релизе.
                setup_name = name
                setup_url = asset.get('browser_download_url')
                size = int(asset.get('size') or 0)
            elif name == HASH_ASSET:
                hash_url = asset.get('browser_download_url')
        if not setup_url:
            return None, f'релиз {tag} без установщика'

        info = UpdateInfo(
            tag=tag,
            title=rel.get('name') or tag,
            notes=body,
            setup_url=setup_url,
            hash_url=hash_url,
            size=size,
            page=rel.get('html_url') or RELEASES_PAGE,
        )
        info.setup_name = setup_name
        return info, ''

    return None, 'новых версий нет'


def download(info, dest_path, progress=None):
    """Скачивает установщик с показом хода и сверяет хеш.

    progress(получено_байт, всего_байт) вызывается по ходу загрузки.
    Возвращает (успех, сообщение).

    Читаем потоком и кусками, а не целиком в память: установщик весит
    70 МБ, и без промежуточных отчётов окно прогресса стояло бы на нуле
    до самого конца, а потом сразу закрывалось.
    """
    req = urllib.request.Request(info.setup_url, headers={
        'User-Agent': USER_AGENT,
    })
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as ex:
        return False, f'не удалось скачать установщик (код {ex.code})'
    except Exception as ex:
        return False, f'не удалось скачать установщик: {ex}'

    total = info.size or 0
    digest = hashlib.sha256()
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    try:
        with resp, open(dest_path, 'wb') as f:
            while True:
                block = resp.read(64 * 1024)
                if not block:
                    break
                f.write(block)
                digest.update(block)
                if progress is not None:
                    try:
                        progress(len(block), total)
                    except Exception:
                        pass        # отчёт не должен ломать загрузку
    except Exception as ex:
        try:
            os.remove(dest_path)
        except Exception:
            pass
        return False, f'загрузка оборвалась: {ex}'

    got = digest.hexdigest()

    # Хеш сверяем, только если он опубликован: без него скачивание всё
    # равно лучше, чем ничего, но проверить целостность нечем.
    if info.hash_url:
        hcode, hraw = _get(info.hash_url)
        if hcode == 200 and hraw:
            text = hraw.decode('utf-8', 'replace')
            m = re.search(r'\b([0-9a-fA-F]{64})\b', text)
            if m:
                want = m.group(1).lower()
                if want != got:
                    try:
                        os.remove(dest_path)
                    except Exception:
                        pass
                    return False, ('скачанный файл повреждён: хеш не '
                                   f'совпал ({got[:12]}… вместо {want[:12]}…)')
    return True, got
