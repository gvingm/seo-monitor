"""
alerts.py — логика алертов и генерации отчётов
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

import config
from db import Alert, Keyword, MonitorLog, Position, get_position_delta, init_db

logger = logging.getLogger(__name__)

# ── Пороги алертов ──────────────────────────────────────────
THRESHOLDS = {
    "position_drop_daily": -10,      # −10 за сутки
    "position_drop_weekly": -5,       # −5 за неделю
    "impressions_drop_pct": -0.30,   # −30% показов неделя к неделе
    "ctr_drop_pct": -0.25,          # −25% CTR при стабильной позиции
    "index_loss_any": True,          # любая приоритетная страница выпала
}


def check_alerts(db: Session, date_: Optional[date] = None) -> list[Alert]:
    """
    Проверяет все активные keywords на алерты, создаёт записи.
    Возвращает список новых алертов.
    """
    if date_ is None:
        date_ = date.today()

    new_alerts: list[Alert] = []
    today = datetime.combine(date_, datetime.min.time(), tzinfo=timezone.utc)
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)

    # Получаем все keywords
    keywords = db.query(Keyword).filter_by(is_active=1).all()

    for kw in keywords:
        alerts_for_kw = _check_keyword_alerts(db, kw, today, yesterday, week_ago)
        for alert in alerts_for_kw:
            db.add(alert)
            new_alerts.append(alert)

    db.commit()
    logger.info(f"Alerts checked: {len(new_alerts)} new alerts")
    return new_alerts


def _check_keyword_alerts(
    db: Session,
    kw: Keyword,
    today: datetime,
    yesterday: datetime,
    week_ago: datetime,
) -> list[Alert]:
    """Проверяет один keyword на все типы алертов."""
    alerts = []

    # Позиция сегодня и вчера
    today_row = db.execute(
        text("""
            SELECT position FROM positions
            WHERE keyword_id = :kid AND date = :today AND position IS NOT NULL
            ORDER BY date DESC LIMIT 1
        """),
        {"kid": kw.id, "today": today}
    ).fetchone()

    yesterday_row = db.execute(
        text("""
            SELECT position FROM positions
            WHERE keyword_id = :kid AND date = :yesterday AND position IS NOT NULL
            ORDER BY date DESC LIMIT 1
        """),
        {"kid": kw.id, "yesterday": yesterday}
    ).fetchone()

    week_ago_row = db.execute(
        text("""
            SELECT position FROM positions
            WHERE keyword_id = :kid AND date = :week_ago AND position IS NOT NULL
            ORDER BY date DESC LIMIT 1
        """),
        {"kid": kw.id, "week_ago": week_ago}
    ).fetchone()

    # 1. Обвал за сутки
    if today_row and yesterday_row:
        delta = today_row[0] - yesterday_row[0]  # positive = improvement
        if delta <= THRESHOLDS["position_drop_daily"]:
            alerts.append(Alert(
                keyword_id=kw.id,
                date=today,
                alert_type="position_drop",
                severity="critical" if delta <= -15 else "warning",
                message=f"Обвал позиции '{kw.keyword}' ({kw.region}): {yesterday_row[0]} → {today_row[0]} (Δ {delta:+.0f})",
                delta=delta,
            ))

    # 2. Обвал за неделю
    if week_ago_row and today_row:
        delta_7d = today_row[0] - week_ago_row[0]
        if delta_7d <= THRESHOLDS["position_drop_weekly"]:
            alerts.append(Alert(
                keyword_id=kw.id,
                date=today,
                alert_type="position_drop_weekly",
                severity="warning",
                message=f"Позиция '{kw.keyword}' ({kw.region}) упала за неделю: {week_ago_row[0]} → {today_row[0]} (Δ {delta_7d:+.0f})",
                delta=delta_7d,
            ))

    # 3. Показы — сравнение неделя к неделе
    this_week_impr = db.execute(
        text("""
            SELECT SUM(impressions) FROM positions
            WHERE keyword_id = :kid AND date >= :week_start AND date < :today
        """),
        {"kid": kw.id, "week_start": today - timedelta(days=7), "today": today}
    ).fetchone()[0] or 0

    prev_week_impr = db.execute(
        text("""
            SELECT SUM(impressions) FROM positions
            WHERE keyword_id = :kid AND date >= :prev_start AND date < :prev_end
        """),
        {"kid": kw.id, "prev_start": today - timedelta(days=14), "prev_end": today - timedelta(days=7)}
    ).fetchone()[0] or 0

    if prev_week_impr > 0:
        impr_ratio = (this_week_impr - prev_week_impr) / prev_week_impr
        if impr_ratio <= THRESHOLDS["impressions_drop_pct"]:
            alerts.append(Alert(
                keyword_id=kw.id,
                date=today,
                alert_type="impressions_drop",
                severity="warning",
                message=f"Показы '{kw.keyword}' ({kw.region}) упали на {impr_ratio*100:.0f}% неделя к неделе: {prev_week_impr} → {this_week_impr}",
                delta=impr_ratio,
            ))

    return alerts


def build_monitor_report(db: Session, days: int = 7) -> dict:
    """
    Генерирует ежедневную сводку для bus/monitor-latest.md
    и возвращает dict для отправки в Telegram / Lark.
    """
    today = datetime.now(timezone.utc)
    start = today - timedelta(days=days)

    # Топ-падения за неделю
    top_drops = db.execute(
        text("""
            WITH ranked AS (
                SELECT
                    k.keyword, k.region,
                    MAX(p.position) as worst_pos,
                    MIN(p.position) as best_pos,
                    MAX(p.position) - MIN(p.position) as pos_swing
                FROM positions p
                JOIN keywords k ON k.id = p.keyword_id
                WHERE p.date >= :start AND p.source = 'yandex'
                GROUP BY k.id, k.keyword, k.region
                HAVING COUNT(*) >= 2
            )
            SELECT keyword, region, worst_pos, best_pos, pos_swing
            FROM ranked
            WHERE pos_swing > 0
            ORDER BY pos_swing DESC
            LIMIT 10
        """),
        {"start": start}
    ).fetchall()

    # Средняя позиция по регионам
    avg_positions = db.execute(
        text("""
            SELECT k.region, AVG(p.position) as avg_pos
            FROM positions p
            JOIN keywords k ON k.id = p.keyword_id
            WHERE p.date >= :start AND p.position IS NOT NULL AND p.source = 'yandex'
            GROUP BY k.region
        """),
        {"start": start}
    ).fetchall()

    # Активные алерты
    active_alerts = db.query(Alert).filter_by(is_resolved=0).order_by(
        Alert.date.desc()
    ).limit(20).all()

    return {
        "date": today.isoformat(),
        "top_drops": [
            {
                "keyword": r[0],
                "region": r[1],
                "worst_pos": float(r[2]),
                "best_pos": float(r[3]),
                "swing": float(r[4]),
            }
            for r in top_drops
        ],
        "avg_positions": {r[0]: float(r[1]) for r in avg_positions},
        "active_alerts_count": len(active_alerts),
        "active_alerts": [
            {"type": a.alert_type, "message": a.message, "date": a.date.isoformat(), "severity": a.severity}
            for a in active_alerts[:10]
        ],
    }


def build_markdown_report(report: dict) -> str:
    """Генерирует Markdown для bus/monitor-latest.md"""
    lines = [
        f"# Мониторинг — {report['date'][:10]}",
        "",
        "## Средняя позиция по регионам",
    ]

    for region, avg in report.get("avg_positions", {}).items():
        lines.append(f"- **{region.upper()}**: {avg:.1f}")

    lines.extend(["", "## Топ-падения за 7 дней", ""])

    if report.get("top_drops"):
        lines.append("| Запрос | Регион | Было | Стало | Изменение |")
        lines.append("|--------|--------|------|-------|-----------|")
        for d in report["top_drops"]:
            sign = "+" if d["swing"] < 0 else "-"
            lines.append(
                f"| {d['keyword']} | {d['region']} | {d['worst_pos']:.0f} | "
                f"{d['best_pos']:.0f} | {sign}{abs(d['swing']):.0f} |"
            )
    else:
        lines.append("_Нет значимых изменений_")

    if report.get("active_alerts"):
        lines.extend(["", "## ⚠️ Активные алерты", ""])
        for a in report["active_alerts"]:
            emoji = "🔴" if a["severity"] == "critical" else "🟡"
            lines.append(f"{emoji} **{a['type']}**: {a['message']}")

    return "\n".join(lines)
