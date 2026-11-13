# Сборка Tegr под Linux, macOS и Android

Документ отвечает на вопрос «как собирать то, что уже есть», а не «как переписать всё с нуля».
Все оценки — в человеко-неделях одного разработчика, знакомого с проектом.

## 0. Коротко

| Платформа | Вариант | Что делаем | Срок | Риск |
|---|---|---|---|---|
| Linux | **L1. PyInstaller + AppImage** | код почти не трогаем | 1–1.5 нед | низкий |
| Linux | L2. Flatpak | манифест + runtime | +1 нед | средний |
| Linux | L3. .deb/PPA, pipx | упаковка | +0.5 нед | низкий |
| macOS | **M1. PyInstaller .app + dmg** | подпись/нотаризация, бандл libmpv | 2–3 нед | средний |
| macOS | M2. Homebrew cask | «формула» вместо сборки | 3–5 дней | низкий |
| macOS | M3. py2app / PyOxidizer | альтернатива PyInstaller | 1–2 нед | выше |
| Android | **A1. Kotlin/Compose + Chaquopy** | переиспользуем backend (4.3k строк) | 8–12 нед | средний |
| Android | A2. Flutter + Chaquopy | то же + другой UI-стек | 10–14 нед | средний |
| Android | A3. Kivy + Buildozer | UI с нуля, плеер слабее | 10–14 нед | высокий |
| Android | A4. Termux + X11 | демо для себя, не продукт | 1–2 дня | — |
| Android | A5. pyqtdeploy / Qt for Android | PyQt6 GPL + сборка Qt из исходников | 6+ нед | очень высокий |

Рекомендуемый порядок: **слой платформы → Linux → macOS → Android MVP**.

## 1. Что уже есть (инвентарь)

- 22 016 строк Python, из них UI (PyQt6) ≈ 14 100: `windows/` 9 470, `player/` 3 470, `widgets/` 1 200.
- `backend/` ≈ 4 310 строк — Telethon, кэш по кускам, очередь загрузки, локальный HTTP-сервер с Range, история. Qt почти нет: только `backend.py` (сигналы).
- `core/` ≈ 2 480 строк — настройки, история, темы, прокси, обновления, пути.
- Windows-специфичного кода немного и он локализован: ~700 строк в 6 файлах.
- Архитектура переносимая: mpv играет `http://127.0.0.1:<port>/stream`, а не поток Telegram напрямую. Это значит, что **любой движок, умеющий HTTP+Range, встаёт на место libmpv**, — критично для Android.

Где именно сидит Windows (полный список, он же чек-лист рефакторинга):

| Файл | Что | Замена |
|---|---|---|
| `widgets/widgets.py:12` | `import ctypes.wintypes` **на уровне модуля** | на не-Windows падает с `ValueError` при импорте — ломает весь UI; нужен ленивый импорт |
| `widgets/widgets.py:1097-1171` | `GetCursorPos`/`GetWindowRect`/`WindowFromPoint` (скрытие панели по курсору) | `QCursor.pos()` + `QWidget.underMouse()` / `QApplication.widgetAt` |
| `player/player.py:81-90, 1646, 1891-1968` | курсор, owner-окно, снятие рамки и скруглений DWM, Alt+Tab | `QCursor`, `Qt.WindowType.Tool`, `setWindowFlag(FramelessWindowHint)`; DWM-код — no-op |
| `player/player.py:568` | `opts['wid'] = int(video_widget.winId())` | X11/macOS — работает; Wayland — render API (см. §3.2) |
| `system/mpv_boot.py` | PE-импорты, `vulkan-1.dll`, `SystemRoot` | `ctypes.util.find_library('mpv')`, `LD_LIBRARY_PATH` / `DYLD_*` |
| `core/proxy.py:315-370` | системный прокси из реестра | env `http_proxy`/`https_proxy`, `gsettings` (GNOME), `scutil --proxy` (macOS) |
| `core/paths.py`, `tegr_runtime.py:53-160` | `LOCALAPPDATA`, Program Files, перенос профиля | XDG (`~/.local/share/Tegr`), `~/Library/Application Support/Tegr` |
| `windows/windows.py:6072` | мьютекс одним экземпляром (на не-Windows — `return True`, т.е. защиты нет) | `QLockFile` — обязателен: два процесса на одном файле сессии Telethon её портят |
| `core/updates.py` | ищет `Tegr_Setup.exe` + SHA256 | платформенные ассеты: `.AppImage`/`.dmg`/`.apk`, свой канал |
| `icons/icons.py:339-424` | тема Windows из реестра, `SM_CXICON` | `QStyleHints.colorScheme()`, `QScreen.logicalDotsPerInch()` |
| `requirements.txt` | `pywin32==306` | маркер `; sys_platform == "win32"` (в коде не используется вовсе — можно удалить) |
| `_build.ps1`, `_build_inst.ps1`, `installer/` | Inno Setup, PE-ресурсы | `packaging/` на каждую платформу |

