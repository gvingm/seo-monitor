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
class RunCollectionResponse(BaseModel)
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
        result = run_daily_collection(db, config, date_)

        # Google GSC
        gsc = run_google_collection(db, config, date_)

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

            loadData();
            setInterval(loadData, 300000); // 5 min
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
        result = run_daily_collection(db, config, date_)
        gsc = run_google_collection(db, config, date_)

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
