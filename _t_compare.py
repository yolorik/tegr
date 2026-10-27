"""Сравнение журналов: до и после фазового замка.

Главный признак проблемы — строки, где за ОДНУ секунду молчит 2+ куска.
Это значит, что два потока по одному файлу душили друг друга.
"""
import re, sys
from collections import Counter

PAT = re.compile(r"(\d\d:\d\d:\d\d).*поток (?:молчит|не отдал данные за) (\d+) с")

def report(path, label):
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except FileNotFoundError:
        print(f"{label}: файла нет ({path})")
        return None
    ev = []
    for ln in lines:
        m = PAT.search(ln)
        if m:
            ev.append((m.group(1), int(m.group(2))))
    if not ev:
        print(f"{label}: событий молчания НЕТ  <-- идеально")
        return dict(total=0, sim=0, secs=0)

    by_sec = Counter(t for t, _ in ev)
    sim = [t for t, n in by_sec.items() if n >= 2]
    print(f"{label}:")
    print(f"   всего молчаний: {len(ev)}")
    print(f"   секунд с молчанием: {len(by_sec)}")
    print(f"   из них 2+ куска СРАЗУ: {len(sim)}  <-- {'ПРОБЛЕМА' if sim else 'чисто'}")
    if sim:
        pct = 100.0 * len(sim) / len(by_sec)
        print(f"   доля: {pct:.0f}%")
    return dict(total=len(ev), sim=len(sim), secs=len(by_sec))

print("=" * 60)
report(r"C:\tgplayer\_log_before_phase.log", "ДО (старый код)")
print("=" * 60)
report(r"C:\tgplayer\tgplayer.log", "ПОСЛЕ (с замком)")
print("=" * 60)
