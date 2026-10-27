# Реальное поведение: пока mpv ЧИТАЕТ, _serve_from_cache поднимает read_gen
# у КАЖДОГО нового куска, которого нет в active/piece_done.
# Воркер видит смену поколения -> бросает свой кусок -> pos сохраняется в трекере.
SEG = 4*1024*1024
pieces = 200
done, active = set(), set()
total_fetched = 0        # сколько МБ реально скачали
queue = list(range(pieces))

# Имитация: плеер читает последовательно со скоростью 1 кусок за 2 с.
# Каждый раз, когда читатель упирается в новый кусок -> read_gen++, воркеры бросают.
read_gen = 0
worker_piece = None
worker_progress = 0.0    # доля скачанного куска
FETCH_RATE = 2.0         # МБ/с на воркер

for tick in range(60):   # 30 с, шаг 0.5 с
    # читатель
    reader_piece = min(int(tick*0.25), pieces-1)
    if reader_piece not in done and reader_piece not in active:
        read_gen += 1
        # воркер бросает свой кусок, взятая доля остаётся в трекере
        if worker_piece is not None and worker_piece not in done:
            fetched = worker_progress*4
            total_fetched += fetched
            worker_progress = 0.0
            worker_piece = None
    # воркер качает
    if worker_piece is None:
        worker_piece = queue.pop(0) if queue else None
        active = {worker_piece} if worker_piece is not None else set()
    if worker_piece is not None:
        worker_progress += FETCH_RATE*0.5/4
        if worker_progress >= 1.0:
            total_fetched += 4
            done.add(worker_piece); active=set()
            worker_piece=None
print("за 30 с: скачано %.0f МБ, готовых кусков %d" % (total_fetched, len(done)))
print("но в кусках размером %d МБ это не больше %.1f МБ" % (4, (len(done)+1)*4))
