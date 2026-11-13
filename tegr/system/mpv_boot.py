"""Загрузка libmpv и диагностика, если она не поднялась.

Импортировать этот модуль нужно ДО остальных: при импорте он кладёт
каталог библиотек в PATH и грузит загрузчик Vulkan первым. libmpv
зависит от него жёстко, а порядок поиска DLL на чужой машине может
отличаться: если на него положиться, отказ выглядит как «не удалось
загрузить libmpv-2.dll», хотя виноват совсем другой файл.
"""
import ctypes
import os
import sys

from ..core import paths

MPV_DIR = paths.bundle_dir()
MPV_IMPORT_ERROR = None
mpv = None


def _vulkan_loader_present():
    """Есть ли загрузчик Vulkan — рядом с плеером или в системе.

    Нужен только для внятного сообщения об ошибке: без него libmpv не
    грузится целиком, и причина снаружи выглядит непонятно.
    """
    if os.path.exists(os.path.join(MPV_DIR, 'vulkan-1.dll')):
        return True
    root = os.environ.get('SystemRoot', r'C:\Windows')
    for sub in ('System32', 'SysWOW64'):
        if os.path.exists(os.path.join(root, sub, 'vulkan-1.dll')):
            return True
    return False


def _pe_imports(path):
    """Имена DLL из таблицы импорта PE-файла.

    Нужны, чтобы понять, на чём именно спотыкается загрузчик: «не удалось
    загрузить» без списка зависимостей не отличить от пропавшего файла.
    """
    import struct
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if data[:2] != b'MZ':
            return []
        pe = struct.unpack_from('<I', data, 0x3C)[0]
        if data[pe:pe + 4] != b'PE\0\0':
            return []
        n_sections = struct.unpack_from('<H', data, pe + 6)[0]
        opt_size = struct.unpack_from('<H', data, pe + 20)[0]
        opt = pe + 24
        magic = struct.unpack_from('<H', data, opt)[0]
        dd = opt + (112 if magic == 0x20b else 96)
        imp_rva = struct.unpack_from('<I', data, dd + 8)[0]
        if not imp_rva:
            return []
        sections = []
        sec = opt + opt_size
        for i in range(n_sections):
            base = sec + i * 40
            va, vsize = struct.unpack_from('<II', data, base + 12)
            raw, rawsize = struct.unpack_from('<II', data, base + 20)
            sections.append((va, vsize, raw, rawsize))

        def to_off(rva):
            for va, vsize, raw, rawsize in sections:
                if va <= rva < va + max(vsize, rawsize):
                    return raw + (rva - va)
            return None

        off = to_off(imp_rva)
        if off is None:
            return []
        names = []
        while off + 20 <= len(data):
            entry = data[off:off + 20]
            if entry == b'\0' * 20:
                break
            name_rva = struct.unpack_from('<I', entry, 12)[0]
            name_off = to_off(name_rva)
            if name_off is None:
                break
            end = data.find(b'\0', name_off)
            if end < 0:
                break
            names.append(data[name_off:end].decode('ascii', 'replace'))
            off += 20
        return names
    except Exception:
        return []


def _cpu_features():
    """Что умеет процессор — по данным самой Windows.

    На старых процессорах libmpv может не загрузиться, и без этого
    списка причину не отличить от пропавшей библиотеки.
    """
    names = [(10, 'SSE2'), (12, 'SSE3'), (37, 'SSE4.1'), (38, 'SSE4.2'),
             (39, 'AVX'), (40, 'AVX2'), (41, 'AVX512F')]
    try:
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    except Exception as ex:
        return f'не удалось спросить ({ex})'
    out = []
    for num, name in names:
        try:
            ok = bool(k32.IsProcessorFeaturePresent(num))
        except Exception:
            ok = False
        out.append(name if ok else '-' + name)
    return ' '.join(out)


