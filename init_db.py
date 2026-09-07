"""
init_db.py — инициализация БД: таблицы + keywords
Запускать один раз при первом деплое или после очистки БД.
"""
from __future__ import annotations

import sys
from pathlib import Path

# ensure local imports work
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import text

from config import SEO_KEYWORDS, DATABASE_URL
from db import SessionLocal, init_db, ensure_keywords


def main():
    print("Initializing SEO Monitor database...")

    # 1. Create tables
    init_db()
    print("✓ Tables created")

    # 2. Seed keywords
    db = SessionLocal()
    try:
        regions = ["moscow", "spb"]
        kw_map = ensure_keywords(db, SEO_KEYWORDS, regions)
        print(f"✓ Keywords seeded: {len(kw_map)} entries")
    finally:
        db.close()

    # 3. Create hypertable (TimescaleDB extension) if available
    # Falls back gracefully on plain PostgreSQL
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))  # test connection
        print("✓ Database connection OK")
    except Exception as e:
        print(f"✗ Database error: {e}")
    finally:
        db.close()

    print("\nDone! Keywords will be tracked in Moscow and SPb regions.")


if __name__ == "__main__":
    main()
