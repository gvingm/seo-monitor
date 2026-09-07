"""
yandex_api.py — Yandex Search API v2 + Webmaster API + Metrika API
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

import config
from db import Position, get_or_create_keyword

logger = logging.getLogger(__name__)

YANDEX_WEBMASTER_API = "https://api.webmaster.yandex.net/v4"
YANDEX_METRIKA_API = "https://api-metrika.yandex.net/stat/v1"
YANDEX_SEARCH_API = "https://api.search.yandex.net/v4"


# ══════════════════════════════════════════════════════════
# Yandex Search API v2 (Cloud) — позиции в XML
# ══════════════════════════════════════════════════════════
class YandexSearchClient:
    """
    Использует Yandex Search API v2 (Cloud).
    Документация: https://yandex.cloud/ru/docs/search-api/

    Регионы:
      Москва = 213, СПб = 2
    """

    def __init__(self, folder_id: str, iam_token: str):
        self.folder_id = folder_id
        self.iam_token = iam_token
        self.base_url = "https://api-search.cloud.yandex.net/search/b2b"
        self.headers = {
            "Authorization": f"Bearer {iam_token}",
            "Content-Type": "application/json",
        }

    def fetch_positions(
        self,
        keywords: list[str],
        region_id: int = 213,
    ) -> list[dict]:
        """
        Получает позиции для списка keywords.
        Yandex Search API отдаёт только XML-snippets — реальные позиции считаем
        по порядку появления домена в выдаче.

        Returns: [{keyword, position, url, title, snippet}, ...]
        """
        results = []
        domain = "didalsk.ru"

        for keyword in keywords:
            try:
                # Запрос к Search API
                payload = {
                    "text": keyword,
                    "maxpassages": 0,
                    "filter": {
                        "max-title-length": 140,
                    },
                }
                # regional_id в URL params
                url = f"{self.base_url}?folderId={self.folder_id}&region_id={region_id}"

                resp = httpx.post(
                    url,
                    headers=self.headers,
                    json=payload,
                    timeout=30.0,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    # Ищем позицию нашего домена в выдаче
                    results.append({
                        "keyword": keyword,
                        "data": data,
                        "domain": domain,
                        "position": self._find_domain_position(data, domain),
                    })
                elif resp.status_code == 429:
                    logger.warning(f"Rate limited, waiting 60s: {keyword}")
                    time.sleep(60)
                    # Retry once
                    resp = httpx.post(url, headers=self.headers, json=payload, timeout=30.0)
                    if resp.status_code == 200:
                        results.append({
                            "keyword": keyword,
                            "data": resp.json(),
                            "domain": domain,
                            "position": self._find_domain_position(resp.json(), domain),
                        })
                else:
                    logger.error(f"Search API error {resp.status_code}: {resp.text[:200]}")

                time.sleep(0.3)  # Rate limiting

            except Exception as e:
                logger.error(f"Search API exception for '{keyword}': {e}")

        return results

    @staticmethod
    def _find_domain_position(data: dict, domain: str) -> Optional[int]:
        """Находит порядковый номер нашего домена в выдаче."""
        try:
            searchresults = data.get("searchResults", [])
            for i, item in enumerate(searchresults, start=1):
                item_url = item.get("url", "")
                if domain in item_url.lower():
                    return i
            return None  # Нет в ТОП-50
        except Exception:
            return None


# ══════════════════════════════════════════════════════════
# Yandex Webmaster API — показы, клики, CTR
# ══════════════════════════════════════════════════════════
class YandexWebmasterClient:
    """
    Бесплатный API Яндекс.Вебмастера.
    История — 2 недели, поэтому накопление в свою БД обязательно.

    Регионы (Вебмастер): Москва=1, СПб=10174

    Документация: https://yandex.ru/dev/webmaster/doc/ru/
    """

    def __init__(self, oauth_token: str, host_id: str):
        self.token = oauth_token
        self.host_id = host_id
        self.headers = {
            "Authorization": f"OAuth {oauth_token}",
            "Content-Type": "application/json",
        }

    def get_query_analytics(
        self,
        date_from: date,
        date_to: date,
        region_id: int = 1,
        query_indicators: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Получает статистику по запросам (показы, клики, CTR, позиция).
        region_id: 1=Москва, 10174=СПб
        """
        if query_indicators is None:
            query_indicators = ["TOTAL_ENTRIES_FIELD", "TOTAL_CLICKS_FIELD", "TOTAL_SHOWS_FIELD", "AVERAGE_POSITION_FIELD"]

        url = f"{YANDEX_WEBMASTER_API}/hosts/{self.host_id}/query-analytics/int32"

        payload = {
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
            "queriedFor": {
                "query_indicators": query_indicators,
            },
            "aggregationType": "day",
        }

        try:
            resp = httpx.post(url, headers=self.headers, json=payload, timeout=60.0)
            if resp.status_code == 200:
                return resp.json().get("queries", [])
            else:
                logger.error(f"Webmaster API error {resp.status_code}: {resp.text[:300]}")
                return []
        except Exception as e:
            logger.error(f"Webmaster API exception: {e}")
            return []

    def get_pages_summary(
        self,
        date_from: date,
        date_to: date,
    ) -> list[dict]:
        """
        Статистика по страницам — какие URL получали показы.
        """
        url = f"{YANDEX_WEBMASTER_API}/hosts/{self.host_id}/pages-summary"
        params = {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "indicator": "TOTAL_SHOWS_FIELD",
        }
        try:
            resp = httpx.get(url, headers=self.headers, params=params, timeout=60.0)
            if resp.status_code == 200:
                return resp.json().get("pages", [])
            else:
                logger.error(f"Webmaster pages-summary error {resp.status_code}")
                return []
        except Exception as e:
            logger.error(f"Webmaster pages-summary exception: {e}")
            return []

    def get_index_status(self) -> dict:
        """Проверяет статус индексации сайта."""
        url = f"{YANDEX_WEBMASTER_API}/hosts/{self.host_id}/summary"
        try:
            resp = httpx.get(url, headers=self.headers, timeout=30.0)
            if resp.status_code == 200:
                return resp.json()
            return {}
        except Exception as e:
            logger.error(f"Webmaster summary exception: {e}")
            return {}


