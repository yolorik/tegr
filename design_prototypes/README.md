# Прототипы оформления Tegr

Только интерфейс. Ни Telegram, ни mpv, ни кэш, ни загрузки здесь не
подключены — это макеты на PyQt6 с демонстрационными данными.

## Варианты

- **Tegr** — выбранное направление: три колонки как в Studio, светлый
  стиль Paper, четыре цветовые темы и масштаб интерфейса 75–150%.
- **Aurora** — тёмный неоновый: сайдбар, фиолетовые свечения, крупный
  поиск, чипы, карточки.
- **Paper** — светлый редакционный: тёплый фон, серифные заголовки,
  верхняя навигация, очередь справа.
- **Studio** — плотный рабочий: три колонки, компактные строки, янтарный
  акцент.

## Запуск

Из корня проекта:

```bash
python design_prototypes/launcher.py
```

Основной вариант — `variant_tegr.py`. Остальные оставлены для
сравнения.

Или отдельно:

```bash
python design_prototypes/variant_tegr.py
python design_prototypes/variant_aurora.py
python design_prototypes/variant_paper.py
python design_prototypes/variant_studio.py
```

Нужен только PyQt6.

## Превью

- `previews/tegr.png` — основной вариант
- `previews/tegr-settings.png` — темы и масштаб
- `previews/tegr-graphite.png` — тёмная тема
- `previews/aurora.png` — главный экран
- `previews/aurora-channels.png` — каналы
- `previews/paper.png` — главный экран
- `previews/paper-channels.png` — каналы
- `previews/studio.png` — главный экран
- `previews/studio-history.png` — история
- `previews/launcher.png` — лаунчер
