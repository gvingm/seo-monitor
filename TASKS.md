# SEO Monitor — Задачи и планы

## Что сделано ✅

### Инфраструктура
- [x] VPS Beget: `root@159.194.226.89`, приложение на `/opt/seo-monitor/`
- [x] Docker-стек: FastAPI + APScheduler + PostgreSQL 16
- [x] Домен: `https://seo.albion1.ru/` (HTTPS, Let's Encrypt, Caddy)
- [x] База: 14 975+ позиций, 17+ дней истории, 134 ключевых слова
- [x] API: `/health`, `/api/config-status`, `/api/clusters`, `/api/recommendations`, `/api/report.md`
- [x] Репозиторий: https://github.com/gvingm/seo-monitor

### SEO-мониторинг
- [x] Яндекс Cloud Search API v2: 134 ключевых слова × 2 региона (268 позиций за 7 сек)
- [x] Яндекс Вебмастер API v4: query-analytics
- [x] 7 тематических кластеров с WoW-дельтой (неделя к неделе)
- [x] Дашборд: avg positions, alerts, sparklines, cluster block
- [x] `/api/alerts` — 50 алертов (позиции выпали из ТОП-20)
- [x] `/api/recommendations` — SEO-аудит didalsk.ru (10 разделов)
- [x] Ежедневный cron в 08:30 MSK (APScheduler)

### Кластеры (текущий статус на 08.09.2026)
| Кластер | Поз. | WoW | Тренд |
|---------|-------|-----|-------|
| Дноуглубление | 6.83 | -14.6% | 🟢 растёт |
| Аренда техники | 4.85 | -21.9% | 🟢 растёт |
| Маломерный флот | 6.94 | -4.7% | 🟢 растёт |
| Гидротехнические | 8.19 | +2.6% | 🔴 падает |
| Протаскивание дюкеров | 14.5 | +21.3% | 🔴 падает |
| Берегоукрепление | — | данных нет | ⚪ пусто |

### Исправления didalsk.ru (через SSH)
- [x] `robots.txt` создан (484 B)
- [x] Yoast XML Sitemap включён
- [x] blogdescription заполнен
- [x] 6 категорий создано
- [x] 880 ревизий очищено
- [x] 90 страниц получили Yoast meta (title, description, focus keywords)

## В работе 🔄

### GSC (Google Search Console)
- [ ] Требуется service account JSON из Google Cloud Console
- [ ] Добавить в свойство `https://didalsk.ru/`
- [ ] Положить в `/app/creds/gsc.json`
- [ ] Указать путь в `.env`: `GOOGLE_APPLICATION_CREDENTIALS=/app/creds/gsc.json`

### Telegram-уведомления
- [ ] Создать бота: @BotFather → `/newbot`
- [ ] Получить bot token
- [ ] Узнать chat_id (свой или группы)
- [ ] Добавить в `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

## Не начато ⬜

### Контент-стратегия
- [ ] **Берегоукрепление** — 16 ключевых слов без данных (нет проектов в портфолио)
  → Нужна страница/кейс или снять кластер с мониторинга
- [ ] **Дюкеры (+21.3%)** — ухудшается; добавить контент
- [ ] **Гидротехнические (+2.6%)** — ухудшается; добавить контент
- [ ] 1–2 статьи на каждый кластер (для топ-20)

### Безопасность didalsk.ru
- [ ] WP File Manager Pro — проверить/удалить (critical security risk)

### Lark Bitable
- [ ] Lark App ID + Secret + scope `base:app:create`
- [ ] Подключить Bitable как дашборд вместо/вместе с веб-интерфейсом

## Ручные скрипты (в репо)
- `cleanup_didalsk.py` — очистка ревизий WP
- `fill_meta_didalsk.py` — заполнение Yoast meta
- `fix_didalsk_seo.py` — SEO-фиксы
- `verify_didalsk.py` — верификация
- `deploy_v5.py` — деплой через SSH

## Роутер
- Dokploy (https://dokploy.albion1.ru/) — используется как UI-обёртка, НЕ для деплоя
- Реальный деплой: `python deploy_v5.py` через SSH на Beget