## 2. Шаг 0 — слой платформы (общий для всех, ~1 неделя)

Без этого любая сборка ломается на первом же импорте. Предлагается пакет `tegr/platform/`
с единственной точкой входа — остальной код спрашивает только её:

```
tegr/platform/__init__.py   # PLATFORM = 'win' | 'linux' | 'mac'
tegr/platform/paths.py      # data_dir(), cache_dir(), is_system_dir()
tegr/platform/system_proxy.py  # system_proxy() -> (host, port) | None
tegr/platform/single_instance.py  # QLockFile вместо мьютекса
tegr/platform/cursor.py     # cursor_pos()
tegr/platform/window_style.py  # frameless/pip/taskbar quirks
tegr/platform/mpv_backend.py   # wid-встраивание или render API
tegr/platform/updater.py    # какие ассеты качать и как ставить
```

Правило: **ни одного `if sys.platform` вне этого пакета**. Сейчас их 8 вразброс — они и есть источник
будущих регрессий.

Заодно: `requirements.txt` → `pyproject.toml` с маркерами (или три файла `requirements-win/mac/linux.txt`).
Проверить, что пины (`PyQt6==6.11.0`, `PyQt6-Qt6==6.11.2`) реально существуют колёсами под
`manylinux x86_64`, `macosx arm64` и `macosx universal2`: для части версий колёс под arm64 нет,
и придётся собирать Qt из исходников — это отдельные 2–3 дня.

## 3. Linux

### 3.1 Вариант L1 — PyInstaller + AppImage (рекомендуется)

Тот же PyInstaller, что и на Windows, только onedir (не onefile: onefile распаковывает ~150 МБ Qt
при каждом старте, а плеер и так живёт минутами).

```bash
# сборка в контейнере, чтобы glibc была старая (Ubuntu 20.04/22.04)
docker run --rm -v "$PWD:/src" -w /src ubuntu:22.04 bash -c '
  apt-get update && apt-get install -y python3.11 python3.11-venv libmpv2 libgl1 libxkbcommon0 \
      libegl1 libdbus-1-3 xdg-utils libfuse2
  python3.11 -m venv .venv && . .venv/bin/activate
  pip install -r requirements-linux.txt pyinstaller
  pyinstaller packaging/linux/Tegr.spec
  appimagetool dist/Tegr dist/Tegr-x86_64.AppImage'
```

- libmpv в AppImage: копировать `libmpv.so.2` + его зависимости (`ldd`) в `usr/lib`, в `AppRun`
  выставлять `LD_LIBRARY_PATH`.
- Трей на Linux — `StatusNotifier`/`libappindicator`: на GNOME без расширения значок не покажется.
  Нужно доработать: при отсутствии трея — иконка в заголовке окна, а не «программа пропала».
- Автообновление: `.AppImage` + `zsync` (AppImageUpdate) либо просто «скачать новый AppImage и
  заменить» — `os.replace` работает, файл не залочен.
- Ожидаемый размер: 120–180 МБ (Qt + libmpv + ffmpeg-библиотеки).

### 3.2 mpv: X11 работает, Wayland — нет

- X11: текущий `wid = video_widget.winId()` работает как есть.
- Wayland: чужое окно встроить нельзя. Два выхода:
  1. **Render API** — `mpv_render_context` + `QOpenGLWidget` (известный паттерн `MpvRenderWidget`).
     ~300–400 строк, зато работает и на Wayland, и на macOS, и даёт корректный HiDPI.
  2. Запускать `mpv` отдельным процессом с `--wid` нельзя (Wayland), а без встраивания
     ломается вся компоновка «панель поверх видео» → не годится.

  Рекомендация: сделать вариант 1 в рамках `platform/mpv_backend.py` ещё на этапе L1 — он же
  понадобится для macOS.

### 3.3 Вариант L2 — Flatpak

`org.kde.Platform//6.8` + `org.kde.Sdk`. Плюсы: изоляция, автообновление, нет проблем с glibc.
Минусы: PyQt6 в runtime надо проверить (если нет — `pip install` в `/app` на этапе сборки манифеста);
libmpv тоже либо из runtime, либо собирать модулем манифеста. Прокси и сокеты трея — через
`--share=network --talk-name=org.kde.StatusNotifierWatcher`. Итог: +1 неделя, но это единственный
путь в Flathub.

