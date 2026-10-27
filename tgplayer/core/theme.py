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
THEME_TITLES = {key: pal['title'] for key, pal in TG_THEMES.items()}
THEME_TITLES[CUSTOM_THEME] = 'Своя — из двух цветов'
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


def derive_palette(bg, accent):
    """Собирает полную палитру из двух цветов, выбранных пользователем.

    Задавать пятнадцать оттенков руками — слишком муторно, поэтому
    остальные выводятся из основного и акцентного: светлые тона
    подсвечиваются к белому, тёмные — к чёрному, а цвет текста выбирается
    по яркости фона, чтобы он оставался читаемым на любом фоне.
    """
    dark = _luminance(bg) < 0.5
    text = '#ffffff' if dark else '#1a1a1a'
    if dark:
        # На тёмном фоне панели и поля чуть светлее основы, а разделители
        # заметно темнее — иначе границы сливаются с фоном.
        bg_deep = _mix(bg, '#000000', 0.40)
        panel = _mix(bg, '#ffffff', 0.07)
        field = _mix(bg, '#ffffff', 0.13)
        hover = _mix(bg, '#ffffff', 0.11)
        divider = _mix(bg, '#000000', 0.55)
        accent_hi = _mix(accent, '#ffffff', 0.15)
        link = _mix(accent, '#ffffff', 0.10)
    else:
        # На светлом фоне всё наоборот: поля, наведение и разделители
        # темнее основы. Раньше здесь смешивали к белому, и на светлой
        # схеме пропадали и разделители, и границы разделов — оставался
        # ровный фон выбранного цвета, на котором читался только текст.
        bg_deep = _mix(bg, '#000000', 0.07)
        panel = bg
        field = _mix(bg, '#000000', 0.07)
        hover = _mix(bg, '#000000', 0.10)
        divider = _mix(bg, '#000000', 0.22)
        accent_hi = _mix(accent, '#000000', 0.15)
        link = accent
    return {
        'title': THEME_TITLES[CUSTOM_THEME],
        'bg': bg,
        'bg_deep': bg_deep,
        'panel': panel,
        'field': field,
        'hover': hover,
        'select': _mix(bg, accent, 0.45),
        'accent': accent,
        'accent_hi': accent_hi,
        'link': link,
        'text': text,
        'text_dim': _mix(text, bg, 0.45),
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


def theme(key=None):
    """Палитра темы: по ключу или текущая."""
    k = key or _theme_key
    if k == CUSTOM_THEME:
        return derive_palette(_custom_bg, _custom_accent)
    return TG_THEMES.get(k, TG_THEMES[DEFAULT_THEME])


def set_theme_key(key):
    """Запоминает выбранную тему. Неизвестный ключ игнорируется."""
    global _theme_key
    if key == CUSTOM_THEME or key in TG_THEMES:
        _theme_key = key
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
QPushButton:hover {{ background: #2b3a4d; }}
QPushButton:pressed {{ background: #1e2836; }}
QPushButton:focus {{ border: 1px solid {TG_ACCENT}; }}
QPushButton:disabled {{ color: {TG_TEXT_DIM}; background: #1c2733; }}
QPushButton#primary {{
    background: {TG_ACCENT}; color: #ffffff; font-weight: 600;
}}
QPushButton#primary:hover {{ background: {TG_ACCENT_HI}; }}
QPushButton#flat {{ background: transparent; color: {TG_LINK}; }}
QPushButton#flat:hover {{ background: {TG_HOVER}; }}

QListWidget, QListView {{
    background: {TG_BG_DEEP}; border: none; border-radius: 10px;
    outline: none; padding: 4px;
}}
QListWidget::item {{
    border-radius: 8px; padding: 8px 10px; margin: 1px 2px;
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
#controlbar QPushButton:hover {{ background: {_rgba('#ffffff', 0.14)}; }}
#controlbar QPushButton:pressed {{ background: {_rgba('#ffffff', 0.07)}; }}
#controlbar QLabel {{ color: {TG_TEXT}; font-size: 11px; }}
#controlbar QSlider {{ background: transparent; }}
#controlbar QSlider::groove:horizontal {{ height: 4px; background: {_rgba('#ffffff', 0.22)}; border-radius: 2px; }}
#controlbar QSlider::sub-page:horizontal {{ background: {TG_ACCENT}; border-radius: 2px; }}
#controlbar QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; background: #ffffff; }}
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

