"""Значки, которые рисуются кодом, а не берутся из файлов.

Готовые PNG пришлось бы таскать за собой и масштабировать, а на панели
поверх видео нужны чёткие светлые глифы любого размера. Поэтому значки
рисуются QPainter-ом по описанию и кешируются: перерисовывать один и тот
же значок на каждый кадр незачем.

Модуль знает только про Qt и про цвета темы: ни окон, ни Telegram здесь
нет.
"""
import ctypes
import math

from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (
    QColor, QIcon, QImage, QPainter, QPen, QPixmap, QPolygon, QPolygonF)

from ..core import paths, theme

_ICON_CACHE = {}


def get_icon(kind):
    if kind in _ICON_CACHE:
        return _ICON_CACHE[kind]
    pm = QPixmap(24, 24)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor(232, 232, 232)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    if kind == 'prev':
        p.drawRect(5, 6, 3, 12)
        p.drawPolygon(QPolygon([QPoint(19, 6), QPoint(19, 18), QPoint(9, 12)]))
    elif kind == 'next':
        p.drawRect(16, 6, 3, 12)
        p.drawPolygon(QPolygon([QPoint(5, 6), QPoint(5, 18), QPoint(15, 12)]))
    elif kind == 'rew':
        p.drawPolygon(QPolygon([QPoint(12, 6), QPoint(12, 18), QPoint(4, 12)]))
        p.drawPolygon(QPolygon([QPoint(20, 6), QPoint(20, 18), QPoint(12, 12)]))
    elif kind == 'ff':
        p.drawPolygon(QPolygon([QPoint(4, 6), QPoint(4, 18), QPoint(12, 12)]))
        p.drawPolygon(QPolygon([QPoint(12, 6), QPoint(12, 18), QPoint(20, 12)]))
    elif kind == 'play':
        p.drawPolygon(QPolygon([QPoint(8, 5), QPoint(8, 19), QPoint(19, 12)]))
    elif kind == 'pause':
        p.drawRect(6, 5, 4, 14)
        p.drawRect(14, 5, 4, 14)
    elif kind == 'vol':
        p.drawPolygon(QPolygon([QPoint(4, 9), QPoint(8, 9), QPoint(13, 5),
                                QPoint(13, 19), QPoint(8, 15), QPoint(4, 15)]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color, 2))
        p.drawArc(QRectF(13, 8, 9, 9), -50 * 16, 100 * 16)
    elif kind == 'mute':
        p.drawPolygon(QPolygon([QPoint(4, 9), QPoint(8, 9), QPoint(13, 5),
                                QPoint(13, 19), QPoint(8, 15), QPoint(4, 15)]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color, 2))
        p.drawLine(16, 9, 22, 15)
        p.drawLine(22, 9, 16, 15)
    elif kind == 'fs':
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color, 2))
        p.drawLine(4, 9, 4, 4); p.drawLine(4, 4, 9, 4)
        p.drawLine(15, 4, 20, 4); p.drawLine(20, 4, 20, 9)
        p.drawLine(20, 15, 20, 20); p.drawLine(20, 20, 15, 20)
        p.drawLine(9, 20, 4, 20); p.drawLine(4, 20, 4, 15)
    elif kind == 'quality':
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color, 2))
        p.drawRect(4, 4, 16, 16)
        p.drawLine(4, 10, 20, 10)
        p.drawLine(10, 4, 10, 10)
    elif kind == 'pip':
        # Рамка экрана и залитое окошко в углу — узнаваемый значок PiP.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color, 2))
        p.drawRect(3, 5, 18, 14)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(12, 12, 8, 6)
    elif kind == 'list':
        # Три строки с точками — узнаваемый значок списка.
        for y in (7, 12, 17):
            p.drawRect(9, y - 1, 11, 2)
            p.drawEllipse(QPoint(5, y), 2, 2)
    elif kind == 'close':
        # Крестик рисуем линиями, а не буквой «✕»: текст на кнопке в
        # стиле панели окрашивался в цвет фона, и кнопка выглядела пустой.
        # Линии не зависят ни от шрифта, ни от правил оформления.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color, 2.4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(7, 7, 17, 17)
        p.drawLine(17, 7, 7, 17)
    p.end()
    icon = QIcon(pm)
    _ICON_CACHE[kind] = icon
    return icon


