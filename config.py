# config.py
import os
from dataclasses import dataclass
from datetime import time

@dataclass(frozen=True)
class Settings:
    DAYS_AHEAD: int = 12
    SLOT_MINUTES: int = 90

    START_TIME: time = time(10, 0)
    END_TIME: time = time(23, 30)

    # общий лимит для всех НЕ каннибала
    LAST_SLOT_20_30: time = time(20, 30)

    # после 22:00 можно бронировать ТОЛЬКО Каннибал
    NIGHT_FROM: time = time(22, 0)
    NIGHT_EXTRA: int = 1000

    TZ: str = "Europe/Moscow"

    ADDRESS: str = "Улица Дружининская 29, вход под Вайлдберис."
    PAYMENT: str = "ОПЛАТА ТОЛЬКО НАЛИЧНЫМИ!"

    # взрослые квесты (2–4=4500, 5=5500, 6=6500)
    ADULT_2_4: int = 4500
    ADULT_5: int = 5500
    ADULT_6: int = 6500

    # детские квесты (2–4=4000, дальше +800/чел)
    KIDS_2_4: int = 4000
    KIDS_ADD_PER_PERSON: int = 800

    DEFAULT_ADMINS: tuple[int, ...] = (262051696, 8175791933)

SETTINGS = Settings()

def get_admin_ids() -> list[int]:
    raw = os.getenv("ADMIN_CHAT_IDS", "").strip()
    if not raw:
        return list(SETTINGS.DEFAULT_ADMINS)
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part or not part.lstrip("-").isdigit():
            raise RuntimeError("ADMIN_CHAT_IDS должен быть списком чисел через запятую")
        ids.append(int(part))
    return ids

# category: "adult" (14+) или "kids" (10–13)
QUESTS = {
    "inferno":   {"title": "Инферно",              "category": "adult", "max_team": 6,  "last_start": SETTINGS.LAST_SLOT_20_30, "has_info": True,  "rules_key": "adult"},
    "patient0":  {"title": "Нулевой пациент",      "category": "adult", "max_team": 6,  "last_start": SETTINGS.LAST_SLOT_20_30, "has_info": True,  "rules_key": "adult"},
    "cannibal":  {"title": "Каннибал",             "category": "adult", "max_team": 6,  "last_start": SETTINGS.END_TIME,        "has_info": True,  "rules_key": "adult"},
    "hospital":  {"title": "Заброшенная больница", "category": "kids",  "max_team": 10, "last_start": SETTINGS.LAST_SLOT_20_30, "has_info": False, "rules_key": "kids"},
    "cabin":     {"title": "Хижина маньяка",       "category": "kids",  "max_team": 6,  "last_start": SETTINGS.LAST_SLOT_20_30, "has_info": False, "rules_key": "kids"},
}

def is_compatible(chosen_key: str, existing_keys: set[str]) -> bool:
    """
    Одновременно максимум 2 брони на слот, но:
    - Каннибал может быть параллельно с любым ОДНИМ другим квестом
    - любые два НЕ-каннибала одновременно нельзя
    - два Каннибала одновременно нельзя
    """
    if not existing_keys:
        return True
    if len(existing_keys) >= 2:
        return False
    if "cannibal" in existing_keys:
        return chosen_key != "cannibal"
    else:
        return chosen_key == "cannibal"
