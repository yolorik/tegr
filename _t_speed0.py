# Проверка: почему ПРЕФЕТЧ не забирает все 4 МБ из очереди
SEG = 4*1024*1024
pieces = 200                      # файл 800 МБ
head_bytes = 2*1024*1024          # BOOTSTRAP_BYTES
order = sorted(range(pieces), key=lambda i: (0 if i*SEG < head_bytes else 1, i))
print("первые 6 кусков очереди:", order[:6])
print("кусков в голове (2 МБ при SEG 4 МБ):", sum(1 for i in order if i*SEG < head_bytes))
print()
print("СЦЕНАРИЙ: смотрим с начала, загрузка идёт 10 с, скачано ~4 куска")
done = set(order[:4])
print("скачано:", sorted(done), "осталось в очереди:", len([i for i in range(pieces) if i not in done]))
print("ПРЕФЕТЧ встаёт на 2-й итерации (~1 с): очередь цела, стартует со 2-го воркера")
