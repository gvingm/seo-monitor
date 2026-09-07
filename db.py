"""
db.py — PostgreSQL database setup and models (SQLAlchemy 2.0)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

from config import DATABASE_URL

# Engine with pool settings for production
engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


# ── Модели ────────────────────────────────────────────────
class Keyword(Base):
    __tablename__ = "keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(500), nullable=False, unique=True)
    region = Column(String(50), nullable=False)  # "moscow" | "spb"
    source = Column(String(20), nullable=False)   # "yandex" | "google"
    cluster = Column(String(50), nullable=True, index=True)  # "дredging" | "shore" | "duct" | "hydro" | "rental" | "fleet" | NULL
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_keywords_keyword_region", "keyword", "region", unique=True),
        Index("ix_keywords_cluster", "cluster"),
    )


class Position(Base):
    """
    Накопленная история позиций.
    Источник: Yandex Search API v2, Yandex Webmaster, Google Search Console
    """
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword_id = Column(Integer, ForeignKey("keywords.id"), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    position = Column(Float, nullable=True)          # 0 = нет в ТОП-50
    impressions = Column(Integer, default=0)           # показы (Вебмастер / GSC)
    clicks = Column(Integer, default=0)               # клики (Вебмастер / GSC)
    ctr = Column(Float, default=0.0)                 # кликабельность
    search_volume = Column(Integer, default=0)        # частотность (оценочная)
    source = Column(String(20), default="yandex")    # "yandex" | "google" | "webmaster"
    region = Column(String(50), default="moscow")
    url = Column(Text, nullable=True)                # URL, который показывается
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_positions_keyword_date", "keyword_id", "date"),
        Index("ix_positions_date_source", "date", "source"),
    )


class Alert(Base):
    """
    Алерты: обвалы, выпадения, аномалии
    """
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword_id = Column(Integer, ForeignKey("keywords.id"), nullable=True)
    date = Column(DateTime(timezone=True), nullable=False)
    alert_type = Column(String(50), nullable=False)   # "position_drop", "index_loss", "ctr_drop", "site_down"
    severity = Column(String(20), default="warning")  # "info" | "warning" | "critical"
    message = Column(Text, nullable=False)
    delta = Column(Float, nullable=True)              # изменение (например -12 позиций)
    is_resolved = Column(Integer, default=0)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MonitorLog(Base):
    """
    Лог работы монитора — какие задачи когда выполнялись
    """
    __tablename__ = "monitor_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task = Column(String(100), nullable=False)     # "fetch_positions", "fetch_webmaster", "check_alerts"
    status = Column(String(20), nullable=False)     # "started" | "success" | "failed"
    started_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime(timezone=True), nullable=True)
    keywords_processed = Column(Integer, default=0)
    errors = Column(Text, nullable=True)
    metadata_ = Column(Text, nullable=True)          # JSON details


# ── CRUD helpers ───────────────────────────────────────────
def get_or_create_keyword(db: Session, keyword: str, region: str, source: str = "yandex", cluster: Optional[str] = None) -> int:
    """Возвращает id keyword, создаёт если нет.

    Модель: один keyword = одна строка (unique на `keyword`).
    Регион хранится в Position, а не в Keyword.
    Cluster — опциональный тематический кластер (если None — определится автоматически).
    """
    # Если cluster не передан — попробуем автодетект через ленивый импорт (избегаем цикла)
    if cluster is None:
        try:
            from clusters import detect_cluster
            cluster = detect_cluster(keyword)
        except Exception:
            cluster = None
    row = db.execute(
        text("""
            INSERT INTO keywords (keyword, region, source, cluster, is_active)
            VALUES (:kw, :reg, :src, :clu, 1)
            ON CONFLICT (keyword) DO UPDATE
                SET updated_at = NOW(),
                    cluster = COALESCE(EXCLUDED.cluster, keywords.cluster)
            RETURNING id
        """),
        {"kw": keyword, "reg": region, "src": source, "clu": cluster}
    ).fetchone()
    return row[0]


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Создаёт все таблицы. Вызывается при старте приложения."""
    Base.metadata.create_all(bind=engine)


def ensure_keywords(db: Session, keywords: list[str], regions: list[str]) -> dict[str, int]:
    """
    Гарантирует наличие всех keywords в БД.
    Возвращает dict: (keyword, region) → keyword_id

    Использует ON CONFLICT (keyword) — модель имеет UNIQUE только на keyword.
    Region хранится в Position, поэтому (keyword, region) дубликаты допустимы
    только на уровне Position.
    """
    result = {}
    for kw in keywords:
        for region in regions:
            row = db.execute(
                text("""
                    INSERT INTO keywords (keyword, region, source, is_active)
                    VALUES (:kw, :reg, 'yandex', 1)
                    ON CONFLICT (keyword) DO UPDATE
                        SET updated_at = NOW()
                    RETURNING id
                """),
                {"kw": kw, "reg": region}
            ).fetchone()
            result[(kw, region)] = row[0]
    db.commit()
    return result


# ── Query helpers ──────────────────────────────────────────
def get_latest_positions(
    db: Session,
    days: int = 30,
    region: Optional[str] = None,
    source: str = "yandex"
) -> list[dict]:
    """Последние N дней позиций для всех keywords."""
    query = text("""
        SELECT k.keyword, k.region, p.date, p.position,
               p.impressions, p.clicks, p.ctr, p.url
        FROM positions p
        JOIN keywords k ON k.id = p.keyword_id
        WHERE p.source = :source
          AND p.date >= NOW() - INTERVAL ':days days'
        ORDER BY k.keyword, p.date DESC
    """)
    rows = db.execute(query, {"days": days, "source": source}).fetchall()
    return [dict(r._mapping) for r in rows]


def get_position_delta(
    db: Session,
    keyword_id: int,
    days_back: int = 7
) -> Optional[float]:
    """
    Разница позиций: текущая − недельной давности.
    None если данных недостаточно.
    """
    row = db.execute(
        text("""
            WITH latest AS (
                SELECT position, date
                FROM positions
                WHERE keyword_id = :kid AND position IS NOT NULL
                ORDER BY date DESC LIMIT 1
            ),
            prev AS (
                SELECT position, date
                FROM positions
                WHERE keyword_id = :kid AND position IS NOT NULL
                  AND date <= (SELECT date FROM latest) - INTERVAL ':days days'
                ORDER BY date DESC LIMIT 1
            )
            SELECT (l.position - p.position) AS delta
            FROM latest l, prev p
        """),
        {"kid": keyword_id, "days": days_back}
    ).fetchone()
    return float(row[0]) if row else None
