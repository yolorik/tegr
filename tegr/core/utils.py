"""Мелочи, которые нужны в разных местах программы.

Здесь только чистые функции: перевод величин, форматы для человека,
проверка портов. Ни окон, ни Telegram — поэтому модуль можно звать
откуда угодно и легко проверять по отдельности.
"""
import socket


def format_time(ms):
    """Миллисекунды -> «ММ:СС», а для длинных роликов «ЧЧ:ММ:СС»."""
    ms = int(ms)
    if ms < 0:
        ms = 0
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def fmt_speed(bps):
    """Человекочитаемый формат скорости загрузки (байты/сек)."""
    if bps >= 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f} МБ/с"
    if bps >= 1024:
        return f"{bps / 1024:.0f} КБ/с"
    return f"{bps:.0f} Б/с"


def fmt_size(size):
    """Человекочитаемый размер файла: «1,4 ГБ», «812 МБ», «0 Б»."""
    try:
        size = float(size)
    except (TypeError, ValueError):
        return ''
    if size <= 0:
        return '0 Б'
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.2f} ГБ"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.0f} МБ"
    if size >= 1024:
        return f"{size / 1024:.0f} КБ"
    return f"{size:.0f} Б"


def port_open(host, port, timeout=0.25):
    """Быстрая проверка, слушает ли кто-нибудь порт на localhost."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def mode_slug(port, ptype):
    """Строка-режим для прокси: 'proxy:http:10809'."""
    return f"proxy:{ptype}:{port}"


def normalize_phone(text):
    """Приводит номер к виду, который понимает Telegram: плюс и цифры.

    Пользователь вводит номер как привык — с пробелами, скобками и
    дефисами; Telegram такие не принимает.
    """
    digits = ''.join(ch for ch in (text or '') if ch.isdigit())
    return ('+' + digits) if digits else ''
