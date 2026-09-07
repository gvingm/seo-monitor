#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Migration: add `cluster` column to `keywords` table and backfill."""
import sys
sys.path.insert(0, ".")
from sqlalchemy import text
from db import engine, SessionLocal, init_db
import clusters as clusters_mod


def migrate():
    """Add cluster column if missing; backfill from regex rules."""
    with engine.begin() as conn:
        # 1. Add column (idempotent)
        conn.execute(text("""
            ALTER TABLE keywords
            ADD COLUMN IF NOT EXISTS cluster VARCHAR(50)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_keywords_cluster ON keywords (cluster)
        """))
    print("ALTER TABLE keywords ADD cluster — OK")

    # 2. Backfill cluster for all existing keywords
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT id, keyword, cluster FROM keywords")).fetchall()
        updated = 0
        for r in rows:
            if r.cluster:
                continue
            new_cluster = clusters_mod.detect_cluster(r.keyword)
            if new_cluster:
                db.execute(
                    text("UPDATE keywords SET cluster = :c WHERE id = :id"),
                    {"c": new_cluster, "id": r.id}
                )
                updated += 1
        db.commit()
        print(f"Backfilled {updated}/{len(rows)} keywords with cluster")
    finally:
        db.close()

    # 3. Show distribution
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT cluster, COUNT(*) c
            FROM keywords
            GROUP BY cluster
            ORDER BY c DESC
        """)).fetchall()
        print("Cluster distribution:")
        for r in rows:
            label = next(
                (c["label"] for c in clusters_mod.CLUSTERS if c["id"] == r.cluster),
                "?"
            )
            print(f"  {r.cluster or '(none)':<10} {r.c:>4}  — {label}")
    finally:
        db.close()


if __name__ == "__main__":
    init_db()  # на всякий — создаст таблицы, если ещё нет
    migrate()
