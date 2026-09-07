"""
google_api.py — Google Search Console API
https://developers.google.com/webmaster-tools/search_console_api-original
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

import config
from db import Position, get_or_create_keyword

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


class GoogleSearchConsoleClient:
    """
    Google Search Console API — бесплатно, история 16 месяцев.
    Нужен service account JSON.
    """

    def __init__(self, credentials_path: str, site_url: str):
        self.site_url = site_url

        # Load credentials
        if credentials_path and os.path.exists(credentials_path):
            creds = service_account.Credentials.from_service_account_file(
                credentials_path, scopes=SCOPES
            )
        else:
            # Попробуем GOOGLE_APPLICATION_CREDENTIALS env
            creds = None
            logger.warning("No valid credentials file found for GSC API")

        self.service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    def get_search_analytics(
        self,
        date_from: date,
        date_to: date,
        dimensions: Optional[list[str]] = None,
        row_limit: int = 5000,
    ) -> list[dict]:
        """
        Получает данные Search Analytics.

        dimensions: "query" | "page" | "country" | "device" | "date"
        date_from/date_to: date objects

        Returns: [{query, clicks, impressions, ctr, position}, ...]
        """
        if dimensions is None:
            dimensions = ["query"]

        body = {
            "startDate": date_from.isoformat(),
            "endDate": date_to.isoformat(),
            "dimensions": dimensions,
            "rowLimit": row_limit,
            "startRow": 0,
        }

        try:
            result = self.service.searchanalytics().query(
                siteUrl=self.site_url, body=body
            ).execute()

            rows = result.get("rows", [])
            result_list = []
            for row in rows:
                entry = {
                    "clicks": row.get("clicks", 0),
                    "impressions": row.get("impressions", 0),
                    "ctr": row.get("ctr", 0.0),
                    "position": row.get("position", 0.0),
                }
                # Add dimension values
                for i, dim in enumerate(dimensions):
                    entry[dim] = row["keys"][i]
                result_list.append(entry)

            return result_list

        except Exception as e:
            logger.error(f"GSC API error: {e}")
            return []


def run_google_collection(
    db: Session,
    cfg: config.AppConfig,
    date_: Optional[date] = None,
) -> dict:
    """
    Собирает данные из Google Search Console за вчерашний день.
    """
    if date_ is None:
        date_ = date.today()

    yesterday = date_ - timedelta(days=1)
    collected = {"queries": 0, "errors": []}

    creds_path = cfg.gsc_credentials
    if not os.path.exists(creds_path):
        logger.warning(f"GSC credentials not found: {creds_path}")
        collected["errors"].append("credentials_not_found")
        return collected

    try:
        client = GoogleSearchConsoleClient(
            credentials_path=creds_path,
            site_url=cfg.gsc_site_url,
        )

        # Получаем данные по запросам за вчера
        rows = client.get_search_analytics(
            date_from=yesterday,
            date_to=yesterday,
            dimensions=["query"],
        )

        for row in rows:
            keyword = row.get("query", "")
            if not keyword:
                continue
            kid = get_or_create_keyword(db, keyword, "google", "google")
            pos = Position(
                keyword_id=kid,
                date=datetime.combine(date_, datetime.min.time(), tzinfo=timezone.utc),
                position=row.get("position"),
                impressions=row.get("impressions", 0),
                clicks=row.get("clicks", 0),
                ctr=row.get("ctr", 0.0),
                source="google",
                region="google",
            )
            db.add(pos)
            collected["queries"] += 1

        db.commit()
        logger.info(f"GSC: saved {len(rows)} queries")

    except Exception as e:
        logger.error(f"GSC collection error: {e}")
        collected["errors"].append(str(e))

    return collected