### 3.4 Вариант L3 — .deb / PPA / `pipx`

`python3 -m pip install .` + `.desktop` + иконка. Самый дешёвый (2–3 дня), но требует Python 3.11
и `libmpv2` из репозитория дистрибутива: `Depends: libmpv2 | libmpv1`. Для Debian/Ubuntu — норм,
для «просто скачал и запустил» — нет. Хорош как дополнение к L1, не как замена.

## 4. macOS

### 4.1 Вариант M1 — PyInstaller `.app` + `.dmg`

```bash
brew install mpv python@3.11 create-dmg
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements-mac.txt pyinstaller
python -m PyInstaller packaging/macos/Tegr.spec   # BUNDLE + Info.plist
# libmpv и его зависимости — внутрь .app
dylibbundler -od -b -x dist/Tegr.app/Contents/MacOS/Tegr \
    -x /opt/homebrew/lib/libmpv*.dylib -d dist/Tegr.app/Contents/Frameworks
codesign --deep --force --options runtime --entitlements packaging/macos/entitlements.plist \
    --sign "Developer ID Application: …" dist/Tegr.app
xcrun notarytool submit Tegr.dmg --keychain-profile tegr --wait
```

Обязательные пункты, без которых «не запускается у людей»:

1. **Подпись + нотаризация** — иначе Gatekeeper блокирует (`Apple Developer Program`, $99/год).
   Entitlements: `com.apple.security.network.client`, `com.apple.security.network.server` (локальный
   HTTP-сервер!), `com.apple.security.cs.allow-dyld-environment-variables` (нужен libmpv).
2. **Две архитектуры**: Homebrew-бутылки однопроцессорные, universal2 libmpv собирается из исходников.
   Дешевле выпускать два dmg (arm64, x86_64), чем один universal.
3. **Встраивание mpv**: на macOS `winId()` возвращает `NSView*`, python-mpv это принимает, но нужен
   `--vo=libmpv` и `--gpu-api=opengl`; на Apple Silicon чаще надёжнее render API из §3.2.
4. **Прокси**: `scutil --proxy` вместо реестра (PAC/HTTP).
5. **Пути**: `~/Library/Application Support/Tegr`, кэш — `~/Library/Caches/Tegr` (иначе бэкапы Time
   Machine будут тянуть десятки гигабайт видео).
6. **Иконка**: `.ico` не годится — `iconutil` из набора PNG (в `tegr/icons/` иконки рисуются
   кодом, значит .icns можно генерировать из тех же пиксельных карт).
7. **Горячие клавиши**: `Cmd` вместо `Ctrl`, а `Cmd+Q`/`Cmd+W` — системные.

Автообновление: Sparkle требует подписи (Appcast + EdDSA) — +3 дня. Проще на первом этапе: проверка
релиза, скачивание `.dmg`, открыть его и попросить перетащить (уже есть `core/updates.py`).

### 4.2 Вариант M2 — Homebrew Cask

`brew install --cask tegr` с формулой, которая тянет готовый `.dmg`. Даёт «одну команду» для
технической аудитории почти бесплатно, но всё равно требует подписанного dmg из M1.

### 4.3 Вариант M3 — py2app / PyOxidizer

`py2app` — аналог PyInstaller, чуть лучше дружит с бандлами, но заметно капризнее с Qt-плагинами.
`PyOxidizer` — встраивает интерпретатор в один бинарник, ускоряет старт, но сборка хрупкая и проект
сейчас в стагнации. Рекомендация: не тратить время, PyInstaller+`BUNDLE` закрывает задачу.

## 5. Android — отдельный проект, но не с нуля

Ключевой факт: **PyQt6 на Android не поставляется**. Ни PyQt6, ни PySide6 не имеют официальных
колёс под Android, а `pyqtdeploy` требует сборки Qt под Android из исходников и GPL-совместимой
лицензии. Значит UI (14 000 строк) придётся переносить, а backend (4 300 строк) — переиспользовать.

### 5.1 Вариант A1 — Kotlin/Compose + Chaquopy (рекомендуется)

`Chaquopy` — плагин Gradle, который кладёт CPython 3.11 внутрь APK и позволяет звать Python из Kotlin.

```
app/
 ├─ src/main/java/…/ui/        # Compose: каналы, видео, история, загрузки, настройки
 ├─ src/main/java/…/player/    # Media3 ExoPlayer на http://127.0.0.1:<port>/stream
 └─ src/main/python/tegr/      # backend/ + core/ почти как есть
      backend/{cache,download,stream,catalog,videos,connection,login,playback}.py
      core/{settings,history,config,proxy,log}.py
```

