"""Оформление в духе Telegram Desktop.

Цвета взяты из тёмной темы Telegram: глубокий сине-серый фон, синий
акцент, серо-голубой второстепенный текст. Таблица стилей одна на всё
приложение — так меню, подсказки и окна, созданные позже, выглядят так
же, как основные, и не приходится раскрашивать каждый виджет.

Модуль знает только про цвета и про Qt-стили: ни окон, ни Telegram здесь
нет. Он же отвечает за масштаб интерфейса — таблица стилей пересобирается
под другой процент целиком, поэтому масштаб меняется на лету.
"""
import re

TG_THEMES = {
    'telegram': {
        'title': 'Telegram — тёмная',
        'bg': '#17212b', 'bg_deep': '#0e1621', 'panel': '#1e2a36',
        'field': '#242f3d', 'hover': '#202b36', 'select': '#2b5278',
        'accent': '#5288c1', 'accent_hi': '#5f9bd4', 'link': '#2ea6ff',
        'text': '#ffffff', 'text_dim': '#7f91a4', 'divider': '#101921',
        'error': '#e5534b', 'ok': '#4dcd5e',
    },
    'telegram_light': {
        'title': 'Telegram — светлая',
        'bg': '#ffffff', 'bg_deep': '#f1f4f7', 'panel': '#ffffff',
        'field': '#f1f4f7', 'hover': '#e8eef5', 'select': '#d3e3f5',
        'accent': '#4a95d1', 'accent_hi': '#3d84bd', 'link': '#168acd',
        'text': '#1a1a1a', 'text_dim': '#8b9aa8', 'divider': '#e3e8ed',
        'error': '#d9534f', 'ok': '#3aa757',
    },
    'midnight': {
        'title': 'Полночь',
        'bg': '#0d1117', 'bg_deep': '#070a0e', 'panel': '#161b22',
        'field': '#1c232c', 'hover': '#1a212a', 'select': '#1f3a5f',
        'accent': '#3d8bfd', 'accent_hi': '#5a9dff', 'link': '#58a6ff',
        'text': '#e6edf3', 'text_dim': '#7d8590', 'divider': '#21262d',
        'error': '#f85149', 'ok': '#3fb950',
    },
    'graphite': {
        'title': 'Графит',
        'bg': '#232323', 'bg_deep': '#1a1a1a', 'panel': '#2b2b2b',
        'field': '#333333', 'hover': '#303030', 'select': '#3f4a55',
        'accent': '#7aa2c8', 'accent_hi': '#8fb4d6', 'link': '#8ab4f8',
        'text': '#eaeaea', 'text_dim': '#9a9a9a', 'divider': '#151515',
        'error': '#e06c75', 'ok': '#98c379',
    },
}
DEFAULT_THEME = 'telegram'
# Своя тема: палитры у неё нет — она собирается из двух цветов, которые
# выбрал пользователь. Поэтому она живёт отдельно от TG_THEMES, где
# лежат только готовые палитры, и попадает в список тем через заголовки.
CUSTOM_THEME = 'custom'
# Системная тема: палитры у неё тоже нет, как у своей. Она берёт одну из
# готовых — тёмную или светлую — по тому, в каком режиме Windows. Какой
# именно ключ подставить, спрашивает system_theme_key().
SYSTEM_THEME = 'system'
THEME_TITLES = {key: pal['title'] for key, pal in TG_THEMES.items()}
THEME_TITLES[CUSTOM_THEME] = 'Своя — из двух цветов'
THEME_TITLES[SYSTEM_THEME] = 'Системная — как в Windows'
_theme_key = DEFAULT_THEME

# Два цвета своей темы: основной (фон) и акцентный.
CUSTOM_BG_DEFAULT = '#17212b'
CUSTOM_ACCENT_DEFAULT = '#5288c1'
_custom_bg = CUSTOM_BG_DEFAULT
_custom_accent = CUSTOM_ACCENT_DEFAULT


def _hex_to_rgb(value):
    """#rgb или #rrggbb -> кортеж (r, g, b). Мусор даёт чёрный."""
    h = str(value or '').strip().lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except (ValueError, IndexError):
        return (0, 0, 0)


