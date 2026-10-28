"""Главное окно и диалоги: папки, каналы, видео, настройки, вход.

Экраны программы, которые человек видит до и после плеера: вход в
Telegram, настройки, список каналов и роликов, полоса состояния и
значок связи. Работа с Telegram и с mpv им не принадлежит — они
разговаривают с backend и открывают PlayerWindow.
"""
import asyncio
import base64
import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import (
    QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QThread,
    QTimer, pyqtSignal)
from PyQt6.QtGui import (
    QAction, QActionGroup, QColor, QFontMetricsF, QIcon, QImage, QKeySequence,
    QPainter, QPainterPath, QPen, QPixmap, QShortcut)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton,
    QScrollArea, QSplitter, QStatusBar, QSystemTrayIcon, QVBoxLayout,
    QWidget, QWidgetAction)

from ..core import config, history, log, paths, proxy, theme, updates, utils, version
from ..core import settings as app_settings
from ..icons import icons
from ..player.player import PlayerWindow
from ..backend.backend import TelegramBackend

class QrLoginDialog(QDialog):
    """Вход по QR-коду: телефон сканирует картинку с экрана.

    Зачем нужен. Telegram не всегда шлёт код по SMS: если у номера уже
    есть активная сессия, код уходит В ПРИЛОЖЕНИЕ — в служебный чат
    «Telegram». Человек ждёт SMS, ничего не приходит, и войти нельзя.
    QR снимает этот вопрос: код не нужен вовсе.

    Порядок действий, который показываем словами: открыть Telegram на
    телефоне → Настройки → Устройства → Подключить устройство → навести
    камеру на код.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Вход по QR-коду')
        self.setModal(True)
        self.setFixedWidth(420)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(10)

        head = QLabel('Вход по QR-коду')
        head.setObjectName('heading')
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(head)

        sub = QLabel('Откройте Telegram на телефоне: Настройки → Устройства → '
                     'Подключить устройство — и наведите камеру на код.')
        sub.setObjectName('hint')
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(6)

        # Картинка QR. Белая подложка обязательна: на тёмной теме чёрные
        # модули кода на прозрачном фоне телефон может не распознать.
        self.lbl_qr = QLabel()
        self.lbl_qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_qr.setFixedSize(260, 260)
        self.lbl_qr.setStyleSheet('background: white; border-radius: 8px;')
        wrap = QHBoxLayout()
        wrap.addStretch(1)
        wrap.addWidget(self.lbl_qr)
        wrap.addStretch(1)
        lay.addLayout(wrap)
        lay.addSpacing(6)

        self.lbl_state = QLabel('Получаю код...')
        self.lbl_state.setObjectName('hint')
        self.lbl_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_state)
        lay.addSpacing(8)

        # Кнопка называется «Назад», а не «Отмена»: она возвращает к
        # вводу номера, а не закрывает программу. Отмена на этом экране
        # пугала — человек думал, что сейчас всё закроется.
        self.btn_back = QPushButton('Назад')
        self.btn_back.setObjectName('flat')
        self.btn_back.setMinimumHeight(34)
        self.btn_back.clicked.connect(self._go_back)
        lay.addWidget(self.btn_back)

        # Что выбрал человек при закрытии окна: 'back' — вернуться к
        # номеру, иначе окно закрыто совсем.
        self.result_kind = None

    def _go_back(self):
        self.result_kind = 'back'
        self.reject()

    def set_url(self, url):
        """Рисует QR по ссылке tg://login?token=..."""
        try:
            import segno
        except Exception as ex:
            print(f"[Вход] нет библиотеки для QR: {ex}")
            self.lbl_state.setText('Не удалось нарисовать код')
            return

        path = os.path.join(tempfile.gettempdir(), 'tgplayer_qr.png')
        try:
            qr = segno.make(url, error='m')
            qr.save(path, scale=8, border=2,
                    dark='black', light='white')
            pix = QPixmap(path)
            if not pix.isNull():
                self.lbl_qr.setPixmap(pix.scaled(
                    244, 244,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
        except Exception as ex:
            print(f"[Вход] QR не нарисовался: {ex}")
            self.lbl_state.setText('Не удалось нарисовать код')

    def set_state(self, state):
        """Подсказка под кодом: что происходит сейчас."""
        text = {
            'scan': 'Сканируйте код телефоном',
            'expired': 'Код обновлён — сканируйте заново',
            'confirm': 'Код отсканирован. Подтвердите вход на телефоне',
        }.get(state, state)
        self.lbl_state.setText(text)


class LoginDialog(QDialog):
    """Один шаг входа в Telegram: телефон, код подтверждения или пароль.

    Шаги идут по очереди, ими управляет backend: он просит данные
    сигналом, а ответ получает через answer_login(). Поэтому диалог
    умеет ровно одно — спросить строку.
    """

    STEPS = {
        'phone': ('Вход в Telegram', 'Введите номер телефона', False),
        'code': ('Подтверждение входа', 'Код из Telegram', False),
        'password': ('Двухфакторная защита', 'Облачный пароль', True),
    }

    def __init__(self, kind, message, parent=None):
        super().__init__(parent)
        self.qr_chosen = False
        title, label, secret = self.STEPS.get(
            kind, ('Вход в Telegram', 'Введите значение', False))
        self.setWindowTitle('Вход в Telegram')
        self.setModal(True)
        self.setFixedWidth(400)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 30, 28, 22)
        lay.setSpacing(0)

        # Круглая «аватарка» вместо значка: так окно сразу читается как
        # телеграмное, а не как системный запрос пароля.
        #
        # Значок рисуем СВОИМ кодом, а не берём иконку программы: у
        # иконки свой фон (квадратная синяя плитка), и поверх круглой
        # подложки он выглядел как вложенный квадрат другого оттенка.
        # Здесь — ровный круг акцентного цвета и белый самолётик поверх.
        #
        # Картинку готовим вчетверо крупнее видимого размера, а показывает
        # её QLabel уже уменьшенной. При рисовании ровно в 76 пикселей край
        # круга шёл ступенькой: дуга не попадала в целые пиксели. Запас по
        # разрешению это убирает.
        logo = QLabel()
        logo.setFixedSize(76, 76)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(self._logo_pixmap(76 * 4))
        # QLabel сам уменьшает картинку плавно, если включить это свойство.
        logo.setScaledContents(True)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(logo)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(18)

        head = QLabel(title)
        head.setObjectName('heading')
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(head)
        lay.addSpacing(6)

        sub = QLabel(label)
        sub.setObjectName('hint')
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(20)

        self.edit = QLineEdit()
        self.edit.setMinimumHeight(42)
        self.edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if secret:
            self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        if kind == 'phone':
            # Плюс НЕ подставляем заранее: номер можно набрать и без него
            # («79991234567»), и добавленный плюс мешал — человек видел
            # лишний символ, стирал его и получал «+79991…».
            self.edit.setPlaceholderText('79991234567 или +79991234567')
        lay.addWidget(self.edit)
        lay.addSpacing(8)

        hint = QLabel(message)
        hint.setObjectName('hint')
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hint)

        self.error = QLabel('')
        self.error.setObjectName('error')
        self.error.setWordWrap(True)
        self.error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.error)
        lay.addSpacing(18)

        self.btn_ok = QPushButton('Продолжить')
        self.btn_ok.setObjectName('primary')
        self.btn_ok.setMinimumHeight(40)
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self._accept)
        lay.addWidget(self.btn_ok)
        lay.addSpacing(6)

        # Переключение на вход по QR. На шаге номера — просто выбор
        # способа. На шагах кода и пароля та же кнопка работает как
        # «назад»: человек понял, что код не приходит (Telegram отправил
        # его в приложение, а не по SMS), и хочет войти по коду с экрана.
        # Отменять вход целиком для этого не нужно.
        self.btn_qr = None
        if not secret:
            self.btn_qr = QPushButton('Войти по QR-коду'
                                      if kind == 'phone' else 'Войти по QR')
            self.btn_qr.setObjectName('flat')
            self.btn_qr.setMinimumHeight(34)
            self.btn_qr.clicked.connect(self._choose_qr)
            lay.addWidget(self.btn_qr)
            lay.addSpacing(4)

        self.btn_cancel = QPushButton('Отмена')
        self.btn_cancel.setObjectName('flat')
        self.btn_cancel.setMinimumHeight(34)
        self.btn_cancel.clicked.connect(self.reject)
        lay.addWidget(self.btn_cancel)

        self.edit.returnPressed.connect(self._accept)
        self.edit.setFocus()

    @staticmethod
    def _logo_pixmap(size):
        """Самолётик для окна входа: круг акцента и белый самолётик.

        Здесь НЕ тот значок, что в трее: в трее средняя часть красится в
        акцент и подложки нет, а в окне входа нужен полный знак — круг
        цвета темы и самолётик целиком поверх него.

        Две тонкости, обе из-за реальных ошибок:

        * Знак центрируем по его настоящим границам, а не по квадрату
          картинки: самолётик смещён внутри неё, и от центрирования по
          квадрату он уезжал вправо.

        * Рисуем в увеличенном масштабе и уменьшаем сглаживанием. Если
          рисовать сразу в 76 пикселей, края круга и знака получаются
          ступенькой — это и было видно.
        """
        scale = 4                      # запас по разрешению
        big = size * scale
        pix = QPixmap(big, big)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            try:
                accent = QColor(theme.theme()['accent'])
            except Exception:
                accent = QColor('#2f7fd6')
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            p.drawEllipse(QRectF(0, 0, big, big))

            path = paths.login_logo_path()
            if path:
                plane = QPixmap(path)
                if not plane.isNull():
                    # Границы самого рисунка внутри файла: по ним и центрируем.
                    bounds = plane.rect()
                    img = plane.toImage()
                    left = img.width()
                    top = img.height()
                    right = bottom = -1
                    for y in range(img.height()):
                        for x in range(img.width()):
                            if img.pixelColor(x, y).alpha() > 20:
                                left = min(left, x)
                                right = max(right, x)
                                top = min(top, y)
                                bottom = max(bottom, y)
                    if right > left and bottom > top:
                        bounds = QRect(left, top, right - left + 1,
                                       bottom - top + 1)

                    # Самолётик занимает около 70% вписанного квадрата,
                    # чтобы по краям круга оставалось поле.
                    inner = int(big * 0.707 * 0.70)
                    ratio = min(inner / bounds.width(), inner / bounds.height())
                    draw_w = max(1, int(bounds.width() * ratio))
                    draw_h = max(1, int(bounds.height() * ratio))
                    p.drawPixmap(
                        (big - draw_w) // 2, (big - draw_h) // 2, draw_w, draw_h,
                        plane, bounds.x(), bounds.y(),
                        bounds.width(), bounds.height())
        finally:
            p.end()
        # Уменьшаем с явным сглаживанием. Без него пиксели выпадают
        # ступенькой — на круге это заметнее всего, потому что его край
        # идёт по дуге.
        return pix.scaled(size, size,
                          Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)

    def _choose_qr(self):
        """Человек выбрал вход по QR вместо номера.

        Ставим флаг и закрываем окно как «принято». Backend по флагу
        понимает, что нужен вход по коду с экрана, и запускает его. Так
        не нужен отдельный диалог — выбор происходит в том же окне.
        """
        self.qr_chosen = True
        self.accept()

    def _accept(self):
        if not self.edit.text().strip():
            self.error.setText('Поле не может быть пустым.')
            return
        self.accept()

    def value(self):
        return self.edit.text().strip()


class NoWheelComboBox(QComboBox):
    """Выпадающий список, который не реагирует на колесо мыши.

    По умолчанию QComboBox переключает значение от любого прокрута — даже
    когда он не в фокусе. Человек крутит колесо, чтобы прокрутить окно
    настроек, а вместо этого меняется масштаб интерфейса или тема. Особенно
    неприятно с масштабом: окно перерисовывается прямо под курсором, и
    попытка доскроллить превращается в погоню за уезжающим значением.

    Колесо пропускаем к родителю — пусть прокручивается страница.
    """

    def wheelEvent(self, event):
        event.ignore()


class DownloadDialog(QDialog):
    """Окно загрузки обновления: шкала, проценты и скорость.

    Раньше это был QMessageBox с одной строкой — окно маленькое, текст
    прижат влево, и непонятно, идёт загрузка или всё встало. Здесь всё
    по центру, видно шкалу и сколько осталось, а закрывать окно крестиком
    нельзя: иначе непонятно, что произошло с загрузкой.
    """

    cancelled = pyqtSignal()

    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle('Загрузка обновления')
        self.setFixedWidth(420)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self._cancelled = False
        self._total = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(14)

        self.lbl_title = QLabel(title)
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_title.setObjectName('about_title')
        root.addWidget(self.lbl_title)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        root.addWidget(self.bar)

        self.lbl_info = QLabel('Подготовка…')
        self.lbl_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_info.setObjectName('hint')
        root.addWidget(self.lbl_info)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = QPushButton('Отмена')
        self.btn_cancel.clicked.connect(self._on_cancel)
        row.addWidget(self.btn_cancel)
        row.addStretch(1)
        root.addLayout(row)

    def _on_cancel(self):
        self._cancelled = True
        self.cancelled.emit()
        self.reject()

    @property
    def was_cancelled(self):
        return self._cancelled

    def on_progress(self, chunk, total):
        """Обновляет шкалу. Вызывается из фонового потока — только сигналом.

        Шаг считаем от total: если GitHub не сообщил размер, шкала
        останется бегущей (0..0), и человек всё равно видит, что процесс
        живой.
        """
        if self._cancelled:
            return
        if total > 0:
            self._total = total
            self._got = getattr(self, '_got', 0) + chunk
            pct = min(100, int(self._got * 100 / total))
            self.bar.setRange(0, 100)
            self.bar.setValue(pct)
            self.lbl_info.setText(
                f'{pct}%  ·  {self._got / 1048576:.1f} '
                f'из {total / 1048576:.1f} МБ')
        else:
            # Размер неизвестен — крутим бесконечную шкалу.
            self.bar.setRange(0, 0)
            self.lbl_info.setText('Загрузка…')


