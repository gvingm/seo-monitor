"""
config.py —集中的配置管理，所有环境变量只在这里读取
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env file for local development
_dotenv = Path(__file__).parent / ".env"
if _dotenv.exists():
    load_dotenv(_dotenv)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int = 0) -> int:
    val = os.environ.get(key)
    return int(val) if val else default


# ── Yandex ────────────────────────────────────────────────
YANDEX_OAUTH_TOKEN: str = _env("YANDEX_OAUTH_TOKEN")
YANDEX_CLOUD_TOKEN: str = _env("YANDEX_CLOUD_TOKEN")
YANDEX_FOLDER_ID: str = _env("YANDEX_FOLDER_ID")
YANDEX_HOST_ID: str = _env("YANDEX_HOST_ID")
# Yandex Webmaster v4 требует user_id в пути. Можно задать вручную или автодетект через /info.
YANDEX_USER_ID: str = _env("YANDEX_USER_ID", "")

YANDEX_REGION_WM_MOSCOW: int = _env_int("YANDEX_REGION_WEBMASTER_MOSCOW", 1)
YANDEX_REGION_WM_SPB: int = _env_int("YANDEX_REGION_WEBMASTER_SPB", 10174)
YANDEX_REGION_SEARCH_MOSCOW: int = _env_int("YANDEX_REGION_SEARCH_MOSCOW", 213)
YANDEX_REGION_SEARCH_SPB: int = _env_int("YANDEX_REGION_SEARCH_SPB", 2)

# ── Google ────────────────────────────────────────────────
GOOGLE_APPLICATION_CREDENTIALS: str = _env("GOOGLE_APPLICATION_CREDENTIALS", "/app/creds/gsc.json")
GSC_SITE_URL: str = _env("GSC_SITE_URL", "https://didalsk.ru")

# ── Database ─────────────────────────────────────────────
DATABASE_URL: str = _env(
    "DATABASE_URL",
    "postgresql://seomonitor:ChangeMe123@db:5432/seomonitor"
)

# ── App ───────────────────────────────────────────────────
APP_SECRET_KEY: str = _env("APP_SECRET_KEY", "dev-secret-change-in-prod")
TZ: str = _env("TZ", "Europe/Moscow")
LOG_LEVEL: str = _env("LOG_LEVEL", "INFO")

# ── Telegram ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = _env("TELEGRAM_CHAT_ID", "")

# ── SEO: отслеживаемые запросы ──────────────────────────
# Загружаем из файла или из переменной окружения (через запятую)
SEO_KEYWORDS_RAW: str = _env(
    "SEO_KEYWORDS",
    # Коммерческие ядра — ядро, 50 запросов
    "дноуглубление, дноуглубительные работы, дноуглубительный флот, "
    "аренда земснаряда, аренда экскаватора на понтоне, дноуглубление судового хода, "
    "расчистка судового хода, речное дноуглубление, дноуглубление порта, "
    "дноуглубление акватории, углубление дна, работы по дноуглублению, "
    "морское дноуглубление, дноуглубительные работы речные, дноуглубление судоходных путей, "
    "аренда шаланды, аренда баржи, аренда буксира, вывоз грунта водным путём, "
    "гидронамыв, намыв грунта, разработка грунта под водой, "
    "разработка траншеи под водой, укладка трубопровода под водой, "
    "подводно-технические работы, водолазные работы, обследование подводное, "
    "дноуглубление экскаватором, дноуглубление землесосом, землесосная установка, "
    "гидромеханизация, грунтосос, земснаряд речной, "
    "техника для дноуглубления, специализированный флот, "
    "дноуглубление подрядчик, дноуглубление компания, дноуглубление заказать, "
    "дноуглубление расценки, дноуглубление стоимость, "
    "дноуглубление СПб, дноуглубление Ленинградская область, "
    "дноуглубление Мурманск, дноуглубление Архангельск, "
    "дноуглубление Карелия, дноуглубление Северо-Запад, "
    "дноуглубление река Нева, дноуглубление Финский залив, "
    "дноуглубление Выборгский залив, дноуглубление Высоцк, "
    "дноуглубление Приморск, дноуглубление Усть-Луга"
)

SEO_KEYWORDS: list[str] = [k.strip() for k in SEO_KEYWORDS_RAW.split(",") if k.strip()]


# ── Dataclasses ──────────────────────────────────────────
@dataclass
class YandexRegions:
    wm_moscow: int = YANDEX_REGION_WM_MOSCOW
    wm_spb: int = YANDEX_REGION_WM_SPB
    search_moscow: int = YANDEX_REGION_SEARCH_MOSCOW
    search_spb: int = YANDEX_REGION_SEARCH_SPB


@dataclass
class AppConfig:
    secret_key: str = APP_SECRET_KEY
    tz: str = TZ
    log_level: str = LOG_LEVEL
    database_url: str = DATABASE_URL
    yandex_oauth: str = YANDEX_OAUTH_TOKEN
    yandex_cloud: str = YANDEX_CLOUD_TOKEN
    yandex_folder: str = YANDEX_FOLDER_ID
    yandex_host: str = YANDEX_HOST_ID
    yandex_user_id: str = YANDEX_USER_ID
    yandex_regions: YandexRegions = field(default_factory=YandexRegions)
    gsc_credentials: str = GOOGLE_APPLICATION_CREDENTIALS
    gsc_site_url: str = GSC_SITE_URL
    telegram_bot_token: str = TELEGRAM_BOT_TOKEN
    telegram_chat_id: str = TELEGRAM_CHAT_ID
    keywords: list[str] = field(default_factory=lambda: SEO_KEYWORDS)

    @property
    def is_production(self) -> bool:
        return bool(self.yandex_oauth and self.yandex_host)
