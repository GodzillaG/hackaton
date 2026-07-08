# ScolioScan School

Мобильный ИИ-комплекс для первичного скрининга риска сколиоза у школьников.

Сценарий работы: ноутбук запускает сервер анализа и Next.js сайт, телефон находится в той же Wi-Fi сети, открывает сайт по IP ноутбука, проходит протокол съёмки из пяти ракурсов и получает отчёт с уровнем риска, метриками, планом действий и изображениями с разметкой.

## Что внутри

```text
.
├── app/                 # Next.js интерфейс для телефона и ноутбука
├── api_server.py        # Flask API для загрузки фото и анализа
├── models/              # MediaPipe .task модель
├── pose_analyzer.py     # MediaPipe/OpenCV анализ асимметрии
├── public/test_images/  # демонстрационные изображения на сайте
├── pyproject.toml       # Python зависимости для uv
└── package.json         # Next.js/npm скрипты
```

## Возможности

- протокол из пяти ракурсов: спереди, со спины, левый бок, правый бок, тест Адамса;
- боковые ракурсы используются как профильный контроль и не повышают фронтальные метрики асимметрии;
- отдельные кнопки для камеры и галереи на телефоне;
- демонстрационные изображения на сайте, которые запускают анализ одним нажатием;
- подготовка фото в браузере: сжатие и конвертация в JPEG перед отправкой;
- автоматический расчёт 5 метрик асимметрии;
- уровни риска `low`, `moderate`, `high`;
- веб-результат: индикатор риска, карточки метрик, рекомендации и план действий;
- overlay-картинки с разметкой и сохранение отчёта в `reports/`;
- Next.js проксирует загрузку фото в Python API, поэтому телефону нужен только порт сайта;
- основной анализ через `models/pose_landmarker_lite.task`;
- резервный контурный анализ через OpenCV, если MediaPipe не нашёл человека на сложном кадре.

## Модель

В проект уже скачана лёгкая модель:

```text
models/pose_landmarker_lite.task
```

Она выбрана для стабильной работы на CPU ноутбука. Если нужно заменить модель вручную:

```bash
mkdir -p models
curl -L -o models/pose_landmarker_lite.task \
  https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
```

Можно запустить API с другой моделью:

```bash
SCOLISCAN_POSE_MODEL=models/pose_landmarker_full.task npm run api
```

## Установка

Python:

```bash
uv --cache-dir /tmp/uv-cache sync
```

Frontend:

```bash
npm install
```

## Запуск локально

Терминал 1, сервер анализа:

```bash
npm run api
```

Сервер анализа будет доступен на:

```text
http://0.0.0.0:5000
```

Пользователь работает только с сайтом: Next.js сам передаёт фото в сервер анализа.

Терминал 2, Next.js:

```bash
npm run dev
```

Сайт будет доступен на:

```text
http://0.0.0.0:3000
```

## Тесты

```bash
npm test
npm run build
```

`npm test` запускает Python unit-тесты для API-ответов, расчёта метрик и вспомогательной логики анализатора.

## Открыть с телефона

1. Подключить ноутбук и телефон к одной Wi-Fi сети.
2. Узнать IP ноутбука.

Windows PowerShell:

```powershell
ipconfig
```

Linux/WSL:

```bash
hostname -I
```

3. На телефоне открыть:

```text
http://IP_НОУТБУКА:3000
```

Например:

```text
http://192.168.1.45:3000
```

Телефон открывает только сайт на порту `3000`. Фото отправляется на этот же адрес, а Next.js уже локально передаёт его в сервер анализа.

## API

Проверка:

```bash
curl http://localhost:5000/health
curl http://localhost:3000/api/health
```

Анализ фото:

```bash
curl -X POST http://localhost:5000/api/analyze \
  -F "student_id=7B-014" \
  -F "image_front=@public/test_images/red_ao_dai_full_body_pd.jpg" \
  -F "image_back=@public/test_images/red_ao_dai_full_body_pd.jpg" \
  -F "image_left=@public/test_images/red_ao_dai_full_body_pd.jpg" \
  -F "image_right=@public/test_images/red_ao_dai_full_body_pd.jpg" \
  -F "image_adams=@public/test_images/red_ao_dai_full_body_pd.jpg"
```

Ответ содержит:

- `risk` — уровень риска, цвет, score и количество сработавших флагов;
- `metric_cards` — данные для красивого отображения метрик;
- `recommendations` — дальнейшие действия;
- `care_plan` — структурированный план по уровню риска;
- `overlay_image` — base64 JPEG с разметкой;
- `views` — результаты по каждому ракурсу протокола;
- `analysis_engine` — `mediapipe_tasks_lite` или `opencv_silhouette`;
- `quality_score` — оценка качества кадра.

## Рекомендации к снимку

- Лучше снимать в полный рост на контрастном фоне.
- Камеру держать ровно, без сильного наклона.
- Одежда не должна скрывать линию плеч и корпуса.
- Для теста Адамса нужен отдельный снимок со спины в наклоне вперёд.
- Если в отчёте выбран метод `opencv_silhouette`, модель не нашла человека на конкретном кадре и включился запасной анализ по силуэту.
- Для проверки можно нажать любое изображение в блоке `Примеры`; оно сразу отправится на анализ.
