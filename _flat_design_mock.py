"""Макеты «плоского» интерфейса Tegr — без многослойных карточек.

Рисует три состояния главного окна (широкое, среднее, узкое) на одной
подложке: весь контент лежит прямо на фоне, а разделяют его тонкие
линии в 1 px. Запуск: python _flat_design_mock.py
"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetrics, QImage, QPainter, QPen)
from PyQt6.QtWidgets import QApplication

# --- палитра ---------------------------------------------------------
BG = '#141a20'
TEXT = '#e9e5de'
DIM = '#8d959e'
MUTED = '#5d6670'
DIV = '#273039'
ACCENT = '#f28c4b'
ACCENT_SOFT = QColor(242, 140, 75, 26)
FIELD = '#1a222a'
GREEN = '#5fbf7a'
AMBER = '#f2b84b'
HANDLE = '#3a444e'
AVATARS = ['#3d5163', '#5a4a3c', '#3f5148', '#4d4457',
           '#4a3d3d', '#3a4a3f', '#54484a', '#414a58']

APP_FONT = 'Segoe UI'


def load_fonts():
    fonts = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
    for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf'):
        path = os.path.join(fonts, name)
        if os.path.exists(path):
            QFontDatabase.addApplicationFont(path)


def fnt(size, bold=False, semibold=False):
    f = QFont(APP_FONT)
    f.setPixelSize(size)
    if bold:
        f.setWeight(QFont.Weight.Bold)
    elif semibold:
        f.setWeight(QFont.Weight.DemiBold)
    return f


def text(p, rect, value, font, color, align=Qt.AlignmentFlag.AlignVCenter
         | Qt.AlignmentFlag.AlignLeft):
    p.setFont(font)
    p.setPen(QColor(color))
    p.drawText(QRectF(*rect), int(align), value)


def elide(font, value, width):
    return QFontMetrics(font).elidedText(
        value, Qt.TextElideMode.ElideRight, int(width))


def line(p, x1, y, x2, color=DIV):
    p.fillRect(QRectF(x1, y, max(1, x2 - x1), 1), QColor(color))


def vline(p, x, y1, y2, color=DIV):
    p.fillRect(QRectF(x, y1, 1, max(1, y2 - y1)), QColor(color))


def dot(p, x, y, r, color):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))


def scrollbar(p, x, y, h, color=HANDLE):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(x, y, 6, h), 3, 3)


def magnifier(p, x, y, color=DIM):
    p.setPen(QPen(QColor(color), 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(x, y, 10, 10))
    p.drawLine(QPointF(x + 8.4, y + 8.4), QPointF(x + 12, y + 12))


def check(p, x, y, color):
    """Галочка линиями: символ ✓ есть не во всех шрифтах."""
    p.setPen(QPen(QColor(color), 1.6))
    p.drawLine(QPointF(x + 2, y + 9), QPointF(x + 6, y + 13))
    p.drawLine(QPointF(x + 6, y + 13), QPointF(x + 13, y + 4))


def arrow_down(p, cx, cy, color=DIM, size=5):
    p.setPen(QPen(QColor(color), 1.5))
    p.drawLine(QPointF(cx, cy - size), QPointF(cx, cy + size))
    p.drawLine(QPointF(cx - size + 1, cy + size - 3),
               QPointF(cx, cy + size))
    p.drawLine(QPointF(cx + size - 1, cy + size - 3),
               QPointF(cx, cy + size))


def chevron_left(p, cx, cy, color=DIM, size=5):
    p.setPen(QPen(QColor(color), 1.6))
    p.drawLine(QPointF(cx + size - 1, cy - size),
               QPointF(cx - size + 1, cy))
    p.drawLine(QPointF(cx - size + 1, cy), QPointF(cx + size - 1, cy + size))


def gear(p, x, y, color=DIM):
    p.setPen(QPen(QColor(color), 1.3))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(x + 3, y + 3, 8, 8))
    for dx, dy in ((7, 0), (7, 14), (0, 7), (14, 7),
                   (2, 2), (12, 2), (2, 12), (12, 12)):
        p.drawLine(QPointF(x + 7, y + 7), QPointF(x + dx - 1, y + dy - 1))


def list_icon(p, x, y, color=DIM):
    p.setPen(QPen(QColor(color), 1.4))
    for i in range(3):
        p.drawLine(QPointF(x, y + i * 5), QPointF(x + 14, y + i * 5))


def avatar(p, x, y, size, letter, color):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(x, y, size, size))
    text(p, (x, y - 1, size, size), letter,
         fnt(int(size * 0.46), semibold=True), '#f2f4f6',
         Qt.AlignmentFlag.AlignCenter)


def topbar(p, w, brand='Tegr', folder='Все каналы'):
    p.fillRect(QRectF(0, 0, w, 56), QColor(BG))
    text(p, (20, 0, 70, 56), brand, fnt(19, bold=True), TEXT)
    # селектор папки — просто текст с шевроном, без «пилюли»
    ff = fnt(13)
    text(p, (104, 0, 160, 56), folder, ff, DIM)
    fx = 104 + QFontMetrics(ff).horizontalAdvance(folder) + 9
    p.setPen(QPen(QColor(MUTED), 1.3))
    p.drawLine(QPointF(fx, 27), QPointF(fx + 4, 31))
    p.drawLine(QPointF(fx + 4, 31), QPointF(fx + 8, 27))
    # статус подключения
    dot(p, w - 196, 28, 3.2, GREEN)
    text(p, (w - 186, 0, 100, 56), 'Подключено', fnt(12), DIM)
    list_icon(p, w - 74, 20, MUTED)
    gear(p, w - 40, 19, MUTED)
    line(p, 0, 55, w, DIV)


def statusbar(p, w, h):
    line(p, 0, h - 28, w, DIV)
    text(p, (20, h - 28, 320, 28), 'Готово · кэш 1,2 ГБ', fnt(11), MUTED)
    text(p, (w - 90, h - 28, 70, 28), '3.7.0', fnt(11), MUTED,
         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)


def search_row(p, x, y, w, placeholder, arrow=True, back=False):
    """Строка поиска без рамки: только текст, значок и линия снизу."""
    tx = x
    if back:
        chevron_left(p, x + 12, y + 24, DIM, 7)
        tx = x + 30
    magnifier(p, tx + 2, y + 18)
    text(p, (tx + 22, y, w - 90, 48), placeholder, fnt(13), MUTED)
    if arrow:
        arrow_down(p, x + w - 18, y + 24, DIM, 5)
    line(p, x, y + 47, x + w, DIV)


def channel_rows(p, x, y, w, rows, selected=0, scroll=True):
    row_h = 62
    for i, (name, subtitle) in enumerate(rows):
        top = y + i * row_h
        if i == selected:
            p.fillRect(QRectF(x, top, w - 12, row_h), ACCENT_SOFT)
            p.fillRect(QRectF(x, top, 2, row_h), QColor(ACCENT))
        avatar(p, x + 16, top + 16, 30, name[0].upper(),
               AVATARS[i % len(AVATARS)])
        nf = fnt(13, semibold=(i == selected))
        text(p, (x + 58, top + 11, w - 90, 22),
             elide(nf, name, w - 92), nf,
             TEXT if i == selected else TEXT)
        text(p, (x + 58, top + 32, w - 90, 18),
             elide(fnt(11), subtitle, w - 92), fnt(11), MUTED)
        line(p, x + 16, top + row_h - 1, x + w - 12, DIV)
    if scroll:
        scrollbar(p, x + w - 9, y + 24, 210)


def video_rows(p, x, y, w, rows, selected=0, scroll=True, row_h=44):
    for i, (title, meta, state) in enumerate(rows):
        top = y + i * row_h
        if i == selected:
            p.fillRect(QRectF(x, top, w - 12, row_h), ACCENT_SOFT)
            p.fillRect(QRectF(x, top, 2, row_h), QColor(ACCENT))
        check_color = ACCENT if state != 'watched' else MUTED
        check(p, x + 12, top + (row_h - 18) // 2, check_color)
        tf = fnt(13)
        text(p, (x + 34, top, w - 200, row_h),
             elide(tf, title, w - 210), tf, TEXT)
        meta_w = 130 if state == 'downloading' else 150
        meta_right = x + w - (44 if state == 'downloading' else 22)
        text(p, (meta_right - meta_w, top, meta_w, row_h),
             elide(fnt(12), meta, meta_w - 2), fnt(12), DIM,
             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        if state == 'downloading':
            # тонкая полоса прогресса прямо в строке, без карточки
            ty = top + row_h - 3
            p.fillRect(QRectF(x + 34, ty, w - 46, 2), QColor(DIV))
            p.fillRect(QRectF(x + 34, ty, (w - 46) * 0.42, 2),
                       QColor(ACCENT))
            arrow_down(p, x + w - 20, top + row_h / 2, ACCENT, 5)
        line(p, x + 34, top + row_h - 1, x + w - 12, DIV)
    if scroll:
        scrollbar(p, x + w - 9, y + 34, 260)


def tabs(p, x, y, w, items, active, underline=True):
    tx = x
    for label in items:
        f = fnt(13, semibold=(label == active))
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(label)
        text(p, (tx, y, tw + 4, 44), label, f,
             TEXT if label == active else DIM)
        if label == active and underline:
            p.fillRect(QRectF(tx, y + 42, tw, 2), QColor(ACCENT))
        tx += tw + 34
    line(p, x - 4, y + 43, x + w + 4, DIV)


def download_rows(p, x, y, w, rows, scroll=True):
    row_h = 58
    for i, (title, meta, pct, state) in enumerate(rows):
        top = y + i * row_h
        tf = fnt(13)
        text(p, (x, top + 8, w - 90, 20),
             elide(tf, title, w - 94), tf, TEXT)
        text(p, (x, top + 28, w - 90, 16),
             elide(fnt(11), meta, w - 94), fnt(11), MUTED)
        color = AMBER if state == 'paused' else (
            MUTED if state == 'done' else ACCENT)
        text(p, (x + w - 60, top + 8, 56, 20), pct, fnt(12), color,
             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        if state != 'done':
            ty = top + row_h - 4
            p.fillRect(QRectF(x, ty, w, 2), QColor(DIV))
            frac = 0.54 if state == 'busy' else 0.31
            p.fillRect(QRectF(x, ty, w * frac, 2), QColor(color))
        line(p, x, top + row_h - 1, x + w, DIV)
    if scroll:
        scrollbar(p, x + w + 3, y + 20, 230)


def draw_wide(path):
    w, h = 1280, 820
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(BG))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    topbar(p, w)
    left, right = 340, 830
    vline(p, left, 56, h - 28)
    vline(p, right, 56, h - 28)

    # --- каналы ---
    search_row(p, 0, 56, left - 12, 'Поиск каналов', arrow=False)
    channels = [
        ('Клан Сопрано (гоблин)', '46 видео · 12 непросмотренных'),
        ('ANiMEON — Аниме онлайн [Бункер]', '128 видео'),
        ('Afro-American Side Of The Moon', '31 видео'),
        ('Dotka immun', '17 видео'),
        ('eternal classic', '9 видео'),
        ('Games WebM', '74 видео'),
        ('H2 | HysteriaVPN', '22 видео'),
        ('Initial D | инициал ди | аниме', '64 видео'),
        ('DeadP47', '58 видео'),
        ('Exilenova+', '15 видео'),
    ]
    channel_rows(p, 0, 105, left, channels)

    # --- видео ---
    search_row(p, left + 16, 56, right - left - 28, 'Поиск видео')
    videos = [
        ('Сеанс 01 серия 01', '57:31 · 1348 МБ', 'watched'),
        ('Сеанс 01 серия 02', '47:39 · 1117 МБ', 'watched'),
        ('Сеанс 01 серия 03', '42:55 · 1006 МБ', 'unwatched'),
        ('Сеанс 01 серия 04', '50:53 · 1192 МБ', 'downloading'),
        ('Сеанс 01 серия 05', '54:20 · 1273 МБ', 'watched'),
        ('Сеанс 01 серия 06', '47:48 · 1121 МБ', 'watched'),
        ('Сеанс 01 серия 07', '49:24 · 1157 МБ', 'watched'),
        ('Сеанс 01 серия 08', '46:52 · 1098 МБ', 'watched'),
        ('Сеанс 01 серия 09', '49:32 · 752 МБ', 'watched'),
        ('Сеанс 01 серия 10', '50:39 · 869 МБ', 'watched'),
        ('Сеанс 01 серия 11', '47:26 · 718 МБ', 'watched'),
        ('Сеанс 01 серия 12', '45:23 · 1063 МБ', 'watched'),
    ]
    video_rows(p, left, 105, right - left, videos)

    # --- история / загрузки ---
    tabs(p, right + 20, 56, right - 6, ['История', 'Загрузки'],
         'Загрузки')
    downloads = [
        ('Сеанс 01 серия 11', '312 / 578 МБ · 2,4 МБ/с', '54%', 'busy'),
        ('Сеанс 01 серия 12', '245 / 790 МБ · пауза', '31%', 'paused'),
        ('Сеанс 01 серия 10', '869 МБ · готово', '100%', 'done'),
        ('Сеанс 02 серия 01', '1425 МБ · в очереди', '0%', 'done'),
        ('Сеанс 02 серия 02', '1418 МБ · готово', '100%', 'done'),
    ]
    download_rows(p, right + 20, 100, w - (right + 20) - 12, downloads)

    statusbar(p, w, h)
    p.end()
    img.save(path)


def draw_medium(path):
    w, h = 1000, 820
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(BG))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    topbar(p, w)
    left = 320
    vline(p, left, 56, h - 28)

    search_row(p, 0, 56, left - 12, 'Поиск каналов', arrow=False)
    channels = [
        ('Клан Сопрано (гоблин)', '46 видео · 12 непросмотренных'),
        ('ANiMEON — Аниме онлайн [Бункер]', '128 видео'),
        ('Afro-American Side Of The Moon', '31 видео'),
        ('Dotka immun', '17 видео'),
        ('eternal classic', '9 видео'),
        ('Games WebM', '74 видео'),
        ('H2 | HysteriaVPN', '22 видео'),
        ('Initial D | инициал ди | аниме', '64 видео'),
    ]
    channel_rows(p, 0, 105, left, channels)

    search_row(p, left + 16, 56, w - left - 44, 'Поиск видео')
    videos = [
        ('Сеанс 01 серия 01', '57:31 · 1348 МБ', 'watched'),
        ('Сеанс 01 серия 02', '47:39 · 1117 МБ', 'watched'),
        ('Сеанс 01 серия 03', '42:55 · 1006 МБ', 'unwatched'),
        ('Сеанс 01 серия 04', '50:53 · 1192 МБ', 'downloading'),
        ('Сеанс 01 серия 05', '54:20 · 1273 МБ', 'watched'),
        ('Сеанс 01 серия 06', '47:48 · 1121 МБ', 'watched'),
        ('Сеанс 01 серия 07', '49:24 · 1157 МБ', 'watched'),
        ('Сеанс 01 серия 08', '46:52 · 1098 МБ', 'watched'),
        ('Сеанс 01 серия 09', '49:32 · 752 МБ', 'watched'),
        ('Сеанс 01 серия 10', '50:39 · 869 МБ', 'watched'),
    ]
    video_rows(p, left, 105, w - left, videos)

    statusbar(p, w, h)
    p.end()
    img.save(path)


def draw_narrow(path):
    w, h = 640, 820
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(BG))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    topbar(p, w, folder='Все каналы')
    tabs(p, 20, 56, w - 20, ['Каналы', 'История', 'Загрузки'], 'Каналы')
    search_row(p, 16, 100, w - 28, 'Поиск видео', back=True)
    videos = [
        ('Сеанс 01 серия 01', '57:31 · 1348 МБ', 'watched'),
        ('Сеанс 01 серия 02', '47:39 · 1117 МБ', 'watched'),
        ('Сеанс 01 серия 03', '42:55 · 1006 МБ', 'unwatched'),
        ('Сеанс 01 серия 04', '50:53 · 1192 МБ', 'downloading'),
        ('Сеанс 01 серия 05', '54:20 · 1273 МБ', 'watched'),
        ('Сеанс 01 серия 06', '47:48 · 1121 МБ', 'watched'),
        ('Сеанс 01 серия 07', '49:24 · 1157 МБ', 'watched'),
        ('Сеанс 01 серия 08', '46:52 · 1098 МБ', 'watched'),
        ('Сеанс 01 серия 09', '49:32 · 752 МБ', 'watched'),
        ('Сеанс 01 серия 10', '50:39 · 869 МБ', 'watched'),
        ('Сеанс 01 серия 11', '47:26 · 718 МБ', 'watched'),
        ('Сеанс 01 серия 12', '45:23 · 1063 МБ', 'watched'),
    ]
    video_rows(p, 0, 149, w, videos)

    statusbar(p, w, h)
    p.end()
    img.save(path)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'design_flat')
    os.makedirs(out, exist_ok=True)
    draw_wide(os.path.join(out, 'flat_wide.png'))
    draw_medium(os.path.join(out, 'flat_medium.png'))
    draw_narrow(os.path.join(out, 'flat_narrow.png'))
    print('saved:', out)
    app.quit()


if __name__ == '__main__':
    main()