class UpdateDialog(QDialog):
    """Предложение обновиться до новой версии.

    Показывает, что нового, и две кнопки: обновить сейчас или отложить.
    Скачивание начинается только по нажатию — молча ничего не тянем.
    Отдельная галочка «не предлагать эту версию»: если новая сборка
    окажется хуже, человек откатится и не должен получать предложение
    обновиться обратно при каждом запуске.
    """

    def __init__(self, parent, info, current_version):
        super().__init__(parent)
        self.setWindowTitle('Доступно обновление')
        self.setMinimumWidth(520)
        self._info = info

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        title = QLabel(f"TGPlayer {info.tag.lstrip('vV')}")
        title.setObjectName('about_title')
        root.addWidget(title)

        sub = QLabel(f"У вас {current_version}. Доступна новая версия.")
        sub.setObjectName('hint')
        sub.setWordWrap(True)
        root.addWidget(sub)

        # Что нового. Описание релиза на GitHub — обычный markdown; теги
        # срезаем, чтобы в окне не было звёздочек и решёток.
        notes = self._plain_notes(info.notes)
        if notes:
            box = QPlainTextEdit()
            box.setReadOnly(True)
            box.setPlainText(notes)
            box.setMinimumHeight(180)
            root.addWidget(box, 1)

        if info.size:
            size_mb = info.size / (1024 * 1024)
            lbl = QLabel(f"Размер загрузки: {size_mb:.1f} МБ")
            lbl.setObjectName('hint')
            root.addWidget(lbl)

        self.chk_skip = QCheckBox('Не предлагать эту версию снова')
        root.addWidget(self.chk_skip)

        row = QHBoxLayout()
        self.btn_later = QPushButton('Позже')
        self.btn_later.clicked.connect(self.reject)
        self.btn_now = QPushButton('Скачать и установить')
        self.btn_now.setObjectName('primary')
        self.btn_now.setMinimumHeight(36)
        self.btn_now.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(self.btn_later)
        row.addWidget(self.btn_now)
        root.addLayout(row)

    @staticmethod
    def _plain_notes(text):
        """Убирает markdown-разметку из описания релиза."""
        lines = []
        for line in (text or '').splitlines():
            line = line.strip()
            if not line:
                lines.append('')
                continue
            # «STABLE» — служебная метка канала, человеку она не нужна.
            if line.upper() == 'STABLE':
                continue
            line = re.sub(r'^#{1,6}\s*', '', line)
            line = re.sub(r'^[-*+]\s+', '• ', line)
            line = re.sub(r'\*\*(.+?)\*\*', r'\1', line)
            line = re.sub(r'`(.+?)`', r'\1', line)
            lines.append(line)
        return '\n'.join(lines).strip()

    @property
    def skip_this_version(self):
        return self.chk_skip.isChecked()


