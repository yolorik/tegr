# Гипотеза: причина затупов и нуля — ОДНА. Тело файла и хвост тянутся двумя
# независимыми потоками запросов по ОДНОМУ И ТОМУ ЖЕ файлу, но у Telegram
# на один файл есть общий лимит скорости. Плюс два конца не кэшируются
# подряд (у mpv ДВА читателя -> два Range), и каждый разрыв -> запрос заново.
#
# Проверяем в журнале: сколько кусков молчит ОДНОВРЕМЕННО.
data = open(r"C:\tgplayer\tgplayer.log", encoding="utf-8", errors="replace").read().splitlines()
import re
ev=[]
for ln in data:
    m = re.search(r"(\d\d:\d\d:\d\d).*поток (?:молчит|не отдал данные за) (\d+) с", ln)
    if m: ev.append((m.group(1), int(m.group(2))))
print("всего событий молчания:", len(ev))
from collections import Counter
c = Counter(t for t,_ in ev)
sim = [t for t,n in c.items() if n>=2]
print("секунд, где молчало 2+ куска СРАЗУ:", len(sim), "из", len(c))
print("=> ровно столько раз оба конца вставали одновременно")
print()
print("Молчания по таймауту:")
print(Counter(s for _,s in ev))