# ══════════════════════════════════════════════════════════
# Yandex Metrika API — поведенческие факторы
# ══════════════════════════════════════════════════════════
class YandexMetrikaClient:
    """
    Бесплатный API Яндекс.Метрики.
    Получает: отказы, глубина просмотра, время на сайте.

    scope: metrika:read
    """

    def __init__(self, oauth_token: str, counter_id: int):
        self.token = oauth_token
        self.counter_id = counter_id
        self.headers = {
            "Authorization": f"OAuth {oauth_token}",
        }

    def get_traffic_by_keyword(
        self,
        date_from: date,
        date_to: date,
        dimensions: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Получает визиты по поисковым запросам (utm-exp-id).
        Работает только если включён параметр allow_dynamic_params в счётчике.
        """
        if dimensions is None:
            dimensions = ["ym:s:UTMContent"]

        url = f"{YANDEX_METRIKA_API}/data"
        params = {
            "ids": self.counter_id,
            "date1": date_from.isoformat(),
            "date2": date_to.isoformat(),
            "dimensions": ",".join(dimensions),
            "metrics": "ym:s:visits,ym:s:pageDepth,ym:s:avgSessionDurationSecs,ym:s:bounceRate",
            "limit": 10000,
        }
        try:
            resp = httpx.get(url, headers=self.headers, params=params, timeout=60.0)
            if resp.status_code == 200:
                data = resp.json()
                rows = data.get("data", [])
                result = []
                for row in rows:
                    dims = row.get("dimensions", [])
                    keyword = dims[0].get("name", "") if dims else ""
                    metrics = row.get("metrics", [])
                    result.append({
                        "keyword": keyword,
                        "visits": int(metrics[0]) if len(metrics) > 0 else 0,
                        "page_depth": float(metrics[1]) if len(metrics) > 1 else 0.0,
                        "avg_duration": int(metrics[2]) if len(metrics) > 2 else 0,
                        "bounce_rate": float(metrics[3]) if len(metrics) > 3 else 0.0,
                    })
                return result
            else:
                logger.error(f"Metrika API error {resp.status_code}")
                return []
        except Exception as e:
            logger.error(f"Metrika API exception: {e}")
            return []


# ══════════════════════════════════════════════════════════
# Сборщик данных — основной workflow
# ══════════════════════════════════════════════════════════
def run_daily_collection(
    db: Session,
    cfg: config.AppConfig,
    date_: Optional[date] = None,
) -> dict:
    """
    Запускается ежедневно в 08:30 МСК.
    1. Search API → позиции по ключевым словам (Moskva + SPb)
    2. Webmaster API → показы, клики, CTR
    3. Записывает всё в БД
    """
    if date_ is None:
        date_ = date.today()

    yesterday = date_ - timedelta(days=1)
    week_ago = date_ - timedelta(days=7)

    collected = {
        "positions": 0,
        "webmaster": 0,
        "alerts": 0,
        "errors": [],
    }

    # --- 1. Yandex Search API (позиции) ---
    # Search API v2 требует Yandex Cloud Service Account с правом search-api:invoke.
    # AI Studio API-ключ (AQVN...) сюда НЕ подходит — нужна отдельная сущность.
    # Если cfg.yandex_cloud выглядит как AI Studio ключ — пропускаем Search API молча.
    is_cloud_sa = cfg.yandex_cloud and not cfg.yandex_cloud.startswith("AQVN")
    if is_cloud_sa and cfg.yandex_folder:
        for region_name, region_id in [
            ("moscow", cfg.yandex_regions.search_moscow),
            ("spb", cfg.yandex_regions.search_spb),
        ]:
            try:
                client = YandexSearchClient(
                    folder_id=cfg.yandex_folder,
                    iam_token=cfg.yandex_cloud,
                )
                results = client.fetch_positions(
                    keywords=cfg.keywords,
                    region_id=region_id,
                )
                for item in results:
                    kw = item["keyword"]
                    kid = get_or_create_keyword(db, kw, region_name, "yandex")
                    pos = Position(
                        keyword_id=kid,
                        date=datetime.combine(date_, datetime.min.time(), tzinfo=timezone.utc),
                        position=item.get("position"),
                        source="yandex",
                        region=region_name,
                    )
                    db.add(pos)
                    collected["positions"] += 1
                db.commit()
                logger.info(f"Search API: saved {len(results)} positions for {region_name}")
            except Exception as e:
                logger.error(f"Search API error ({region_name}): {e}")
                collected["errors"].append(f"search_api_{region_name}: {e}")
    else:
        logger.info("Search API skipped: AI Studio key detected (need Cloud Service Account for Search API)")

    # --- 2. Yandex Webmaster (показы, клики) ---
    if cfg.yandex_oauth and cfg.yandex_host:
        for region_name, region_id in [
            ("moscow", cfg.yandex_regions.wm_moscow),
            ("spb", cfg.yandex_regions.wm_spb),
        ]:
            try:
                client = YandexWebmasterClient(
                    oauth_token=cfg.yandex_oauth,
                    host_id=cfg.yandex_host,
                )
                # Получаем данные за вчера и неделю
                queries = client.get_query_analytics(yesterday, yesterday, region_id)
                for q in queries:
                    keyword = q.get("query", "")
                    if not keyword:
                        continue
                    kid = get_or_create_keyword(db, keyword, region_name, "webmaster")
                    pos = Position(
                        keyword_id=kid,
                        date=datetime.combine(date_, datetime.min.time(), tzinfo=timezone.utc),
                        position=q.get("position"),
                        impressions=q.get("shows", 0),
                        clicks=q.get("clicks", 0),
                        ctr=q.get("ctr", 0.0),
                        source="webmaster",
                        region=region_name,
                    )
                    db.add(pos)
                    collected["webmaster"] += 1
                db.commit()
                logger.info(f"Webmaster: saved {len(queries)} queries for {region_name}")
            except Exception as e:
                logger.error(f"Webmaster API error ({region_name}): {e}")
                collected["errors"].append(f"webmaster_{region_name}: {e}")

    return collected
