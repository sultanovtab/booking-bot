# config.py
import os
from dataclasses import dataclass
from datetime import time

@dataclass(frozen=True)
class Settings:
    # бронь только на ближайшие дни
    DAYS_AHEAD: int = 12

    # слоты (минут) и время работы (последний старт 23:30)
    SLOT_MINUTES: int = 90
    START_TIME: time = time(10, 0)
    END_TIME: time = time(23, 30)

    # Москва
    TZ: str = "Europe/Moscow"

    # Адрес/оплата
    ADDRESS: str = "Улица Дружининская 29, вход под Вайлдберис."
    PAYMENT: str = "ОПЛАТА ТОЛЬКО НАЛИЧНЫМИ!"

    # Цены
    PRICE_2_4: int = 4500
    PRICE_5: int = 5500
    PRICE_6: int = 6500
    AFTER_21_EXTRA: int = 1000
    AFTER_21_TIME: time = time(21, 0)

    # Админы по умолчанию (твои)
    DEFAULT_ADMINS: tuple[int, ...] = (262051696, 8175791933)

SETTINGS = Settings()

def get_admin_ids() -> list[int]:
    """
    Можно управлять админами без кода через ENV:
    ADMIN_CHAT_IDS="262051696,8175791933"
    Если env нет — берём DEFAULT_ADMINS
    """
    raw = os.getenv("ADMIN_CHAT_IDS", "").strip()
    if not raw:
        return list(SETTINGS.DEFAULT_ADMINS)

    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part or not part.lstrip("-").isdigit():
            raise RuntimeError("ADMIN_CHAT_IDS должен быть списком чисел через запятую, пример: 123,456")
        ids.append(int(part))
    return ids