def diagnosis():
    """Подробный отчёт: почему не загрузился libmpv-2.dll.

    Проверяем каждую зависимость настоящей загрузкой, а не наличием
    файла: часть имён — это контракты API set, файлов с такими именами
    не существует, их разрешает сам загрузчик Windows. Поэтому «файла
    нет» здесь ничего не значит, а «не грузится» — значит.
    """
    lines = []
    try:
        path = os.path.join(MPV_DIR, 'libmpv-2.dll')
        lines.append(f"файл: {path}")
        exists = os.path.exists(path)
        lines.append(f"  существует: {exists}")
        if exists:
            lines.append(f"  размер: {os.path.getsize(path)} байт")
        lines.append(f"каталог поиска DLL: {MPV_DIR}")
        try:
            wv = sys.getwindowsversion()
            lines.append(f"Windows: {wv.major}.{wv.minor} "
                         f"сборка {wv.build}")
        except Exception:
            lines.append("Windows: не удалось определить")
        lines.append(f"процессор: {_cpu_features()}")
        lines.append(f"ошибка загрузки: {MPV_IMPORT_ERROR or 'нет'}")
        if exists:
            bad = []
            for name in _pe_imports(path):
                try:
                    ctypes.WinDLL(name)
                except Exception as ex:
                    bad.append(f"{name}: {ex}")
            if bad:
                lines.append(f"не загружаются зависимости ({len(bad)}):")
                lines.extend("  " + b for b in bad)
            else:
                lines.append("все зависимости загружаются по отдельности")
    except Exception as ex:
        lines.append(f"диагностика не удалась: {ex}")
    return "\n".join(lines)


def _bootstrap():
    """Кладёт каталог библиотек в путь поиска и грузит зависимости."""
    global MPV_IMPORT_ERROR
    if sys.platform.startswith('win'):
        if hasattr(os, 'add_dll_directory'):
            os.add_dll_directory(MPV_DIR)
        os.environ['PATH'] = (MPV_DIR + os.pathsep
                              + os.environ.get('PATH', ''))
        vk = os.path.join(MPV_DIR, 'vulkan-1.dll')
        if os.path.exists(vk):
            try:
                ctypes.WinDLL(vk)
                print(f"[MPV] Загружен загрузчик Vulkan: {vk}")
            except OSError as ex:
                print(f"[MPV] Не удалось загрузить vulkan-1.dll: {ex}")
        for dll in ('libmpv-2.dll', 'mpv-2.dll', 'mpv.dll', 'libmpv.dll'):
            p = os.path.join(MPV_DIR, dll)
            if not os.path.exists(p):
                continue
            try:
                ctypes.CDLL(p)
                print(f"[MPV] Загружена библиотека: {p}")
                break
            except OSError as ex:
                # Код ошибки Windows решает всё: 126 — не хватает
                # зависимости, 193 — не тот разряд, 1114 — сбой
                # инициализации DLL (обычно не хватает возможностей
                # процессора). Без него отчёт бесполезен.
                MPV_IMPORT_ERROR = ex
                # libmpv собран с жёсткой зависимостью от загрузчика
                # Vulkan. На машинах без драйвера с поддержкой Vulkan
                # (старые и встроенные адаптеры, виртуальные дисплеи) его
                # нет в системе, и библиотека не грузится целиком — плеер
                # не играет вообще ничего. Поэтому загрузчик кладём рядом
                # с плеером.
                if not _vulkan_loader_present():
                    print("[MPV] Не хватает vulkan-1.dll — положите его "
                          "рядом с плеером (он есть в поставке программы).")
                # Полный отчёт: на чужом ПК без него причину не видно, а
                # «не удалось загрузить» одинаково выглядит и при
                # пропавшем файле, и при нехватке зависимости, и при
                # слишком старом процессоре.
                print("[MPV] Диагностика:\n" + diagnosis())


_bootstrap()


def _import_python_mpv():
    """Импортирует python-обёртку mpv. Ошибку запоминаем, а не роняем."""
    global mpv, MPV_IMPORT_ERROR
    try:
        import mpv as _mpv
        mpv = _mpv
        MPV_IMPORT_ERROR = None
    except Exception as e:
        mpv = None
        MPV_IMPORT_ERROR = e


_import_python_mpv()

# Qt не должен сыпать предупреждениями мультимедиа: плеер работает через
# libmpv, а встроенный ffmpeg Qt тут ни при чём.
os.environ["QT_LOGGING_RULES"] = "qt.multimedia.ffmpeg=false"