def _rgb_to_hex(rgb):
    r, g, b = (max(0, min(255, int(round(v)))) for v in rgb)
    return '#%02x%02x%02x' % (r, g, b)


def _mix(color_a, color_b, amount):
    """Смешивает два цвета: amount=0 — первый, 1 — второй."""
    a, b = _hex_to_rgb(color_a), _hex_to_rgb(color_b)
    t = max(0.0, min(1.0, float(amount)))
    return _rgb_to_hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def _luminance(value):
    """Воспринимаемая яркость 0..1 — по ней решаем, тёмная тема или светлая."""
    r, g, b = _hex_to_rgb(value)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def is_light(key=None):
    """Светлая ли тема. По этому признаку выбираем цвет подложки плеера.

    Смотрим на панель (она же основной цвет), а не на подложку окна:
    подложка в своей теме намеренно темнее, и по ней светлая схема
    могла бы определиться как тёмная.
    """
    try:
        return _luminance(theme(key)['panel']) >= 0.5
    except Exception:
        return False


# Насколько подложка окна темнее основного цвета своей темы.
CUSTOM_BACKDROP_DARKEN = 0.30


def derive_palette(bg, accent):
    """Собирает полную палитру из двух цветов, выбранных пользователем.

    `bg` — основной цвет: им закрашены панели и списки. Подложка окна и
    диалогов выводится темнее основного, иначе всё окно оказывается
    одного цвета и панели сливаются с фоном. Остальные оттенки — поля,
    наведение, разделители — тоже выводятся из основного и акцентного,
    а цвет текста выбирается по яркости основного, чтобы он читался.
    """
    main = bg
    dark = _luminance(main) < 0.5
    text = '#ffffff' if dark else '#1a1a1a'
    # Подложка — всегда темнее основного, независимо от выбранного цвета.
    backdrop = _mix(main, '#000000', CUSTOM_BACKDROP_DARKEN)
    if dark:
        # На тёмном основном поля чуть светлее, а разделители заметно
        # темнее — иначе границы сливаются. Панель остаётся ровно
        # выбранным цветом.
        bg_deep = _mix(main, '#000000', 0.48)
        panel = main
        field = _mix(main, '#ffffff', 0.13)
        hover = _mix(main, '#ffffff', 0.11)
        divider = _mix(main, '#000000', 0.55)
        accent_hi = _mix(accent, '#ffffff', 0.15)
        link = _mix(accent, '#ffffff', 0.10)
    else:
        # На светлом основном всё наоборот: поля, наведение и разделители
        # темнее основы, иначе границы разделов пропадают.
        bg_deep = _mix(backdrop, '#000000', 0.10)
        panel = main
        field = _mix(main, '#000000', 0.07)
        hover = _mix(main, '#000000', 0.10)
        divider = _mix(main, '#000000', 0.22)
        accent_hi = _mix(accent, '#000000', 0.15)
        link = accent
    return {
        'title': THEME_TITLES[CUSTOM_THEME],
        'bg': backdrop,
        'bg_deep': bg_deep,
        'panel': panel,
        'field': field,
        'hover': hover,
        'select': _mix(main, accent, 0.45),
        'accent': accent,
        'accent_hi': accent_hi,
        'link': link,
        'text': text,
        'text_dim': _mix(text, main, 0.45),
        'divider': divider,
        'error': '#e5534b' if dark else '#d9534f',
        'ok': '#4dcd5e' if dark else '#3aa757',
    }


def set_custom_colors(bg=None, accent=None):
    """Запоминает цвета своей темы. Мусорные значения игнорируются."""
    global _custom_bg, _custom_accent
    if bg and str(bg).startswith('#'):
        _custom_bg = str(bg)
    if accent and str(accent).startswith('#'):
        _custom_accent = str(accent)
    return _custom_bg, _custom_accent


def custom_colors():
    """Текущие цвета своей темы."""
    return _custom_bg, _custom_accent


def _system_resolved_key():
    """Какой готовой палитрой прикидывается системная тема сейчас.

    Чтение из реестра держим в icons: оттуда же его берёт значок в трее,
    и второй копии того же кода быть не должно. Но и сюда его не тяну
    безусловно — theme не должен зависеть от модуля значков, который
    тянет Qt. Поэтому вызываем по месту и при ошибке остаёмся тёмными.
    """
    try:
        from ..icons import icons
        return icons.system_theme_key()
    except Exception:
        return SYSTEM_DARK_THEME_FALLBACK


