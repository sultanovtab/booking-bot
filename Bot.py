import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN в .env")
if not ADMIN_CHAT_ID or not ADMIN_CHAT_ID.lstrip("-").isdigit():
    raise RuntimeError("Не найден корректный ADMIN_CHAT_ID в .env (должно быть число)")

ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)

DB_PATH = "bookings.sqlite3"

SERVICES = [
    ("inferno", "Инферно"),
    ("patient0", "Нулевой пациент"),
    ("cannibal", "Каннибал"),
]


def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            tg_user_id INTEGER NOT NULL,
            tg_username TEXT,
            name TEXT NOT NULL,
            service_key TEXT NOT NULL,
            service_title TEXT NOT NULL,
            team_size INTEGER NOT NULL,
            ages TEXT NOT NULL
        )
        """
    )
    con.commit()
    con.close()


def save_booking(tg_user_id: int, tg_username: str | None, name: str,
                 service_key: str, service_title: str, team_size: int, ages: list[int]) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO bookings (created_at, tg_user_id, tg_username, name, service_key, service_title, team_size, ages)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.utcnow().isoformat(timespec="seconds"),
            tg_user_id,
            tg_username,
            name,
            service_key,
            service_title,
            team_size,
            ",".join(map(str, ages)),
        ),
    )
    con.commit()
    booking_id = cur.lastrowid
    con.close()
    return booking_id


class BookingFlow(StatesGroup):
    waiting_name = State()
    waiting_service = State()
    waiting_team_size = State()
    waiting_ages = State()


@dataclass
class DraftBooking:
    name: str | None = None
    service_key: str | None = None
    service_title: str | None = None
    team_size: int | None = None
    ages: list[int] | None = None


def services_kb():
    kb = InlineKeyboardBuilder()
    for key, title in SERVICES:
        kb.button(text=title, callback_data=f"service:{key}")
    kb.adjust(1)
    return kb.as_markup()


def team_size_kb():
    kb = InlineKeyboardBuilder()
    for n in range(2, 7):
        kb.button(text=str(n), callback_data=f"team:{n}")
    kb.adjust(5)
    return kb.as_markup()


def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Забронировать", callback_data="action:book")
    kb.button(text="ℹ️ Что умеет бот", callback_data="action:help")
    kb.adjust(1)
    return kb.as_markup()


def is_valid_name(text: str) -> bool:
    text = text.strip()
    if len(text) < 2 or len(text) > 60:
        return False
    # допускаем рус/лат, пробелы, дефис
    return bool(re.fullmatch(r"[A-Za-zА-Яа-яЁё\- ]+", text))


def parse_age(text: str) -> int | None:
    text = text.strip()
    if not text.isdigit():
        return None
    age = int(text)
    if 5 <= age <= 90:
        return age
    return None


async def start(message: Message):
    await message.answer(
        "Привет! Я бот для брони.\n\nНажми «Забронировать», и я соберу данные команды.",
        reply_markup=main_menu_kb(),
    )


async def cmd_book(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(BookingFlow.waiting_name)
    await state.update_data(draft=DraftBooking().__dict__)
    await message.answer("Как тебя зовут? (только буквы, пробелы, дефис)")


async def action_buttons(call: CallbackQuery, state: FSMContext):
    if call.data == "action:help":
        await call.message.edit_text(
            "Я собираю бронь: имя → услуга → размер команды (2–6) → возраст каждого участника.\n\n"
            "Команды:\n"
            "• /start — меню\n"
            "• /book — начать бронь заново\n"
            "• /cancel — отменить",
            reply_markup=main_menu_kb(),
        )
        await call.answer()
        return

    if call.data == "action:book":
        await call.answer()
        await state.clear()
        await state.set_state(BookingFlow.waiting_name)
        await state.update_data(draft=DraftBooking().__dict__)
        await call.message.edit_text("Как тебя зовут? (только буквы, пробелы, дефис)")
        return


async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Ок, отменил. Если нужно — нажми «Забронировать».", reply_markup=main_menu_kb())


async def got_name(message: Message, state: FSMContext):
    name = message.text or ""
    if not is_valid_name(name):
        await message.answer("Имя выглядит странно 😅\nНапиши, пожалуйста, только буквами (можно пробел/дефис).")
        return

    data = await state.get_data()
    draft = DraftBooking(**data.get("draft", {}))
    draft.name = name.strip()

    await state.update_data(draft=draft.__dict__)
    await state.set_state(BookingFlow.waiting_service)

    await message.answer("Выбери услугу:", reply_markup=services_kb())


async def choose_service(call: CallbackQuery, state: FSMContext):
    await call.answer()
    m = re.fullmatch(r"service:(.+)", call.data or "")
    if not m:
        return

    service_key = m.group(1)
    service_title = next((t for k, t in SERVICES if k == service_key), None)
    if not service_title:
        await call.message.answer("Не понял услугу. Попробуй ещё раз командой /book")
        return

    data = await state.get_data()
    draft = DraftBooking(**data.get("draft", {}))
    draft.service_key = service_key
    draft.service_title = service_title

    await state.update_data(draft=draft.__dict__)
    await state.set_state(BookingFlow.waiting_team_size)

    await call.message.edit_text("Сколько человек в команде? (2–6)", reply_markup=team_size_kb())


async def choose_team(call: CallbackQuery, state: FSMContext):
    await call.answer()
    m = re.fullmatch(r"team:(\d+)", call.data or "")
    if not m:
        return
    team_size = int(m.group(1))
    if team_size < 2 or team_size > 6:
        return

    data = await state.get_data()
    draft = DraftBooking(**data.get("draft", {}))
    draft.team_size = team_size
    draft.ages = []

    await state.update_data(draft=draft.__dict__)
    await state.set_state(BookingFlow.waiting_ages)

    await call.message.edit_text(
        f"Ок. Теперь по очереди введи возраст каждого участника.\n"
        f"Участник 1 из {team_size}:"
    )


async def got_age(message: Message, state: FSMContext, bot: Bot):
    age = parse_age(message.text or "")
    if age is None:
        await message.answer("Возраст должен быть числом от 5 до 90. Введи ещё раз:")
        return

    data = await state.get_data()
    draft = DraftBooking(**data.get("draft", {}))

    if draft.team_size is None:
        await message.answer("Что-то пошло не так. Начни заново: /book")
        return

    ages = draft.ages or []
    ages.append(age)
    draft.ages = ages

    # ещё не всех собрали
    if len(ages) < draft.team_size:
        await state.update_data(draft=draft.__dict__)
        idx = len(ages) + 1
        await message.answer(f"Участник {idx} из {draft.team_size}:")
        return

    # всё собрали -> сохраняем
    booking_id = save_booking(
        tg_user_id=message.from_user.id,
        tg_username=message.from_user.username,
        name=draft.name or "",
        service_key=draft.service_key or "",
        service_title=draft.service_title or "",
        team_size=draft.team_size,
        ages=ages,
    )

    # подтверждение пользователю
    await message.answer(
        "✅ Бронь создана!\n\n"
        f"Номер брони: #{booking_id}\n"
        f"Имя: {draft.name}\n"
        f"Услуга: {draft.service_title}\n"
        f"Команда: {draft.team_size}\n"
        f"Возраста: {', '.join(map(str, ages))}\n\n"
        "Администратор получил уведомление и свяжется с вами.",
        reply_markup=main_menu_kb(),
    )

    # уведомление админу
    username = f"@{message.from_user.username}" if message.from_user.username else "(без username)"
    await bot.send_message(
        ADMIN_CHAT_ID,
        "📌 Новая бронь\n\n"
        f"ID: #{booking_id}\n"
        f"Клиент: {draft.name}\n"
        f"TG: {username} | user_id={message.from_user.id}\n"
        f"Услуга: {draft.service_title}\n"
        f"Команда: {draft.team_size}\n"
        f"Возраст: {', '.join(map(str, ages))}",
    )

    await state.clear()


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(start, CommandStart())
    dp.message.register(cmd_book, Command("book"))
    dp.message.register(cancel, Command("cancel"))

    dp.callback_query.register(action_buttons, F.data.in_({"action:book", "action:help"}))
    dp.message.register(got_name, BookingFlow.waiting_name)

    dp.callback_query.register(choose_service, F.data.startswith("service:"), BookingFlow.waiting_service)
    dp.callback_query.register(choose_team, F.data.startswith("team:"), BookingFlow.waiting_team_size)

    dp.message.register(got_age, BookingFlow.waiting_ages)

    return dp


async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = build_dispatcher()

    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