class SettingsDialog(QDialog):
    """Настройки плеера — всё, что можно включить или выключить.

    Раньше эти переключатели жили в меню значка в трее: их было
    неудобно искать, и не видно, что уже включено. Здесь они собраны по
    смыслу и с пояснением, на что влияют.

    Изменения применяются сразу — кнопки «Сохранить» нет намеренно:
    половину из них (тему, например) надо видеть в момент переключения.
    """
    changed = pyqtSignal(str, object)

    def __init__(self, parent, values):
        super().__init__(parent)
        self.setWindowTitle('Настройки')
        self.setModal(True)
        self.setMinimumWidth(480)
        # Пока заполняем поля, сигналы не нужны: иначе открытие окна
        # сразу «применяло» бы то, что и так уже стоит.
        self._loading = True

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)

        title = QLabel('Настройки')
        title.setObjectName('heading')
        root.addWidget(title)
        sub = QLabel('Изменения применяются сразу')
        sub.setObjectName('hint')
        root.addWidget(sub)

        # Прокрутка. На 1366x768 всё содержимое в окно не влезает, и до
        # нижних настроек было не добраться. Полоса появляется сама и
        # только тогда, когда содержимое выше окна: пока место есть, окно
        # выглядит как обычное.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        inner = QWidget()
        body = QVBoxLayout(inner)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(10)
        self.scroll.setWidget(inner)
        root.addWidget(self.scroll, 1)

        # Высоту ограничиваем экраном: иначе окно растянулось бы выше
        # монитора и прокручивать было бы нечего.
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setMaximumHeight(
                max(320, screen.availableGeometry().height() - 70))

        # --- Подключение ---
        # Сам выбор способа переехал в отдельное меню по значку в левом
        # нижнем углу окна: его меняют заметно чаще всего остального.
        # Здесь осталась только кнопка, чтобы не искать это меню заново.
        conn = QGroupBox('Подключение')
        conn_lay = QVBoxLayout(conn)
        conn_lay.setContentsMargins(12, 8, 12, 12)
        conn_lay.setSpacing(8)
        self.lbl_conn = QLabel('')
        self.lbl_conn.setObjectName('hint')
        self.lbl_conn.setWordWrap(True)
        conn_lay.addWidget(self.lbl_conn)
        self.btn_conn = QPushButton('Настроить подключение...')
        self.btn_conn.clicked.connect(
            lambda: self.changed.emit('open_connection', True))
        conn_lay.addWidget(self.btn_conn, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(conn)

        # --- Оформление ---
        look = QGroupBox('Оформление')
        look_lay = QFormLayout(look)
        look_lay.setContentsMargins(12, 8, 12, 12)
        look_lay.setSpacing(8)
        self.cmb_theme = NoWheelComboBox()
        for key, title in theme.THEME_TITLES.items():
            self.cmb_theme.addItem(title, key)
        pos = self.cmb_theme.findData(values.get('theme', theme.DEFAULT_THEME))
        self.cmb_theme.setCurrentIndex(pos if pos >= 0 else 0)
        self.cmb_theme.currentIndexChanged.connect(
            lambda: self._emit('theme', self.cmb_theme.currentData()))
        look_lay.addRow('Тема:', self.cmb_theme)

        # Своя тема собирается из двух цветов — так её и задаём: остальные
        # оттенки выводятся из них автоматически.
        self.btn_bg = QPushButton()
        self.btn_bg.setObjectName('color')
        self.btn_accent = QPushButton()
        self.btn_accent.setObjectName('color')
        self._paint_color_button(
            self.btn_bg, values.get('custom_bg') or theme.CUSTOM_BG_DEFAULT)
        self._paint_color_button(
            self.btn_accent,
            values.get('custom_accent') or theme.CUSTOM_ACCENT_DEFAULT)
        self.btn_bg.clicked.connect(
            lambda: self._pick_color('custom_bg', self.btn_bg))
        self.btn_accent.clicked.connect(
            lambda: self._pick_color('custom_accent', self.btn_accent))
        look_lay.addRow('Свой фон:', self.btn_bg)
        look_lay.addRow('Свой акцент:', self.btn_accent)

        # Масштаб интерфейса: на маленьком экране крупный шрифт читается
        # лучше, а на большом мониторе удобнее уменьшить.
        self.cmb_scale = NoWheelComboBox()
        for percent in range(theme.UI_SCALE_RANGE[0], theme.UI_SCALE_RANGE[1] + 1, 10):
            self.cmb_scale.addItem(f'{percent}%', percent)
        try:
            cur_scale = int(values.get('ui_scale', theme.UI_SCALE_DEFAULT))
        except (TypeError, ValueError):
            cur_scale = theme.UI_SCALE_DEFAULT
        pos_scale = self.cmb_scale.findData(cur_scale)
        if pos_scale < 0:
            pos_scale = self.cmb_scale.findData(theme.UI_SCALE_DEFAULT)
        self.cmb_scale.setCurrentIndex(max(0, pos_scale))
        self.cmb_scale.currentIndexChanged.connect(
            lambda: self._emit('ui_scale', self.cmb_scale.currentData()))
        look_lay.addRow('Масштаб интерфейса:', self.cmb_scale)

        hint_look = QLabel('Свои цвета применяются, когда выбрана тема '
                           '«Своя — из двух цветов».')
        hint_look.setObjectName('hint')
        hint_look.setWordWrap(True)
        look_lay.addRow(hint_look)
        body.addWidget(look)

        # --- Воспроизведение ---
        play = QGroupBox('Воспроизведение')
        play_lay = QVBoxLayout(play)
        play_lay.setContentsMargins(12, 8, 12, 12)
        play_lay.setSpacing(8)
        hw_row = QHBoxLayout()
        hw_row.addWidget(QLabel('Декодирование видео:'))
        self.cmb_hwdec = NoWheelComboBox()
        for i, mode in enumerate(config.HWDEC_MODES):
            self.cmb_hwdec.addItem(mode[1], i)
        try:
            cur = int(values.get('hwdec', config.HWDEC_INDEX))
        except (TypeError, ValueError):
            cur = config.HWDEC_INDEX
        self.cmb_hwdec.setCurrentIndex(
            max(0, min(len(config.HWDEC_MODES) - 1, cur)))
        self.cmb_hwdec.currentIndexChanged.connect(
            lambda: self._emit('hwdec', self.cmb_hwdec.currentIndex()))
        hw_row.addWidget(self.cmb_hwdec, 1)
        play_lay.addLayout(hw_row)
        # Шаг перемотки стрелками: у каждого свой привычный размер.
        seek_row = QHBoxLayout()
        seek_row.addWidget(QLabel('Шаг перемотки:'))
        self.cmb_seek = NoWheelComboBox()
        for step in config.SEEK_STEPS:
            self.cmb_seek.addItem('%d с' % step, step)
        try:
            cur_step = int(values.get('seek_step', config.DEFAULT_SEEK_STEP))
        except (TypeError, ValueError):
            cur_step = config.DEFAULT_SEEK_STEP
        if cur_step not in config.SEEK_STEPS:
            cur_step = config.DEFAULT_SEEK_STEP
        self.cmb_seek.setCurrentIndex(config.SEEK_STEPS.index(cur_step))
        self.cmb_seek.currentIndexChanged.connect(
            lambda: self._emit('seek_step',
                               self.cmb_seek.currentData()))
        seek_row.addWidget(self.cmb_seek, 1)
        play_lay.addLayout(seek_row)
        self.chk_compat = self._check(
            play_lay, 'compat', 'Режим совместимости (старые видеокарты)',
            values.get('compat'))
        self.chk_auto = self._check(
            play_lay, 'auto_quality', 'Подбирать качество по скорости канала',
            values.get('auto_quality'))
        body.addWidget(play)

        # --- Окно и список ---
        win = QGroupBox('Окно и список')
        win_lay = QVBoxLayout(win)
        win_lay.setContentsMargins(12, 8, 12, 12)
        win_lay.setSpacing(8)
        self.chk_tray = self._check(
            win_lay, 'close_to_tray', 'Сворачивать в трей при закрытии',
            values.get('close_to_tray'))
        self.chk_list = self._check(
            win_lay, 'playlist_panel', 'Показывать список роликов канала',
            values.get('playlist_panel'))
        body.addWidget(win)

        # --- Кэш ---
        cache = QGroupBox('Кэш')
        cache_lay = QVBoxLayout(cache)
        cache_lay.setContentsMargins(12, 8, 12, 12)
        cache_lay.setSpacing(8)
        self.chk_cache = self._check(
            cache_lay, 'clean_cache_on_exit', 'Удалять кэш при выходе',
            values.get('clean_cache_on_exit'))
        hint = QLabel('Если выключить, скачанное остаётся на диске: '
                      'повторный запуск ролика идёт сразу, но место оно '
                      'занимает всегда.')
        hint.setObjectName('hint')
        hint.setWordWrap(True)
        cache_lay.addWidget(hint)
        btn_clear = QPushButton('Очистить кэш сейчас')
        btn_clear.clicked.connect(lambda: self.changed.emit('clear_cache', True))
        cache_lay.addWidget(btn_clear, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(cache)

        # --- О программе ---
        #
        # Последним блоком, чтобы не мешал настройкам. Первая строка
        # крупнее остальных: по ней видно версию, с которой человек
        # работает, — раньше сборки различали по дате файла, и это путало.
        about = QGroupBox('О программе')
        about_lay = QVBoxLayout(about)
        about_lay.setContentsMargins(12, 8, 12, 12)
        about_lay.setSpacing(4)
        for i, line in enumerate(version.about_text()):
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            if i == 0:
                lbl.setObjectName('about_title')
            else:
                lbl.setObjectName('hint')
            about_lay.addWidget(lbl)

        # Обновления — здесь же, рядом со сведениями о версии.
        upd_row = QHBoxLayout()
        self.cmb_channel = NoWheelComboBox()
        self.cmb_channel.addItem('Только проверенные (stable)', 'stable')
        self.cmb_channel.addItem('Все сборки (beta)', 'beta')
        pos_ch = self.cmb_channel.findData(
            values.get('update_channel', 'stable'))
        self.cmb_channel.setCurrentIndex(max(0, pos_ch))
        self.cmb_channel.currentIndexChanged.connect(
            lambda: self._emit('update_channel',
                               self.cmb_channel.currentData()))
        upd_row.addWidget(QLabel('Канал обновлений:'))
        upd_row.addWidget(self.cmb_channel, 1)
        about_lay.addLayout(upd_row)

        self.chk_upd = QCheckBox('Проверять обновления при запуске')
        self.chk_upd.setChecked(bool(values.get('check_updates', True)))
        self.chk_upd.toggled.connect(
            lambda on: self._emit('check_updates', bool(on)))
        about_lay.addWidget(self.chk_upd)

        btn_upd = QPushButton('Проверить обновления сейчас')
        btn_upd.clicked.connect(
            lambda: self.changed.emit('check_updates_now', True))
        about_lay.addWidget(btn_upd, 0, Qt.AlignmentFlag.AlignLeft)

        body.addWidget(about)

        body.addStretch(1)
        # Кнопка живёт вне прокрутки: до неё надо дотягиваться всегда.
        btn_ok = QPushButton('Готово')
        btn_ok.setObjectName('primary')
        btn_ok.setMinimumHeight(38)
        btn_ok.clicked.connect(self.accept)
        root.addWidget(btn_ok)

        self._loading = False

    def _check(self, layout, key, text, value):
        box = QCheckBox(text)
        box.setChecked(bool(value))
        box.toggled.connect(lambda on: self._emit(key, bool(on)))
        layout.addWidget(box)
        return box

    def _paint_color_button(self, btn, color):
        """Красит кнопку в выбранный цвет и подписывает его кодом.

        Цвет текста подбираем по яркости фона: на светлом фоне белые
        буквы не видны.
        """
        color = str(color or theme.CUSTOM_BG_DEFAULT)
        btn.setText(color)
        btn.setStyleSheet(
            'background: %s; color: %s;' % (
                color,
                '#000000' if theme._luminance(color) > 0.55 else '#ffffff'))

    def _pick_color(self, key, btn):
        """Открывает системный выбор цвета и применяет выбранное."""
        picked = QColorDialog.getColor(
            QColor(btn.text()), self, 'Выберите цвет')
        if not picked.isValid():
            return
        value = picked.name()
        self._paint_color_button(btn, value)
        # Смена цвета — это и есть выбор своей темы: переключаем список,
        # иначе настройка ни на что не повлияла бы.
        pos = self.cmb_theme.findData(theme.CUSTOM_THEME)
        if pos >= 0 and self.cmb_theme.currentIndex() != pos:
            self.cmb_theme.blockSignals(True)
            self.cmb_theme.setCurrentIndex(pos)
            self.cmb_theme.blockSignals(False)
            self._emit('theme', theme.CUSTOM_THEME)
        self._emit(key, value)

    def set_conn_status(self, text):
        """Показывает состояние подключения прямо в окне настроек."""
        try:
            self.lbl_conn.setText(str(text or ''))
        except Exception:
            pass

    def _emit(self, key, value):
        if self._loading:
            return
        self.changed.emit(key, value)


class FoundDialog:
    """Найденный в поиске чат в том виде, в каком его ждёт остальной код.

    У каналов из списка есть .id, .name и .entity; у результата поиска —
    .title и сам объект. Обёртка сводит одно к другому, чтобы найденный
    канал открывался тем же путём, что и свой: тогда у него работает и
    список роликов, и запуск воспроизведения.
    """

    def __init__(self, chat):
        self.entity = chat
        self.id = getattr(chat, 'id', None)
        self.name = getattr(chat, 'title', '') or ''

    def __repr__(self):
        return f"FoundDialog({self.name!r})"


# Поправка к базовой линии надписи в строке состояния. Буквы ложатся по
# целым точкам экрана, поэтому замер даёт не одно значение, а широкий
# ровный участок: от -0.6 до +0.25 точки запас сверху и снизу выходит
# одинаковым. Ноль стоит посередине этого участка, так что надпись
# остаётся по центру и при другом округлении шрифта.
STATUS_INK_NUDGE = 0.0


class StatusLabel(QLabel):
    """Надпись строки состояния, посаженная по центру полосы.

    Обычный QLabel с выравниванием по центру центрует прямоугольник
    строки шрифта, а не сами буквы. У Segoe UI подъём 13 точек при росте
    заглавных чуть больше восьми: почти весь запас уходит наверх, и буквы
    садятся выше середины — тем заметнее, чем ниже полоса. Сдвинуть саму
    метку нельзя: она дочернее окно, и Qt обрезает её по краям полосы.
    Поэтому метку растягиваем на всю полосу, а строку внутри неё рисуем
    сами, по своей базовой линии.

    Поля слева и справа берём из свойств, а не из геометрии метки: так
    их можно менять в одном месте, и метка остаётся растянутой на всю
    полосу, как ей и положено.
    """

    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.pad_left = 0
        self.pad_right = 0

    def _text_color(self):
        """Цвет надписи из темы.

        Правило QStatusBar {{ color: ... }} красит саму полосу, а метке
        её палитра не передаётся: у метки WindowText остаётся белым. Если
        взять цвет у метки, надпись станет белой вместо приглушённой,
        поэтому спрашиваем цвет у полосы-родителя, а к метке обращаемся
        только если полосы почему-то нет.
        """
        bar = self.parentWidget()
        if isinstance(bar, QStatusBar):
            return bar.palette().color(bar.foregroundRole())
        return self.palette().color(self.foregroundRole())

    def paintEvent(self, event):
        """Рисует надпись вручную, чтобы задать её положение по вертикали.

        Выравнивание по центру здесь не годится — оно центрует
        прямоугольник строки шрифта, а он выше самих букв, и надпись
        садится выше середины. Поэтому базовую линию считаем сами.
        """
        painter = QPainter(self)
        fm = QFontMetricsF(self.font())
        baseline = self._baseline(fm)
        painter.setFont(self.font())
        painter.setPen(self._text_color())
        painter.drawText(QPointF(float(self.rect().left() + self.pad_left),
                                 baseline), self.text())
        painter.end()

    def _baseline(self, fm):
        """Базовая линия, при которой надпись встаёт по центру полосы.

        Опираемся на то, сколько строк буквы занимают над базовой линией
        и под ней (см. _ink_spans). Так запас выходит одинаковым сверху и
        снизу на любой высоте полосы: над буквами и под ними остаётся
        ровно столько пустых строк, сколько задано при расчёте высоты.
        """
        above, below = self._ink_spans(fm)
        body = above + below
        # Нечётный остаток отдаём нижнему запасу: если делить пополам с
        # округлением вниз, запас снизу выходил больше верхнего.
        pad = max(0, (self.height() - body) // 2)
        return max(1, pad + above + STATUS_INK_NUDGE)

    def _ink_spans(self, fm):
        """Сколько строк буквы занимают над базовой линией и под ней.

        Числа замерены по снимкам надписей на всех масштабах (80, 100,
        120, 150%): над базовой линией лежит рост заглавных плюс запас на
        сглаживание, под линией — хвосты вроде «у» и «р». Подъём и спуск
        шрифта для этого не годятся: они вдвое больше самих букв, и
        поправка по ним уводила надпись вверх.

        Мерка не зависит от текущей надписи: иначе высота полосы и
        положение строки менялись бы при каждой смене сообщения.
        """
        cap = fm.capHeight() or fm.ascent()
        # Замерено по снимкам настоящих надписей: над базовой линией
        # укладывается рост заглавных, под ней — одна-две строки хвостов
        # вроде «у» и «р». Брать под хвосты больше нельзя: запас снизу
        # выходил вдвое больше верхнего, и надпись садилась к верху.
        above = int(round(cap))
        below = max(1, int(round(cap * 0.12)))
        return above, below


class ThinStatusBar(QStatusBar):
    """Строка состояния, высота которой выводится из шрифта.

    QStatusBar сам расставляет отступы для своих элементов — две точки
    слева и три сверху и снизу — и пересчитывает их при каждом изменении
    размера. Через настройки их не убрать, поэтому надпись кладём прямо
    в полосу и размещаем сами.

    Высота считается от метрик шрифта, а не берётся отдельной
    константой: шрифт растёт вместе с масштабом интерфейса, и жёсткая
    высота при этом рассинхронизировалась бы с ним. При 12 точках
    шрифта и высоте 13 полоса была вровень с буквами, а при 120% шрифт
    становился 14 точек при полосе 15 — запас скакал от нуля к единице,
    и надпись то липла к краю, то висела выше середины.

    Про вертикаль отдельно — см. StatusLabel: буквы центруем по росту
    заглавных, иначе они садятся выше середины.
    """

    # Поля: слева четыре точки, справа три при 100%, и они растут вместе
    # с масштабом. На 120% левое поле выходит в пять точек — столько, на
    # сколько надпись просили отодвинуть от края окна.
    PAD_LEFT = 4
    PAD_RIGHT = 3

    def __init__(self, parent=None, height=None, margin=None):
        super().__init__(parent)
        # margin оставлен ради совместимости со старыми вызовами: поля
        # теперь считаются от шрифта, а не от одного числа.
        self._margin = max(0, int(margin if margin is not None else 0))
        self.setSizeGripEnabled(False)
        self.label = StatusLabel('', self)
        self.label.setObjectName('statusline')
        self.label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._fit(height)
        self._place()

    def _fit(self, height=None):
        """Ставит высоту полосы по фактическому шрифту надписи.

        Запас нужен одинаковый сверху и снизу. Высоту строим от того,
        сколько строк буквы занимают над базовой линией и под ней: у
        Segoe UI это подъём строчных плюс выносные элементы, и только так
        выходит ровный запас на любом масштабе.

        Вызывается ещё раз из MainWindow после применения таблицы стилей:
        при создании окна шрифт из стиля ещё не дошёл до метки, и высота
        считалась бы по системному.
        """
        fm = QFontMetricsF(self.label.font())
        above, below = self._ink_spans(fm)
        if height is None:
            # Запас с каждой стороны — не меньше двух точек и не меньше
            # трети высоты букв: на крупном масштабе двух точек мало.
            body = above + below
            pad = max(2, int(round(body * 0.2)))
            need = body + 2 * pad
        else:
            need = int(height)
        self.setFixedHeight(max(9, need))
        self._place()

    def _ink_spans(self, fm):
        """Сколько строк буквы занимают над базовой линией и под ней.

        Числа замерены по снимкам: у Segoe UI над линией укладывается
        рост заглавных плюс строка на сглаживание, под линией — хвосты
        вроде «р» и «у» высотой около пятой части.

        Мерка не зависит от текущей надписи: иначе высота полосы и
        положение строки менялись бы при каждой смене сообщения.
        """
        cap = fm.capHeight() or fm.ascent()
        above = int(round(cap)) + 1
        below = max(1, int(round(cap * 0.2)))
        return above, below

    def refit(self):
        """Пересчитывает высоту после смены шрифта или масштаба."""
        self._fit()

    def setText(self, text):
        self.label.setText(text)
        self._place()

    def _place(self):
        """Растягивает надпись на всю полосу, оставляя поля по бокам.

        Метку растягиваем во всю высоту полосы: строку внутри неё
        размещает StatusLabel.paintEvent по базовой линии. По горизонтали
        поля заданы у самой метки — так они не зависят от её геометрии.

        Поля растут вместе с масштабом интерфейса: при 100% левое поле
        четыре точки, при 120% — пять, как и положено по масштабу.
        """
        factor = theme.ui_scale() / 100.0
        self.label.pad_left = max(1, int(round(self.PAD_LEFT * factor)))
        self.label.pad_right = max(1, int(round(self.PAD_RIGHT * factor)))
        self.label.setGeometry(0, 0, max(0, self.width()),
                               max(0, self.height()))
        self.label.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place()


class ConnectionMenu(QMenu):
    """Меню подключения к Telegram.

    Переехало из окна настроек: способ подключения меняют заметно чаще
    всего остального, и держать его за две кнопки было неудобно. Те же
    три варианта, что и в Telegram: без прокси, системный прокси и свой
    MTProto со своими сервером, портом и секретом.
    """
    changed = pyqtSignal(str, object)

    def __init__(self, parent, values):
        super().__init__(parent)
        self._loading = True
        values = values or {}

        self.lbl_state = QLabel('')
        self.lbl_state.setObjectName('hint')
        # Вторая строка — последнее сообщение бэкенда. Держим её отдельно:
        # раньше состояние связи и текст состояния писались в одну метку, и
        # сообщение «Загрузка...» затирало «Связь есть».
        self.lbl_status = QLabel('')
        self.lbl_status.setObjectName('hint')
        self.lbl_status.setWordWrap(True)
        head = QWidget()
        head_lay = QVBoxLayout(head)
        head_lay.setContentsMargins(0, 0, 0, 0)
        head_lay.setSpacing(2)
        head_lay.addWidget(self.lbl_state)
        head_lay.addWidget(self.lbl_status)
        act_head = QWidgetAction(self)
        act_head.setDefaultWidget(self._wrap(head))
        self.addAction(act_head)
        self.addSeparator()

        mode = str(values.get('proxy_mode') or config.DEFAULT_PROXY_MODE)
        if mode not in config.PROXY_MODES:
            mode = config.DEFAULT_PROXY_MODE

        # Эксклюзивная группа: выбран ровно один способ.
        self.group = QActionGroup(self)
        self.group.setExclusive(True)
        self.act_mode = {}
        for key in config.PROXY_MODES:
            act = self.addAction(config.PROXY_MODE_TITLES[key])
            act.setCheckable(True)
            act.setChecked(key == mode)
            act.triggered.connect(
                lambda _checked, k=key: self._choose_mode(k))
            self.group.addAction(act)
            self.act_mode[key] = act

        self.addSeparator()

        # Поля своего MTProto — прямо в меню: держать их в отдельном окне
        # ради трёх строк незачем.
        self.ed_host = QLineEdit(str(values.get('mtproxy_host') or ''))
        self.ed_host.setPlaceholderText(config.MT_PROXY_DEFAULT[0])
        self.ed_port = QLineEdit(str(values.get('mtproxy_port') or ''))
        self.ed_port.setPlaceholderText(str(config.MT_PROXY_DEFAULT[1]))
        self.ed_secret = QLineEdit(str(values.get('mtproxy_secret') or ''))
        self.ed_secret.setPlaceholderText('секрет MTProto')

        form = QWidget()
        form_lay = QFormLayout(form)
        form_lay.setContentsMargins(14, 4, 14, 8)
        form_lay.setSpacing(6)
        form_lay.addRow('Сервер:', self.ed_host)
        form_lay.addRow('Порт:', self.ed_port)
        form_lay.addRow('Секрет:', self.ed_secret)
        self.form = form
        act_form = QWidgetAction(self)
        act_form.setDefaultWidget(form)
        self.addAction(act_form)

        for ed in (self.ed_host, self.ed_port, self.ed_secret):
            # editingFinished, а не textChanged: иначе каждое нажатие
            # клавиши писало бы настройки и дёргало переподключение.
            ed.editingFinished.connect(self._emit_mtproxy)

        self.set_connected(False)
        self._sync_form(mode)
        self._loading = False

    @staticmethod
    def _wrap(widget):
        """Обёртка с отступами: QWidgetAction иначе жмётся к краям."""
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(18, 8, 18, 2)
        lay.addWidget(widget)
        return box

    def _sync_form(self, mode):
        """Поля своего прокси нужны только для своего прокси."""
        self.form.setEnabled(mode == 'custom')

    def _choose_mode(self, key):
        self._sync_form(key)
        self._emit('proxy_mode', key)

    def _emit_mtproxy(self):
        self._emit('mtproxy', (
            self.ed_host.text().strip(),
            self.ed_port.text().strip(),
            self.ed_secret.text().strip(),
        ))

    def set_connected(self, connected):
        """Показывает в меню, есть ли сейчас связь."""
        self.lbl_state.setText(
            'Связь есть' if connected else 'Связи с Telegram нет')
        self.lbl_state.setObjectName('hint' if connected else 'error')
        # Стиль перечитываем вручную: objectName сменился, а таблица
        # стилей сама об этом не узнает.
        self.lbl_state.style().unpolish(self.lbl_state)
        self.lbl_state.style().polish(self.lbl_state)

    def set_status(self, text):
        """Последнее сообщение бэкенда — отдельной строкой под состоянием."""
        try:
            self.lbl_status.setText(str(text or ''))
        except Exception:
            pass

    def current_mode(self):
        for key, act in self.act_mode.items():
            if act.isChecked():
                return key
        return config.DEFAULT_PROXY_MODE

    def _emit(self, key, value):
        if self._loading:
            return
        self.changed.emit(key, value)


class CornerConnectionButton(QWidget):
    """Значок связи в левом нижнем углу окна.

    Обычный дочерний виджет, а не отдельное окно. Отдельное окно с
    флагом «поверх» висело над всеми программами сразу — и над чужими
    тоже, чего быть не должно. Дочерний виджет всегда остаётся внутри
    своего окна, а чтобы он не прятался за списком каналов, его поднимают
    поверх соседей.

    Размер берём по системному курсору: на мониторах с крупным курсором
    значок иначе оказался бы крошечным.
    """

    clicked = pyqtSignal()

    # Отступы от угла окна при 100%. Все они растут вместе с масштабом
    # интерфейса: иначе на 150% значок лип к строке состояния, а на 50%
    # висел высоко над ней.
    MARGIN = 10
    SHIFT_RIGHT = 4
    # Вверх поднимаем на строку состояния и на высоту горизонтальной
    # полосы прокрутки списка каналов, которую убрали: раньше значок
    # стоял ровно на её месте.
    #
    # Отрицательное значение опускает значок: на глаз он висел слишком
    # высоко над строкой состояния.
    SHIFT_DOWN = 8
    SHIFT_UP = 16 - icons.SCROLLBAR_THICKNESS - SHIFT_DOWN

    # Шаг и частота вращения кольца при подключении.
    SPIN_STEP = 30
    SPIN_INTERVAL = 60

    def __init__(self, owner, scale=None):
        super().__init__(owner)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._owner = owner
        self._state = None
        self._angle = 0
        self._size = self._size_for(scale)
        self.setFixedSize(self._size, self._size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('Подключение')
        # Кольцо при подключении крутится, пока идёт попытка.
        self._spin = QTimer(self)
        self._spin.setInterval(self.SPIN_INTERVAL)
        self._spin.timeout.connect(self._tick)
        if owner is not None:
            owner.installEventFilter(self)
        self.update_position()

    def size_px(self):
        return self._size

    def _size_for(self, scale=None):
        """Размер значка по системному курсору и масштабу интерфейса.

        Отдельный параметр scale оставлен для тестов: если он не задан,
        берётся текущий масштаб. Иначе значок оставался одного размера на
        всех масштабах, а строка состояния под ним росла — зазор таял, и
        на 150% значок налезал на полосу.
        """
        if scale is None:
            factor = theme.ui_scale() / 100.0
        else:
            factor = max(1, int(scale))
        return max(16, int(round(icons.cursor_size() * factor)))

    def refit(self):
        """Пересчитывает размер и место после смены масштаба интерфейса."""
        self._size = self._size_for()
        self.setFixedSize(self._size, self._size)
        self.update_position()

    def state(self):
        """Текущее состояние значка."""
        return self._state

    def set_state(self, state):
        """Меняет вид значка: связь, обрыв или идёт подключение."""
        state = state if state in ('ok', 'off', 'connecting') else 'off'
        if state == self._state:
            return
        self._state = state
        if state == 'connecting':
            self._angle = 0
            self._spin.start()
            self.setToolTip('Подключение к Telegram...')
        else:
            self._spin.stop()
            self.setToolTip('Связь с Telegram есть' if state == 'ok'
                            else 'Связи с Telegram нет')
        self.update()

    def set_connected(self, connected):
        """Совместимость: связь есть или нет."""
        self.set_state('ok' if connected else 'off')

    def _tick(self):
        self._angle = (self._angle + self.SPIN_STEP) % 360
        self.update()

    def paintEvent(self, event):
        # Картинку берём из кэша: без него каждый кадр анимации собирал бы
        # слой, размывал его через blur и заново рисовал тень — заметная
        # работа для процессора слабой машины.
        pm = icons.connection_pixmap(self._size, self._state or 'off', self._angle)
        p = QPainter(self)
        p.drawPixmap(0, 0, pm)
        p.end()

    def _status_height(self):
        """Высота строки состояния окна-хозяина, если она видна."""
        try:
            status = self._owner.statusBar()
        except Exception:
            return 0
        if status is None:
            return 0
        try:
            return int(status.height()) if status.isVisible() else 0
        except Exception:
            return 0

    def update_position(self):
        """Ставит значок в левый нижний угол окна, но выше строки состояния.

        Все отступы пересчитываются от масштаба интерфейса: значок
        сидит на постоянном расстоянии от краёв и от полосы состояния, а
        не уезжает при смене масштаба.
        """
        owner = self._owner
        if owner is None:
            return
        factor = theme.ui_scale() / 100.0
        x = int(round((self.MARGIN + self.SHIFT_RIGHT) * factor))
        shift_up = int(round(self.SHIFT_UP * factor))
        margin = int(round(self.MARGIN * factor))
        y = (owner.height() - self._size - margin
             - shift_up - self._status_height())
        self.move(max(0, x), max(0, y))
        # Поверх соседей: иначе значок уходит под список каналов.
        self.raise_()

    def eventFilter(self, obj, event):
        if obj is self._owner and event.type() in (
                QEvent.Type.Resize, QEvent.Type.Show):
            self.update_position()
        return super().eventFilter(obj, event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    # Результат проверки обновлений из фонового потока.
    #
    # Сигнал, а не QTimer: таймер из чужого потока ненадёжен (у него нет
    # своего цикла событий Qt), а сигнал доставляется в поток получателя
    # через очередь как положено. Без этого проверка то срабатывала, то
    # нет — и хуже всего, что молча.
    _update_ready = pyqtSignal(object, str, bool)
    # Ход загрузки обновления и её завершение — тоже сигналами.
    _download_progress = pyqtSignal(int, int)
    _download_done = pyqtSignal(bool, str, str)
    # Вход по QR: ссылка для кода, состояние подсказки и итог.
    _qr_url = pyqtSignal(str)
    _qr_state = pyqtSignal(str)
    _qr_done = pyqtSignal(str)

    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        # Сигнал проверки обновлений идёт из фонового потока — соединение
        # ставим сразу, до всего остального.
        self._update_ready.connect(self._on_update_checked)
        self.player_window = None
        self._active_dialog_id = None
        self._preload_doc_id = None
        # Названия роликов текущего канала: по ним заново собирается список
        # с пометками, когда что-то посмотрели.
        self._video_titles = []
        # Сами сообщения того же канала: список можно отфильтровать и
        # развернуть, поэтому индексы берутся отсюда, а не из current_messages.
        self._video_messages = []
        # Порядок списка: сначала старые (как эпизоды сериала) или сначала
        # новые. Именно этот порядок совпадает с порядком плейлиста, поэтому
        # «следующий ролик» идёт вниз по списку, а не вверх.
        self._order_newest_first = False
        # В списке сейчас заглушка, а не ролики: по этому флагу open_player
        # не даёт открыть «видео» с номером строки-заглушки.
        self._list_has_videos = False

        # Поведение крестика: свернуть в трей или закрыть программу.
        # _really_quit отличает настоящий выход (из меню трея) от крестика,
        # иначе из трея было бы не выйти.
        self._close_to_tray = bool(self._setting('close_to_tray'))
        self._really_quit = False
        self._tray_notice_shown = False
        self.tray = None
        # Один и тот же пункт показывается и в меню окна, и в меню трея,
        # чтобы галочки не разъезжались.
        self.act_close_to_tray = QAction(
            'Сворачивать в трей при закрытии', self)
        self.act_close_to_tray.setCheckable(True)
        self.act_close_to_tray.setChecked(self._close_to_tray)
        self.act_close_to_tray.toggled.connect(self._toggle_close_to_tray)

        # Режим совместимости: на старых и встроенных видеокартах
        # аппаратный декодер отдаёт чёрный кадр при живом звуке. Плеер
        # умеет включать его сам, но иногда проще задать заранее.
        self.act_compat = QAction(
            'Режим совместимости (старые видеокарты)', self)
        self.act_compat.setCheckable(True)
        self.act_compat.setChecked(bool(self._setting('compat')))
        self.act_compat.toggled.connect(self._toggle_compat)

        # Тема оформления применяется сразу: её надо видеть в момент
        # переключения, а не после перезапуска.
        theme.set_theme_key(self._setting('theme', theme.DEFAULT_THEME))
        self._apply_theme()

        # Избранные каналы: отметки лежат в настройках, а в списке такие
        # каналы поднимаются наверх.
        self._favorites = set(self._setting('favorites') or [])
        # Скрытые каналы: убраны из списка, но лежат в папке «Скрытые»,
        # откуда их можно вернуть.
        self._hidden = set(self._setting('hidden') or [])

        # Найденное в Telegram. Ищем сразу везде, отдельного режима нет:
        # если канала нет среди своих, его логично искать в Telegram, а не
        # догадываться, что для этого есть ещё какая-то кнопка.
        self._search_results = []
        self._search_rows = []
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(450)
        self._search_timer.timeout.connect(self._run_channel_search)

        # Версия в заголовке: по ней видно, какая сборка запущена, без
        # лазания в свойства файла и по датам.
        self.setWindowTitle(version.window_title(config.APP_TITLE))
        icon = paths.load_app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self.resize(800, 550)

        central = QWidget()
        main_lay = QVBoxLayout(central)
        # Нижнее поле убираем: по умолчанию QVBoxLayout оставляет под
        # сплиттером девять точек пустой площади цвета фона, а строка
        # состояния снизу того же цвета. На экране они сливаются в одну
        # полосу, и её высота кажется вдвое больше настоящей.
        main_lay.setContentsMargins(9, 9, 9, 0)

        # Выбор папки из ТГ
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Папка:"))
        self.folder_combo = QComboBox()
        self.folder_combo.currentIndexChanged.connect(self.on_folder_changed)
        top_bar.addWidget(self.folder_combo, 1)

        self.channel_search = QLineEdit()
        self.channel_search.setPlaceholderText(
            "Поиск: свои каналы и весь Telegram...")
        self.channel_search.textChanged.connect(self._on_channel_query)
        top_bar.addWidget(self.channel_search, 1)

        # Кнопок «Обновить» и «Войти» здесь больше нет.
        #
        # «Обновить» дублировала то, что и так происходит само: список
        # перечитывается при открытии канала, а устаревшие данные видны
        # по пустому списку. «Войти» появилась на время, когда вход
        # показывался поверх плеера и его можно было случайно закрыть.
        # Теперь вход идёт ДО окна и есть вход по QR — кнопка не нужна.

        # Меню окна: настройки и уход за кэшем.
        self.btn_menu = QPushButton("⚙")
        self.btn_menu.setToolTip("Настройки")
        self.btn_menu.setFixedWidth(34)
        win_menu = QMenu(self)
        act_settings = win_menu.addAction("Настройки...")
        act_settings.triggered.connect(self.open_settings)
        win_menu.addSeparator()
        win_menu.addAction(self.act_close_to_tray)
        win_menu.addAction(self.act_compat)
        win_menu.addSeparator()
        act_clear = win_menu.addAction("Очистить кэш сейчас")
        act_clear.triggered.connect(self.clear_cache)
        self.btn_menu.setMenu(win_menu)
        top_bar.addWidget(self.btn_menu)

        main_lay.addLayout(top_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Список каналов
        self.channel_list = QListWidget()
        self.channel_list.setIconSize(QSize(28, 28))
        # Горизонтальная прокрутка не нужна: длинные названия и так
        # обрезаются с подсказкой, а полоса только съедала высоту снизу.
        self.channel_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.channel_list.currentRowChanged.connect(self.on_channel_selected)
        # ПКМ по каналу: избранное, а в режиме поиска — подписка.
        self.channel_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.channel_list.customContextMenuRequested.connect(
            self._on_channel_menu)
        splitter.addWidget(self.channel_list)

        # Список видео: сверху поиск по названию и переключатель порядка.
        video_pane = QWidget()
        video_lay = QVBoxLayout(video_pane)
        video_lay.setContentsMargins(0, 0, 0, 0)
        video_lay.setSpacing(4)

        video_bar = QHBoxLayout()
        self.video_search = QLineEdit()
        self.video_search.setPlaceholderText("Поиск видео...")
        self.video_search.textChanged.connect(
            lambda *_: self._render_video_list())
        video_bar.addWidget(self.video_search, 1)

        self.btn_order = QPushButton("↑ Сначала старые")
        self.btn_order.setToolTip("Порядок роликов в списке")
        self.btn_order.clicked.connect(self._toggle_order)
        video_bar.addWidget(self.btn_order)
        video_lay.addLayout(video_bar)

        self.video_list = QListWidget()
        # itemActivated срабатывает и на Enter, и на двойной клик.
        self.video_list.itemActivated.connect(self.open_player)
        self.video_list.currentRowChanged.connect(self.on_video_selected)
        video_lay.addWidget(self.video_list, 1)

        # Кнопка догрузки стоит под списком, а не строкой внутри: строка
        # списка вела бы себя как ролик — на неё можно было бы встать и
        # нажать Enter, и открылся бы случайный ролик.
        self.btn_more = QPushButton()
        self.btn_more.setToolTip(
            "Прочитать более старые сообщения канала: в канале их больше, "
            "чем помещается в один список")
        self.btn_more.clicked.connect(self._load_more_videos)
        self.btn_more.hide()
        video_lay.addWidget(self.btn_more)

        splitter.addWidget(video_pane)

        splitter.setSizes([300, 500])
        main_lay.addWidget(splitter)

        self.setCentralWidget(central)
        app_settings.restore_window_geometry(self, 'main_geometry', self._settings_dict())

        self.backend.folders_loaded.connect(self.display_folders)
        self.backend.videos_loaded.connect(self.display_videos)
        self.backend.channels_checked.connect(self._on_channels_checked)
        self.backend.status_changed.connect(self.set_status)
        self.backend.error_occurred.connect(self.show_error)
        self.backend.login_step.connect(self._on_login_step)
        self.backend.search_results.connect(self._show_search_results)

        # Строка состояния: высота выводится из шрифта (см. ThinStatusBar),
        # поля по бокам заданы там же, ручки размера в углу нет.
        self._status_bar = ThinStatusBar(self)
        self.setStatusBar(self._status_bar)
        self.lbl_status = self._status_bar.label
        self.set_status("Загрузка...")

        # Значок связи в левом нижнем углу: по нему же открывается меню
        # подключения. Состояние приходит сигналом от бэкенда.
        self.conn_menu = ConnectionMenu(self, self._settings_dict())
        self.conn_menu.changed.connect(self._apply_setting)
        self.conn_menu.set_status("Подключение...")
        self.conn_button = CornerConnectionButton(self)
        self.conn_button.clicked.connect(self._open_connection_menu)
        self._connected = False
        self._conn_state = 'off'
        try:
            self.backend.connection_changed.connect(self._on_connection_changed)
            self.backend.status_changed.connect(self._on_status_for_conn)
        except Exception:
            pass

        self._setup_tray()

    def _open_connection_menu(self):
        """Меню подключения по нажатию на значок в углу окна."""
        self.conn_menu.set_connected(self._connected)
        self.conn_menu.exec(
            self.conn_button.mapToGlobal(QPoint(0, -self.conn_menu.sizeHint().height())))

    def _on_status_for_conn(self, text):
        """Строка состояния подключения идёт и в меню."""
        try:
            self.conn_menu.set_status(text)
        except Exception:
            pass

    def _on_connection_changed(self, state):
        """Связь появилась, пропала или идёт подключение."""
        state = state if state in ('ok', 'off', 'connecting') else 'off'
        self._conn_state = state
        self._connected = (state == 'ok')
        try:
            # Значок в углу различает все три состояния: галочка, крестик
            # и крутящееся кольцо при подключении.
            self.conn_button.set_state(state)
            # В меню состояние тоже показываем: его открывают как раз
            # чтобы понять, есть ли связь.
            self.conn_menu.set_connected(self._connected)
        except Exception:
            pass
        self._update_tray_icon()

    def _tray_icon(self):
        """Значок трея: самолётик, средняя часть — в цвете акцента темы.

        В картинке средняя часть залита ключевым цветом #FF00FF. Здесь мы
        заменяем его на акцент текущей темы — поэтому значок сам меняется
        вместе с оформлением, и не нужен набор картинок под каждую тему.

        Почему ключевой цвет, а не прозрачный вырез: вырез пришлось бы
        заполнять через маски и наложения, и на этом уже дважды ломались
        — знак превращался в контур. Замена цвета проста и предсказуема.

        Заменяем с запасом по близости: не «ровно #FF00FF», а всё, что
        похоже на маджента. Так захватываются и сглаженные края заливки.
        Белые и синеватые плоскости далеко от этого цвета, их не трогаем.
        """
        path = paths.tray_icon_path()
        if not path:
            return None
        source = QImage(path)
        if source.isNull():
            return None

        try:
            accent = QColor(theme.theme()['accent'])
        except Exception:
            accent = QColor('#5288c1')

        icon = QIcon()
        for size in (16, 20, 24, 32, 48, 64):
            img = source.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation).convertToFormat(
                    QImage.Format.Format_ARGB32)
            img = self._replace_key_color(img, accent)
            icon.addPixmap(QPixmap.fromImage(img))
        return icon

    @staticmethod
    def _replace_key_color(img, accent):
        """Меняет ключевой цвет #FF00FF на акцент прямо в пикселях.

        Маджента — красный и синий в максимуме, зелёного нет. Именно по
        этому признаку и отличаем её от белого (все три в максимуме) и от
        синеватых плоскостей (красного мало).
        """
        w, h = img.width(), img.height()
        ar, ag, ab = accent.red(), accent.green(), accent.blue()
        for y in range(h):
            for x in range(w):
                c = img.pixelColor(x, y)
                if c.alpha() < 40:
                    continue
                r, g, b = c.red(), c.green(), c.blue()
                # Маджента: красный и синий высокие, зелёный — нет.
                if r > 150 and b > 150 and g < 120:
                    img.setPixelColor(x, y, QColor(ar, ag, ab, c.alpha()))
        return img

    def _update_tray_icon(self):
        """Перекрашивает значок трея под текущую тему.

        Акцентный цвет меняется вместе с темой, а значок собран из него —
        значит, после смены оформления его надо собрать заново. Раньше
        здесь стояла иконка программы, и от темы она не зависела.
        """
        if self.tray is None:
            return
        try:
            icon = self._tray_icon()
            if icon is not None and not icon.isNull():
                self.tray.setIcon(icon)
        except Exception as ex:
            print(f"[Трей] значок не обновился: {ex}")

    def _on_login_step(self, kind, message):
        """Показывает шаг входа и возвращает ответ в поток backend'а.

        Вызывается в Qt-потоке по сигналу: backend в это время ждёт
        результат на своём loop, поэтому ответить надо обязательно —
        иначе он останется висеть на ожидании навсегда.
        """
        if kind == 'error':
            QMessageBox.warning(self, 'Вход в Telegram', message)
            self.backend.answer_login(None)
            return
        dlg = LoginDialog(kind, message, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Выбран вход по QR. Отмену НЕ отправляем: по ней backend
            # выставил бы флаг «человек отказался» и закрыл программу —
            # именно это и происходило, окно просто исчезало.
            # Вместо этого сразу показываем код с экрана.
            if getattr(dlg, 'qr_chosen', False):
                self._login_by_qr()
                return
            self.backend.answer_login(dlg.value())
            return

        # Окно закрыли. На шаге номера это отказ от входа — программа
        # закроется. На шаге кода человек мог просто передумать и решить
        # войти по QR: тогда назад, к выбору, а не выход.
        if kind in ('code', 'password'):
            self.backend.answer_login(None)
            self.backend._stop_connect = False
            self._login_by_qr()
            return
        self.backend.answer_login(None)

    def _login_by_qr(self):
        """Вход по QR: показываем код и ждём подтверждения с телефона."""
        print("[Вход] выбран вход по QR")
        dlg = QrLoginDialog(self)

        def on_url(url):
            # Из потока backend в Qt — только сигналом.
            self._qr_url.emit(url)

        def on_state(state):
            self._qr_state.emit(state)

        self._qr_url.connect(dlg.set_url)
        self._qr_state.connect(dlg.set_state)

        def worker():
            import asyncio

            async def run():
                try:
                    status = await self.backend.qr_login_flow(on_url, on_state)
                except Exception as ex:
                    print(f"[Вход] QR не удался: {ex}")
                    status = 'error'
                print(f"[Вход] QR завершён: {status}")
                self._qr_done.emit(status)

            asyncio.run_coroutine_threadsafe(run(), self.backend.loop)

        def on_done(status):
            dlg.accept()
            if status == 'ok':
                self.set_status('Вход выполнен')
                # Данные загрузим заново: только что вошли в аккаунт.
                self.backend.submit_coroutine(self.backend.refresh_catalogs())

        self._qr_done.connect(on_done)
        worker()
        dlg.exec()

        # Окно закрыли кнопкой «Назад» — возвращаемся к вводу номера.
        # Для этого останавливаем ожидание входа: иначе backend остался
        # бы ждать сканирования кода, который уже никто не видит.
        if getattr(dlg, 'result_kind', None) == 'back':
            print("[Вход] возврат к вводу номера")
            self.backend._stop_connect = False
            self._login_by_phone_again()

        for sig, slot in ((self._qr_url, dlg.set_url),
                          (self._qr_state, dlg.set_state)):
            try:
                sig.disconnect(slot)
            except Exception:
                pass
        try:
            self._qr_done.disconnect(on_done)
        except Exception:
            pass

    def _login_by_phone_again(self):
        """Возвращает к окну ввода номера после отказа от QR.

        Окно спрашивает номер так же, как при обычном входе, и ответ
        уходит в backend. Отдельного канала не нужно: это тот же шаг.
        """
        dlg = LoginDialog(
            'phone',
            "Введите номер телефона в международном формате, "
            "например +79991234567", self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if getattr(dlg, 'qr_chosen', False):
                self._login_by_qr()
                return
            self.backend.answer_login(dlg.value())
        else:
            self.backend.answer_login(None)

    def set_status(self, text):
        """Сообщение в строке состояния главного окна."""
        try:
            self.lbl_status.setText(text)
        except Exception:
            self.statusBar().showMessage(text)

    def _settings_dict(self):
        return app_settings.of_backend(self.backend)

    def _setting(self, key, default=None):
        return self._settings_dict().get(key, default)

    def _set_setting_quiet(self, key, value):
        """Сохраняет настройку без окна и без всплывающих сообщений."""
        try:
            self.backend.set_setting(key, value)
        except Exception as ex:
            print(f"[Настройки] не сохранилось «{key}»: {ex}")

    def _setup_tray(self):
        """Значок в системном трее — сюда сворачивается окно по крестику.

        Если трея в системе нет, режим не включается: окно спряталось бы,
        и вернуть его стало бы нечем.
        """
        available = QSystemTrayIcon.isSystemTrayAvailable()
        print(f"[Трей] доступен: {available}")
        if not available:
            return
        # Значок в трее — самолётик, у которого средняя часть прозрачна:
        # под неё подкладывается цвет акцента из текущей темы. Так значок
        # перекрашивается вместе с оформлением, а не остаётся всегда одним
        # и тем же.
        icon = self._tray_icon()
        if icon is None or icon.isNull():
            print("[Трей] самолётик не найден — использую запасной значок")
            icon = self._fallback_tray_icon()
        else:
            print(f"[Трей] значок готов, размеры: "
                  f"{[(s.width(), s.height()) for s in icon.availableSizes()]}")
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip(f'{version.APP_NAME} {version.VERSION}')

        # Переключатели переехали в окно настроек: в меню значка их было
        # неудобно искать, и не видно, что уже включено.
        menu = QMenu()
        act_show = menu.addAction('Показать окно')
        act_show.triggered.connect(self._show_from_tray)
        menu.addSeparator()
        act_settings = menu.addAction('Настройки...')
        act_settings.triggered.connect(self.open_settings)
        menu.addSeparator()
        act_quit = menu.addAction('Выход')
        act_quit.triggered.connect(self._quit_app)

        tray.setContextMenu(menu)
        # Одиночный клик по значку тоже возвращает окно.
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self.tray = tray
        # Диагностика: видно, дошёл ли значок до оболочки. isVisible()
        # остаётся True даже когда Windows значок не показала, поэтому
        # дополнительно смотрим, есть ли у него место на экране.
        print(f"[Трей] показан: visible={tray.isVisible()}, "
              f"место={tray.geometry().x()}x{tray.geometry().y()}")
        QTimer.singleShot(1500, self._check_tray_alive)

    def _check_tray_alive(self):
        """Проверяет через полторы секунды, что значок в трее жив.

        Нужно для разбора случаев «иконки нет»: Windows может принять
        значок и тут же его потерять (например, если оболочка ещё не
        готова). По журналу видно, что произошло на самом деле.
        """
        tray = self.tray
        if tray is None:
            print("[Трей] значка нет вовсе")
            return
        geo = tray.geometry()
        print(f"[Трей] через 1.5 с: visible={tray.isVisible()}, "
              f"геометрия={geo.width()}x{geo.height()} "
              f"в точке {geo.x()},{geo.y()}")

    @staticmethod
    def _fallback_tray_icon():
        """Запасной значок, если tgplayer.ico не нашёлся.

        Рисуем сами: в трее должен быть хоть какой-то видимый знак, иначе
        непонятно, что программа запущена и свернулась. Цвет — акцент
        темы, чтобы значок подходил к оформлению.
        """
        size = 64
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            try:
                accent = QColor(theme.theme()['accent'])
            except Exception:
                accent = QColor('#5288c1')
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), 14, 14)
            # Треугольник «играть» белым.
            painter.setBrush(QColor(255, 255, 255))
            path = QPainterPath()
            path.moveTo(size * 0.38, size * 0.28)
            path.lineTo(size * 0.38, size * 0.72)
            path.lineTo(size * 0.74, size * 0.50)
            path.closeSubpath()
            painter.drawPath(path)
        finally:
            painter.end()
        return QIcon(pix)

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_from_tray()

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _toggle_close_to_tray(self, enabled):
        self._close_to_tray = bool(enabled)
        try:
            self.backend.set_setting('close_to_tray', self._close_to_tray)
        except Exception:
            pass
        self.set_status('Крестик сворачивает в трей' if enabled
                        else 'Крестик закрывает программу')

    def _toggle_compat(self, enabled):
        """Запоминает режим совместимости и применяет его к открытому плееру."""
        enabled = bool(enabled)
        try:
            self.backend.set_setting('compat', enabled)
        except Exception:
            pass
        pw = self.player_window
        if pw is not None and not getattr(pw, '_closed', True):
            if bool(getattr(pw, '_compat', False)) != enabled:
                pw.toggle_compat()
        self.set_status('Режим совместимости включён' if enabled
                        else 'Режим совместимости выключен')

    # ---------- оформление, настройки и кэш ----------

    def _apply_theme(self):
        """Применяет текущую тему ко всему приложению сразу.

        Стиль один на всё приложение, поэтому менять его надо целиком:
        тогда меню и окна, открытые позже, выглядят как уже открытые.
        """
        # Цвета своей темы и масштаб берём из настроек: их меняют в том же
        # окне, что и тему, и пересобирать стиль надо целиком.
        try:
            cfg = self._settings_dict()
            theme.set_custom_colors(cfg.get('custom_bg'), cfg.get('custom_accent'))
            theme.set_ui_scale(cfg.get('ui_scale', theme.UI_SCALE_DEFAULT))
        except Exception:
            pass
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.build_style_scaled())
        # Шрифт из стиля доходит до надписи только сейчас, поэтому высоту
        # строки состояния пересчитываем здесь, а не при её создании.
        try:
            self._status_bar.refit()
        except Exception:
            pass
        # Значок связи стоит над полосой состояния и зависит от её высоты
        # и от масштаба: пересчитываем и его.
        try:
            self.conn_button.refit()
        except Exception:
            pass
        # Значок трея собран из акцентного цвета — после смены темы его
        # надо пересобрать, иначе он останется в цветах прошлой темы.
        self._update_tray_icon()

    # --- Обновления -----------------------------------------------------

    def check_updates_silent(self):
        """Проверка при запуске: тихо, в фоне, ошибки только в журнал.

        Человек открыл программу, чтобы смотреть видео, а не читать про
        обновления. Поэтому нет сети или GitHub молчит — просто молчим
        тоже. Блокирующее окно о неудачной проверке раздражало бы.
        """
        enabled = self._setting('check_updates', True)
        print(f"[Обновление] запуск проверки: включена={enabled}, "
              f"канал={self._update_channel()}")
        if not enabled:
            return
        self._run_update_check(manual=False)

    def check_updates_manual(self):
        """Проверка по кнопке: здесь ответ показываем всегда."""
        self._run_update_check(manual=True)

    def _update_channel(self):
        channel = self._setting('update_channel', 'stable')
        return channel if channel in ('stable', 'beta') else 'stable'

    def _run_update_check(self, manual):
        """Запускает проверку в отдельном потоке, чтобы окно не замирало."""
        channel = self._update_channel()

        def worker():
            try:
                info, reason = updates.check(channel)
            except Exception as ex:
                info, reason = None, f'{type(ex).__name__}: {ex}'
            print(f"[Обновление] поток закончил: info={info} reason={reason}")
            # Возврат в поток Qt — через сигнал, а не через QTimer.
            #
            # QTimer.singleShot из чужого потока ненадёжен: у фонового
            # потока нет своего цикла событий Qt, и таймер может не
            # сработать вовсе или сработать не в том потоке. Сигнал,
            # наоборот, доставляется в поток получателя через очередь
            # событий — это штатный и безопасный способ.
            try:
                self._update_ready.emit(info, reason, manual)
            except Exception as ex:
                print(f"[Обновление] сигнал не ушёл: {ex}")

        threading.Thread(target=worker, daemon=True,
                         name='update-check').start()

    def _on_update_checked(self, info, reason, manual):
        if info is None:
            print(f"[Обновление] {reason}")
            if manual:
                QMessageBox.information(
                    self, 'Обновления',
                    f'У вас последняя версия {version.VERSION}.\n\n{reason}' if
                    'нет' in reason else f'Проверить не удалось: {reason}')
            return

        print(f"[Обновление] доступна {info.tag}")
        # Эту версию уже отклонили — не пристаём повторно.
        if not manual and self._setting('skipped_version', '') == info.tag:
            print(f"[Обновление] {info.tag} ранее отклонена — пропускаю")
            return
        self._offer_update(info)

    def _offer_update(self, info):
        """Показывает окно с предложением обновиться."""
        dlg = UpdateDialog(self, info, version.VERSION)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            if dlg.skip_this_version:
                self._set_setting_quiet('skipped_version', info.tag)
            return
        if dlg.skip_this_version:
            self._set_setting_quiet('skipped_version', info.tag)
        self._download_and_install(info)

    def _download_and_install(self, info):
        """Скачивает установщик и запускает его.

        Пока идёт загрузка, показываем окно со шкалой. Окно держим
        модальным: пока непонятно, что с загрузкой, работать с плеером
        всё равно не имеет смысла.
        """
        # Временная папка: установщик — временный файл, держать его рядом
        # с программой незачем.
        dest = os.path.join(tempfile.gettempdir(),
                            f'TGPlayer_Setup_{info.tag.lstrip("vV")}.exe')

        ver = info.tag.lstrip('vV')
        dlg = DownloadDialog(self, f'Скачиваю TGPlayer {ver}')

        def worker():
            ok, msg = updates.download(
                info, dest,
                progress=lambda chunk, total: (
                    self._download_progress.emit(chunk, total)))
            # Через сигнал, а не QTimer: из фонового потока таймер
            # срабатывает через раз и молча — на этом уже обжигались
            # и с проверкой обновлений.
            self._download_done.emit(ok, msg, dest)

        def upload_progress(chunk, total):
            # Счётчик копим в диалоге, а не в замыкании: так он не растёт
            # между попытками и не утекает.
            dlg.on_progress(chunk, total)

        self._download_progress.connect(upload_progress)
        self._download_done.connect(
            lambda ok, msg, path: (dlg.accept(),
                                   self._after_download(ok, msg, path)))

        threading.Thread(target=worker, daemon=True,
                         name='update-download').start()
        dlg.exec()

        try:
            self._download_progress.disconnect(upload_progress)
        except Exception:
            pass
        try:
            self._download_done.disconnect()
        except Exception:
            pass

    def _after_download(self, ok, msg, path):
        if not ok:
            print(f"[Обновление] скачать не удалось: {msg}")
            QMessageBox.warning(self, 'Обновление',
                                f'Не удалось скачать обновление.\n\n{msg}')
            return
        print(f"[Обновление] скачано, хеш {msg}")
        answer = QMessageBox.question(
            self, 'Обновление готово',
            'Обновление скачано и проверено.\n\n'
            'Сейчас программа закроется, установится новая версия и '
            'запустится снова.\n\nПродолжить?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._launch_installer(path)

    def _launch_installer(self, path):
        """Запускает установщик и закрывает плеер.

        Установщик не может перезаписать работающий .exe — файл занят, —
        поэтому сначала заказываем закрытие программы, а установщик
        запускаем так, чтобы он подождал две секунды. Без этого он
        начинал копировать сразу и падал на заблокированном файле.
        """
        try:
            subprocess.Popen([path, '/SILENT', '/SUPPRESSMSGBOXES',
                              '/CLOSEAPPLICATIONS', '/RESTARTAPPLICATIONS'])
        except Exception as ex:
            print(f"[Обновление] установщик не запустился: {ex}")
            QMessageBox.warning(self, 'Обновление',
                                f'Не удалось запустить установщик: {ex}')
            return
        print("[Обновление] установщик запущен, закрываю плеер")
        # Небольшая пауза: установщику нужно время, чтобы начать, а плееру
        # — корректно закрыть соединение с Telegram.
        QTimer.singleShot(300, QApplication.instance().quit)

    def open_settings(self):
        """Окно настроек: всё, что можно включить или выключить."""
        dlg = SettingsDialog(self, self._settings_dict())
        dlg.changed.connect(self._apply_setting)
        # Состояние подключения показываем прямо в окне: иначе непонятно,
        # помогла ли смена способа и идёт ли попытка прямо сейчас.
        try:
            self.backend.status_changed.connect(dlg.set_conn_status)
            dlg.set_conn_status("Текущий способ: "
                                + config.PROXY_MODE_TITLES.get(
                                    self._setting('proxy_mode',
                                                  config.DEFAULT_PROXY_MODE), ''))
        except Exception:
            pass
        try:
            dlg.exec()
        finally:
            try:
                self.backend.status_changed.disconnect(dlg.set_conn_status)
            except Exception:
                pass

    def _apply_setting(self, key, value):
        """Применяет изменение из окна настроек и запоминает его.

        Обёртка не для красоты: это слот, вызванный из C++, и любое
        исключение здесь PyQt6 завершает мгновенным закрытием программы —
        без сообщения и без записи в журнал. Именно так программа и
        закрывалась при выборе своего цвета. Теперь ошибка попадает в
        журнал и в строку состояния, а окно остаётся живым.
        """
        try:
            self._apply_setting_inner(key, value)
        except Exception as ex:
            print(f"[Настройки] Не удалось применить «{key}»: "
                  f"{type(ex).__name__}: {ex}")
            self.set_status(f"Не удалось применить настройку: {ex}")

    def _apply_setting_inner(self, key, value):
        if key == 'open_connection':
            # Кнопка в настройках открывает то же меню, что и значок в
            # углу окна: одно место для правки, а не два.
            self._open_connection_menu()
            return
        if key == 'check_updates_now':
            self.check_updates_manual()
            return
        if key in ('check_updates', 'update_channel'):
            # Эти две живут только в файле настроек: применяются при
            # следующей проверке, перерисовывать интерфейс не нужно.
            self.backend.set_setting(key, value)
            return
        if key == 'clear_cache':
            self.clear_cache()
            return
        if key == 'proxy_mode':
            self.backend.set_setting('proxy_mode', value)
            try:
                self.backend.set_proxy(value)
            except Exception:
                pass
            self.set_status("Подключение: "
                            + config.PROXY_MODE_TITLES.get(value, str(value)))
            return
        if key == 'mtproxy':
            host, port, secret = value
            try:
                port_num = int(port) if str(port).strip() else config.MT_PROXY_DEFAULT[1]
            except (TypeError, ValueError):
                port_num = config.MT_PROXY_DEFAULT[1]
            self.backend.set_setting('mtproxy_host', host)
            self.backend.set_setting('mtproxy_port', port_num)
            self.backend.set_setting('mtproxy_secret', secret)
            try:
                self.backend.set_proxy('custom', host, port_num, secret)
            except Exception:
                pass
            self.set_status("Подключаюсь через свой прокси...")
            return
        if key in ('custom_bg', 'custom_accent'):
            self.backend.set_setting(key, value)
            # Имена в настройках и в самой функции разные, поэтому
            # передаём явно. Раньше здесь подставлялись имена настроек, и
            # в слоте улетал TypeError — а такое исключение PyQt6 завершает
            # мгновенным закрытием программы, без единого слова в логе.
            if key == 'custom_bg':
                theme.set_custom_colors(bg=value)
            else:
                theme.set_custom_colors(accent=value)
            self._apply_theme()
            return
        if key == 'ui_scale':
            self.backend.set_setting('ui_scale', int(value))
            theme.set_ui_scale(value)
            self._apply_theme()
            self.set_status('Масштаб интерфейса: %d%%' % theme.ui_scale())
            return
        if key == 'theme':
            theme.set_theme_key(value)
            self._apply_theme()
        elif key in ('compat', 'close_to_tray'):
            # У этих пунктов есть галочка в меню окна, и она же —
            # хранилище состояния. Щёлкаем её, а не дублируем логику.
            act = (self.act_compat if key == 'compat'
                   else self.act_close_to_tray)
            act.setChecked(bool(value))
            return
        elif key in ('hwdec', 'auto_quality', 'playlist_panel'):
            self._apply_to_player(key, value)
        try:
            self.backend.set_setting(key, value)
        except Exception:
            pass

    def _apply_to_player(self, key, value):
        """Переносит настройку в уже открытое окно плеера."""
        pw = self.player_window
        if pw is None or getattr(pw, '_closed', True):
            return
        try:
            if key == 'hwdec':
                pw.set_hwdec(int(value))
            elif key == 'playlist_panel':
                if bool(getattr(pw, '_playlist_shown', False)) != bool(value):
                    pw._toggle_playlist()
        except Exception:
            pass

    def clear_cache(self):
        """Удаляет скачанные файлы кэша прямо сейчас."""
        keep = []
        pw = self.player_window
        if pw is not None and not getattr(pw, '_closed', True):
            for attr in ('_current_doc_id', '_next_doc_id'):
                doc_id = getattr(pw, attr, None)
                if doc_id is not None:
                    keep.append(doc_id)
        try:
            self.backend.clear_cache(keep)
        except Exception:
            pass
        self.set_status('Кэш очищен')

    # ---------- избранные каналы ----------

    def _dialog_key(self, dialog):
        """Отметка канала для настроек: id диалога строкой.

        У каналов Telethon id отрицательный и помеченный (-100...), у
        личных чатов — положительный; строки хватает, чтобы различить их
        в settings.json.
        """
        return str(getattr(dialog, 'id', '') or '')

    def _is_favorite(self, dialog):
        return self._dialog_key(dialog) in self._favorites

    def _save_favorites(self):
        try:
            self.backend.set_setting('favorites', sorted(self._favorites))
        except Exception:
            pass

    def _toggle_favorite(self, dialog):
        """Закрепляет канал сверху списка или снимает закрепление."""
        key = self._dialog_key(dialog)
        if not key:
            return
        if key in self._favorites:
            self._favorites.discard(key)
            self.set_status('Канал убран из избранного')
        else:
            self._favorites.add(key)
            self.set_status('Канал закреплён сверху')
        self._save_favorites()
        # Список пересобирается: избранное должно оказаться наверху.
        current = self._active_dialog_id
        self.filter_channels()
        self._reselect_channel(current)

    def _reselect_channel(self, dialog_id):
        """Возвращает выбор на прежний канал после пересборки списка."""
        if dialog_id is None:
            return
        for row, entry in enumerate(getattr(self, '_search_rows', [])):
            if entry is None or entry[0] != 'mine':
                continue
            if getattr(entry[1], 'id', None) == dialog_id:
                self.channel_list.setCurrentRow(row)
                # Список роликов берём из кеша бэкенда по каналу, а не из
                # общего current_messages: текущим каналом в бэкенде
                # остаётся последний открытый, и после пересборки списка
                # на экране оказались бы ролики чужого канала.
                cached = self._cached_videos(dialog_id)
                if cached is None:
                    return
                self._video_messages = list(cached[0])
                self._video_titles = list(cached[1])
                self._render_video_list()
                self._update_more_button()
                return

    def _on_channel_menu(self, pos):
        """ПКМ по списку: подписка, отписка, избранное и папки."""
        row = self.channel_list.indexAt(pos).row()
        if row < 0:
            return

        rows = getattr(self, '_search_rows', [])
        if row >= len(rows) or rows[row] is None:
            return
        kind, target = rows[row]
        dialog = target if kind == 'mine' else None
        found = target if kind == 'found' else None

        # Для найденного чат — это он сам, для своего — его сущность.
        chat = found if found is not None else getattr(dialog, 'entity', None)
        name = (getattr(chat, 'title', None)
                or getattr(dialog, 'name', '') or 'чат')

        menu = QMenu(self)

        if found is not None:
            if getattr(found, 'left', True):
                act = menu.addAction(f'Подписаться: {name}')
                act.triggered.connect(lambda: self._join_channel(found))
            else:
                act = menu.addAction(f'Отписаться: {name}')
                act.triggered.connect(lambda: self._leave_channel(found))

        if dialog is not None:
            if self._is_favorite(dialog):
                act = menu.addAction(f'Убрать из избранного: {name}')
            else:
                act = menu.addAction(f'Закрепить сверху: {name}')
            act.triggered.connect(lambda: self._toggle_favorite(dialog))
            if chat is not None:
                act = menu.addAction(f'Отписаться: {name}')
                act.triggered.connect(lambda: self._leave_channel(chat))

        # Папки: разложить по ним — то, ради чего канал и добавляют.
        folders = [f for f in getattr(self.backend, 'folders', [])
                   if f.get('filter_id') is not None]
        if folders and chat is not None:
            menu.addSeparator()
            sub = menu.addMenu('Добавить в папку')
            for folder in folders:
                act = sub.addAction(folder['title'])
                act.triggered.connect(
                    lambda _=False, fid=folder['filter_id'], c=chat:
                    self._add_to_folder(fid, c))

        if dialog is not None:
            # Скрытие стоит отдельно от папок и последним: это действие
            # убирает канал с глаз, промахнуться по нему не хочется.
            menu.addSeparator()
            if self._is_hidden(dialog):
                act = menu.addAction(f'Вернуть в список: {name}')
            else:
                act = menu.addAction(f'Скрыть из списка: {name}')
            act.triggered.connect(lambda: self._toggle_hidden(dialog))
        if menu.isEmpty():
            return
        menu.exec(self.channel_list.mapToGlobal(pos))

    # ---------- поиск по всему Telegram ----------

    def _on_channel_query(self, text):
        """Ввод в строке поиска: свои каналы сразу, Telegram — вдогонку."""
        query = text.strip()
        if len(query) < 2:
            # На одну букву Telegram не опрашиваем: запрос уйдёт в сеть, а
            # вернёт мусор. Свои каналы отфильтруются и без него.
            self._search_timer.stop()
            self._search_results = []
            self.filter_channels()
            return
        # Свои показываем мгновенно, чтобы список не ждал сеть.
        self.filter_channels()
        # А по Telegram спрашиваем с задержкой: иначе запрос уходил бы на
        # каждую букву.
        self._search_timer.start()

    def _run_channel_search(self):
        """Спрашивает Telegram о каналах по введённому запросу."""
        query = self.channel_search.text().strip()
        if len(query) < 2:
            return
        self.set_status(f"Поиск «{query}» в Telegram...")
        self.backend.submit_coroutine(self.backend.search_channels(query))

    def _add_list_note(self, text):
        """Строка-пояснение в списке каналов: выбрать её нельзя.

        Разделитель между своими и найденными — это тоже она.
        """
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(QColor(theme.theme()['text_dim']))
        self.channel_list.addItem(item)
        self._search_rows.append(None)

    def filter_channels(self):
        """Список каналов: свои сверху, найденное в Telegram — ниже.

        Так видно и то, что уже есть, и то, что можно добавить. Свои идут
        первыми: если канал уже есть, искать его в общем списке незачем.
        """
        query = self.channel_search.text().strip().lower()

        mine = []
        for d in getattr(self, 'current_dialogs', []):
            name = (getattr(d, 'name', '')
                    or getattr(getattr(d, 'entity', None), 'title', '') or '')
            if not query or query in name.lower():
                mine.append(d)
        # Каналы без видео уходят вниз: это не мусор, но и открывать их
        # незачем. Внутри каждой группы порядок прежний — избранное
        # сверху, остальное как пришло из Telegram.
        #
        # Сортировка стабильная, поэтому равные элементы остаются на своих
        # местах — внутри «с видео» и «без видео» порядок не меняется.
        # Ответ фоновой проверки приходит по каналам вразнобой, и без этого
        # список перетасовывался бы на каждой порции.
        mine.sort(key=lambda d: (self._empty_note(d) != '',
                                 0 if self._is_favorite(d) else 1))
        self._empty_shown = sum(1 for d in mine if self._empty_note(d))

        self.channel_list.blockSignals(True)
        self.channel_list.clear()
        # Строка списка -> что за ней стоит. Нужна, потому что в общем
        # списке свои каналы и найденные лежат вперемешку, и по номеру
        # строки уже не понять, что выбрано.
        self._search_rows = []

        for d in mine:
            name = (getattr(d, 'name', '')
                    or getattr(getattr(d, 'entity', None), 'title', '') or '')
            mark = '★ ' if self._is_favorite(d) else ''
            item = QListWidgetItem(icons.get_avatar_icon(name), mark + name)
            # Цвет у всех каналов один: раньше каналы без видео были серыми,
            # и список выглядел так, будто программа покрасила половину
            # строк случайно. «Нет видео» и так видно по месту в списке и по
            # подсказке при наведении.
            item.setToolTip(name)
            self.channel_list.addItem(item)
            self._search_rows.append(('mine', d))

        if self._search_results:
            self._add_list_note(
                f"— Найдено в Telegram: {len(self._search_results)} —")

        for found in self._search_results:
            title = getattr(found, 'title', '') or 'без названия'
            count = getattr(found, 'participants_count', None)
            kind = 'группа' if getattr(found, 'megagroup', False) else 'канал'
            label = f"{title}  ·  {kind}"
            if count:
                label += f"  ·  {count} подписчиков"
            if not getattr(found, 'left', True):
                # Уже подписан: подписываться второй раз незачем.
                label = '✓ ' + label
            item = QListWidgetItem(icons.get_avatar_icon(title), label)
            item.setToolTip(title)
            self.channel_list.addItem(item)
            self._search_rows.append(('found', found))

        # Сколько каналов без видео — видно до конца списка: иначе
        # канал просто «уехал» вниз без объяснения.
        if self._empty_shown:
            self._add_list_note(
                f"— Без видео: {self._empty_shown} —")

        if not mine and not self._search_results:
            self._add_list_note('Ничего не найдено')
        self.channel_list.blockSignals(False)

        # Открытый канал сохраняем. Раньше список видео и активный канал
        # сбрасывались здесь всегда, и любая пересборка списка — пометка
        # от фоновой проверки, поиск по видеороликам, скрытие канала —
        # обнуляла окно: список роликов пропадал, а догрузка, положившая
        # их в backend, не могла достучаться до окна (ответ приходил для
        # канала, который «уже не открыт»).
        self._restore_open_channel()

    def _restore_open_channel(self):
        """Возвращает в окно ролики открытого канала после пересборки.

        Берём из кеша бэкенда по id канала, а не из общего
        current_messages: там лежит канал, который бэкенд считает
        активным, и он не обязан совпадать с открытым в окне.
        """
        dialog_id = getattr(self, '_active_dialog_id', None)
        cached = self._cached_videos(dialog_id) if dialog_id else None
        if cached is None:
            self._video_messages = []
            self._video_titles = []
            self._list_has_videos = False
            self.video_list.clear()
            self.video_list.addItem("Выберите канал")
            self.btn_more.hide()
            return
        self._video_messages = list(cached[0])
        self._video_titles = list(cached[1])
        self._render_video_list()
        self._update_more_button()
        # Статус не трогаем: список пересобирается на каждую букву, и
        # сообщение мигало бы вместо того, чтобы что-то сообщать.

    def _cached_videos(self, dialog_id):
        """Список роликов канала из кеша бэкенда; None, если не загружен.

        Проверяем тип: у поддельных бэкендов (тесты, предпросмотр) поля
        может не быть или вместо словаря оказаться заглушка-функция.
        """
        cache = getattr(self.backend, '_videos_by_dialog', None)
        if not hasattr(cache, 'get'):
            return None
        return cache.get(dialog_id)

    def _video_more_flag(self, dialog_id):
        """Есть ли в канале что догружать."""
        flags = getattr(self.backend, '_video_more', None)
        if not hasattr(flags, 'get'):
            return None
        return flags.get(dialog_id)

    def _empty_note(self, dialog):
        """Подпись «нет видео», если канал проверен и оказался пустым.

        Пометка ставится по последним сообщениям канала, поэтому это
        догадка: канал с роликами в глубине истории попадёт в «нет видео».
        Открытие канала её снимает — настоящий список роликов важнее.
        """
        peer_id = self.backend._dialog_peer_id(dialog)
        if peer_id is None:
            return ''
        if self.backend._dialog_has_video.get(peer_id) is False:
            return 'нет видео'
        return ''

    def _on_channels_checked(self, result):
        """Фоновая проверка ответила по части каналов — пересобираем список."""
        if not isinstance(result, dict) or not result:
            return
        self.filter_channels()

    def _load_more_videos(self):
        """Кнопка «Догрузить ещё» под списком роликов."""
        dialog = None
        for entry in reversed(getattr(self, '_search_rows', [])):
            # Строки-пояснения («— Найдено в Telegram —») лежат в этом же
            # списке как None, и распаковка пары на них падала.
            if not entry:
                continue
            kind, target = entry
            if kind == 'mine' and getattr(target, 'id', None) == \
                    self._active_dialog_id:
                dialog = target
                break
        if dialog is None:
            # Кнопка видна, а канал не найден в списке: тогда нажатие
            # молча ничего не делало. Пишем в журнал — по нему видно, что
            # именно произошло.
            print(f"[Догрузка] Канал {self._active_dialog_id} не найден "
                  f"в списке — нажатие пропущено")
            return
        cached = getattr(self.backend, '_videos_by_dialog', {}).get(dialog.id)
        print(f"[Догрузка] Нажатие: «{getattr(dialog, 'name', '')}» "
              f"id={dialog.id}, в кеше "
              f"{len(cached[0]) if cached else 'нет'}, "
              f"есть ещё: "
              f"{self._video_more_flag(dialog.id)}")
        self.btn_more.setEnabled(False)
        self.btn_more.setText("Загрузка...")
        self.set_status("Догрузка видео...")
        self.backend.submit_coroutine(
            self.backend.load_more_videos(dialog))

    def _is_hidden(self, dialog):
        return self._dialog_key(dialog) in getattr(self, '_hidden', set())

    def _showing_hidden_folder(self):
        """Открыта ли сейчас папка «Скрытые».

        Смотрим не на backend.folders: там лежит результат последней
        пересборки, и после скачивания списка из Telegram он может быть
        уже другим. Отметку hidden несёт копия папок, по которой список
        собран в окне.
        """
        index = self.folder_combo.currentIndex()
        folders = getattr(self, '_folder_snapshot', None) or []
        if 0 <= index < len(folders):
            return bool(folders[index].get('hidden'))
        return False

    def _save_hidden(self):
        try:
            self.backend.set_setting('hidden', sorted(self._hidden))
        except Exception:
            pass

    def _toggle_hidden(self, dialog):
        """Скрывает канал из списка или возвращает его обратно."""
        key = self._dialog_key(dialog)
        if not key:
            return
        name = (getattr(dialog, 'name', '')
                or getattr(getattr(dialog, 'entity', None), 'title', '') or '')
        if key in self._hidden:
            self._hidden.discard(key)
            self.set_status(f"Канал возвращён: {name}")
        else:
            self._hidden.add(key)
            if self._active_dialog_id == getattr(dialog, 'id', None):
                # Спрятанный канал закрываем: держать список его роликов
                # на экране, когда самого канала в списке нет, странно.
                self._active_dialog_id = None
                self._video_messages = []
                self._video_titles = []
                self._render_video_list()
                self.btn_more.hide()
            self.set_status(
                f"Канал скрыт: {name} (вернуть — папка «Скрытые»)")
        self._save_hidden()
        # Папки пересобираются без сети: счётчик в «Скрытых» меняется
        # сразу, а не после обновления каталогов.
        self._reload_folders()

    def _reload_folders(self):
        """Просит бэкенд пересобрать папки; при отказе делает это сам."""
        coro = None
        try:
            coro = self.backend.reload_folders()
        except Exception as ex:
            print(f"[Папки] Пересобираю без бэкенда: {ex}")
            self._rebuild_folders_locally()
            return
        if not hasattr(coro, 'close'):
            # Поддельный бэкенд вернул не корутину: отправлять нечего.
            self._rebuild_folders_locally()
            return
        try:
            # Бэкенд без поднятой петли задачу не принимает, и это штатный
            # случай — во время подключения петли ещё нет. Проверяем
            # результат, а не полагаемся на исключение.
            if self.backend.submit_coroutine(coro) is None:
                raise RuntimeError('петля событий не готова')
            return
        except Exception as ex:
            print(f"[Папки] Пересобираю без бэкенда: {ex}")
            try:
                coro.close()
            except Exception:
                pass
        self._rebuild_folders_locally()

    def _rebuild_folders_locally(self):
        """Пересобирает папки в окне, когда бэкенд недоступен.

        Скрытие канала — действие в интерфейсе, и оно не должно пропадать
        целиком из-за того, что фоновая петля занята или ещё не поднята.
        """
        hidden = getattr(self, '_hidden', set())
        folders = []
        # Источник — копия папок, по которой сейчас собран список в окне.
        # Брать backend.folders нельзя: там результат последней
        # пересборки, и после обновления списка каналов он может быть
        # уже другим.
        known = (getattr(self, '_folder_snapshot', None)
                 or getattr(self.backend, 'folders', None) or [])
        # Каналы для «Скрытых» ищем и в dialogs (у настоящего бэкенда там
        # полный список), и в самих папках: в окне папки есть всегда, а
        # полный список — не у всякого бэкенда.
        docs_all = list(getattr(self.backend, 'dialogs', None) or [])
        seen = {self._dialog_key(d) for d in docs_all}
        for f in known:
            for d in (f.get('dialogs') or []):
                key = self._dialog_key(d)
                if key and key not in seen:
                    seen.add(key)
                    docs_all.append(d)
        for f in known:
            if f.get('hidden'):
                continue
            item = dict(f)
            item['dialogs'] = [d for d in (f.get('dialogs') or [])
                               if self._dialog_key(d) not in hidden]
            folders.append(item)
        if hidden:
            docs = [d for d in docs_all if self._dialog_key(d) in hidden]
            if docs:
                folders.append({
                    "title": f"Скрытые ({len(docs)})",
                    "dialogs": docs,
                    "filter_id": None,
                    "hidden": True,
                })
        self.display_folders(folders)

    def _join_channel(self, found):
        """Подписывается на найденный канал или вступает в группу."""
        title = getattr(found, 'title', '') or 'чат'
        self.set_status(f"Подписка на «{title}»...")
        self.backend.submit_coroutine(self.backend.join_channel(found))

    def _leave_channel(self, chat):
        """Отписывается от канала или выходит из группы.

        Спрашиваем подтверждение: отписка уносит канал из списка, и
        случайным щелчком её делать не за что.
        """
        title = (getattr(chat, 'title', '') or getattr(chat, 'name', '')
                 or 'чат')
        answer = QMessageBox.question(
            self, 'Отписка',
            f"Отписаться от «{title}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.set_status(f"Отписка от «{title}»...")
        self.backend.submit_coroutine(self.backend.leave_channel(chat))

    def _add_to_folder(self, filter_id, chat):
        """Кладёт канал или группу в папку Telegram."""
        title = (getattr(chat, 'title', '') or getattr(chat, 'name', '')
                 or 'чат')
        self.set_status(f"Добавляю «{title}» в папку...")
        self.backend.submit_coroutine(
            self.backend.add_to_folder(filter_id, chat))

    def _quit_app(self):
        """Настоящий выход: из трея программа закрывается только так."""
        self._really_quit = True
        self.close()
        QApplication.quit()

    def _notify_minimized(self):
        """Один раз объясняет, куда делось окно и как выйти."""
        if self._tray_notice_shown or self.tray is None:
            return
        self._tray_notice_shown = True
        try:
            self.tray.showMessage(
                'TG Player',
                'Свёрнуто в трей, окно продолжает работать.\n'
                'Выход — правый клик по значку → «Выход».',
                QSystemTrayIcon.MessageIcon.Information, 4000)
        except Exception:
            pass

    def start_login(self):
        """Готовит окно к тому, что первым будет вход, а не плеер.

        Порядок: сначала окно входа, потом главное окно. Раньше было
        наоборот — плеер открывался сразу, окно входа всплывало поверх,
        и при случайном закрытии оставался пустой плеер без каналов.

        Сам цикл подключения запускает WorkerThread (см. start_loop), и он
        же спрашивает данные через login_step. Здесь только ждём сигнал
        об успехе и показываем окно.
        """
        print("[Вход] сначала вход в аккаунт, окно плеера — после")
        try:
            self.backend.connection_changed.connect(self._on_login_connection)
        except Exception as ex:
            print(f"[Вход] сигнал не подключён: {ex}")

    def _on_login_connection(self, state):
        """Показывает главное окно, когда вход выполнен.

        connection_changed приходит с 'ok' только когда аккаунт
        авторизован — то есть после успешного входа.
        """
        if state != 'ok':
            return
        if getattr(self, '_shown_after_login', False):
            return
        self._shown_after_login = True
        print("[Вход] вход выполнен — показываю главное окно")
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh_data(self):
        """Перечитывает папки и каналы из Telegram.

        Вызывается там, где список надо обновить не по кнопке: после
        входа, после смены канала, по горячей клавише.
        """
        self.set_status("Обновление папок...")
        self.backend.submit_coroutine(self.backend.refresh_catalogs())

    def show_error(self, message):
        QMessageBox.critical(self, "Ошибка", message)

    def display_folders(self, folders):
        # Выбранная папка переживает перезагрузку списка: иначе «Скрытые»
        # при каждом обновлении папок уступала место «Все каналы», и
        # спрятанные каналы пропадали из виду вместе с возможностью их
        # вернуть.
        folders = list(folders or [])
        wanted = getattr(self, '_current_folder_title', None)
        # Копия папок, по которой собран список. backend.folders для этого
        # не годится: там результат последней пересборки, и после
        # скачивания списка каналов он уже другой.
        self._folder_snapshot = folders
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        for f in folders:
            self.folder_combo.addItem(f["title"])
        self.folder_combo.blockSignals(False)
        if not folders:
            return
        index = 0
        stored = [f["title"] for f in folders]
        for i, title in enumerate(stored):
            if title == wanted:
                index = i
                break
        else:
            # Папки пересобрали и название «Скрытых» изменилось (в нём
            # число). Тогда восстанавливаем по признаку, а не по тексту,
            # иначе выбор слетал бы на «Все каналы» после каждого скрытия.
            for i, f in enumerate(folders):
                if f.get('hidden') and wanted and wanted.startswith('Скрытые'):
                    index = i
                    break
        self.folder_combo.blockSignals(True)
        self.folder_combo.setCurrentIndex(index)
        self.folder_combo.blockSignals(False)
        # Индекс мог не сдвинуться, если он уже был нужным. Тогда сигнал
        # смены индекса не придёт, а список всё равно надо пересобрать:
        # копия папок в окне уже новая.
        self.on_folder_changed(index)

    def on_folder_changed(self, index):
        # Список папок берём из окна, а не из backend.folders: там
        # результат последней пересборки, и после скачивания списка
        # каналов он уже другой. Раньше из-за этого выбор папки молча
        # отбрасывался проверкой индекса, и «Скрытые» не открывались.
        folders = getattr(self, '_folder_snapshot', None) or \
            getattr(self.backend, 'folders', [])
        if index < 0 or index >= len(folders):
            return
        folder = folders[index]
        self._current_folder_title = folder["title"]
        if folder.get('hidden'):
            # Папка «Скрытые»: каналы для неё лежат не в Telegram, а в
            # настройках, и собираются заново при каждой пересборке.
            # Список берём из неё же — если каналы уже собраны, они и
            # должны быть видны, иначе папка открывается пустой.
            self.current_dialogs = folder.get("dialogs") or []
            self.filter_channels()
            return
        self.current_dialogs = folder["dialogs"]
        self.filter_channels()

    def _show_search_results(self, results):
        """Пришёл ответ от Telegram — пересобираем список."""
        self._search_results = list(results or [])
        self.filter_channels()

    def on_channel_selected(self, row):
        # Свои каналы и найденные лежат в одном списке вперемешку,
        # поэтому смотрим не на номер строки, а на разметку списка.
        rows = getattr(self, '_search_rows', [])
        if row < 0 or row >= len(rows) or rows[row] is None:
            return
        kind, target = rows[row]
        dialog = target if kind == 'mine' else FoundDialog(target)
        self._open_channel(dialog)

    def _open_channel(self, dialog):
        """Делает канал активным и просит бэкенд загрузить его ролики.

        Работает и для своего канала, и для найденного: у найденного роль
        диалога играет обёртка FoundDialog.
        """
        self._active_dialog_id = dialog.id
        # Бэкенд должен знать активный канал: по нему он решает, применять
        # ли результат загрузки (иначе устаревший ответ подменял плееру
        # список роликов).
        self.backend.active_dialog_id = dialog.id
        # Запоминаем название канала: оно нужно в заголовке окна плеера.
        self.backend.active_dialog_title = (
            getattr(dialog, 'name', '')
            or getattr(getattr(dialog, 'entity', None), 'title', '')
        )
        self._preload_doc_id = None
        self.video_list.clear()
        self._list_has_videos = False
        self.video_list.addItem("Загрузка видео...")
        self.backend.submit_coroutine(
            self.backend.load_videos_from_dialog(dialog)
        )

    def display_videos(self, dialog_id, videos):
        # Игнорируем устаревший ответ, если канал уже переключили.
        if dialog_id != self._active_dialog_id:
            print(f"[Список] Ответ канала {dialog_id} отброшен: "
                  f"открыт {self._active_dialog_id}")
            return
        self._video_messages = list(self.backend.current_messages)
        self._video_titles = list(videos)
        self._render_video_list()
        self._update_more_button()

    def _update_more_button(self):
        """Показывает кнопку догрузки, если в канале есть что догружать."""
        more = bool(self._video_more_flag(self._active_dialog_id))
        # Кнопку могли нажать: после ответа она снова годится для нажатия.
        self.btn_more.setEnabled(True)
        # В подписи — сколько сообщений читается за раз, а не сколько
        # роликов: догружается история канала.
        self.btn_more.setText(
            config.MORE_BUTTON_TEXT.format(page=config.VIDEO_PAGE))
        if more:
            # Список роликов здесь не важен: канал может быть пуст, но
            # история у него длиннее прочитанного, и без кнопки к этим
            # роликам не добраться.
            self.btn_more.show()
        else:
            self.btn_more.hide()

    def _toggle_order(self):
        """Переключает порядок роликов: сначала новые или сначала старые."""
        self._order_newest_first = not getattr(
            self, '_order_newest_first', True)
        self.btn_order.setText('↓ Сначала новые'
                               if self._order_newest_first
                               else '↑ Сначала старые')
        self._render_video_list()

    @staticmethod
    def _video_suffix(attr, doc):
        """« · 12:34 · 45 МБ» — длительность и размер ролика в списке."""
        parts = []
        duration = getattr(attr, 'duration', 0) or 0
        if duration:
            parts.append(utils.format_time(duration * 1000))
        size = getattr(doc, 'size', 0) or 0
        if size:
            parts.append(f"{size / (1024 * 1024):.0f} МБ")
        return ('   · ' + ' · '.join(parts)) if parts else ''

    def _render_video_list(self):
        """Показывает список видео: поиск, порядок, пометка просмотра.

        Номер строки больше не равен индексу в current_messages: список
        можно отфильтровать и развернуть, поэтому настоящий индекс ролика
        лежит в данных строки (UserRole). Пока они совпадали, всё работало
        случайно — после любого фильтра открывался бы не тот ролик.
        """
        self.video_list.clear()
        messages = list(getattr(self, '_video_messages', None) or [])
        titles = list(getattr(self, '_video_titles', None) or [])
        if not messages:
            self._list_has_videos = False
            self.video_list.addItem("В канале нет видео")
            return

        query = self.video_search.text().strip().lower()
        rows = []
        for i, message in enumerate(messages):
            title = titles[i] if i < len(titles) else ''
            if query and query not in title.lower():
                continue
            rows.append((i, message, title))

        if getattr(self, '_order_newest_first', False):
            rows.reverse()

        if not rows:
            self._list_has_videos = False
            self.video_list.addItem("Ничего не найдено")
            return

        self._list_has_videos = True
        for i, message, title in rows:
            mark = ''
            extra = ''
            # Метку берём прямо из документа: _video_info может не знать
            # сообщение, а «просмотрено» должно стоять в любом случае.
            try:
                doc = message.media.document
                if self.backend.is_watched(doc.id):
                    mark = '✓ '
            except Exception:
                doc = None
            try:
                info = self.backend._video_info(message)
                if info:
                    info_doc, attr, _ = info
                    extra = self._video_suffix(attr, doc or info_doc)
            except Exception:
                pass
            item = QListWidgetItem(mark + title + extra)
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.video_list.addItem(item)

    def _row_index(self, row):
        """Индекс ролика в current_messages по номеру строки списка.

        Список фильтруется и разворачивается, поэтому номер строки и
        индекс в current_messages — разные вещи. Настоящий индекс хранится
        в данных строки, а заглушки («Загрузка видео...») его не имеют.
        """
        item = self.video_list.item(row)
        if item is None:
            return None
        try:
            index = int(item.data(Qt.ItemDataRole.UserRole))
        except (TypeError, ValueError):
            return None
        if 0 <= index < len(self.backend.current_messages):
            return index
        return None

    def on_video_selected(self, row):
        # Прелоад начала выбранного ролика, чтобы он стартовал мгновенно.
        # Пока плеер открыт — прелоад не трогаем (плеер управляет кешем сам).
        if self.player_window is not None:
            return
        index = self._row_index(row)
        if index is None:
            return
        try:
            message = self.backend.current_messages[index]
            doc = message.media.document
        except Exception:
            return
        if doc.id == self._preload_doc_id:
            return
        if self._preload_doc_id is not None:
            self.backend.discard_download(self._preload_doc_id)
        self._preload_doc_id = doc.id
        # Прелоад тоже идёт по сообщению: иначе при истёкшей ссылке он
        # молча застрянет, а ролик начнёт играть только с нуля.
        self.backend.submit_coroutine(
            self.backend.ensure_cache(message, workers=config.PRELOAD_WORKERS)
        )

    def open_player(self, item):
        messages = self.backend.current_messages
        # Заглушки «Загрузка видео...» и «В канале нет видео» тоже
        # активируемы: без этой проверки Enter по такой строке открывал
        # ролик с тем же номером из прошлого списка.
        if not self._list_has_videos:
            return
        row = self._row_index(self.video_list.row(item))
        if row is None:
            return

        # Плеер берёт управление кешем на себя.
        self._preload_doc_id = None

        # Если окно плеера уже открыто — просто переключаем ролик в нём.
        # mpv уже инициализирован, поэтому старт происходит мгновенно,
        # без повторной инициализации libmpv и создания окна.
        pw = self.player_window
        if pw is not None:
            # Список мог смениться — закрепляем за окном актуальный.
            pw.set_playlist(messages)
            if pw.pip_window.isVisible():
                # Главное окно плеера спрятано, видео живёт в PiP: show()
                # дал бы пустое окно рядом с картинкой.
                pw.play_video(row)
            else:
                pw.show()
                pw.raise_()
                pw.activateWindow()
                QTimer.singleShot(0, lambda: self._start_playback(pw, row))
            return

        # Плеер — окно верхнего уровня, без родителя: иначе Qt сворачивает
        # и закрывает дочерние окна вместе с главным, и вместе со списком
        # каналов пропадал играющий ролик. Связь с главным окном держится
        # сигналами, а закрывает плеер MainWindow.closeEvent.
        self.player_window = PlayerWindow(self.backend, row, None)
        self.player_window.sig_closed.connect(self._on_player_closed)
        # Как только в плеере пошёл новый ролик — обновляем галочки в списке.
        self.player_window.sig_watched.connect(self._render_video_list)
        self.player_window.show()
        self.player_window.activateWindow()
        self.player_window.setFocus()

        # Запускаем видео только после того, как окно реально показано:
        # иначе mpv инициализирует вывод на скрытом окне и даёт чёрный
        # экран до первого перезапуска файла.
        pw = self.player_window
        QTimer.singleShot(0, lambda: self._start_playback(pw, row))

    def _start_playback(self, pw, row, attempt=0):
        """Запускает ролик, только когда окно показано и получило размер.

        Если mpv инициализирует видео-вывод на окне 0x0, картинка
        остаётся чёрной до первого перезапуска файла (ровно это и делает
        клавиша H). Поэтому ждём, пока виджет видео получит реальные
        размеры, и только потом отдаём файл в mpv.
        """
        if pw is not self.player_window or pw._closed:
            return

        vw = pw.video_widget
        if not pw.isVisible() or vw.width() < 2 or vw.height() < 2:
            if attempt < 60:
                QTimer.singleShot(
                    25, lambda: self._start_playback(pw, row, attempt + 1)
                )
            else:
                print("[Плеер] окно так и не получило размер — играю как есть")
                pw.play_video(row)
            return

        pw.play_video(row)

    def _on_player_closed(self):
        self.player_window = None

    def showEvent(self, event):
        """Пересчитывает строку состояния и значок, когда шрифт применён.

        Таблица стилей доходит до метки только при показе окна, позже
        создания: при создании высота полосы считалась бы по системному
        шрифту и на всех масштабах осталась бы прежней. Значок связи
        стоит над полосой, поэтому его место зависит от той же высоты.
        """
        super().showEvent(event)
        try:
            self._status_bar.refit()
        except Exception:
            pass
        try:
            self.conn_button.refit()
        except Exception:
            pass
        # Проверка обновлений — один раз за запуск и с задержкой: сначала
        # человек должен увидеть каналы, а уже потом окно про обновление.
        # Проверка идёт в отдельном потоке, окно не замирает.
        if not getattr(self, '_update_check_started', False):
            self._update_check_started = True
            QTimer.singleShot(4000, self.check_updates_silent)

    def closeEvent(self, event):
        # Крестик может означать «свернуть в трей»: тогда окно просто
        # прячется. Плеер — отдельное окно и продолжает играть, поэтому
        # кеш трогать нельзя, иначе воспроизведение оборвётся.
        if (self._close_to_tray and not self._really_quit
                and self.tray is not None and self.tray.isVisible()):
            event.ignore()
            self.hide()
            self._notify_minimized()
            return

        # Плеер — окно верхнего уровня, Qt не закрывает его вместе с
        # главным. А вся очистка кеша живёт именно в closeEvent плеера:
        # без явного close() файлы играющего ролика остались бы на диске.
        pw = self.player_window
        if pw is not None:
            try:
                pw.close()
            except Exception:
                pass
            self.player_window = None
        # Размер и место главного окна — на следующий запуск.
        app_settings.save_window_geometry(self, 'main_geometry', self._settings_dict())
        if self.tray is not None:
            self.tray.hide()
        super().closeEvent(event)


class WorkerThread(QThread):
    def __init__(self, backend):
        super().__init__()
        self.backend = backend

    def run(self):
        self.backend.start_loop()


def _single_instance_guard():
    """Не даёт запустить второй экземпляр программы.

    Два процесса на одном файле сессии Telethon — известный способ её
    испортить, плюс они подерутся за порт локального сервера. Возвращает
    объект, который надо держать живым до выхода, либо None, если плеер
    уже запущен.
    """
    if not sys.platform.startswith('win'):
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, 'TGPlayerSingleInstance')
        if not handle:
            return True
        if kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return None
        return handle
    except Exception:
        return True