# Цвета кружков — из палитры аватарок Telegram.
_AVATAR_COLORS = ('#e17076', '#7bc862', '#e5ca77', '#65aadd', '#a695e7',
                  '#ee7aae', '#6ec9cb', '#faa774')


def get_avatar_icon(name, size=28):
    """Кружок с первой буквой названия — как аватарки в Telegram.

    Цвет выбирается по самому названию, поэтому у одного канала он
    всегда один и тот же, а у соседних — разный.
    """
    letter = (name or '?').strip()[:1].upper() or '?'
    key = ('avatar', letter, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    color = _AVATAR_COLORS[sum(ord(c) for c in (name or '?'))
                           % len(_AVATAR_COLORS)]
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(0, 0, size - 1, size - 1)
    font = p.font()
    font.setPixelSize(max(8, int(size * 0.5)))
    font.setBold(True)
    p.setFont(font)
    p.setPen(QPen(QColor('#ffffff')))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, letter)
    p.end()
    icon = QIcon(pm)
    _ICON_CACHE[key] = icon
    return icon


# Цвета состояния связи: синий — подключено, красный — нет.
CONNECTION_OK_COLOR = '#2ea6ff'
CONNECTION_BAD_COLOR = '#e5534b'

# Толщина обводки значка связи — долей от его размера. Кольцо рисуется
# акцентным цветом темы и кладётся серединой на край кружка, поэтому
# половина толщины заходит на заливку, половина выступает наружу.
# Ниже точки толщина не опускается: иначе у мелкого значка обводка
# пропадёт совсем.
RING_WIDTH_SHARE = 0.055

# Высота строки состояния главного окна. У обычной она около 21 точки,
# а здесь только короткие сообщения, поэтому берём заметно меньше. При
# 13 точках и шрифте 12 надпись занимает ровно середину полосы.
STATUS_BAR_HEIGHT = 13

# Толщина полос прокрутки из таблицы стилей: 10 точек плюс отступы по 2.
# Значок связи стоит над нижней полосой списка каналов, поэтому её высота
# нужна и в расчёте положения.
SCROLLBAR_THICKNESS = 14

# Размер значка для трея. Шину отдаём крупную картинку, а уменьшает её
# уже система: так значок остаётся чётким при любом масштабе экрана.
TRAY_ICON_SIZE = 128


def cursor_size():
    """Размер системного курсора в точках.

    От него считаем размер значка связи в углу окна: на мониторах с
    крупным курсором значок иначе оказался бы крошечным.
    """
    try:
        # SM_CXCURSOR = 13
        size = int(ctypes.windll.user32.GetSystemMetrics(13))
    except Exception:
        size = 0
    return size if size > 0 else 32


def app_glyph(size, color=None):
    """Знак из иконки программы, без её синего кружка.

    Иконка — синий кружок с белым самолётиком. Для трея нужен тот же
    знак, но на своём фоне, поэтому оставляем только светлые пиксели,
    а синий кружок делаем прозрачным. Цвет знака задаётся параметром:
    None — белый, как в иконке, иначе знак перекрашивается в него с
    сохранением прозрачности краёв. Результат кешируется: обход
    пикселей разовый, но повторять его на каждую смену состояния незачем.
    """
    key = ('glyph', int(size), str(color))
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    src = paths.load_app_icon()
    if src is None:
        _ICON_CACHE[key] = None
        return None
    img = src.pixmap(int(size), int(size)).toImage().convertToFormat(
        QImage.Format.Format_ARGB32)
    tint = QColor(color) if color else None
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() == 0:
                continue
            # Знак белый, кружок под ним синий. Всё, что заметно темнее
            # белого, — это фон иконки, и его надо убрать.
            if min(c.red(), c.green(), c.blue()) < 170:
                img.setPixelColor(x, y, QColor(0, 0, 0, 0))
            elif tint is None:
                img.setPixelColor(x, y, QColor(255, 255, 255, c.alpha()))
            else:
                img.setPixelColor(x, y, QColor(
                    tint.red(), tint.green(), tint.blue(), c.alpha()))
    _ICON_CACHE[key] = img
    return img


