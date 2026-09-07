"""
main.py — FastAPI application + APScheduler для ежедневного мониторинга
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

import config
from alerts import build_markdown_report, build_monitor_report, check_alerts
from db import (
    Alert,
    Keyword,
    MonitorLog,
    Position,
    get_db,
    get_latest_positions,
    init_db,
)
from google_api import run_google_collection
from yandex_api import run_daily_collection

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("seo-monitor")

# ── Scheduler ────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone=config.TZ)

# ── Lifespan ─────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("SEO Monitor starting up...")
    init_db()
    logger.info("Database tables created/verified")

    # Планируем ежедневный сбор в 08:30 МСК
    scheduler.add_job(
        daily_monitor_task,
        CronTrigger(hour=8, minute=30, timezone=config.TZ),
        id="daily_monitor",
        name="Ежедневный мониторинг (08:30 МСК)",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started — daily monitor at 08:30 MSK")

    yield

    scheduler.shutdown(wait=False)
    logger.info("SEO Monitor shutting down")


app = FastAPI(
    title="SEO Monitor — didalsk.ru",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic models ──────────────────────────────────────────
class RunCollectionResponse(BaseModel):
    class Config:
        from_attributes = True

    status: str
    positions: int = 0
    webmaster: int = 0
    google: int = 0
    alerts_new: int = 0
    errors: list[str] = []


class PositionRow(BaseModel):
    keyword: str
    region: str
    date: datetime
    position: float | None
    impressions: int
    clicks: int
    ctr: float
    url: str | None


class AlertRow(BaseModel):
    id: int
    alert_type: str
    severity: str
    message: str
    date: datetime
    is_resolved: bool


# ── Health ───────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "seo-monitor", "tz": config.TZ}


# ── Ручной запуск сбора данных ────────────────────────────────
@app.post("/api/collect", response_model=RunCollectionResponse)
async def collect_now(date_str: str | None = None):
    """
    Ручной запуск сбора данных.
    date_str: ISO date (YYYY-MM-DD), по умолчанию сегодня.
    """
    if date_str:
        date_ = date.fromisoformat(date_str)
    else:
        date_ = date.today()

    logger.info(f"Manual collection started for {date_}")

    # Записываем в лог
    log_entry = MonitorLog(task="manual_collect", status="started")
    db_gen = get_db()
    db = next(db_gen)

    try:
        # Yandex (Search + Webmaster)
        result = run_daily_collection(db, config.AppConfig(), date_)

        # Google GSC
        gsc = run_google_collection(db, config.AppConfig(), date_)

        # Проверка алертов
        new_alerts = check_alerts(db, date_)

        log_entry.status = "success"
        log_entry.finished_at = datetime.now(timezone.utc)
        log_entry.keywords_processed = len(config.SEO_KEYWORDS)
        log_entry.metadata_ = str(result)

        return RunCollectionResponse(
            status="success",
            positions=result.get("positions", 0),
            webmaster=result.get("webmaster", 0),
            google=gsc.get("queries", 0),
            alerts_new=len(new_alerts),
            errors=result.get("errors", []) + gsc.get("errors", []),
        )
    except Exception as e:
        logger.error(f"Collection failed: {e}")
        log_entry.status = "failed"
        log_entry.finished_at = datetime.now(timezone.utc)
        log_entry.errors = str(e)
        return RunCollectionResponse(status="error", errors=[str(e)])
    finally:
        db.add(log_entry)
        db.commit()
        db.close()


# ── Позиции ─────────────────────────────────────────────────
@app.get("/api/positions", response_model=list[PositionRow])
async def get_positions(
    days: int = 30,
    region: str | None = None,
    source: str = "yandex",
    limit: int = 500,
):
    db_gen = get_db()
    db = next(db_gen)
    try:
        query = text("""
            SELECT k.keyword, k.region, p.date, p.position,
                   p.impressions, p.clicks, p.ctr, p.url
            FROM positions p
            JOIN keywords k ON k.id = p.keyword_id
            WHERE p.source = :source
              AND p.date >= NOW() - (:days || ' days')::interval
            ORDER BY k.keyword, p.date DESC
            LIMIT :limit
        """)
        rows = db.execute(query, {"days": days, "source": source, "limit": limit}).fetchall()
        return [PositionRow(**dict(r._mapping)) for r in rows]
    finally:
        db.close()


# ── Алерты ──────────────────────────────────────────────────
@app.get("/api/alerts", response_model=list[AlertRow])
async def get_alerts(resolved: bool = False, limit: int = 50):
    db_gen = get_db()
    db = next(db_gen)
    try:
        query = db.query(Alert)
        if not resolved:
            query = query.filter_by(is_resolved=0)
        rows = query.order_by(Alert.date.desc()).limit(limit).all()
        return [AlertRow(
            id=r.id,
            alert_type=r.alert_type,
            severity=r.severity,
            message=r.message,
            date=r.date,
            is_resolved=bool(r.is_resolved),
        ) for r in rows]
    finally:
        db.close()


@app.post("/api/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: int):
    db_gen = get_db()
    db = next(db_gen)
    try:
        alert = db.query(Alert).filter_by(id=alert_id).first()
        if not alert:
            return JSONResponse(status_code=404, content={"error": "not found"})
        alert.is_resolved = 1
        alert.resolved_at = datetime.now(timezone.utc)
        db.commit()
        return {"status": "ok", "resolved": alert_id}
    finally:
        db.close()


# ── Сводка / Dashboard data ─────────────────────────────────
@app.get("/api/summary")
async def get_summary(days: int = 7):
    db_gen = get_db()
    db = next(db_gen)
    try:
        report = build_monitor_report(db, days=days)
        return report
    finally:
        db.close()


# ── Статус конфигурации (что настроено, что нет) ─────────────
@app.get("/api/config-status")
async def config_status():
    """Возвращает карту: какие ключи API заполнены, какие нет."""
    import os
    return {
        "yandex_oauth":        bool(config.YANDEX_OAUTH_TOKEN),
        "yandex_cloud_token":  bool(config.YANDEX_CLOUD_TOKEN),
        "yandex_folder_id":    bool(config.YANDEX_FOLDER_ID),
        "yandex_host_id":      bool(config.YANDEX_HOST_ID),
        "google_creds_file":   bool(config.GOOGLE_APPLICATION_CREDENTIALS
                                    and os.path.exists(config.GOOGLE_APPLICATION_CREDENTIALS)),
        "telegram_bot":        bool(config.TELEGRAM_BOT_TOKEN),
        "telegram_chat":       bool(config.TELEGRAM_CHAT_ID),
        "database":            bool(config.DATABASE_URL),
        "app_secret":          bool(config.APP_SECRET_KEY and config.APP_SECRET_KEY != "ChangeMe"),
        "keywords_loaded":     len(config.SEO_KEYWORDS),
    }


# ── Сид демо-данных (чтобы дашборд не висел пустым) ──────────
@app.post("/api/seed-demo")
async def seed_demo():
    """Сидирует несколько демо-записей, чтобы дашборд ожил."""
    from db import Keyword, Position
    import random
    db_gen = get_db()
    db = next(db_gen)
    try:
        today = date.today()
        # Создаём/находим ключевые слова (один keyword — для обоих регионов)
        kw_ids = {}
        for kw in config.SEO_KEYWORDS[:5]:
            existing = db.query(Keyword).filter_by(keyword=kw).first()
            if existing:
                kw_ids[kw] = existing.id
                continue
            k = Keyword(
                keyword=kw,
                region="all",
                source="yandex",
                is_active=1,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(k)
            db.flush()
            kw_ids[kw] = k.id
        db.commit()

        # Позиции за 7 дней
        rows = 0
        for kw in config.SEO_KEYWORDS[:5]:
            for d_offset in range(7):
                d = today - timedelta(days=d_offset)
                random.seed(hash((kw, d_offset)))
                pos_moscow = random.randint(3, 45)
                pos_spb = random.randint(5, 50)
                for region, pos in [("moscow", pos_moscow), ("spb", pos_spb)]:
                    p = Position(
                        keyword_id=kw_ids[kw],
                        date=d,
                        position=float(pos),
                        impressions=random.randint(50, 500),
                        clicks=random.randint(0, 30),
                        ctr=round(random.uniform(0.02, 0.15), 4),
                        source="yandex",
                        region=region,
                        url="https://didalsk.ru/" + kw.replace(" ", "-")[:30],
                        created_at=datetime.now(timezone.utc),
                    )
                    db.add(p)
                    rows += 1
        db.commit()

        # Создаём несколько демо-алертов на основе искусственных падений
        from db import Alert
        sample_alerts = [
            ("position_drop_daily", "critical",
             "«дноуглубительный флот» упал с 5 на 32 за сутки (−27)", "дноуглубительный флот"),
            ("position_drop_weekly", "warning",
             "«аренда земснаряда» потеряла 18 позиций за неделю", "аренда земснаряда"),
            ("ctr_drop_pct", "info",
             "CTR «дноуглубление» снизился на 35% при стабильной позиции", "дноуглубление"),
        ]
        for atype, sev, msg, kw in sample_alerts:
            db.add(Alert(
                alert_type=atype,
                severity=sev,
                message=msg,
                keyword_id=kw_ids.get(kw),
                date=today,
                is_resolved=0,
                created_at=datetime.now(timezone.utc),
            ))
        db.commit()

        return {"status": "ok", "rows_added": rows, "keywords": len(kw_ids),
                "alerts_added": len(sample_alerts)}
    finally:
        db.close()


# ── Позиции по ключам (для таблицы + sparkline) ──────────────
@app.get("/api/positions-history")
async def positions_history(days: int = 7):
    """История позиций по ключам и регионам, для sparkline-таблицы."""
    db_gen = get_db()
    db = next(db_gen)
    try:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        # Join с Keyword, чтобы сразу получить текст ключевого слова
        rows = (
            db.query(Position, Keyword.keyword)
            .join(Keyword, Position.keyword_id == Keyword.id)
            .filter(Position.date >= since)
            .order_by(Position.date.asc())
            .all()
        )
        # Группируем: {(keyword, region): [pos1, pos2, ...]}
        grouped: dict = {}
        for pos, kw_text in rows:
            k = (kw_text, pos.region)
            grouped.setdefault(k, []).append({
                "date": pos.date.date().isoformat(),
                "position": pos.position,
                "impressions": pos.impressions,
                "clicks": pos.clicks,
            })
        result = []
        for (kw, region), series in grouped.items():
            positions = [p["position"] for p in series if p["position"] is not None]
            result.append({
                "keyword": kw,
                "region": region,
                "series": series,
                "best": min(positions) if positions else None,
                "worst": max(positions) if positions else None,
                "avg": round(sum(positions) / len(positions), 1) if positions else None,
                "delta": round(positions[-1] - positions[0], 1) if len(positions) >= 2 else 0,
            })
        result.sort(key=lambda r: (r["region"], r["avg"] or 999))
        return {"items": result, "total_keywords": len(result)}
    finally:
        db.close()


# ── Markdown-отчёт для пересылки (Ларк / Телеграм) ──────────
@app.get("/api/report.md", response_class=HTMLResponse)
async def markdown_report(days: int = 7):
    """Генерирует markdown-сводку: метрики, топ-дропы, алерты, история."""
    db_gen = get_db()
    db = next(db_gen)
    try:
        report = build_monitor_report(db, days=days)
        # Подтянем историю и алерты
        from sqlalchemy import func
        since = datetime.now(timezone.utc) - timedelta(days=days)
        hist_rows = (db.query(Position, Keyword.keyword)
                     .join(Keyword, Position.keyword_id == Keyword.id)
                     .filter(Position.date >= since)
                     .all())
        grouped: dict = {}
        for pos, kw_text in hist_rows:
            grouped.setdefault((kw_text, pos.region), []).append(pos.position)
        alerts = (db.query(Alert)
                  .filter(Alert.is_resolved == 0)
                  .order_by(Alert.created_at.desc())
                  .limit(20)
                  .all())

        lines = [
            f"# 🔍 SEO Monitor — didalsk.ru",
            f"_Отчёт за {days} дней, {date.today().isoformat()}_",
            "",
            "## 📊 Средние позиции",
        ]
        avg = report.get("avg_positions") or {}
        if avg:
            for region, val in avg.items():
                lines.append(f"- **{region}**: {val:.1f}")
        else:
            lines.append("- _нет данных_")

        lines += ["", "## 🔻 Топ-дропы"]
        drops = report.get("top_drops") or []
        if drops:
            for d in drops[:10]:
                lines.append(
                    f"- **{d.get('keyword','?')}** ({d.get('region','?')}): "
                    f"с {d.get('best_pos',0):.0f} на {d.get('worst_pos',0):.0f} "
                    f"(swing {d.get('swing',0):.0f})"
                )
        else:
            lines.append("- _нет значимых колебаний_")

        lines += ["", f"## 🚨 Активные алерты ({len(alerts)})"]
        if alerts:
            severity_emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
            for a in alerts:
                emoji = severity_emoji.get(a.severity, "⚪")
                lines.append(f"- {emoji} **{a.alert_type}** — {a.message}")
        else:
            lines.append("- ✅ Всё стабильно")

        lines += ["", f"## 📈 Сводка по {len(grouped)} ключам"]
        for (kw, region), positions in sorted(grouped.items(), key=lambda x: x[0][0]):
            if not positions:
                continue
            best = min(positions)
            worst = max(positions)
            avg_p = sum(positions) / len(positions)
            lines.append(f"- **{kw}** ({region}): avg {avg_p:.1f}, лучшая {best:.0f}, худшая {worst:.0f}")

        lines += [
            "",
            "---",
            f"_Источник: http://159.194.226.89:8788/_",
        ]
        md = "\n".join(lines)
        # Возвращаем как text/markdown
        return HTMLResponse(content=md, media_type="text/markdown; charset=utf-8")
    finally:
        db.close()


# ── HTML Dashboard ──────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <title>SEO Monitor — didalsk.ru</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: -apple-system, system-ui, sans-serif;
                   background: #0f1117; color: #e5e7eb; padding: 24px; }
            h1 { font-size: 24px; margin-bottom: 24px; color: #f9fafb; }
            .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
            .card { background: #1f2937; border-radius: 12px; padding: 20px; }
            .card h2 { font-size: 14px; color: #9ca3af; text-transform: uppercase;
                       letter-spacing: 0.05em; margin-bottom: 12px; }
            .metric { font-size: 36px; font-weight: 700; color: #f9fafb; }
            .metric-label { font-size: 13px; color: #6b7280; margin-top: 4px; }
            .btn { display: inline-block; padding: 10px 20px; background: #3b82f6;
                   color: white; border-radius: 8px; text-decoration: none;
                   font-size: 14px; cursor: pointer; border: none; margin: 4px; }
            .btn:hover { background: #2563eb; }
            .status-ok { color: #22c55e; }
            .status-err { color: #ef4444; }
            table { width: 100%; border-collapse: collapse; margin-top: 16px; }
            th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #374151; }
            th { color: #9ca3af; font-size: 12px; text-transform: uppercase; }
            tr:hover { background: #1f2937; }
            .alert-critical { color: #ef4444; }
            .alert-warning { color: #f59e0b; }
            .alert-info { color: #3b82f6; }
        </style>
    </head>
    <body>
        <h1>🔍 SEO Monitor — didalsk.ru</h1>
        <div class="grid">
            <div class="card">
                <h2>Статус</h2>
                <div id="status" class="metric">...</div>
            </div>
            <div class="card">
                <h2>Активных алертов</h2>
                <div id="alerts-count" class="metric">—</div>
            </div>
            <div class="card">
                <h2>Средняя позиция (Москва)</h2>
                <div id="avg-moscow" class="metric">—</div>
            </div>
            <div class="card">
                <h2>Средняя позиция (СПб)</h2>
                <div id="avg-spb" class="metric">—</div>
            </div>
        </div>

        <div style="margin-top: 24px;">
            <button class="btn" onclick="runCollection()">▶ Запустить сбор сейчас</button>
            <button class="btn" onclick="loadData()">↻ Обновить</button>
            <button class="btn" style="background:#7c3aed;" onclick="seedDemo()">🧪 Сид демо-данных</button>
            <button class="btn" style="background:#10b981;" onclick="copyReport()">📄 Копировать отчёт</button>
        </div>

        <div class="card" style="margin-top: 24px;">
            <h2>Конфигурация (API)</h2>
            <div id="config-status" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; font-size: 13px;"></div>
        </div>

        <div class="card" style="margin-top: 24px;">
            <h2>Активные алерты</h2>
            <table>
                <thead>
                    <tr>
                        <th>Тип</th>
                        <th>Сообщение</th>
                        <th>Дата</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody id="alerts-table"></tbody>
            </table>
        </div>

        <div class="card" style="margin-top: 24px;">
            <h2>История позиций по ключам (sparkline)</h2>
            <table>
                <thead>
                    <tr>
                        <th>Ключ</th>
                        <th>Регион</th>
                        <th>Лучшая</th>
                        <th>Худшая</th>
                        <th>Средняя</th>
                        <th>Δ за 7д</th>
                        <th>Тренд</th>
                    </tr>
                </thead>
                <tbody id="history-table"></tbody>
            </table>
        </div>

        <script>
            const API = '';

            async function loadData() {
                const res = await fetch(API + '/api/summary');
                const data = await res.json();
                document.getElementById('status').textContent = 'Работает';
                document.getElementById('status').className = 'metric status-ok';
                document.getElementById('alerts-count').textContent = data.active_alerts_count || 0;
                document.getElementById('avg-moscow').textContent =
                    data.avg_positions?.moscow ? data.avg_positions.moscow.toFixed(1) : '—';
                document.getElementById('avg-spb').textContent =
                    data.avg_positions?.spb ? data.avg_positions.spb.toFixed(1) : '—';

                // Alerts table
                const alertsRes = await fetch(API + '/api/alerts');
                const alerts = await alertsRes.json();
                const tbody = document.getElementById('alerts-table');
                tbody.innerHTML = alerts.map(a => `
                    <tr>
                        <td><span class="alert-${a.severity}">${a.alert_type}</span></td>
                        <td>${a.message}</td>
                        <td>${a.date ? a.date.slice(0,10) : '—'}</td>
                        <td><button onclick="resolveAlert(${a.id})">✓</button></td>
                    </tr>
                `).join('');
            }

            async function runCollection() {
                const btn = document.querySelector('.btn');
                btn.textContent = '⏳ Сбор данных...';
                btn.disabled = true;
                const res = await fetch(API + '/api/collect', {method:'POST'});
                const data = await res.json();
                alert('Сбор завершён: ' + JSON.stringify(data, null, 2));
                btn.textContent = '▶ Запустить сбор сейчас';
                btn.disabled = false;
                loadData();
            }

            async function resolveAlert(id) {
                await fetch(API + '/api/alerts/' + id + '/resolve', {method:'POST'});
                loadData();
            }

            async function seedDemo() {
                const r = await fetch(API + '/api/seed-demo', {method:'POST'});
                const data = await r.json();
                alert('Добавлено ' + data.rows_added + ' демо-записей, '
                      + (data.alerts_added||0) + ' алертов');
                loadData();
                loadHistory();
            }

            async function copyReport() {
                const r = await fetch(API + '/api/report.md');
                const md = await r.text();
                try {
                    await navigator.clipboard.writeText(md);
                    alert('Markdown-отчёт скопирован в буфер');
                } catch (e) {
                    // Fallback: показать в новом окне
                    const w = window.open('', '_blank');
                    w.document.write('<pre style="white-space:pre-wrap;font-family:monospace;padding:20px;">'
                                     + md.replace(/</g, '&lt;') + '</pre>');
                }
            }

            async function loadHistory() {
                const r = await fetch(API + '/api/positions-history?days=7');
                const data = await r.json();
                const tbody = document.getElementById('history-table');
                tbody.innerHTML = data.items.map(row => {
                    // Inline SVG sparkline
                    const series = row.series || [];
                    const w = 120, h = 30, pad = 2;
                    const xs = series.length;
                    const positions = series.map(p => p.position).filter(p => p != null);
                    if (positions.length < 2) {
                        return `<tr>
                            <td>${row.keyword}</td>
                            <td>${row.region}</td>
                            <td>${row.best ?? '—'}</td>
                            <td>${row.worst ?? '—'}</td>
                            <td>${row.avg ?? '—'}</td>
                            <td>${row.delta > 0 ? '+' : ''}${row.delta}</td>
                            <td><svg width="${w}" height="${h}"><text x="5" y="20" fill="#6b7280" font-size="10">нет данных</text></svg></td>
                        </tr>`;
                    }
                    const minP = Math.min(...positions);
                    const maxP = Math.max(...positions);
                    const range = Math.max(maxP - minP, 1);
                    const points = series.map((p, i) => {
                        const x = pad + (i / Math.max(xs - 1, 1)) * (w - 2 * pad);
                        const y = pad + (1 - (p.position - minP) / range) * (h - 2 * pad);
                        return `${x.toFixed(1)},${y.toFixed(1)}`;
                    }).join(' ');
                    const lastY = pad + (1 - (positions[positions.length - 1] - minP) / range) * (h - 2 * pad);
                    const trendColor = row.delta > 5 ? '#ef4444' : (row.delta < -5 ? '#22c55e' : '#9ca3af');
                    return `<tr>
                        <td>${row.keyword}</td>
                        <td><span style="color:#9ca3af;">${row.region}</span></td>
                        <td>${row.best}</td>
                        <td>${row.worst}</td>
                        <td>${row.avg}</td>
                        <td style="color:${trendColor};font-weight:600;">${row.delta > 0 ? '+' : ''}${row.delta}</td>
                        <td><svg width="${w}" height="${h}" style="vertical-align:middle;">
                            <polyline points="${points}" fill="none" stroke="${trendColor}" stroke-width="1.5"/>
                            <circle cx="${(w - 2 * pad).toFixed(1)}" cy="${lastY.toFixed(1)}" r="2.5" fill="${trendColor}"/>
                        </svg></td>
                    </tr>`;
                }).join('');
            }

            async function loadConfig() {
                const r = await fetch(API + '/api/config-status');
                const s = await r.json();
                const labels = {
                    yandex_oauth:       'Yandex OAuth (Webmaster/Metrika)',
                    yandex_cloud_token: 'Yandex Cloud Token (Search API)',
                    yandex_folder_id:   'Yandex Folder ID',
                    yandex_host_id:     'Yandex Host ID (Webmaster)',
                    google_creds_file:  'Google service account (GSC)',
                    telegram_bot:       'Telegram Bot Token',
                    telegram_chat:      'Telegram Chat ID',
                    database:           'PostgreSQL',
                    app_secret:         'APP_SECRET_KEY',
                    keywords_loaded:    'Ключевых слов загружено',
                };
                const el = document.getElementById('config-status');
                el.innerHTML = Object.keys(labels).map(k => {
                    let v = s[k];
                    if (k === 'keywords_loaded') return '<div>📚 ' + labels[k] + ': <b>' + v + '</b></div>';
                    const ok = !!v;
                    const emoji = ok ? '✅' : '❌';
                    const color = ok ? '#22c55e' : '#ef4444';
                    return '<div style="color:' + color + ';">' + emoji + ' ' + labels[k] + '</div>';
                }).join('');
            }

            loadData();
            loadHistory();
            loadConfig();
            setInterval(loadData, 300000); // 5 min
            setInterval(loadHistory, 300000); // 5 min
            setInterval(loadConfig, 60000); // 1 min
        </script>
    </body>
    </html>
    """
    return html


