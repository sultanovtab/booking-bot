# config.py
from dataclasses import dataclass
from datetime import time

@dataclass(frozen=True)
class Settings:
    DAYS_AHEAD: int = 12
    SLOT_MINUTES: int = 90

    START_TIME: time = time(10, 0)
    END_TIME: time = time(23, 30)  # последний старт 23:30

    ADDRESS: str = "Улица Дружининская 29, вход под Вайлдберис."
    PAYMENT: str = "ОПЛАТА ТОЛЬКО НАЛИЧНЫМИ!"

    # цены
    BASE_2_4: int = 4500
    PRICE_5: int = 5500
    PRICE_6: int = 6500
    AFTER_21_EXTRA: int = 1000
    AFTER_21_TIME: time = time(21, 0)

SETTINGS = Settings()