SYSTEM_DARK_THEME_FALLBACK = 'telegram'


def theme(key=None):
    """Палитра темы: по ключу или текущая."""
    k = key or _theme_key
    if k == CUSTOM_THEME:
        return derive_palette(_custom_bg, _custom_accent)
    if k == SYSTEM_THEME:
        # Системная — не отдельная палитра, а указатель: какая тема
        # Windows, такую готовую палитру и берём.
        k = _system_resolved_key()
    return TG_THEMES.get(k, TG_THEMES[DEFAULT_THEME])


def resolved_theme_key(key=None):
    """Ключ готовой палитры, которая сейчас на экране.

    Нужен там, где важен не цвет, а именно выбор: например, при пересчёте
    оформления — чтобы понять, поменялась ли системная тема на другую.
    """
    k = key or _theme_key
    if k == SYSTEM_THEME:
        return _system_resolved_key()
    return k


def set_theme_key(key):
    """Запоминает выбранную тему. Неизвестный ключ игнорируется."""
    global _theme_key
    if key in (CUSTOM_THEME, SYSTEM_THEME) or key in TG_THEMES:
        _theme_key = key
    return _theme_key


def theme_key():
    """Ключ выбранной темы — с учётом того, что это может быть системная."""
    return _theme_key


# Масштаб интерфейса в процентах: 50..150, где 100 — обычный размер.
UI_SCALE_DEFAULT = 100
UI_SCALE_RANGE = (50, 150)
_ui_scale = UI_SCALE_DEFAULT


def set_ui_scale(percent):
    """Запоминает масштаб интерфейса, ограничивая его разумными рамками."""
    global _ui_scale
    try:
        value = int(percent)
    except (TypeError, ValueError):
        return _ui_scale
    low, high = UI_SCALE_RANGE
    _ui_scale = max(low, min(high, value))
    return _ui_scale


def ui_scale():
    """Текущий масштаб интерфейса в процентах."""
    return _ui_scale


def scale_px(value):
    """Пиксельный размер с учётом масштаба интерфейса.

    Нужен там, где размеры заданы не в таблице стилей, а кодом: строки
    списков, значки и свои paintEvent. Таблица стилей масштабируется
    отдельно (scale_css), и без этой функции такие элементы оставались
    бы одного размера при любом масштабе.
    """
    try:
        factor = _ui_scale / 100.0
    except Exception:
        factor = 1.0
    return max(1, int(float(value) * factor + 0.5))


_PX_RE = re.compile(r'(\d+)px')


def scale_css(css, factor=None):
    """Умножает все размеры в таблице стилей на масштаб интерфейса.

    Стиль весь в пикселях, поэтому достаточно пересчитать их одним
    проходом: тогда вместе с текстом растут и отступы, и скругления, и
    полосы прокрутки — иначе на 150% надписи упирались бы друг в друга.
    """
    if factor is None:
        factor = _ui_scale / 100.0
    if abs(factor - 1.0) < 0.01:
        return css

    # Округляем вверх от половины: обычное round() округляет 6,5 к чётному
    # и давало бы 6 вместо 7 — на глаз это выглядит как ошибка.
    def _resize(match):
        scaled = int(match.group(1)) * factor
        return '%dpx' % max(1, int(scaled + 0.5))

    return _PX_RE.sub(_resize, css)


