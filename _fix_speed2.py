import io
p = r"C:\tgplayer\_t_real_speed.py"
t = io.open(p, encoding="utf-8").read()
# Пометки могут быть ещё не собраны — берём любой диалог с большим количеством сообщений
t = t.replace('''    marks = dict(getattr(backend, '_dialog_has_video', {}) or {})
    good = {str(k) for k, v in marks.items() if v}
    dialog = None
    for d in (getattr(backend, 'dialogs', []) or []):
        try:
            peer = backend._dialog_peer_id(d)
        except Exception:
            peer = None
        if peer is not None and str(peer) in good:
            dialog = d
            break
    if dialog is None:
        say('ПРОВАЛ: нет канала с видео')
        return

    backend.active_dialog_id = getattr(dialog, 'id', None)
    await backend.load_videos_from_dialog(dialog)
    msgs = list(getattr(backend, 'current_messages', []) or [])
    if not msgs:
        say('ПРОВАЛ: список видео пуст')
        return''','''    marks = dict(getattr(backend, '_dialog_has_video', {}) or {})
    good = {str(k) for k, v in marks.items() if v}
    say('каналов с видео по пометкам: %d' % len(good))
    dialogs = list(getattr(backend, 'dialogs', []) or [])
    say('диалогов: %d' % len(dialogs))

    # Перебираем каналы, пока не найдём видео: у части диалогов роликов
    # в последних сообщениях нет, и список выйдет пустым.
    msgs = []
    order = []
    for d in dialogs:
        try:
            peer = backend._dialog_peer_id(d)
        except Exception:
            peer = None
        if peer is not None and str(peer) in good:
            order.insert(0, d)
        else:
            order.append(d)

    for dialog in order[:12]:
        backend.active_dialog_id = getattr(dialog, 'id', None)
        try:
            await backend.load_videos_from_dialog(dialog)
        except Exception as ex:
            say('  канал %s: %s' % (getattr(dialog, 'id', '?'), ex))
            continue
        msgs = list(getattr(backend, 'current_messages', []) or [])
        if msgs:
            say('нашли канал с видео: %s' % (getattr(dialog, 'name', '') or getattr(dialog, 'id', '')))
            break
    if not msgs:
        say('ПРОВАЛ: список видео пуст во всех проверенных')
        return''')
io.open(p, "w", encoding="utf-8").write(t)
print("готово")
