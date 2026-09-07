# SEO Monitor — didalsk.ru

Ежедневный мониторинг позиций didalsk.ru в Яндексе и Google с уведомлениями в Телеграм.

## Доступ

| URL | Что |
|---|---|
| http://159.194.226.89:8788/ | HTML-дашборд (метрики, алерты, sparkline, конфиг) |
| http://159.194.226.89:8788/docs | Swagger UI |
| http://159.194.226.89:8788/health | Health check |
| http://159.194.226.89:8788/api/report.md | Markdown-отчёт для пересылки в Ларк/Телеграм |

## Стек

- **Backend:** Python 3.11 (distroless), FastAPI, APScheduler, SQLAlchemy 2.0
- **DB:** PostgreSQL 16
- **Image:** `gcr.io/distroless/python3-debian12:nonroot` (~120 MB, nonroot)
- **Развёрнут:** на Beget VPS через SSH (Dokploy compose-агент не справляется с клоном, деплоим руками)
- **Repo:** https://github.com/gvingm/seo-monitor

## Структура

```
seo-monitor/
├── main.py               # FastAPI app, scheduler, dashboard
├── config.py             # Settings из .env + ключевые слова
├── db.py                 # SQLAlchemy модели: Keyword, Position, Alert, MonitorLog
├── yandex_api.py         # Yandex Search API v2 + Webmaster + Metrika
├── google_api.py         # Google Search Console
├── alerts.py             # 7 типов алертов + markdown-отчёт
├── init_db.py            # Schema init
├── Dockerfile            # python:3.11-slim → distroless, target=/deps + PYTHONPATH
├── docker-compose.yml    # app + db, healthcheck
├── .env                  # Секреты (НЕ коммитить)
├── .env.example          # Шаблон
├── requirements.txt      # Pinned deps
├── deploy_ssh.py         # Авто-деплой через paramiko (используется нами)
└── README.md             # ← этот файл
```

## API endpoints

| Метод | Путь | Описание |
|---|---|---|
| GET | `/` | HTML-дашборд |
| GET | `/health` | `{"status":"ok",...}` |
| GET | `/api/summary?days=7` | Топ-дропы, средние позиции, активные алерты |
| GET | `/api/positions?limit=N` | Сырой список позиций |
| GET | `/api/positions-history?days=7` | Спарклайн-данные по ключам |
| GET | `/api/alerts` | Активные алерты |
| POST | `/api/alerts/{id}/resolve` | Закрыть алерт |
| POST | `/api/collect` | Запустить сбор данных (ручной) |
| POST | `/api/seed-demo` | Сидировать демо-данные (для просмотра дашборда) |
| GET | `/api/config-status` | Какие API-ключи заполнены |
| GET | `/api/report.md` | Markdown-отчёт за 7 дней |
| GET | `/docs` | Swagger UI |
| GET | `/openapi.json` | OpenAPI спека |

## Конфигурация (`.env`)

```bash
# Yandex
YANDEX_OAUTH_TOKEN=                    # https://oauth.yandex.ru/ (scope: webmaster:read, metrika:read)
YANDEX_CLOUD_TOKEN=                    # Yandex Cloud → Service Account key
YANDEX_FOLDER_ID=                      # Yandex Cloud folder ID
YANDEX_HOST_ID=                        # https://webmaster.yandex.ru/sites/<ID>/
YANDEX_REGION_WEBMASTER_MOSCOW=1
YANDEX_REGION_WEBMASTER_SPB=10174
YANDEX_REGION_SEARCH_MOSCOW=213
YANDEX_REGION_SEARCH_SPB=2

# Google
GOOGLE_APPLICATION_CREDENTIALS=/app/creds/gsc.json   # Service Account JSON
GSC_SITE_URL=https://didalsk.ru

# Database
DATABASE_URL=postgresql://seomonitor:ChangeMe123@db:5432/seomonitor

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# App
APP_SECRET_KEY=<hex 64>
TZ=Europe/Moscow
LOG_LEVEL=INFO
```

**Ключевые слова** редактируются в `config.py:SEO_KEYWORDS` (52 шт по умолчанию).

## Деплой

```bash
# На сервере (Beget VPS, root @ 159.194.226.89)
cd /opt/seo-monitor
git pull origin master
docker compose up -d --no-deps --build app

# Проверить
docker ps -a | grep seo-monitor
docker logs seo-monitor-app --tail 50
curl -sS http://localhost:8788/health
```

## Сбор данных по расписанию

APScheduler запускает `daily_monitor_task` каждый день в **08:30 МСК**:
1. `run_daily_collection` — Yandex Webmaster + Search API
2. `run_google_collection` — Google Search Console
3. `check_alerts` — 7 типов алертов (падения позиций, CTR, показы)
4. `build_markdown_report` + `send_telegram_notification` — отчёт в Телеграм

## Алерты (7 типов)

| Тип | Severity | Порог |
|---|---|---|
| `position_drop_daily` | critical | −10+ позиций за сутки |
| `position_drop_weekly` | warning | −5+ за неделю |
| `impressions_drop_pct` | warning | −30% показов WoW |
| `ctr_drop_pct` | info | −25% CTR при стабильной позиции |
| `index_loss_any` | critical | Приоритетная страница выпала |
| `site_down` | critical | health endpoint не отвечает |
| `*` (Telegram fail) | warning | Не дошёл отчёт в Телеграм |

## Демо-режим

Кнопка «🧪 Сид демо-данных» в дашборде или `POST /api/seed-demo`:
- 5 ключевых слов × 7 дней × 2 региона = 70 позиций
- 3 демо-алерта (critical / warning / info)
- Можно вызывать многократно — уникальность по `(keyword, date, region, source)` через `position_id`

## Lessons (distroless + paramiko)

Сохранены в agent memory. Главные:
- `CMD ["main.py"]` — не `["/usr/bin/python3.11", "main.py"]` (entrypoint сам python3.11)
- `pip install --target=/deps` + `PYTHONPATH=/deps` — distroless Python ищет пакеты там
- Build-стадия = `python:3.11-slim` под distroless python3.11 ABI
- Все `mkdir`/`chmod` — в build-стадии, distroless без shell
- paramiko работает из коробки на Windows, `sys.stdout` форсить в UTF-8 (cp1251 падает)