def _rgba(hex_color, alpha):
    """#rrggbb + прозрачность — для таблицы стилей.

    В палитре цвета заданы обычными #rrggbb, а полупрозрачные панели
    поверх видео требуют rgba(): считать это вручную в каждой строке
    стиля слишком муторно.
    """
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def build_style(key=None):
    """Таблица стилей для выбранной темы.

    Собирается из палитры, поэтому переключение темы — это всего лишь
    заново применить стиль к приложению: раскрашивать виджеты по одному
    не нужно.
    """
    t = theme(key)
    TG_BG = t['bg']
    TG_BG_DEEP = t['bg_deep']
    TG_PANEL = t['panel']
    TG_FIELD = t['field']
    TG_HOVER = t['hover']
    TG_SELECT = t['select']
    TG_ACCENT = t['accent']
    TG_ACCENT_HI = t['accent_hi']
    TG_LINK = t['link']
    TG_TEXT = t['text']
    TG_TEXT_DIM = t['text_dim']
    TG_DIVIDER = t['divider']
    TG_ERROR = t['error']
    TG_OK = t['ok']
    # Цвет подложки панели управления. На светлой теме панель светлая,
    # поэтому всё, что на ней нарисовано, должно быть тёмным. Иначе
    # светлые значки (а они были всегда светлыми) пропадали на светлой
    # подложке — «кнопок не видно».
    light = is_light(key)
    # На светлой теме подложка окна темнее основного цвета, а панель
    # управления должна оставаться светлой — берём основной цвет панели.
    TG_BAR_BG = TG_PANEL if light else TG_BG_DEEP
    TG_BAR_FG = TG_TEXT if light else '#ffffff'
    TG_BAR_TRACK = (_rgba('#000000', 0.22) if light
                    else _rgba('#ffffff', 0.22))
    TG_BAR_HOVER = (_rgba('#000000', 0.10) if light
                    else _rgba('#ffffff', 0.14))
    TG_BAR_PRESS = (_rgba('#000000', 0.05) if light
                    else _rgba('#ffffff', 0.07))
    return f"""
* {{ font-family: 'Segoe UI', 'Noto Sans', sans-serif; }}
QWidget {{ background: {TG_BG}; color: {TG_TEXT}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {TG_BG}; }}
QLabel {{ background: transparent; }}
QLabel#heading {{ font-size: 19px; font-weight: 600; color: {TG_ACCENT}; }}
QLabel#hint {{ color: {TG_TEXT_DIM}; font-size: 12px; }}
QLabel#error {{ color: {TG_ERROR}; font-size: 12px; }}

QLineEdit, QComboBox, QSpinBox {{
    background: {TG_FIELD}; border: 1px solid transparent;
    border-radius: 8px; padding: 7px 12px;
    selection-background-color: {TG_ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {TG_ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {TG_PANEL}; border: 1px solid {TG_DIVIDER};
    border-radius: 8px; padding: 4px; outline: none;
    selection-background-color: {TG_SELECT};
}}

QPushButton {{
    background: {TG_FIELD}; border: 1px solid transparent; border-radius: 8px;
    padding: 7px 16px;
}}
QPushButton:hover {{ background: {TG_HOVER}; }}
QPushButton:pressed {{ background: {TG_SELECT}; }}
QPushButton:focus {{ border: 1px solid {TG_ACCENT}; }}
QPushButton:disabled {{ color: {TG_TEXT_DIM}; background: {TG_FIELD}; }}
QPushButton#primary {{
    background: {TG_ACCENT}; color: #ffffff; font-weight: 600;
}}
QPushButton#primary:hover {{ background: {TG_ACCENT_HI}; }}
QPushButton#flat {{ background: transparent; color: {TG_LINK}; }}
QPushButton#flat:hover {{ background: {TG_HOVER}; }}
/* Кнопка настроек с шестерёнкой в верхней полосе: без подписи, поэтому
   по размеру она квадратная и не тянется вместе с полосой. */
QToolButton#settingbtn {{
    background: {TG_FIELD}; border: 1px solid transparent;
    border-radius: 8px; padding: 0px;
}}
QToolButton#settingbtn:hover {{ background: {TG_HOVER}; }}
QToolButton#settingbtn:pressed {{ background: {TG_SELECT}; }}
QToolButton#settingbtn::menu-indicator {{ image: none; width: 0px; }}

QListWidget, QListView {{
    background: {TG_BG_DEEP}; border: none; border-radius: 10px;
    outline: none; padding: 4px;
}}
/* Поля строки нулевые: у строк со своим виджетом Qt ставит виджет на
   место СОДЕРЖИМОГО строки, то есть сдвигает его на левое и верхнее
   поле (8 и 10 точек). Виджет выходил ниже подложки на 9 точек и
   оказывался бы вне её, а при обычной высоте строки подложка была бы
   выше текста. Отступы рисует сам виджет строки (см. VideoRow). */
QListWidget::item {{
    border-radius: 8px; padding: 0px; margin: 1px 2px;
}}
QListWidget::item:hover {{ background: {TG_HOVER}; }}
QListWidget::item:selected {{ background: {TG_SELECT}; color: #ffffff; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {TG_FIELD}; border-radius: 5px; min-height: 30px;
}}
/* Ползунок подкрашивается акцентом: в своей теме видно, что он активен. */
QScrollBar::handle:vertical:hover {{ background: {TG_ACCENT}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {TG_FIELD}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {TG_ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{
    background: {TG_PANEL}; border: 1px solid {TG_DIVIDER};
    border-radius: 10px; padding: 6px;
}}
QMenu::item {{ padding: 8px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {TG_SELECT}; }}
QMenu::item:disabled {{ color: {TG_TEXT_DIM}; }}
QMenu::separator {{ height: 1px; background: {TG_DIVIDER}; margin: 5px 8px; }}

QSplitter::handle {{ background: {TG_DIVIDER}; }}
QSplitter::handle:horizontal {{ width: 2px; }}
/* Полоску между панелями подсвечиваем акцентом: видно, что её можно тянуть. */
QSplitter::handle:hover {{ background: {TG_ACCENT}; }}
QSplitter::handle:pressed {{ background: {TG_ACCENT_HI}; }}

QStatusBar {{ background: {TG_BG}; color: {TG_TEXT_DIM}; }}
QStatusBar::item {{ border: none; }}
/* Строка состояния ниже обычной, поэтому и шрифт в ней на точку мельче
   общего: при 13 текст в неё не влезал и обрезался сверху и снизу. */
QLabel#statusline {{ font-size: 12px; }}

/* Вкладки главного окна.
   Без этих правил Qt рисует их стилем системы. На Windows 11 он тёмный,
   и вкладки выглядят как часть программы, а на Windows 10 — светлым
   прямоугольником с белой подложкой и серыми подписями: в тёмном окне
   это выглядело как чужая деталь. Задаём и саму панель, и корешок, и
   состояние выбранного — тогда вид одинаковый везде. */
QTabWidget::pane {{
    border: none; background: transparent; top: -1px;
}}
QTabWidget::tab-bar {{ left: 0px; }}
QTabBar {{
    background: transparent;
    /* Собственное рисование: иначе стиль системы рисует свои рамки
       поверх наших цветов и на Windows 10 снова появляется белое. */
    qproperty-drawBase: 0;
}}
QTabBar::tab {{
    background: {TG_FIELD};
    color: {TG_TEXT_DIM};
    border: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 6px 16px;
    margin-right: 4px;
    min-width: 70px;
}}
QTabBar::tab:hover {{ background: {TG_HOVER}; color: {TG_TEXT}; }}
QTabBar::tab:selected {{
    background: {TG_SELECT}; color: #ffffff;
}}
QTabBar::tab:!selected {{ margin-top: 3px; }}
QTabBar::scroller {{ width: 0px; }}
QTabBar QToolButton {{
    background: {TG_FIELD}; border: none; border-radius: 4px;
    color: {TG_TEXT}; padding: 0px;
}}

QToolTip {{
    background: {TG_PANEL}; color: {TG_TEXT};
    border: 1px solid {TG_DIVIDER}; border-radius: 6px; padding: 5px 8px;
}}

QSlider::groove:horizontal {{
    height: 4px; background: {TG_FIELD}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {TG_ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 12px; height: 12px; margin: -4px 0;
    border-radius: 6px; background: {TG_ACCENT};
}}
QSlider::handle:horizontal:hover {{ background: {TG_ACCENT_HI}; }}

QProgressBar {{
    background: {TG_FIELD}; border: none; border-radius: 3px;
    text-align: center; color: {TG_TEXT}; font-size: 10px;
}}
QProgressBar::chunk {{ background: {TG_ACCENT}; border-radius: 3px; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {TG_DIVIDER}; background: {TG_FIELD};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border: 1px solid {TG_ACCENT};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {TG_ACCENT}; border: 1px solid {TG_ACCENT};
}}

QMessageBox {{ background: {TG_PANEL}; }}

/* Подложку панель рисует сама (ControlBar.paintEvent): у обычного QWidget
   background из таблицы стилей не закрашивается без WA_StyledBackground,
   а с ним легко получить непрозрачную плашку в одном режиме и пустоту в
   другом. Здесь описано только то, что рисуют сами дочерние виджеты. */
#controlbar QPushButton {{ background: transparent; border: none; border-radius: 8px; }}
#controlbar QPushButton:hover {{ background: {TG_BAR_HOVER}; }}
#controlbar QPushButton:pressed {{ background: {TG_BAR_PRESS}; }}
#controlbar QLabel {{ color: {TG_BAR_FG}; font-size: 11px; }}
#controlbar QSlider {{ background: transparent; }}
#controlbar QSlider::groove:horizontal {{ height: 4px; background: {TG_BAR_TRACK}; border-radius: 2px; }}
#controlbar QSlider::sub-page:horizontal {{ background: {TG_ACCENT}; border-radius: 2px; }}
#controlbar QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; background: {TG_BAR_FG}; }}
#controlbar QSlider::handle:horizontal:hover {{ background: {TG_ACCENT_HI}; }}

/* Переключатель автоспроизведения: пилюля рисует себя сама, поэтому
   системной рамки и подложки у него быть не должно. */
#controlbar QWidget#autoplayswitch {{ background: transparent; border: none; }}

/* Панель управления в маленьком окне PiP. Своего фона у неё нет:
   элементы управления лежат прямо на видео — подложка перекрывала бы
   кадр и спорила с ним цветом. Видимость даёт обводка кружков и сама
   иконка на тёмном кадре либо светлая заливка на светлом. */
#pipbar {{ background: transparent; }}
/* Кнопки без всякого оформления: только сама иконка поверх видео.
   Подложку при наведении не рисуем — она закрывала бы кадр, и в
   маленьком окне это заметно. Видно и так: курсор меняется на «руку». */
#pipbar QPushButton {{ background: transparent; border: none; padding: 0px; }}
#pipbar QSlider {{ background: transparent; border: none; }}
#pipbar QSlider::groove:horizontal {{ height: 3px; background: {_rgba('#ffffff', 0.28)}; border-radius: 2px; }}
#pipbar QSlider::sub-page:horizontal {{ background: {TG_ACCENT}; border-radius: 2px; }}
#pipbar QSlider::handle:horizontal {{ width: 10px; height: 10px; margin: -4px 0; border-radius: 5px; background: #ffffff; }}
#pipbar QSlider::handle:horizontal:hover {{ background: {TG_ACCENT_HI}; }}

/* Панель со списком роликов канала внутри окна плеера. */
#playlistpanel {{ background: {_rgba(TG_PANEL, 0.94)}; border-radius: 12px; }}
#playlistpanel QLabel {{ color: {TG_TEXT_DIM}; font-size: 11px; }}
#playlistpanel QListWidget {{ background: transparent; border: none; color: {TG_TEXT}; font-size: 12px; }}
#playlistpanel QListWidget::item {{ padding: 6px 8px; border-radius: 8px; }}
#playlistpanel QListWidget::item:selected {{ background: {TG_SELECT}; color: {TG_TEXT}; }}
#playlistpanel QListWidget::item:hover {{ background: {_rgba('#ffffff', 0.08)}; }}

/* Подсказка о режиме поиска по всему Telegram. */

/* Окно настроек. */
QGroupBox {{ border: 1px solid {TG_DIVIDER}; border-radius: 10px;
             margin-top: 14px; padding: 12px 10px 8px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px;
                    color: {TG_TEXT_DIM}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""


def build_style_scaled(key=None, scale=None):
    """Таблица стилей с учётом масштаба интерфейса."""
    factor = (_ui_scale if scale is None else float(scale)) / 100.0
    return scale_css(build_style(key), factor)


def apply_theme_settings(cfg):
    """Ставит тему, свои цвета и масштаб из настроек.

    Одним вызовом, чтобы порядок «сначала цвета, потом палитра, потом
    масштаб» не приходилось повторять в каждом месте.
    """
    cfg = cfg or {}
    set_custom_colors(cfg.get('custom_bg'), cfg.get('custom_accent'))
    set_ui_scale(cfg.get('ui_scale', UI_SCALE_DEFAULT))
    set_theme_key(cfg.get('theme', DEFAULT_THEME))
    return build_style_scaled()