# ── Background task: daily monitor ────────────────────────────
async def daily_monitor_task():
    """
    Ежедневный запуск: собирает данные + алерты + отчёты.
    Вызывается APScheduler в 08:30 МСК.
    """
    logger.info("=== Daily monitor task started ===")
    date_ = date.today()

    db_gen = get_db()
    db = next(db_gen)
    log_entry = MonitorLog(task="daily_monitor", status="started")

    try:
        # 1. Сбор данных
        result = run_daily_collection(db, config.AppConfig(), date_)
        gsc = run_google_collection(db, config.AppConfig(), date_)

        # 2. Алерты
        new_alerts = check_alerts(db, date_)

        # 3. Сводка → Markdown
        report = build_monitor_report(db, days=1)
        md_report = build_markdown_report(report)

        log_entry.status = "success"
        log_entry.finished_at = datetime.now(timezone.utc)
        log_entry.keywords_processed = len(config.SEO_KEYWORDS)
        log_entry.metadata_ = f"yandex={result}, gsc={gsc}, alerts={len(new_alerts)}"

        logger.info(f"Daily monitor done: positions={result.get('positions')}, "
                    f"webmaster={result.get('webmaster')}, gsc={gsc.get('queries')}, "
                    f"alerts={len(new_alerts)}")

        # Отправка в Telegram (если настроен)
        await send_telegram_notification(report, new_alerts)

    except Exception as e:
        logger.error(f"Daily monitor task failed: {e}")
        log_entry.status = "failed"
        log_entry.finished_at = datetime.now(timezone.utc)
        log_entry.errors = str(e)
    finally:
        db.add(log_entry)
        db.commit()
        db.close()


async def send_telegram_notification(report: dict, alerts: list):
    """Отправляет уведомление в Telegram."""
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return

    # Формируем текст
    lines = [f"🔍 *SEO Monitor — {date.today().isoformat()}*", ""]

    for region, avg in report.get("avg_positions", {}).items():
        lines.append(f"📍 {region.upper()}: {avg:.1f}")

    if alerts:
        lines.append(f"⚠️ *{len(alerts)} новых алертов*")
        for a in alerts[:5]:
            emoji = "🔴" if a.severity == "critical" else "🟡"
            lines.append(f"{emoji} {a.message[:120]}")
    else:
        lines.append("✅ Всё стабильно")

    text = "\n".join(lines)

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
                timeout=10.0,
            )
    except Exception as e:
        logger.error(f"Telegram notification failed: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8788)