Что переиспользуется без изменений (≈5 800 строк, четверть проекта):
`cache_math.py`, `http_range.py`, `cache.py`, `download.py`, `stream.py`, `videos.py`,
`catalog.py`, `connection.py`, `playback.py`, `settings.py`, `history.py`, `config.py`.
Убрать `PyQt6.pyqtSignal` из `backend.py` — заменить на колбэки → Kotlin-мост.

Что переписывается: `player/player.py`, `windows/*`, `widgets/*` → Compose (эквивалент ~6–7 тыс. строк,
но на Kotlin это быстрее, чем на PyQt6).

Плеер — два пути:

| Движок | Плюсы | Минусы |
|---|---|---|
| **Media3 ExoPlayer** (рекомендуется для MVP) | стандартный, аппаратное декодирование, HLS/MP4, PiP, «картинка в картинке» из коробки | нет тонких настроек mpv (списки vo/hwdec), нет «любых» кодеков |
| **libmpv** (`mpv-android` → AAR, JNI) | паритет с десктопом | сборка .so под 4 ABI, **GPL**, +30–40 МБ, JNI-обвязка |

Важно: `stream.py` уже отдаёт `206 Partial Content` и умеет приоритет кусков — ExoPlayer на это
опирается так же, как mpv. То есть «мгновенный старт и перемотка» переезжают бесплатно.

Android-специфика, которую надо заложить сразу:

1. `ForegroundService` с типом `mediaPlayback` (+ `FOREGROUND_SERVICE_MEDIA_PLAYBACK`): иначе Android 12+
   убьёт загрузку при выключенном экране.
2. `usesCleartextTraffic` / `network-security-config` с исключением для `127.0.0.1` — иначе Android 9+
   режет HTTP к локальному серверу.
3. Кэш — `context.cacheDir` или `getExternalFilesDir`, лимит видимый в настройках; Android
   склонен чистить кэш при нехватке места.
4. Разрешения: `INTERNET`, `FOREGROUND_SERVICE*`, `POST_NOTIFICATIONS` (13+); для внешнего кэша — ничего.
5. Doze/энергосбережение: `WifiLock` + `PARTIAL_WAKE_LOCK` на время активной загрузки, и честная
   пауза, когда система всё же усыпила.
6. Экономия размера: Chaquopy +~15–25 МБ на ABI; собирать `abiFilters` только arm64-v8a + armeabi-v7a.
7. Свой `api_id`/`api_hash` (сейчас в `core/config.py` — `API_ID = 38968042`, десктопный). Telegram
   требует отдельное приложение на каждую платформу; чужой api_id — нарушение ToS и риск бана.
8. Вход по QR-коду на телефоне бессмысленен — нужен вход по номеру (код возвращается в
   `login.py` — это уже написано, но отключено), плюс `passcode`/2FA.
9. Публикация: Google Play формально требует отсутствия «подделок Telegram»; реалистично —
   GitHub Releases APK + F-Droid (`Неофициальный клиент` в описании).

Срок MVP (просмотр каналов, стриминг через кэш, история, докачка): **6–8 недель**.
Полный паритет (темы, PiP, панель списка серий, прокси, автообновление): **10–12 недель**.

### 5.2 Вариант A2 — Flutter + Chaquopy

UI на Flutter (Dart), backend — тот же Python через MethodChannel/FFI-мост к Chaquopy.
Плюс: один UI-код на Android/iOS/desktop, хорошая анимация, проще нанимать людей.
Минус: +2–4 недели на мост и на переписывание UI на Dart; выигрыш по качеству UI при таком
«плоском» дизайне небольшой. Имеет смысл, если в планах iOS.

### 5.3 Вариант A3 — Kivy + Buildozer (python-for-android)

Единственный путь «оставить Python и на UI». Но: UI с нуля на Kivy (свой язык разметки, свои
виджеты), вид далёк от текущего плоского дизайна, libmpv-рецепта в python-for-android нет
(плеер — `android.media.MediaPlayer` или `ffpyplayer`, кодеки хуже), сборка APK капризна.
Оправдано только для быстрого прототипа «работает ли сеть и кэш на телефоне» — 1–2 недели.

### 5.4 Вариант A4 — Termux + Termux:X11 (демо, не продукт)

`pkg install x11-repo python-pyqt6` (если пакет есть в вашем репозитории) + `mpv` + запуск
`player_tegr.py` под X-сервером. Даёт понимание, «как ведёт себя Telethon и кэш на Android»,
за 1–2 дня, но распространять это нельзя: нет APK, батарея, фон, права.