def connection_colors():
    """Цвета значка связи из общей палитры плеера.

    Фон — основной цвет темы, знаки (галочка, крестик, кольцо) —
    акцентный. Так значок выглядит частью оформления, а не отдельным
    элементом со своими цветами.
    """
    try:
        pal = theme.theme()
        return str(pal.get('bg') or '#17212b'), str(pal.get('accent')
                                                    or '#5288c1')
    except Exception:
        return '#17212b', '#5288c1'


def _ring_line(painter, box, color, thickness):
    """Обводка кружка одной акцентной линией.

    Никакого градиента и размытия: обводка — это ровное кольцо у самого
    края кружка, набранное акцентным цветом темы в полную силу.

    Штрих кладём серединой точно на контур кружка, поэтому кольцо
    наполовину заходит на заливку и наполовину выступает наружу. Так
    стык обводки с заливкой получается плотным, без светлой щели.

    Рисуется это в увеличенном слое (см. paint_connection): при
    уменьшении сглаживание даёт ровный край без ступенек на любом
    размере значка.
    """
    c = QColor(color)
    c.setAlpha(255)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(c, float(thickness), Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.MiterJoin))
    painter.drawEllipse(box)


def _draw_connection_mark(painter, size, state, angle):
    """Рисует галочку, крестик или кольцо подключения.

    Отдельной функцией, потому что картинку собираем увеличенной и потом
    уменьшаем: у линий и дуг появляются ровные края вместо зубцов.
    """
    ok = (state == 'ok')
    # Знак — акцентным цветом темы, как и обводка кружка.
    _, accent = connection_colors()
    if state == 'connecting':
        # Кольцо из двух частей: бледная дорожка показывает полный круг, а
        # по ней бежит яркая дуга. Обе строятся из одного и того же
        # прямоугольника, поэтому центрированы одинаково.
        radius = size * 0.27
        rect = QRectF(size * 0.5 - radius, size * 0.5 - radius,
                      radius * 2, radius * 2)
        thick = max(2.0, size * 0.10)
        track = QColor(accent)
        track.setAlpha(110)
        painter.setPen(QPen(track, thick,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(rect)
        # Три четверти окружности. Углы в Qt считаются в шестнадцатых
        # долях градуса и против часовой стрелки, поэтому растущий angle
        # крутит дугу по часовой.
        painter.setPen(QPen(QColor(accent), thick,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, int((90 + angle) * 16), int(-280 * 16))
        return

    painter.setPen(QPen(QColor(accent), max(2.0, size * 0.11),
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    if ok:
        painter.drawPolyline(QPolygonF([
            QPointF(size * 0.26, size * 0.52),
            QPointF(size * 0.43, size * 0.69),
            QPointF(size * 0.75, size * 0.31),
        ]))
    else:
        painter.drawLine(QPointF(size * 0.33, size * 0.33),
                         QPointF(size * 0.67, size * 0.67))
        painter.drawLine(QPointF(size * 0.67, size * 0.33),
                         QPointF(size * 0.33, size * 0.67))


def paint_connection(painter, size, state, angle=0, glyph=False):
    """Рисует кружок состояния связи.

    state — 'ok' (связь есть), 'off' (связи нет) или 'connecting'
    (идёт подключение). У подключения вместо знака вращающееся кольцо с
    разрывом, поэтому angle задаёт поворот в градусах.

    glyph=True добавляет знак программы по центру — так выглядит иконка
    в трее, чтобы оставаться узнаваемой, как ярлык и установщик.
    """
    ok = (state == 'ok')
    size = int(size)
    inset = max(1, int(size * 0.05))
    box = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)

    # Тень под кружком убрана: она добавляла по краю свою кайму, и вместе
    # с обводкой край читался как грязный, а не размытый.

    # Кружок в цвете основы темы, знак — в акцентном. Красный фон при
    # обрыве убран: состояние читается по крестику, а значок остаётся
    # частью оформления, как и просили.
    bg_color, accent = connection_colors()
    # Рисуем в увеличенном слое и уменьшаем: у линий и заливки получаются
    # ровные края вместо зубцов.
    scale = 4
    big = size * scale
    layer = QPixmap(big, big)
    layer.fill(Qt.GlobalColor.transparent)
    lp = QPainter(layer)
    lp.setRenderHint(QPainter.RenderHint.Antialiasing)
    lp.scale(scale, scale)

    # Сначала заливаем сам кружок: без этого от значка осталась бы одна
    # обводка на прозрачном фоне.
    lp.setPen(Qt.PenStyle.NoPen)
    lp.setBrush(QColor(bg_color))
    lp.drawEllipse(box)
    # Обводка — одно ровное акцентное кольцо по краю кружка. Ни
    # градиента, ни размытия: только чистый цвет темы.
    #
    # Толщину берём долей от размера и не даём ей упасть ниже точки: на
    # мелком значке постоянная толщина съедала бы почти весь кружок, а на
    # крупном выглядела бы ниткой.
    ring = max(1.0, size * RING_WIDTH_SHARE)
    _ring_line(lp, box, accent, ring)
    lp.end()
    lp = None

    # Никакого blur'а поверх: край обводки и так ровный, а лишний проход
    # только съедал время на каждой перерисовке значка.
    painter.drawPixmap(0, 0, layer.scaled(
        size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation))

    mark = app_glyph(int(size * 0.72), accent) if glyph else None
    if mark is not None:
        side = mark.width()
        painter.drawImage(int((size - side) / 2), int((size - side) / 2), mark)
        return

    # Знак рисуем в четыре раза крупнее и уменьшаем: у линий и дуг
    # получаются ровные края вместо зубцов, особенно на мелком значке.
    scale = 4
    big = size * scale
    layer = QPixmap(big, big)
    layer.fill(Qt.GlobalColor.transparent)
    lp = QPainter(layer)
    lp.setRenderHint(QPainter.RenderHint.Antialiasing)
    lp.scale(scale, scale)
    lp.translate(0, 0)
    _draw_connection_mark(lp, size, state, angle)
    lp.end()
    lp = None
    painter.drawPixmap(0, 0, layer.scaled(
        size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation))


def connection_pixmap(size, state, angle=0, glyph=False):
    """Кружок состояния связи отдельной картинкой.

    Результат кешируется: в обычной работе рисуем один и тот же значок
    сотни раз (каждая анимация подключения — новая картинка), а сборка
    слоя с размытием стоит недёшево. Ключ включает цвета темы: при
    смене темы кэш пересоздаётся сам собой.
    """
    size = int(size)
    angle = int(angle) % 360
    bg_color, accent = connection_colors()
    key = ('cpix', size, state, angle, bool(glyph), bg_color, accent)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint_connection(p, size, state, angle, glyph)
    p.end()
    _ICON_CACHE[key] = pm
    return pm


def connection_icon(connected, size=64, glyph=False):
    """Кружок состояния связи: связь есть или связи нет.

    Для трея glyph=True — знак программы: иконка остаётся узнаваемой, а
    состояние видно по цвету кружка. Без него рисуется галочка или
    крестик — так выглядит значок в углу окна.
    """
    state = 'ok' if connected else 'off'
    key = ('conn', state, int(size), bool(glyph))
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    icon = QIcon(connection_pixmap(int(size), state, 0, glyph))
    _ICON_CACHE[key] = icon
    return icon

