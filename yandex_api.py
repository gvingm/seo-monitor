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
YANDEX_OAUTH_INFO = "https://login.yandex.ru/info?format=json"


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
        # Yandex Cloud Search API v2 — sync endpoint
        self.base_url = "https://searchapi.api.cloud.yandex.net/v2/web/search"
        self.headers = {
            "Authorization": f"Bearer {iam_token}",
            "Content-Type": "application/json",
        }

    def fetch_positions(
        self,
        keywords: list[str],
        region_id: int = 213,
        max_concurrent: int = 8,
    ) -> list[dict]:
        """
        Получает позиции для списка keywords параллельно (max_concurrent запросов).
        Yandex Search API возвращает XML в поле rawData — парсим и ищем наш домен.

        Returns: [{keyword, position, url, title, snippet}, ...]
        """
        import concurrent.futures
        results = []
        domain = "didalsk.ru"

        def fetch_one(keyword: str) -> dict:
            payload = {
                "query": {
                    "search_type": "SEARCH_TYPE_RU",
                    "query_text": keyword,
                    "family": "default",
                },
                "folderId": self.folder_id,
            }
            url = f"{self.base_url}?folderId={self.folder_id}"
            try:
                resp = httpx.post(
                    url,
                    headers=self.headers,
                    json=payload,
                    timeout=30.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "keyword": keyword,
                        "data": data,
                        "domain": domain,
                        "position": self._find_domain_position(data, domain),
                    }
                elif resp.status_code == 429:
                    return {
                        "keyword": keyword,
                        "error": "rate_limited",
                        "data": None,
                        "position": None,
                    }
                else:
                    logger.error(f"Search API error {resp.status_code} for '{keyword}': {resp.text[:200]}")
                    return {
                        "keyword": keyword,
                        "error": f"http_{resp.status_code}",
                        "data": None,
                        "position": None,
                    }
            except Exception as e:
                logger.error(f"Search API exception for '{keyword}': {e}")
                return {
                    "keyword": keyword,
                    "error": str(e)[:100],
                    "data": None,
                    "position": None,
                }

        # Параллельно, чтобы уложиться в разумное время
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as ex:
            futures = {ex.submit(fetch_one, kw): kw for kw in keywords}
            for fut in concurrent.futures.as_completed(futures):
                results.append(fut.result())

        # Сохраняем порядок (as_completed меняет)
        order = {kw: i for i, kw in enumerate(keywords)}
        results.sort(key=lambda r: order.get(r["keyword"], 0))
        return results

    @staticmethod
    def _find_domain_position(data: dict, domain: str) -> Optional[int]:
        """Находит порядковый номер нашего домена в выдаче.

        Yandex Cloud Search API v2 возвращает результат в поле `rawData`
        как base64-кодированный XML. Парсим XML и ищем наш домен в <url>.
        """
        import base64
        import xml.etree.ElementTree as ET
        try:
            raw = data.get("rawData")
            if not raw:
                return None
            xml_bytes = base64.b64decode(raw)
            root = ET.fromstring(xml_bytes)
            # Ищем все <url> в <grouping> → <group> → <doc> → <url>
            for pos, url_el in enumerate(root.iter("url"), start=1):
                url_text = (url_el.text or "").lower()
                if domain in url_text:
                    return pos
                if pos >= 50:
                    break
            return None
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

    def __init__(self, oauth_token: str, host_id: str, user_id: str = ""):
        self.token = oauth_token
        self.host_id = host_id
        # Webmaster v4 требует /user/{user_id}/hosts/... — узнаём через /info, если не задан
        self.user_id = user_id or self._discover_user_id()
        self.headers = {
            "Authorization": f"OAuth {oauth_token}",
            "Content-Type": "application/json",
        }

    def _discover_user_id(self) -> str:
        """Получает user_id через Yandex OAuth /info."""
        try:
            r = httpx.get(
                YANDEX_OAUTH_INFO,
                headers={"Authorization": f"OAuth {self.token}"},
                timeout=15,
            )
            if r.status_code == 200:
                uid = str(r.json().get("id", ""))
                if uid:
                    logger.info(f"Webmaster: discovered user_id={uid} from OAuth token")
                    return uid
        except Exception as e:
            logger.error(f"Webmaster: failed to discover user_id: {e}")
        return ""

    def _base(self) -> str:
        """Базовый URL с user_id. Если не получилось — фолбэк на /hosts/ (вернёт 404)."""
        if self.user_id:
            return f"{YANDEX_WEBMASTER_API}/user/{self.user_id}/hosts/{self.host_id}"
        return f"{YANDEX_WEBMASTER_API}/hosts/{self.host_id}"

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

        url = f"{self._base()}/query-analytics/int32"

        payload = {
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
            "queriedFor": {
                "query_indicators": query_indicators,
            },
            "region_id": region_id,
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
        url = f"{self._base()}/pages-summary"
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
        url = f"{self._base()}/summary"
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
    # Search API v2 требует Yandex Cloud Service Account с правом search-api.webSearch.user
    # и API-ключ этого SA (формат AQVN...). Пускаем если задан и ключ, и folder.
    if cfg.yandex_cloud and cfg.yandex_folder:
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
        logger.info("Search API skipped: no token or folder")

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
                    user_id=cfg.yandex_user_id,
                )
                # Получаем данные за вчера и неделю
                queries = client.get_query_analytics(yesterday, yesterday, region_id)
                for q in queries:
                    keyword = q.get("query_text") or q.get("query", "")
                    if not keyword:
                        continue
                    # v4: индикаторы лежат в q["indicators"]
                    indicators = q.get("indicators", {}) or {}
                    shows = int(indicators.get("TOTAL_SHOWS_FIELD", 0) or 0)
                    clicks = int(indicators.get("TOTAL_CLICKS_FIELD", 0) or 0)
                    avg_pos = indicators.get("AVERAGE_POSITION_FIELD")
                    ctr = (clicks / shows) if shows > 0 else 0.0
                    kid = get_or_create_keyword(db, keyword, region_name, "webmaster")
                    pos = Position(
                        keyword_id=kid,
                        date=datetime.combine(date_, datetime.min.time(), tzinfo=timezone.utc),
                        position=avg_pos,
                        impressions=shows,
                        clicks=clicks,
                        ctr=ctr,
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
