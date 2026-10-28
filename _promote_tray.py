# -*- coding: utf-8 -*-
"""Закрепляем иконку TGPlayer в области уведомлений Windows.

Windows хранит в реестре список всех значков трея и флаг IsPromoted:
1 — показывать всегда, 0 или отсутствие — прятать в переполненном меню.
На этой машине 54 записи, из них 45 скрыто, поэтому новая иконка сразу
уходит под стрелку и её не видно.

Ключ у каждой программы свой, имя ключа — хеш от пути к .exe. Проще всего
не считать его самим, а найти запись по полю ExecutablePath и выставить
IsPromoted. Если записи ещё нет (программа ни разу не показывала значок),
создаём свой ключ с нужным путём.
"""
import subprocess
import sys
import winreg

REG_PATH = r'Software\Microsoft\Windows\CurrentVersion\Explorer'
NOTIFY = r'Control Panel\NotifyIconSettings'


def find_keys(exe_path):
    """Все ключи иконок, привязанные к этому exe."""
    found = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY, 0, winreg.KEY_READ)
    except OSError:
        return found
    try:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                sub = winreg.OpenKey(root, name, 0, winreg.KEY_READ)
                try:
                    path, _ = winreg.QueryValueEx(sub, 'ExecutablePath')
                finally:
                    sub.Close()
            except OSError:
                continue
            if path.lower() == exe_path.lower():
                found.append(name)
    finally:
        root.Close()
    return found


def promote(key_name):
    full = f'{NOTIFY}\\{key_name}'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, full, 0,
                        winreg.KEY_SET_VALUE | winreg.KEY_READ) as k:
        before = None
        try:
            before, _ = winreg.QueryValueEx(k, 'IsPromoted')
        except OSError:
            pass
        winreg.SetValueEx(k, 'IsPromoted', 0, winreg.REG_DWORD, 1)
        return before


def create_entry(exe_path):
    """Создаёт запись для exe, если её ещё нет.

    Имя ключа Windows строит как хеш пути; нам его значение не нужно —
    достаточно любого уникального имени, Explorer читает содержимое.
    """
    import hashlib
    name = hashlib.sha1(exe_path.lower().encode('utf-16-le')).hexdigest()[:32]
    full = f'{NOTIFY}\\{name}'
    try:
        k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, full, 0, winreg.KEY_WRITE)
    except OSError as ex:
        print(f'  не удалось создать ключ: {ex}')
        return None
    try:
        winreg.SetValueEx(k, 'ExecutablePath', 0, winreg.REG_SZ, exe_path)
        winreg.SetValueEx(k, 'IsPromoted', 0, winreg.REG_DWORD, 1)
    finally:
        k.Close()
    return name


def restart_explorer():
    """Перезапуск проводника — иначе изменения видны только после входа."""
    print('перезапускаю проводник...')
    subprocess.run(['taskkill', '/f', '/im', 'explorer.exe'],
                   capture_output=True)
    subprocess.Popen(['explorer.exe'])
    print('готово')


def main():
    try:
        exe = sys.argv[1] if len(sys.argv) > 1 else r'C:\tgplayer\dist\TGPlayer\TGPlayer.exe'
    except Exception:
        exe = r'C:\tgplayer\dist\TGPlayer\TGPlayer.exe'

    print(f'программа: {exe}')
    keys = find_keys(exe)
    if keys:
        print(f'найдено записей: {len(keys)}')
        for k in keys:
            before = promote(k)
            print(f'  {k}: IsPromoted было {before}, стало 1')
    else:
        print('записей нет — создаю')
        name = create_entry(exe)
        if name:
            print(f'  создано: {name}')

    restart_explorer()


if __name__ == '__main__':
    main()
