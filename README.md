# ScolioScan School

Мобильный ИИ-комплекс для первичного скрининга риска сколиоза у школьников.

Сценарий работы: ноутбук запускает сервер анализа и Next.js сайт, телефон находится в той же Wi-Fi сети, открывает сайт по IP ноутбука, входит в аккаунт, выбирает Basic-анализ по одному фото или Advanced-протокол из пяти ракурсов и получает отчёт с уровнем риска, метриками, планом действий и изображениями с разметкой.

## Что внутри

```text
.
├── app/                 # Next.js интерфейс для телефона и ноутбука
├── api_server.py        # Flask API для загрузки фото и анализа
├── storage.py           # SQLite пользователи, сессии, лимиты, отчёты и изображения
├── models/              # MediaPipe .task модель
├── pose_analyzer.py     # MediaPipe/OpenCV анализ асимметрии
├── docs/                # продуктовая логика и pricing rationale
├── public/education/    # medtech visual для обучающей страницы
├── public/test_images/  # демонстрационные изображения на сайте
├── pyproject.toml       # Python зависимости для uv
└── package.json         # Next.js/npm скрипты
```

## Возможности

- Basic-анализ по одному фронтальному фото как режим по умолчанию;
- Advanced-анализ из пяти ракурсов: спереди, со спины, левый бок, правый бок, тест Адамса;
- три уровня доступа: `Free` (`$0`, 5 Basic/мес), `Plus` (`$19/мес`, 80 Basic/мес и 4 Advanced/мес), `Corporate` (цена и лимиты по договору школы);
- подтверждение статуса ученика по школьному коду для доступа к корпоративным лимитам;
- серверная проверка лимитов Basic/Advanced перед запуском анализа;
- боковые ракурсы используются как профильный контроль и не повышают фронтальные метрики асимметрии;
- регистрация и вход через SQLite;
- учётная запись по умолчанию: `admin` / `12345678`;
- отдельные кнопки для камеры и галереи на телефоне;
- демонстрационные изображения на сайте, которые запускают анализ одним нажатием;
- подготовка фото в браузере: сжатие и конвертация в JPEG перед отправкой;
- автоматический расчёт 5 метрик асимметрии;
- уровни риска `low`, `moderate`, `high`;
- веб-результат: индикатор риска, карточки метрик, рекомендации и план действий;
- страница `/learn/posture` с рекомендациями по посадке, перерывам и школьному рабочему месту;
- overlay-картинки с разметкой, JSON отчёта и история сохраняются в SQLite;
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

При первом запуске API создаёт базу:

```text
data/scolioscan.db
```

Также создаётся аккаунт `admin` с паролем `12345678`. Старые файлы из корневой папки `reports/` мигрируются в SQLite под этим аккаунтом. Тарифы, активные подписки, корпоративные коды и usage-лимиты тоже хранятся в SQLite.

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

Вход:

```bash
TOKEN=$(curl -sS -X POST http://localhost:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"12345678"}' \
  | uv --cache-dir /tmp/uv-cache run --no-sync python -c "import json,sys; print(json.load(sys.stdin)['token'])")
```

Basic-анализ одного фото:

```bash
curl -X POST http://localhost:5000/api/analyze \
  -H "Authorization: Bearer $TOKEN" \
  -F "student_id=7B-014" \
  -F "image=@public/test_images/red_ao_dai_full_body_pd.jpg"
```

Тарифы и активация Advanced:

```bash
curl http://localhost:5000/api/billing/plans

curl -X POST http://localhost:5000/api/billing/checkout \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"plan_id":"plus"}'
```

Корпоративный доступ ученика:

```bash
curl -X POST http://localhost:5000/api/billing/verify-student \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"school_code":"SCHOOL-ACCESS-2026","student_id":"7B-014"}'
```

Advanced-анализ пяти фото:

```bash
curl -X POST http://localhost:5000/api/analyze \
  -H "Authorization: Bearer $TOKEN" \
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

История:

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/reports
curl -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/reports/REPORT_ID
```

## Рекомендации к снимку

- Лучше снимать в полный рост на контрастном фоне.
- Камеру держать ровно, без сильного наклона.
- Одежда не должна скрывать линию плеч и корпуса.
- Для теста Адамса нужен отдельный снимок со спины в наклоне вперёд.
- Если в отчёте выбран метод `opencv_silhouette`, модель не нашла человека на конкретном кадре и включился запасной анализ по силуэту.
- Для проверки можно нажать любое изображение в блоке `Примеры`; оно сразу отправится на анализ.
