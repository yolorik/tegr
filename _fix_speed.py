import io
p = r"C:\tgplayer\_t_real_speed.py"
t = io.open(p, encoding="utf-8").read()
t = t.replace('''    # Мост Qt <-> asyncio: гоняем замер в потоке бэкенда.
    fut = asyncio.run_coroutine_threadsafe(measure(backend, app), backend.loop)''','''    # Ждём, пока рабочий поток поднимет цикл событий.
    loop = None
    for _ in range(200):
        app.processEvents()
        time.sleep(0.05)
        loop = getattr(backend, 'loop', None)
        if loop is not None and loop.is_running():
            break
    if loop is None or not loop.is_running():
        say('ПРОВАЛ: цикл бэкенда не поднялся')
        return

    # Мост Qt <-> asyncio: гоняем замер в потоке бэкенда.
    fut = asyncio.run_coroutine_threadsafe(measure(backend, app), loop)''')
io.open(p, "w", encoding="utf-8").write(t)
print("готово")