### 5.5 Вариант A5 — pyqtdeploy / Qt for Android

Технически возможно: собрать Qt 6 под Android, `pyqtdeploy` упакует PyQt6. На практике — дни на
сборку тулчейна, версии PyQt6-Qt6 (6.11.x) под Android-колёса отсутствуют, а PyQt6 — **GPLv3**
(закрытая поставка требует коммерческой лицензии). Не рекомендуется.

## 6. Рекомендуемый маршрут

1. **Неделя 1**: слой `tegr/platform/`, ленивый `ctypes.wintypes`, `QLockFile`, XDG/AppSupport-пути,
   `requirements` с маркерами, CI-матрица (ubuntu/macos/windows) — тесты уже идут с `QT_QPA_PLATFORM=offscreen`,
   так что Linux-джоб заведётся почти сразу.
2. **Недели 2–3**: Linux L1 (AppImage) + render API для mpv (закрывает Wayland и пригодится на macOS).
3. **Недели 4–6**: macOS M1 (подпись, нотаризация, два dmg), затем Flatpak, если нужен Flathub.
4. **Недели 7–18**: Android A1 — сначала «Python-сервис + Compose-заглушка + ExoPlayer»,
   затем перенос экранов в порядке: каналы → видео → плеер → история/загрузки → настройки.

Почему не Android первым: без слоя платформы и без отвязанного от Qt backend перенос упрётся
в те же `ctypes.wintypes` и реестр, только уже внутри Gradle.

## 7. Что менять в коде — чек-лист

- [ ] `tegr/widgets/widgets.py:12` — убрать модульный `import ctypes.wintypes`, импорт внутри try.
- [ ] `tegr/player/player.py:81, 1646, 1891–1968, 2550, 2586, 2618, 2795` — курсор/owner/DWM через `platform/`.
- [ ] `tegr/player/player.py:560–570` — выбор backend mpv (wid или render).
- [ ] `tegr/system/mpv_boot.py` — `find_library` + диагностика под `.so`/`.dylib`.
- [ ] `tegr/core/proxy.py:315–370` — системный прокси на Linux/macOS.
- [ ] `tegr/core/paths.py`, `tegr_runtime.py:53–160` — XDG / Application Support.
- [ ] `tegr/windows/windows.py:6072` — `QLockFile` на всех платформах.
- [ ] `tegr/core/updates.py:44` — ассеты по платформе (`SETUP_ASSETS` → словарь).
- [ ] `tegr/icons/icons.py:339–424` — тема из `QStyleHints`, метрики из `QScreen`.
- [ ] `requirements.txt` — `pywin32` с маркером (или удалить: не импортируется).
- [ ] `tegr.ico` → `tegr.icns` (macOS) и PNG 256/512 (Linux).
- [ ] `_build.ps1` → `packaging/linux/`, `packaging/macos/`, `packaging/android/`.
- [ ] `.github/workflows/` — джобы: тесты (3 ОС) + сборка артефактов (AppImage, dmg, APK).

## 8. Риски

| Риск | Где болит | Что делать |
|---|---|---|
| PyQt6 — **GPLv3** | любая платная/закрытая поставка | либо открыть исходники (сейчас так и есть), либо купить коммерческую лицензию, либо миграция на PySide6 (LGPL) — это отдельный проект на 2–3 недели |
| libmpv — **LGPLv2.1+/GPL** | AppImage/Flatpak/APK | собирать libmpv в LGPL-режиме (`--enable-lgpl`) и не тянуть GPL-части; в APK — GPL-сборки mpv-android избегать |
| Нотаризация Apple | macOS | $99/год + Apple ID; без этого — «правый клик → Открыть» |
| Чужой Telegram `api_id` | Android/новые платформы | свой api_id на каждую платформу |
| Onefile-сборка | все | onedir: старт быстрее, антивирус спокойнее, обновление по файлам |
| Два экземпляра на Linux/macOS | порча сессии Telethon | `QLockFile` — до первого релиза |
| Wayland | Linux | render API, а не `wid` |

## 9. Минимум, который можно сделать прямо сейчас

1. `packaging/linux/Tegr.spec` + `_build_linux.sh` (копия `_build.ps1`, без PE-ресурсов и Inno).
2. `packaging/macos/Tegr.spec` + `Info.plist` + `entitlements.plist`.
3. `docs/` — этот файл как отправная точка для трекера задач.
4. Прогнать тесты на Linux-раннере: если `ctypes.wintypes` всплывёт — он всплывёт именно там.
