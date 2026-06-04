import asyncio
import logging
import random
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ChatPermissions, ChatMemberUpdated, ContentType
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, JOIN_TRANSITION, LEAVE_TRANSITION

# ==============================
# ТОКЕН БОТА
# ==============================
BOT_TOKEN = "8574174287:AAGBQdko0QpRZSxb6J8PXVd1AYcz0GlgLwQ"

# ==============================
# РАЗРЕШЁННАЯ ГРУППА
# ==============================
ALLOWED_CHAT_ID = -1002320683005

# ==============================
# WHITELIST
# ==============================
ALLOWED_USERS = {2007585386, 5705359189}
ALLOWED_USERNAMES = {"Nikitasova01", "MrDakplease"}

# ==============================
# КУДА СЛАТЬ РЕПОРТЫ
# ==============================
REPORT_RECEIVERS = {2007585386, 5705359189}

# ==============================
# КАНАЛ ДЛЯ АНОНИМНЫХ СООБЩЕНИЙ
# ==============================
ANON_CHANNEL_ID = -1002320683005

# ==============================
# Антиспам
# ==============================
SPAM_MAX_MESSAGES = 5
SPAM_WINDOW = 5
MAX_MESSAGE_LENGTH = 350
spam_tracker: dict[int, list[float]] = defaultdict(list)
waiting_for_anon = set()

# ==============================
# Состояние чата (открыт/закрыт)
# ==============================
chat_locked = False

# ==============================
# Антирейд
# ==============================
antiraid_enabled = False
ANTIRAID_JOIN_LIMIT = 5      # максимум новых участников за окно
ANTIRAID_JOIN_WINDOW = 10    # секунд
recent_joins: list[float] = []

# ==============================
# Антилинк
# ==============================
antilink_enabled = False
LINK_PATTERN = re.compile(
    r"(https?://|www\.|t\.me/|@\w+\.\w+|"
    r"(?<!\w)(?:telegram\.me|tg\.me|discord\.gg|bit\.ly|goo\.gl))",
    re.IGNORECASE,
)

# ==============================
# Настройки чата
# ==============================
settings: dict = {
    "antispam": True,
    "antilink": False,
    "anticaps": False,
    "antiraid": False,
    "max_length": MAX_MESSAGE_LENGTH,
}

# ==============================
# Запретные слова
# ==============================
BANNED_WORDS = [
    "дс", "дискорд", "хохол",
    "гойда", "сво", "卐", "1488",
]

# ==============================
# ШУТКИ
# ==============================
JOKES = [
    "Почему программисты не смотрят в окно? Потому что там слишком много bagов.",
    "Встречаются два байта. Один говорит: «У тебя биты не выровнены!» Второй: «Это не баг, это фича.»",
    "Жена программиста отправила его в магазин: «Купи хлеб, и если будут яйца — возьми десяток». Он вернулся с 10 буханками хлеба.",
    "— Как дела? — Null. — Что? — Не определено.",
    "404: Шутка не найдена. Попробуй перезагрузить чат.",
    "Оптимист говорит: стакан наполовину полон. Пессимист: наполовину пуст. Программист: стакан в два раза больше, чем нужно.",
    "— Почему ты такой молчаливый? — Boolean. True or False. Третьего не дано.",
    "Бог создал мир за 6 дней, потому что у него не было legacy code.",
    "Баг — это не баг. Это недокументированная фича.",
    "— Сколько программистов нужно, чтобы вкрутить лампочку? — Ни одного, это аппаратная проблема.",
]

# ==============================
# СИСТЕМА УРОВНЕЙ
# ==============================
EXP_PER_MESSAGE_MIN = 3
EXP_PER_MESSAGE_MAX = 8
EXP_COOLDOWN = 60
DAILY_BONUS_MIN = 50
DAILY_BONUS_MAX = 150
exp_cooldown_tracker: dict[int, float] = {}

LEVEL_NAMES = {
    0:  "🐣 Новичок",
    1:  "📖 Читатель",
    2:  "💬 Болтун",
    3:  "🌟 Активист",
    4:  "🔥 Ветеран",
    5:  "💎 Легенда",
    6:  "👑 Элита",
    7:  "🚀 Гуру",
    8:  "⚡ Мастер",
    9:  "🏆 Чемпион",
    10: "🌌 Бог чата",
}

def get_level_name(level: int) -> str:
    return LEVEL_NAMES.get(min(level, max(LEVEL_NAMES.keys())), LEVEL_NAMES[max(LEVEL_NAMES.keys())])

def exp_for_level(level: int) -> int:
    if level <= 0:
        return 0
    return int(100 * (level ** 1.5))

def calculate_level(total_exp: int) -> tuple[int, int, int]:
    level = 0
    while exp_for_level(level + 1) <= total_exp:
        level += 1
    base = exp_for_level(level)
    current_in = total_exp - base
    need_next = exp_for_level(level + 1) - base
    return level, current_in, need_next

def make_progress_bar(current: int, total: int, length: int = 10) -> str:
    if total == 0:
        return "█" * length
    filled = max(0, min(length, int(length * current / total)))
    return "█" * filled + "░" * (length - filled)

# ==============================
# ПРАВИЛА
# ==============================
RULES_TEXT = """
📜 <b>ПРАВИЛА!</b>

<b>1.0</b> Угозы — <i>1 день</i>
<b>1.1</b> Дофига матов — <i>5 часов</i>
<b>1.2</b> Оскорбелия — <i>12 часов</i>
<b>1.3</b> Оскорбление родителей — <i>1 день</i>
<b>1.4</b> Скам — <i>от 7 дней до БАН</i>
<b>1.5</b> Спам — <i>12 часов</i>
<b>1.6</b> Осудительные вещи — <i>1 день</i>
<b>1.7</b> Флуд — <i>12 часов</i>
<b>1.8</b> Капс — <i>1 час</i>
<b>1.9</b> Реклама — <i>от 7 дней до БАН</i>
<b>1.10</b> 18+ контент — <i>1 день</i>
<b>1.11</b> Токсичность, провокация — <i>1 день</i>
<b>1.12</b> Обход наказания — <i>от 12 дней до БАН</i>
<b>1.13</b> Доксинг — <i>БАН</i>
<b>1.14</b> Попрошайничество — <i>2 часа</i>
<b>1.15</b> Обижать Дака — <i>12 часов</i>
<b>1.16</b> Обижать Ульяну — <i>от 1 дня до 2 дней</i>
<b>1.17</b> Расизм — <i>12 часов</i>

Напишите /report причина, если кто-то нарушает правила
⚠️ Лимит букв в одном сообщении — 350
Соблюдайте правила, приятного общения! 😊
""".strip()

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ─────────────────────────────────────────────
# База данных SQLite
# ─────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect("moderation.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS mutes (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            until INTEGER,
            reason TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bans (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            until INTEGER,
            reason TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS warns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            reason TEXT,
            created_at INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS levels (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            exp INTEGER DEFAULT 0
        )
    """)
    # Логи действий бота
    c.execute("""
        CREATE TABLE IF NOT EXISTS mod_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            admin_id INTEGER,
            admin_name TEXT,
            target_id INTEGER,
            target_name TEXT,
            reason TEXT,
            created_at INTEGER
        )
    """)
    # Ежедневные бонусы
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_bonus (
            user_id INTEGER PRIMARY KEY,
            last_claim INTEGER
        )
    """)
    # История активности по неделям
    c.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            ts INTEGER
        )
    """)
    conn.commit()
    conn.close()


# ── Mutes ──
def db_add_mute(user_id: int, username: str, until: int | None, reason: str):
    conn = sqlite3.connect("moderation.db")
    conn.execute(
        "INSERT OR REPLACE INTO mutes (user_id, username, until, reason) VALUES (?, ?, ?, ?)",
        (user_id, username, until, reason)
    )
    conn.commit()
    conn.close()

def db_remove_mute(user_id: int):
    conn = sqlite3.connect("moderation.db")
    conn.execute("DELETE FROM mutes WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def db_get_mutes():
    conn = sqlite3.connect("moderation.db")
    rows = conn.execute("SELECT user_id, username, until, reason FROM mutes").fetchall()
    conn.close()
    return rows

def db_is_muted(user_id: int) -> tuple[bool, int | None]:
    conn = sqlite3.connect("moderation.db")
    row = conn.execute("SELECT until FROM mutes WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return False, None
    until = row[0]
    if until is None or until > int(time.time()):
        return True, until
    return False, None

def db_is_banned(user_id: int) -> tuple[bool, int | None]:
    conn = sqlite3.connect("moderation.db")
    row = conn.execute("SELECT until FROM bans WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return False, None
    until = row[0]
    if until is None or until > int(time.time()):
        return True, until
    return False, None


# ── Bans ──
def db_add_ban(user_id: int, username: str, until: int | None, reason: str):
    conn = sqlite3.connect("moderation.db")
    conn.execute(
        "INSERT OR REPLACE INTO bans (user_id, username, until, reason) VALUES (?, ?, ?, ?)",
        (user_id, username, until, reason)
    )
    conn.commit()
    conn.close()

def db_remove_ban(user_id: int):
    conn = sqlite3.connect("moderation.db")
    conn.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def db_get_bans():
    conn = sqlite3.connect("moderation.db")
    rows = conn.execute("SELECT user_id, username, until, reason FROM bans").fetchall()
    conn.close()
    return rows


# ── Warns ──
def db_add_warn(user_id: int, username: str, reason: str):
    conn = sqlite3.connect("moderation.db")
    conn.execute(
        "INSERT INTO warns (user_id, username, reason, created_at) VALUES (?, ?, ?, ?)",
        (user_id, username, reason, int(time.time()))
    )
    conn.commit()
    conn.close()

def db_get_warns(user_id: int):
    conn = sqlite3.connect("moderation.db")
    rows = conn.execute(
        "SELECT reason, created_at FROM warns WHERE user_id = ? ORDER BY created_at ASC",
        (user_id,)
    ).fetchall()
    conn.close()
    return rows

def db_get_warns_with_id(user_id: int):
    conn = sqlite3.connect("moderation.db")
    rows = conn.execute(
        "SELECT id, reason, created_at FROM warns WHERE user_id = ? ORDER BY created_at ASC",
        (user_id,)
    ).fetchall()
    conn.close()
    return rows

def db_remove_warn_by_id(warn_id: int):
    conn = sqlite3.connect("moderation.db")
    conn.execute("DELETE FROM warns WHERE id = ?", (warn_id,))
    conn.commit()
    conn.close()

def db_get_all_warns_grouped():
    conn = sqlite3.connect("moderation.db")
    rows = conn.execute(
        "SELECT user_id, username, COUNT(*) FROM warns GROUP BY user_id ORDER BY COUNT(*) DESC"
    ).fetchall()
    conn.close()
    return rows


# ── Levels ──
def db_get_user_exp(user_id: int) -> tuple[int, str, str]:
    conn = sqlite3.connect("moderation.db")
    row = conn.execute(
        "SELECT exp, username, full_name FROM levels WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if row:
        return row[0], row[1] or "", row[2] or ""
    return 0, "", ""

def db_set_user_exp(user_id: int, username: str, full_name: str, exp: int):
    exp = max(0, exp)
    conn = sqlite3.connect("moderation.db")
    conn.execute(
        "INSERT OR REPLACE INTO levels (user_id, username, full_name, exp) VALUES (?, ?, ?, ?)",
        (user_id, username, full_name, exp)
    )
    conn.commit()
    conn.close()

def db_add_exp(user_id: int, username: str, full_name: str, amount: int) -> tuple[int, int]:
    old_exp, old_uname, old_fname = db_get_user_exp(user_id)
    new_exp = max(0, old_exp + amount)
    db_set_user_exp(user_id, username or old_uname, full_name or old_fname, new_exp)
    old_level, _, _ = calculate_level(old_exp)
    new_level, _, _ = calculate_level(new_exp)
    return old_level, new_level

def db_get_top(limit: int = 10) -> list:
    conn = sqlite3.connect("moderation.db")
    rows = conn.execute(
        "SELECT user_id, username, full_name, exp FROM levels ORDER BY exp DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return rows


# ── Mod Logs ──
def db_add_log(action: str, admin_id: int, admin_name: str, target_id: int, target_name: str, reason: str):
    conn = sqlite3.connect("moderation.db")
    conn.execute(
        "INSERT INTO mod_logs (action, admin_id, admin_name, target_id, target_name, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (action, admin_id, admin_name, target_id, target_name, reason, int(time.time()))
    )
    conn.commit()
    conn.close()

def db_get_logs(limit: int = 20):
    conn = sqlite3.connect("moderation.db")
    rows = conn.execute(
        "SELECT action, admin_name, target_name, reason, created_at FROM mod_logs ORDER BY created_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return rows

def db_get_history(user_id: int):
    conn = sqlite3.connect("moderation.db")
    rows = conn.execute(
        "SELECT action, admin_name, reason, created_at FROM mod_logs WHERE target_id = ? ORDER BY created_at DESC LIMIT 20",
        (user_id,)
    ).fetchall()
    conn.close()
    return rows


# ── Daily Bonus ──
def db_get_last_daily(user_id: int) -> int:
    conn = sqlite3.connect("moderation.db")
    row = conn.execute("SELECT last_claim FROM daily_bonus WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row else 0

def db_set_last_daily(user_id: int):
    conn = sqlite3.connect("moderation.db")
    conn.execute(
        "INSERT OR REPLACE INTO daily_bonus (user_id, last_claim) VALUES (?, ?)",
        (user_id, int(time.time()))
    )
    conn.commit()
    conn.close()


# ── Activity Log ──
def db_log_activity(user_id: int, username: str, full_name: str):
    conn = sqlite3.connect("moderation.db")
    conn.execute(
        "INSERT INTO activity_log (user_id, username, full_name, ts) VALUES (?, ?, ?, ?)",
        (user_id, username, full_name, int(time.time()))
    )
    conn.commit()
    conn.close()

def db_get_week_top(limit: int = 10) -> list:
    since = int(time.time()) - 7 * 86400
    conn = sqlite3.connect("moderation.db")
    rows = conn.execute(
        """SELECT user_id, username, full_name, COUNT(*) as msgs
           FROM activity_log
           WHERE ts >= ?
           GROUP BY user_id
           ORDER BY msgs DESC
           LIMIT ?""",
        (since, limit)
    ).fetchall()
    conn.close()
    return rows


# ─────────────────────────────────────────────
# Утилиты
# ─────────────────────────────────────────────

def fmt_time_left(until: int | None) -> str:
    if until is None:
        return "навсегда"
    now = int(time.time())
    left = until - now
    if left <= 0:
        return "истёк"
    h, rem = divmod(left, 3600)
    m = rem // 60
    if h > 0:
        return f"{h}ч {m}м"
    return f"{m}м"

def is_allowed(message: Message) -> bool:
    if message.sender_chat:
        return True
    if not message.from_user:
        return False
    if message.from_user.id in ALLOWED_USERS:
        return True
    if message.from_user.username and message.from_user.username in ALLOWED_USERNAMES:
        return True
    return False

def is_private(message: Message) -> bool:
    return message.chat.type == "private"

def is_allowed_chat(message: Message) -> bool:
    return message.chat.id == ALLOWED_CHAT_ID or is_private(message)

def format_display(name: str) -> str:
    return f"@{name}" if not name.lstrip('-').isdigit() else f"id{name}"

def format_duration(seconds: int) -> str:
    if seconds >= 86400:
        return f"{seconds // 86400} д"
    if seconds >= 3600:
        return f"{seconds // 3600} ч"
    return f"{seconds // 60} м"

async def notify_user(user_id: int, text: str):
    try:
        await bot.send_message(user_id, text, parse_mode="HTML")
    except Exception:
        pass

def admin_display(message: Message) -> str:
    if message.from_user:
        u = message.from_user
        return f"@{u.username}" if u.username else u.full_name or str(u.id)
    return "admin"


# ─────────────────────────────────────────────
# Авто-выход из чужих групп
# ─────────────────────────────────────────────

@dp.my_chat_member()
async def on_bot_added(event: ChatMemberUpdated):
    if event.chat.id != ALLOWED_CHAT_ID and event.chat.type in ("group", "supergroup", "channel"):
        if event.new_chat_member.status in ("member", "administrator"):
            try:
                await bot.send_message(event.chat.id, "❌ Я работаю только в одной группе. До свидания!")
            except Exception:
                pass
            await bot.leave_chat(event.chat.id)


# ─────────────────────────────────────────────
# Удаление системных сообщений
# ─────────────────────────────────────────────

@dp.message(F.content_type.in_({
    ContentType.NEW_CHAT_MEMBERS,
    ContentType.LEFT_CHAT_MEMBER,
    ContentType.NEW_CHAT_TITLE,
    ContentType.NEW_CHAT_PHOTO,
    ContentType.DELETE_CHAT_PHOTO,
    ContentType.GROUP_CHAT_CREATED,
    ContentType.PINNED_MESSAGE,
}))
async def delete_service_messages(message: Message):
    try:
        await message.delete()
    except Exception:
        pass


# ─────────────────────────────────────────────
# Антиспам + фильтр слов + лимит букв + EXP
# ─────────────────────────────────────────────

NO_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_media_messages=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
)


@dp.message(F.chat.id == ALLOWED_CHAT_ID, F.text, ~F.text.startswith("/"))
async def auto_filter(message: Message):
    global antilink_enabled
    if not message.from_user:
        return
    is_admin = (
        message.from_user.id in ALLOWED_USERS or
        (message.from_user.username and message.from_user.username in ALLOWED_USERNAMES)
    )

    user_id = message.from_user.id
    username = message.from_user.username or str(user_id)
    full_name = message.from_user.full_name or ""
    name = f"@{username}" if message.from_user.username else full_name
    now = time.time()

    if not is_admin:
        text_lower = message.text.lower()

        # Запретные слова — мут 5 часов
        if any(re.search(r'(?<![а-яёa-z])' + re.escape(word) + r'(?![а-яёa-z])', text_lower) for word in BANNED_WORDS):
            mute_5h = 5 * 3600
            until_5h = message.date + timedelta(seconds=mute_5h)
            try:
                await message.delete()
                await bot.restrict_chat_member(chat_id=message.chat.id, user_id=user_id, permissions=NO_PERMS, until_date=until_5h)
                db_add_mute(user_id, username, int(until_5h.timestamp()), "запрещённое слово")
                db_add_log("mute", 0, "bot", user_id, username, "запрещённое слово")
                await message.answer(f"🔇 <b>{name}</b> замьючен на 5 ч\n📌 Причина: запрещённое слово", parse_mode="HTML")
                await notify_user(user_id, f"🔇 Тебя замьютили на <b>5 ч</b>\n📌 Причина: использование запрещённого слова")
            except Exception:
                pass
            return

        # Антилинк
        if antilink_enabled and LINK_PATTERN.search(message.text):
            until_1h = message.date + timedelta(hours=1)
            try:
                await message.delete()
                await bot.restrict_chat_member(chat_id=message.chat.id, user_id=user_id, permissions=NO_PERMS, until_date=until_1h)
                db_add_mute(user_id, username, int(until_1h.timestamp()), "отправка ссылки")
                db_add_log("mute", 0, "bot", user_id, username, "отправка ссылки")
                await message.answer(f"🔗 <b>{name}</b> замьючен на 1 ч\n📌 Причина: отправка ссылок запрещена", parse_mode="HTML")
                await notify_user(user_id, "🔗 Тебя замьютили на <b>1 ч</b>\n📌 Причина: отправка ссылок запрещена")
            except Exception:
                pass
            return

        # Лимит символов — мут 1 час
        if len(message.text) > MAX_MESSAGE_LENGTH:
            until_1h = message.date + timedelta(hours=1)
            try:
                await message.delete()
                await bot.restrict_chat_member(chat_id=message.chat.id, user_id=user_id, permissions=NO_PERMS, until_date=until_1h)
                db_add_mute(user_id, username, int(until_1h.timestamp()), f"превышен лимит {MAX_MESSAGE_LENGTH} символов")
                await message.answer(f"🔇 <b>{name}</b> замьючен на 1 ч\n📌 Причина: сообщение превышает {MAX_MESSAGE_LENGTH} символов", parse_mode="HTML")
                await notify_user(user_id, f"🔇 Тебя замьютили на <b>1 ч</b>\n📌 Причина: сообщение превышает {MAX_MESSAGE_LENGTH} символов")
            except Exception:
                pass
            return

        # Спам — мут 1 час
        spam_tracker[user_id] = [t for t in spam_tracker[user_id] if now - t < SPAM_WINDOW]
        spam_tracker[user_id].append(now)
        if len(spam_tracker[user_id]) >= SPAM_MAX_MESSAGES:
            spam_tracker[user_id].clear()
            until_1h = message.date + timedelta(hours=1)
            try:
                await message.delete()
                await bot.restrict_chat_member(chat_id=message.chat.id, user_id=user_id, permissions=NO_PERMS, until_date=until_1h)
                db_add_mute(user_id, username, int(until_1h.timestamp()), "спам")
                db_add_log("mute", 0, "bot", user_id, username, "спам")
                await message.answer(f"🔇 <b>{name}</b> замьючен на 1 ч\n📌 Причина: спам", parse_mode="HTML")
                await notify_user(user_id, f"🔇 Тебя замьютили на <b>1 ч</b>\n📌 Причина: спам")
            except Exception:
                pass
            return

    # ── Начисление опыта + логирование активности ──
    last_exp = exp_cooldown_tracker.get(user_id, 0)
    if now - last_exp >= EXP_COOLDOWN:
        exp_gain = random.randint(EXP_PER_MESSAGE_MIN, EXP_PER_MESSAGE_MAX)
        old_lvl, new_lvl = db_add_exp(user_id, username, full_name, exp_gain)
        exp_cooldown_tracker[user_id] = now
        db_log_activity(user_id, username, full_name)
        if new_lvl > old_lvl:
            lvl_name = get_level_name(new_lvl)
            await message.answer(
                f"🎉 <b>{name}</b> достиг нового уровня!\n"
                f"✨ Уровень <b>{new_lvl}</b> — {lvl_name}",
                parse_mode="HTML"
            )


# ─────────────────────────────────────────────
# Вспомогательные функции парсинга
# ─────────────────────────────────────────────

def parse_args(text: str):
    parts = text.strip().split(maxsplit=3)
    if len(parts) < 3:
        return None
    target = parts[0].lstrip("@")
    try:
        amount = int(parts[1])
    except ValueError:
        return None
    unit = parts[2].lower()
    if unit in ("ч", "h", "час", "часов"):
        seconds = amount * 3600
    elif unit in ("м", "m", "мин", "минут"):
        seconds = amount * 60
    elif unit in ("д", "d", "день", "дней"):
        seconds = amount * 86400
    else:
        return None
    reason = parts[3] if len(parts) > 3 else "не указана"
    return target, seconds, reason

def parse_target_only(text: str):
    return text.strip().lstrip("@") or None

async def resolve_target(chat_id: int, target: str, reply_message: Message = None):
    if reply_message:
        u = reply_message.from_user
        if u:
            return u.id, u.username or str(u.id)
    if not target:
        return None, None
    if target.lstrip("-").isdigit():
        user_id = int(target)
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            name = member.user.username or str(user_id)
            return user_id, name
        except Exception:
            return user_id, str(user_id)
    try:
        member = await bot.get_chat_member(chat_id, f"@{target}")
        return member.user.id, target
    except Exception:
        return None, target

async def parse_mute_ban_args(message: Message):
    args_text = message.text.partition(" ")[2].strip()
    if message.reply_to_message:
        parts = args_text.split(maxsplit=2)
        if len(parts) < 2:
            return None, None, None, None
        try:
            amount = int(parts[0])
        except ValueError:
            return None, None, None, None
        unit = parts[1].lower()
        if unit in ("ч", "h", "час", "часов"):
            seconds = amount * 3600
        elif unit in ("м", "m", "мин", "минут"):
            seconds = amount * 60
        elif unit in ("д", "d", "день", "дней"):
            seconds = amount * 86400
        else:
            return None, None, None, None
        reason = parts[2] if len(parts) > 2 else "не указана"
        user_id, name = await resolve_target(message.chat.id, None, message.reply_to_message)
    else:
        parsed = parse_args(args_text)
        if not parsed:
            return None, None, None, None
        target, seconds, reason = parsed
        user_id, name = await resolve_target(message.chat.id, target)
    return user_id, name, seconds, reason


async def resolve_level_target(message: Message) -> tuple[int | None, str, str]:
    args_text = message.text.partition(" ")[2].strip()
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return u.id, u.username or str(u.id), u.full_name or ""
    if args_text:
        target = args_text.lstrip("@")
        uid, uname = await resolve_target(message.chat.id, target)
        return uid, uname or target, ""
    if message.from_user:
        u = message.from_user
        return u.id, u.username or str(u.id), u.full_name or ""
    return None, "", ""


async def parse_admin_level_args(message: Message) -> tuple[int | None, str, str, int | None]:
    parts = message.text.strip().split()
    arg_parts = parts[2:]
    if message.reply_to_message and message.reply_to_message.from_user:
        if not arg_parts:
            return None, "", "", None
        try:
            amount = int(arg_parts[0])
        except ValueError:
            return None, "", "", None
        u = message.reply_to_message.from_user
        return u.id, u.username or str(u.id), u.full_name or "", amount
    else:
        if len(arg_parts) < 2:
            return None, "", "", None
        target = arg_parts[0].lstrip("@")
        try:
            amount = int(arg_parts[1])
        except ValueError:
            return None, "", "", None
        uid, uname = await resolve_target(message.chat.id, target)
        return uid, uname or target, "", amount


# ─────────────────────────────────────────────
# Приветствие
# ─────────────────────────────────────────────

@dp.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def welcome_new_member(event: ChatMemberUpdated):
    global antiraid_enabled, recent_joins
    if event.chat.id != ALLOWED_CHAT_ID:
        return

    # Антирейд
    if antiraid_enabled:
        now = time.time()
        recent_joins = [t for t in recent_joins if now - t < ANTIRAID_JOIN_WINDOW]
        recent_joins.append(now)
        if len(recent_joins) >= ANTIRAID_JOIN_LIMIT:
            u = event.new_chat_member.user
            try:
                await bot.ban_chat_member(chat_id=event.chat.id, user_id=u.id)
                await bot.send_message(
                    event.chat.id,
                    f"🛡️ <b>Антирейд:</b> обнаружена подозрительная активность! "
                    f"Пользователь <b>{u.full_name}</b> заблокирован.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            return

    u = event.new_chat_member.user
    name = f"@{u.username}" if u.username else u.full_name
    await bot.send_message(
        event.chat.id,
        f"👋 Привет {name}! Добро пожаловать в группу!\n"
        f"Я брат Дака, соблюдай правила, приятного общения!\n\n"
        f"/rules — чтобы посмотреть правила\n"
        f"/report причина — чтобы сделать репорт\n"
        f"/ранг — твой текущий уровень и опыт",
        parse_mode="HTML",
    )

@dp.chat_member(ChatMemberUpdatedFilter(LEAVE_TRANSITION))
async def farewell_member(event: ChatMemberUpdated):
    if event.chat.id != ALLOWED_CHAT_ID:
        return
    u = event.old_chat_member.user
    if u.is_bot:
        return
    name = f"@{u.username}" if u.username else u.full_name
    await bot.send_message(
        event.chat.id,
        f"😭 {name} нам очень жаль что вам не понравилось общение с нами, прощайте!",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────
# /-чат и /+чат
# ─────────────────────────────────────────────

@dp.message(Command("-чат"))
async def cmd_close_chat(message: Message):
    global chat_locked
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    chat_locked = True
    no_send = ChatPermissions(
        can_send_messages=False,
        can_send_media_messages=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
    )
    try:
        await bot.set_chat_permissions(message.chat.id, no_send)
        await message.reply("🔒 Чат закрыт. Писать могут только администраторы.")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

@dp.message(Command("+чат"))
async def cmd_open_chat(message: Message):
    global chat_locked
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    chat_locked = False
    full_perms = ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )
    try:
        await bot.set_chat_permissions(message.chat.id, full_perms)
        await message.reply("🔓 Чат открыт. Все участники могут писать.")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# ─────────────────────────────────────────────
# /rules
# ─────────────────────────────────────────────

@dp.message(Command("rules"))
async def cmd_rules(message: Message):
    if not is_allowed_chat(message):
        return
    await message.reply(RULES_TEXT, parse_mode="HTML")


# ─────────────────────────────────────────────
# /help
# ─────────────────────────────────────────────

@dp.message(Command("help"))
async def cmd_help(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    await message.reply(
        "📋 <b>Список команд:</b>\n\n"
        "📜 <b>/rules</b> — правила чата\n"
        "🚨 <b>/report</b> текст — репорт модераторам\n"
        "👻 <b>/activ</b> — анонимно в канал (личка)\n"
        "🆔 <b>/id</b> — узнать ID\n\n"
        "🏅 <b>/ранг</b> [@ник] — уровень и опыт\n"
        "📊 <b>/profile</b> — твой профиль\n"
        "🏆 <b>/leader</b> — топ-30\n"
        "🔥 <b>/leaderweek</b> — топ активных за 7 дней\n"
        "🎁 <b>/daily</b> — ежедневный бонус XP\n"
        "🎲 <b>/roll</b> — случайное число 1–100\n"
        "😂 <b>/joke</b> — рандомная шутка\n\n"
        "🔇 <b>/mute</b> @ник 30 м [причина]\n"
        "🔊 <b>/unmute</b> @ник\n"
        "🔨 <b>/ban</b> @ник 24 ч [причина]\n"
        "🔨 <b>/ban</b> @ник perm [причина] — навсегда\n"
        "✅ <b>/unban</b> @ник\n"
        "👢 <b>/kick</b> @ник [причина]\n"
        "⚠️ <b>/warn</b> @ник [причина]\n"
        "🚫 <b>/ro</b> @ник [время] [причина]\n"
        "🧹 <b>/clear</b> <кол-во> — удалить сообщения\n"
        "🔇 <b>/muteall</b> 10м — заблокировать всех\n\n"
        "📊 <b>/activmute</b> — кто в муте\n"
        "📊 <b>/activban</b> — кто в бане\n"
        "📊 <b>/activwarns</b> @ник — варны пользователя\n"
        "👮 <b>/whois</b> @ник — полная инфа о юзере\n"
        "🧾 <b>/history</b> @ник — история наказаний\n"
        "📜 <b>/logs</b> — последние действия бота\n\n"
        "🛡️ <b>/antiraid on/off</b> — защита от рейда\n"
        "🔗 <b>/antilink on/off</b> — блок ссылок\n"
        "🔍 <b>/scan</b> — проверить чат на спам\n"
        "⚙️ <b>/settings</b> — настройки чата\n\n"
        "🛠 <b>Только для админов:</b>\n"
        "➕ <b>/add lvl</b> @ник (число) — добавить уровни\n"
        "➕ <b>/add exp</b> @ник (число) — добавить опыт\n"
        "⭐ <b>/setxp</b> @ник (число) — установить XP\n"
        "➖ <b>/fine lvl</b> @ник (число) — снять уровни\n"
        "➖ <b>/fine exp</b> @ник (число) — снять опыт\n\n"
        "💡 Все команды работают через ответ на сообщение",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────
# /activ
# ─────────────────────────────────────────────

@dp.message(Command("activ"))
async def cmd_activ(message: Message):
    if not is_private(message):
        return await message.reply("💡 Эта команда работает только в личке с ботом.")
    if not message.from_user or message.from_user.id not in ALLOWED_USERS:
        return
    waiting_for_anon.add(message.from_user.id)
    await message.answer("👻 Режим невидимки активирован!\nОтправь сообщение, стикер или голосовое — оно уйдёт в канал анонимно.")


# ─────────────────────────────────────────────
# /report
# ─────────────────────────────────────────────

@dp.message(Command("report"))
async def cmd_report(message: Message):
    if not is_allowed_chat(message):
        return
    report_text = message.text.partition(" ")[2].strip()
    if not report_text:
        return await message.reply("⚠️ Напиши причину репорта: /report <текст>")

    u = message.from_user
    sender_name = f"@{u.username}" if u and u.username else (u.full_name if u else "Аноним")
    sender_id = u.id if u else "неизвестен"
    chat_name = message.chat.title or str(message.chat.id)

    report_msg = (
        f"🚨 <b>Новый репорт!</b>\n\n"
        f"👤 От: <b>{sender_name}</b> (ID: <code>{sender_id}</code>)\n"
        f"💬 Чат: <b>{chat_name}</b>\n"
        f"📝 Причина: {report_text}"
    )

    if message.reply_to_message and message.reply_to_message.from_user:
        ru = message.reply_to_message.from_user
        reported_name = f"@{ru.username}" if ru.username else ru.full_name
        report_msg += f"\n\n🎯 Жалоба на: <b>{reported_name}</b> (ID: <code>{ru.id}</code>)"

    sent = False
    for mod_id in REPORT_RECEIVERS:
        try:
            await bot.send_message(mod_id, report_msg, parse_mode="HTML")
            if message.reply_to_message:
                await bot.forward_message(chat_id=mod_id, from_chat_id=message.chat.id, message_id=message.reply_to_message.message_id)
            sent = True
        except Exception:
            pass

    await message.reply("✅ Репорт отправлен модераторам!" if sent else "❌ Не удалось отправить репорт.")


# ─────────────────────────────────────────────
# /id
# ─────────────────────────────────────────────

@dp.message(Command("id"))
async def cmd_id(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        name = f"@{u.username}" if u.username else u.full_name
        await message.reply(f"👤 {name}\n🆔 ID: <code>{u.id}</code>", parse_mode="HTML")
    elif message.from_user:
        u = message.from_user
        name = f"@{u.username}" if u.username else u.full_name
        await message.reply(f"👤 Твой ID:\n🆔 <code>{u.id}</code>", parse_mode="HTML")


# ─────────────────────────────────────────────
# /ранг — для всех
# ─────────────────────────────────────────────

@dp.message(Command("ранг"))
async def cmd_rank(message: Message):
    if not is_allowed_chat(message):
        return

    user_id, username, full_name = await resolve_level_target(message)
    if not user_id:
        return await message.reply("❌ Пользователь не найден.")

    exp, db_username, db_fullname = db_get_user_exp(user_id)
    uname = username or db_username
    fname = full_name or db_fullname
    if uname and not uname.lstrip('-').isdigit():
        display_name = f"@{uname}"
    elif fname:
        display_name = fname
    else:
        display_name = f"id{user_id}"

    level, current_in_level, need_next = calculate_level(exp)
    lvl_name = get_level_name(level)
    bar = make_progress_bar(current_in_level, need_next)

    top = db_get_top(limit=1000)
    rank_pos = next((i + 1 for i, row in enumerate(top) if row[0] == user_id), None)
    rank_text = f"🏆 Место в топе: <b>#{rank_pos}</b>\n" if rank_pos else ""

    await message.reply(
        f"🏅 <b>Ранг {display_name}</b>\n\n"
        f"⭐ Уровень: <b>{level}</b> — {lvl_name}\n"
        f"✨ Опыт: <b>{exp}</b> (всего)\n"
        f"📊 Прогресс: {bar} <b>{current_in_level}/{need_next}</b>\n"
        f"{rank_text}",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
# /profile — профиль пользователя (для всех)
# ─────────────────────────────────────────────

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    if not is_allowed_chat(message):
        return
    if not message.from_user:
        return

    u = message.from_user
    user_id = u.id
    username = u.username or str(user_id)
    full_name = u.full_name or ""
    display = f"@{username}" if u.username else full_name

    exp, _, _ = db_get_user_exp(user_id)
    level, current_in, need_next = calculate_level(exp)
    lvl_name = get_level_name(level)
    bar = make_progress_bar(current_in, need_next)

    warns = db_get_warns(user_id)
    muted, mute_until = db_is_muted(user_id)
    banned, ban_until = db_is_banned(user_id)

    top = db_get_top(limit=1000)
    rank_pos = next((i + 1 for i, row in enumerate(top) if row[0] == user_id), None)

    # Активность за неделю
    since = int(time.time()) - 7 * 86400
    conn = sqlite3.connect("moderation.db")
    week_msgs = conn.execute(
        "SELECT COUNT(*) FROM activity_log WHERE user_id = ? AND ts >= ?", (user_id, since)
    ).fetchone()[0]
    conn.close()

    mute_status = f"🔇 До {fmt_time_left(mute_until)}" if muted else "✅ Нет"
    ban_status = f"🔨 До {fmt_time_left(ban_until)}" if banned else "✅ Нет"

    await message.reply(
        f"👤 <b>Профиль {display}</b>\n"
        f"🆔 ID: <code>{user_id}</code>\n\n"
        f"⭐ Уровень: <b>{level}</b> — {lvl_name}\n"
        f"✨ Опыт: <b>{exp}</b>\n"
        f"📊 Прогресс: {bar} <b>{current_in}/{need_next}</b>\n"
        f"🏆 Место в топе: <b>#{rank_pos or '—'}</b>\n\n"
        f"💬 Сообщений за неделю: <b>{week_msgs}</b>\n"
        f"⚠️ Варны: <b>{len(warns)}</b>\n"
        f"🔇 Мут: {mute_status}\n"
        f"🔨 Бан: {ban_status}",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
# /leader — топ-30
# ─────────────────────────────────────────────

@dp.message(Command("leader"))
async def cmd_leader(message: Message):
    if not is_allowed_chat(message):
        return

    top = db_get_top(limit=30)
    if not top:
        return await message.reply("📊 Таблица лидеров пока пуста. Пиши в чат, чтобы получить опыт!")

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["🏆 <b>ТОП-30 ЛИДЕРОВ ЧАТА</b>\n"]

    for i, (user_id, username, full_name, exp) in enumerate(top, start=1):
        level, current_in, need_next = calculate_level(exp)
        lvl_name = get_level_name(level)
        if username and not username.lstrip('-').isdigit():
            display = f"@{username}"
        elif full_name:
            display = full_name
        else:
            display = f"id{user_id}"
        medal = medals.get(i, f"{i}.")
        lines.append(
            f"{medal} <b>{display}</b>\n"
            f"   ⭐ Ур. {level} — {lvl_name}  |  ✨ {exp} exp\n"
        )

    if message.from_user:
        uid = message.from_user.id
        in_top = any(row[0] == uid for row in top)
        if not in_top:
            all_top = db_get_top(limit=1000)
            my_pos = next((i + 1 for i, row in enumerate(all_top) if row[0] == uid), None)
            if my_pos:
                my_exp, _, _ = db_get_user_exp(uid)
                my_level, _, _ = calculate_level(my_exp)
                lines.append(f"\n─────────────────\n📍 Ты на месте #{my_pos}  |  ⭐ Ур. {my_level}  |  ✨ {my_exp} exp")

    await message.reply("\n".join(lines), parse_mode="HTML")


# ─────────────────────────────────────────────
# /leaderweek — топ активных за 7 дней
# ─────────────────────────────────────────────

@dp.message(Command("leaderweek"))
async def cmd_leaderweek(message: Message):
    if not is_allowed_chat(message):
        return

    top = db_get_week_top(limit=10)
    if not top:
        return await message.reply("📊 За последние 7 дней активности не зафиксировано.")

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["🔥 <b>ТОП-10 АКТИВНЫХ ЗА 7 ДНЕЙ</b>\n"]

    for i, (user_id, username, full_name, msgs) in enumerate(top, start=1):
        if username and not username.lstrip('-').isdigit():
            display = f"@{username}"
        elif full_name:
            display = full_name
        else:
            display = f"id{user_id}"
        medal = medals.get(i, f"{i}.")
        lines.append(f"{medal} <b>{display}</b> — 💬 {msgs} сообщ.\n")

    await message.reply("\n".join(lines), parse_mode="HTML")


# ─────────────────────────────────────────────
# /daily — ежедневный бонус XP
# ─────────────────────────────────────────────

@dp.message(Command("daily"))
async def cmd_daily(message: Message):
    if not is_allowed_chat(message):
        return
    if not message.from_user:
        return

    u = message.from_user
    user_id = u.id
    username = u.username or str(user_id)
    full_name = u.full_name or ""
    display = f"@{username}" if u.username else full_name

    last = db_get_last_daily(user_id)
    now = int(time.time())
    cooldown = 86400  # 24 часа

    if now - last < cooldown:
        remaining = cooldown - (now - last)
        h, rem = divmod(remaining, 3600)
        m = rem // 60
        return await message.reply(
            f"⏳ <b>{display}</b>, следующий бонус через <b>{h}ч {m}м</b>!",
            parse_mode="HTML"
        )

    bonus = random.randint(DAILY_BONUS_MIN, DAILY_BONUS_MAX)
    old_lvl, new_lvl = db_add_exp(user_id, username, full_name, bonus)
    db_set_last_daily(user_id)

    exp, _, _ = db_get_user_exp(user_id)
    level, current_in, need_next = calculate_level(exp)
    lvl_name = get_level_name(level)

    text = (
        f"🎁 <b>{display}</b>, ты забрал ежедневный бонус!\n"
        f"✨ +{bonus} XP\n"
        f"⭐ Уровень: <b>{level}</b> — {lvl_name}"
    )
    if new_lvl > old_lvl:
        text += f"\n🎉 Новый уровень! <b>{new_lvl}</b> — {get_level_name(new_lvl)}"

    await message.reply(text, parse_mode="HTML")


# ─────────────────────────────────────────────
# /setxp — установить XP вручную (только админы)
# ─────────────────────────────────────────────

@dp.message(Command("setxp"))
async def cmd_setxp(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return

    args_text = message.text.partition(" ")[2].strip()

    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        uid = u.id
        uname = u.username or str(u.id)
        fname = u.full_name or ""
        try:
            amount = int(args_text)
        except ValueError:
            return await message.reply("⚠️ Укажи количество XP: /setxp <число> (в ответ на сообщение)")
    else:
        parts = args_text.split(maxsplit=1)
        if len(parts) < 2:
            return await message.reply("⚠️ Использование: /setxp @ник <число>")
        target = parts[0].lstrip("@")
        try:
            amount = int(parts[1])
        except ValueError:
            return await message.reply("❌ Число XP должно быть целым.")
        uid, uname = await resolve_target(message.chat.id, target)
        if not uid:
            return await message.reply("❌ Пользователь не найден.")
        fname = ""

    if amount < 0:
        return await message.reply("❌ XP не может быть отрицательным.")

    old_exp, db_uname, db_fname = db_get_user_exp(uid)
    db_set_user_exp(uid, uname or db_uname, fname or db_fname, amount)
    level, _, _ = calculate_level(amount)
    display = f"@{uname}" if uname and not uname.lstrip('-').isdigit() else f"id{uid}"

    await message.reply(
        f"⭐ <b>{display}</b>: XP установлен в <b>{amount}</b>\n"
        f"⭐ Уровень: <b>{level}</b> — {get_level_name(level)}",
        parse_mode="HTML"
    )
    await notify_user(uid, f"⭐ Твой XP установлен на <b>{amount}</b>\n⭐ Уровень: <b>{level}</b> — {get_level_name(level)}")


# ─────────────────────────────────────────────
# /roll — случайное число
# ─────────────────────────────────────────────

@dp.message(Command("roll"))
async def cmd_roll(message: Message):
    if not is_allowed_chat(message):
        return
    n = random.randint(1, 100)
    name = ""
    if message.from_user:
        u = message.from_user
        name = f"@{u.username}" if u.username else u.full_name
    await message.reply(f"🎲 <b>{name}</b> бросил кубик: <b>{n}</b> из 100!", parse_mode="HTML")


# ─────────────────────────────────────────────
# /joke — рандомная шутка
# ─────────────────────────────────────────────

@dp.message(Command("joke"))
async def cmd_joke(message: Message):
    if not is_allowed_chat(message):
        return
    joke = random.choice(JOKES)
    await message.reply(f"😂 {joke}")


# ─────────────────────────────────────────────
# /antiraid on/off
# ─────────────────────────────────────────────

@dp.message(Command("antiraid"))
async def cmd_antiraid(message: Message):
    global antiraid_enabled
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    arg = message.text.partition(" ")[2].strip().lower()
    if arg == "on":
        antiraid_enabled = True
        await message.reply("🛡️ Антирейд <b>включён</b>. Подозрительные аккаунты будут блокироваться.", parse_mode="HTML")
    elif arg == "off":
        antiraid_enabled = False
        await message.reply("🛡️ Антирейд <b>выключен</b>.", parse_mode="HTML")
    else:
        status = "включён 🟢" if antiraid_enabled else "выключен 🔴"
        await message.reply(f"🛡️ Антирейд сейчас <b>{status}</b>\nИспользование: /antiraid on/off", parse_mode="HTML")


# ─────────────────────────────────────────────
# /antilink on/off
# ─────────────────────────────────────────────

@dp.message(Command("antilink"))
async def cmd_antilink(message: Message):
    global antilink_enabled
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    arg = message.text.partition(" ")[2].strip().lower()
    if arg == "on":
        antilink_enabled = True
        await message.reply("🔗 Антилинк <b>включён</b>. Ссылки будут удаляться.", parse_mode="HTML")
    elif arg == "off":
        antilink_enabled = False
        await message.reply("🔗 Антилинк <b>выключен</b>.", parse_mode="HTML")
    else:
        status = "включён 🟢" if antilink_enabled else "выключен 🔴"
        await message.reply(f"🔗 Антилинк сейчас <b>{status}</b>\nИспользование: /antilink on/off", parse_mode="HTML")


# ─────────────────────────────────────────────
# /clear <кол-во> — удалить последние N сообщений
# ─────────────────────────────────────────────

@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    args = message.text.partition(" ")[2].strip()
    try:
        count = int(args)
        if count < 1 or count > 100:
            raise ValueError
    except ValueError:
        return await message.reply("⚠️ Укажи количество сообщений от 1 до 100: /clear <число>")

    deleted = 0
    msg_id = message.message_id
    for i in range(1, count + 2):
        try:
            await bot.delete_message(message.chat.id, msg_id - i)
            deleted += 1
        except Exception:
            pass

    try:
        await message.delete()
    except Exception:
        pass

    confirm = await bot.send_message(message.chat.id, f"🧹 Удалено <b>{deleted}</b> сообщений.", parse_mode="HTML")
    await asyncio.sleep(3)
    try:
        await confirm.delete()
    except Exception:
        pass


# ─────────────────────────────────────────────
# /muteall <время> — временный lockdown
# ─────────────────────────────────────────────

@dp.message(Command("muteall"))
async def cmd_muteall(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return

    args = message.text.partition(" ")[2].strip().split()
    seconds = 0
    if args:
        try:
            amount = int(args[0][:-1])
            unit = args[0][-1].lower()
            if unit == "м":
                seconds = amount * 60
            elif unit == "ч":
                seconds = amount * 3600
            elif unit == "д":
                seconds = amount * 86400
        except (ValueError, IndexError):
            return await message.reply("⚠️ Использование: /muteall 10м | /muteall 2ч | /muteall 1д")

    no_send = ChatPermissions(
        can_send_messages=False,
        can_send_media_messages=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
    )

    try:
        await bot.set_chat_permissions(message.chat.id, no_send)
        duration_text = format_duration(seconds) if seconds else "до ручного снятия"
        await message.reply(f"🔇 <b>Lockdown активирован</b> на {duration_text}.\nВсем запрещено писать.", parse_mode="HTML")

        if seconds:
            await asyncio.sleep(seconds)
            full_perms = ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            )
            await bot.set_chat_permissions(message.chat.id, full_perms)
            await bot.send_message(message.chat.id, "🔓 Lockdown снят. Все могут писать.")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# ─────────────────────────────────────────────
# /scan — проверка чата на подозрительных
# ─────────────────────────────────────────────

@dp.message(Command("scan"))
async def cmd_scan(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return

    now = int(time.time())
    lines = ["🔍 <b>Результаты сканирования чата</b>\n"]

    # Активные муты
    mutes = db_get_mutes()
    active_mutes = [(uid, uname, until, r) for uid, uname, until, r in mutes if until is None or until > now]

    # Активные баны
    bans = db_get_bans()
    active_bans = [(uid, uname, until, r) for uid, uname, until, r in bans if until is None or until > now]

    # Многоварновые
    warns_grouped = db_get_all_warns_grouped()
    multi_warned = [(uid, uname, cnt) for uid, uname, cnt in warns_grouped if cnt >= 3]

    lines.append(f"🔇 Активных мутов: <b>{len(active_mutes)}</b>")
    lines.append(f"🔨 Активных банов: <b>{len(active_bans)}</b>")
    lines.append(f"⚠️ Пользователей с 3+ варнами: <b>{len(multi_warned)}</b>")

    if multi_warned:
        lines.append("\n👁 <b>Подозрительные (3+ варна):</b>")
        for uid, uname, cnt in multi_warned[:5]:
            display = f"@{uname}" if uname and not uname.lstrip('-').isdigit() else f"id{uid}"
            lines.append(f"• {display} — {cnt} варн(ов)")

    lines.append(f"\n✅ Сканирование завершено.")
    await message.reply("\n".join(lines), parse_mode="HTML")


# ─────────────────────────────────────────────
# /whois @user — полная инфа о пользователе
# ─────────────────────────────────────────────

@dp.message(Command("whois"))
async def cmd_whois(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return

    args_text = message.text.partition(" ")[2].strip()
    target = parse_target_only(args_text)
    chat_id = message.chat.id if not is_private(message) else ALLOWED_CHAT_ID
    user_id, name = await resolve_target(chat_id, target, message.reply_to_message)

    if not user_id:
        return await message.reply("❌ Укажи пользователя: /whois @ник или ID")

    exp, db_uname, db_fname = db_get_user_exp(user_id)
    level, current_in, need_next = calculate_level(exp)
    lvl_name = get_level_name(level)

    warns = db_get_warns(user_id)
    muted, mute_until = db_is_muted(user_id)
    banned, ban_until = db_is_banned(user_id)

    display_name = f"@{name}" if name and not name.lstrip('-').isdigit() else f"id{user_id}"
    mute_status = f"🔇 До {fmt_time_left(mute_until)}" if muted else "✅ Чист"
    ban_status = f"🔨 До {fmt_time_left(ban_until)}" if banned else "✅ Чист"

    # Место в топе
    top = db_get_top(limit=1000)
    rank_pos = next((i + 1 for i, row in enumerate(top) if row[0] == user_id), None)

    await message.reply(
        f"👮 <b>Информация о пользователе</b>\n\n"
        f"👤 Ник: <b>{display_name}</b>\n"
        f"🆔 ID: <code>{user_id}</code>\n\n"
        f"⭐ Уровень: <b>{level}</b> — {lvl_name}\n"
        f"✨ Опыт: <b>{exp}</b>\n"
        f"🏆 Место в топе: <b>#{rank_pos or '—'}</b>\n\n"
        f"⚠️ Варны: <b>{len(warns)}</b>\n"
        f"🔇 Мут: {mute_status}\n"
        f"🔨 Бан: {ban_status}",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
# /history @user — история наказаний
# ─────────────────────────────────────────────

@dp.message(Command("history"))
async def cmd_history(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return

    args_text = message.text.partition(" ")[2].strip()
    target = parse_target_only(args_text)
    chat_id = message.chat.id if not is_private(message) else ALLOWED_CHAT_ID
    user_id, name = await resolve_target(chat_id, target, message.reply_to_message)

    if not user_id:
        return await message.reply("❌ Укажи пользователя: /history @ник или ID")

    display = f"@{name}" if name and not name.lstrip('-').isdigit() else f"id{user_id}"
    history = db_get_history(user_id)

    if not history:
        return await message.reply(f"✅ У {display} нет истории наказаний.")

    lines = [f"🧾 <b>История наказаний {display}</b>\n"]
    action_icons = {"mute": "🔇", "ban": "🔨", "kick": "👢", "warn": "⚠️", "unmute": "🔊", "unban": "✅"}

    for action, admin_name, reason, ts in history:
        dt = datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
        icon = action_icons.get(action, "📌")
        lines.append(f"{icon} <b>{action.upper()}</b> — {dt}\n   📌 {reason} (от {admin_name})\n")

    await message.reply("\n".join(lines), parse_mode="HTML")


# ─────────────────────────────────────────────
# /logs — последние действия бота
# ─────────────────────────────────────────────

@dp.message(Command("logs"))
async def cmd_logs(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return

    logs = db_get_logs(limit=15)
    if not logs:
        return await message.reply("📜 Лог действий пуст.")

    action_icons = {"mute": "🔇", "ban": "🔨", "kick": "👢", "warn": "⚠️", "unmute": "🔊", "unban": "✅"}
    lines = ["📜 <b>Последние действия бота</b>\n"]

    for action, admin_name, target_name, reason, ts in logs:
        dt = datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")
        icon = action_icons.get(action, "📌")
        lines.append(f"{icon} <b>{action.upper()}</b> → {target_name} [{dt}]\n   📌 {reason} (адм: {admin_name})\n")

    await message.reply("\n".join(lines), parse_mode="HTML")


# ─────────────────────────────────────────────
# /settings — настройки чата
# ─────────────────────────────────────────────

@dp.message(Command("settings"))
async def cmd_settings(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return

    def st(val): return "🟢 Вкл" if val else "🔴 Выкл"

    await message.reply(
        f"⚙️ <b>Настройки чата</b>\n\n"
        f"🛡️ Антиспам: {st(settings['antispam'])}\n"
        f"🔗 Антилинк: {st(antilink_enabled)}\n"
        f"🚨 Антирейд: {st(antiraid_enabled)}\n"
        f"📏 Лимит символов: <b>{MAX_MESSAGE_LENGTH}</b>\n"
        f"🔒 Чат заблокирован: {st(chat_locked)}\n\n"
        f"💡 Управление:\n"
        f"/antiraid on/off\n"
        f"/antilink on/off\n"
        f"/-чат / /+чат",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
# /add lvl | /add exp — только для админов
# ─────────────────────────────────────────────

@dp.message(Command("add"))
async def cmd_add(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return

    parts = message.text.strip().split()
    if len(parts) < 2:
        return await message.reply(
            "⚠️ Использование:\n"
            "/add lvl @ник (число) — добавить уровни\n"
            "/add exp @ник (число) — добавить опыт"
        )

    subcmd = parts[1].lower()
    if subcmd not in ("lvl", "exp"):
        return await message.reply("❌ Укажи тип: <b>lvl</b> или <b>exp</b>", parse_mode="HTML")

    uid, username, full_name, amount = await parse_admin_level_args(message)
    if uid is None or amount is None:
        return await message.reply(
            f"⚠️ Использование:\n"
            f"/add {subcmd} @ник (число)\n"
            f"или ответь на сообщение: /add {subcmd} (число)"
        )
    if amount <= 0:
        return await message.reply("❌ Число должно быть больше 0.")

    display = f"@{username}" if username and not username.lstrip('-').isdigit() else f"id{uid}"
    old_exp, db_uname, db_fname = db_get_user_exp(uid)
    old_level, _, _ = calculate_level(old_exp)
    admin_name = admin_display(message)

    if subcmd == "lvl":
        target_level = old_level + amount
        target_exp = exp_for_level(target_level)
        exp_to_add = target_exp - old_exp
        db_add_exp(uid, username or db_uname, full_name or db_fname, exp_to_add)
        actual_exp, _, _ = db_get_user_exp(uid)
        new_level, _, _ = calculate_level(actual_exp)
        db_add_log("add_lvl", message.from_user.id if message.from_user else 0, admin_name, uid, username, f"+{amount} уровней")
        await message.reply(
            f"➕ <b>{display}</b>: +{amount} уровн(ей)\n"
            f"⭐ Новый уровень: <b>{new_level}</b> — {get_level_name(new_level)}\n"
            f"✨ Опыт: <b>{actual_exp}</b>",
            parse_mode="HTML"
        )
        await notify_user(uid, f"⬆️ Тебе добавили <b>{amount}</b> уровн(ей)!\n⭐ Твой уровень: <b>{new_level}</b> — {get_level_name(new_level)}")
    else:
        db_add_exp(uid, username or db_uname, full_name or db_fname, amount)
        actual_exp, _, _ = db_get_user_exp(uid)
        new_level, _, _ = calculate_level(actual_exp)
        db_add_log("add_exp", message.from_user.id if message.from_user else 0, admin_name, uid, username, f"+{amount} exp")
        await message.reply(
            f"➕ <b>{display}</b>: +{amount} опыта\n"
            f"⭐ Уровень: <b>{new_level}</b> — {get_level_name(new_level)}\n"
            f"✨ Опыт: <b>{actual_exp}</b>",
            parse_mode="HTML"
        )
        await notify_user(uid, f"⬆️ Тебе добавили <b>{amount}</b> опыта!\n⭐ Уровень: <b>{new_level}</b> — {get_level_name(new_level)}")


# ─────────────────────────────────────────────
# /fine lvl | /fine exp — только для админов
# ─────────────────────────────────────────────

@dp.message(Command("fine"))
async def cmd_fine(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return

    parts = message.text.strip().split()
    if len(parts) < 2:
        return await message.reply(
            "⚠️ Использование:\n"
            "/fine lvl @ник (число) — понизить уровень\n"
            "/fine exp @ник (число) — снять опыт"
        )

    subcmd = parts[1].lower()
    if subcmd not in ("lvl", "exp"):
        return await message.reply("❌ Укажи тип: <b>lvl</b> или <b>exp</b>", parse_mode="HTML")

    uid, username, full_name, amount = await parse_admin_level_args(message)
    if uid is None or amount is None:
        return await message.reply(
            f"⚠️ Использование:\n"
            f"/fine {subcmd} @ник (число)\n"
            f"или ответь на сообщение: /fine {subcmd} (число)"
        )
    if amount <= 0:
        return await message.reply("❌ Число должно быть больше 0.")

    display = f"@{username}" if username and not username.lstrip('-').isdigit() else f"id{uid}"
    old_exp, db_uname, db_fname = db_get_user_exp(uid)
    old_level, _, _ = calculate_level(old_exp)
    admin_name = admin_display(message)

    if subcmd == "lvl":
        target_level = max(0, old_level - amount)
        target_exp = exp_for_level(target_level)
        db_set_user_exp(uid, username or db_uname, full_name or db_fname, target_exp)
        actual_exp, _, _ = db_get_user_exp(uid)
        new_level, _, _ = calculate_level(actual_exp)
        db_add_log("fine_lvl", message.from_user.id if message.from_user else 0, admin_name, uid, username, f"-{amount} уровней")
        await message.reply(
            f"➖ <b>{display}</b>: -{amount} уровн(ей)\n"
            f"⭐ Новый уровень: <b>{new_level}</b> — {get_level_name(new_level)}\n"
            f"✨ Опыт: <b>{actual_exp}</b>",
            parse_mode="HTML"
        )
        await notify_user(uid, f"⬇️ У тебя сняли <b>{amount}</b> уровн(ей)\n⭐ Твой уровень: <b>{new_level}</b> — {get_level_name(new_level)}")
    else:
        new_exp = max(0, old_exp - amount)
        db_set_user_exp(uid, username or db_uname, full_name or db_fname, new_exp)
        actual_exp, _, _ = db_get_user_exp(uid)
        new_level, _, _ = calculate_level(actual_exp)
        db_add_log("fine_exp", message.from_user.id if message.from_user else 0, admin_name, uid, username, f"-{amount} exp")
        await message.reply(
            f"➖ <b>{display}</b>: -{amount} опыта\n"
            f"⭐ Уровень: <b>{new_level}</b> — {get_level_name(new_level)}\n"
            f"✨ Опыт: <b>{actual_exp}</b>",
            parse_mode="HTML"
        )
        await notify_user(uid, f"⬇️ У тебя сняли <b>{amount}</b> опыта\n⭐ Уровень: <b>{new_level}</b> — {get_level_name(new_level)}")


# ─────────────────────────────────────────────
# /mute
# ─────────────────────────────────────────────

@dp.message(Command("mute"))
async def cmd_mute(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    user_id, name, seconds, reason = await parse_mute_ban_args(message)
    if seconds is None:
        return await message.reply(
            "⚠️ Использование:\n"
            "• /mute @ник 30 м причина\n"
            "• /mute 123456789 30 м причина\n"
            "• Ответить на сообщение: /mute 30 м причина"
        )
    if not user_id:
        return await message.reply("❌ Пользователь не найден.")
    until = message.date + timedelta(seconds=seconds)
    admin_name = admin_display(message)
    try:
        await bot.restrict_chat_member(chat_id=message.chat.id, user_id=user_id, permissions=NO_PERMS, until_date=until)
        db_add_mute(user_id, name, int(until.timestamp()), reason)
        db_add_log("mute", message.from_user.id if message.from_user else 0, admin_name, user_id, name, reason)
        duration_text = format_duration(seconds)
        await message.reply(f"🔇 <b>{format_display(name)}</b> замьючен на <b>{duration_text}</b>\n📌 Причина: {reason}", parse_mode="HTML")
        chat_name = message.chat.title or str(message.chat.id)
        await notify_user(user_id, f"🔇 Тебя замьютили в чате <b>{chat_name}</b> на <b>{duration_text}</b>\n📌 Причина: {reason}")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# ─────────────────────────────────────────────
# /ban + /ban perm
# ─────────────────────────────────────────────

@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return

    args_text = message.text.partition(" ")[2].strip()
    perm = False
    user_id = None
    name = None
    reason = "не указана"
    admin_name = admin_display(message)

    if message.reply_to_message:
        parts = args_text.split(maxsplit=1)
        if parts and parts[0].lower() == "perm":
            perm = True
            reason = parts[1] if len(parts) > 1 else "не указана"
            user_id, name = await resolve_target(message.chat.id, None, message.reply_to_message)
        else:
            user_id, name, seconds, reason = await parse_mute_ban_args(message)
            if seconds is None:
                return await message.reply("⚠️ Укажи время: /ban 24 ч причина\nили /ban perm причина")
    else:
        parts = args_text.split(maxsplit=2)
        if len(parts) >= 2 and parts[1].lower() == "perm":
            perm = True
            target = parts[0].lstrip("@")
            reason = parts[2] if len(parts) > 2 else "не указана"
            user_id, name = await resolve_target(message.chat.id, target)
        else:
            user_id, name, seconds, reason = await parse_mute_ban_args(message)
            if seconds is None:
                return await message.reply(
                    "⚠️ Использование:\n"
                    "• /ban @ник 24 ч причина\n"
                    "• /ban @ник perm причина — навсегда\n"
                    "• Ответить на сообщение: /ban 24 ч причина\n"
                    "• Ответить на сообщение: /ban perm причина"
                )

    if not user_id:
        return await message.reply("❌ Пользователь не найден.")

    try:
        chat_name = message.chat.title or str(message.chat.id)
        if perm:
            await bot.ban_chat_member(chat_id=message.chat.id, user_id=user_id)
            db_add_ban(user_id, name, None, reason)
            db_add_log("ban", message.from_user.id if message.from_user else 0, admin_name, user_id, name, reason)
            await message.reply(f"🔨 <b>{format_display(name)}</b> забанен <b>навсегда</b>\n📌 Причина: {reason}", parse_mode="HTML")
            await notify_user(user_id, f"🔨 Тебя забанили в чате <b>{chat_name}</b> <b>навсегда</b>\n📌 Причина: {reason}")
        else:
            until = message.date + timedelta(seconds=seconds)
            await bot.ban_chat_member(chat_id=message.chat.id, user_id=user_id, until_date=until)
            db_add_ban(user_id, name, int(until.timestamp()), reason)
            db_add_log("ban", message.from_user.id if message.from_user else 0, admin_name, user_id, name, reason)
            duration_text = format_duration(seconds)
            await message.reply(f"🔨 <b>{format_display(name)}</b> забанен на <b>{duration_text}</b>\n📌 Причина: {reason}", parse_mode="HTML")
            await notify_user(user_id, f"🔨 Тебя забанили в чате <b>{chat_name}</b> на <b>{duration_text}</b>\n📌 Причина: {reason}")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# ─────────────────────────────────────────────
# /unmute
# ─────────────────────────────────────────────

@dp.message(Command("unmute"))
async def cmd_unmute(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    args_text = message.text.partition(" ")[2].strip()
    target = parse_target_only(args_text)
    user_id, name = await resolve_target(message.chat.id, target, message.reply_to_message)
    if not user_id:
        return await message.reply("❌ Пользователь не найден.")
    admin_name = admin_display(message)
    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=True),
            use_independent_chat_permissions=False,
        )
        db_remove_mute(user_id)
        db_add_log("unmute", message.from_user.id if message.from_user else 0, admin_name, user_id, name, "—")
        await message.reply(f"🔊 <b>{format_display(name)}</b> размьючен.", parse_mode="HTML")
        chat_name = message.chat.title or str(message.chat.id)
        await notify_user(user_id, f"🔊 Тебя размьютили в чате <b>{chat_name}</b>!")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# ─────────────────────────────────────────────
# /unban
# ─────────────────────────────────────────────

@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    args_text = message.text.partition(" ")[2].strip()
    target = parse_target_only(args_text)
    user_id, name = await resolve_target(message.chat.id, target, message.reply_to_message)
    if not user_id:
        return await message.reply("❌ Пользователь не найден.")
    admin_name = admin_display(message)
    try:
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=user_id, only_if_banned=True)
        db_remove_ban(user_id)
        db_add_log("unban", message.from_user.id if message.from_user else 0, admin_name, user_id, name, "—")
        await message.reply(f"✅ <b>{format_display(name)}</b> разбанен.", parse_mode="HTML")
        chat_name = message.chat.title or str(message.chat.id)
        await notify_user(user_id, f"✅ Тебя разбанили в чате <b>{chat_name}</b>!")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# ─────────────────────────────────────────────
# /kick
# ─────────────────────────────────────────────

@dp.message(Command("kick"))
async def cmd_kick(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    args_text = message.text.partition(" ")[2].strip()
    admin_name = admin_display(message)
    if message.reply_to_message:
        user_id, name = await resolve_target(message.chat.id, None, message.reply_to_message)
        reason = args_text or "не указана"
    else:
        parts = args_text.split(maxsplit=1)
        if not parts:
            return await message.reply("⚠️ Использование: /kick @ник [причина]")
        target = parts[0].lstrip("@")
        reason = parts[1] if len(parts) > 1 else "не указана"
        user_id, name = await resolve_target(message.chat.id, target)
    if not user_id:
        return await message.reply("❌ Пользователь не найден.")
    try:
        chat_name = message.chat.title or str(message.chat.id)
        await notify_user(user_id, f"👢 Тебя кикнули из чата <b>{chat_name}</b>\n📌 Причина: {reason}")
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=user_id)
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=user_id, only_if_banned=True)
        db_add_log("kick", message.from_user.id if message.from_user else 0, admin_name, user_id, name, reason)
        await message.reply(f"👢 <b>{format_display(name)}</b> кикнут.\n📌 Причина: {reason}", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# ─────────────────────────────────────────────
# /warn
# ─────────────────────────────────────────────

@dp.message(Command("warn"))
async def cmd_warn(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    args_text = message.text.partition(" ")[2].strip()
    admin_name = admin_display(message)
    if message.reply_to_message:
        u = message.reply_to_message.from_user
        if not u:
            return await message.reply("❌ Не удалось получить данные пользователя.")
        user_id = u.id
        name = u.username or str(u.id)
        reason = args_text or "не указана"
    else:
        parts = args_text.split(maxsplit=1)
        if not parts:
            return await message.reply("⚠️ Использование: /warn @ник [причина]")
        target = parts[0].lstrip("@")
        reason = parts[1] if len(parts) > 1 else "не указана"
        user_id, name = await resolve_target(message.chat.id, target)
    if not user_id:
        return await message.reply("❌ Пользователь не найден.")
    db_add_warn(user_id, name, reason)
    db_add_log("warn", message.from_user.id if message.from_user else 0, admin_name, user_id, name, reason)
    warns = db_get_warns(user_id)
    await message.reply(
        f"⚠️ <b>{format_display(name)}</b>, ты получил предупреждение! (всего: {len(warns)})\n📌 Причина: {reason}",
        parse_mode="HTML",
    )
    chat_name = message.chat.title or str(message.chat.id)
    await notify_user(user_id, f"⚠️ Ты получил предупреждение в чате <b>{chat_name}</b>\n📌 Причина: {reason}\nВсего варнов: {len(warns)}")


# ─────────────────────────────────────────────
# /ro
# ─────────────────────────────────────────────

@dp.message(Command("ro"))
async def cmd_ro(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    user_id, name, seconds, reason = await parse_mute_ban_args(message)
    if seconds is None:
        args_text = message.text.partition(" ")[2].strip()
        if message.reply_to_message:
            user_id, name = await resolve_target(message.chat.id, None, message.reply_to_message)
            reason = args_text or "не указана"
            seconds = 0
        else:
            parts = args_text.split(maxsplit=1)
            if not parts:
                return await message.reply("⚠️ Использование: /ro @ник [время] [причина]")
            target = parts[0].lstrip("@")
            reason = parts[1] if len(parts) > 1 else "не указана"
            user_id, name = await resolve_target(message.chat.id, target)
            seconds = 0
    if not user_id:
        return await message.reply("❌ Пользователь не найден.")
    kwargs = dict(chat_id=message.chat.id, user_id=user_id, permissions=NO_PERMS)
    if seconds:
        kwargs["until_date"] = message.date + timedelta(seconds=seconds)
    try:
        await bot.restrict_chat_member(**kwargs)
        duration_text = f"на {format_duration(seconds)}" if seconds else "навсегда"
        await message.reply(f"🚫 <b>{format_display(name)}</b> переведён в режим чтения {duration_text}\n📌 Причина: {reason}", parse_mode="HTML")
        chat_name = message.chat.title or str(message.chat.id)
        await notify_user(user_id, f"🚫 Тебя перевели в режим чтения в чате <b>{chat_name}</b> {duration_text}\n📌 Причина: {reason}")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# ─────────────────────────────────────────────
# /activmute
# ─────────────────────────────────────────────

@dp.message(Command("activmute"))
async def cmd_activmute(message: Message):
    if not is_allowed(message):
        return
    rows = db_get_mutes()
    now = int(time.time())
    active = [(uid, uname, until, reason) for uid, uname, until, reason in rows if until is None or until > now]
    if not active:
        return await message.reply("✅ Нет замьюченных пользователей.")
    text = f"🔇 <b>Замьюченные ({len(active)}):</b>\n\n"
    for uid, uname, until, reason in active:
        display = f"@{uname}" if uname and not uname.lstrip('-').isdigit() else f"id{uid}"
        left = fmt_time_left(until)
        text += f"• {display} — осталось: <b>{left}</b>\n  📌 {reason}\n"
    await message.reply(text, parse_mode="HTML")


# ─────────────────────────────────────────────
# /activban
# ─────────────────────────────────────────────

@dp.message(Command("activban"))
async def cmd_activban(message: Message):
    if not is_allowed(message):
        return
    rows = db_get_bans()
    now = int(time.time())
    active = [(uid, uname, until, reason) for uid, uname, until, reason in rows if until is None or until > now]
    if not active:
        return await message.reply("✅ Нет забаненных пользователей.")
    text = f"🔨 <b>Забаненные ({len(active)}):</b>\n\n"
    for uid, uname, until, reason in active:
        display = f"@{uname}" if uname and not uname.lstrip('-').isdigit() else f"id{uid}"
        left = fmt_time_left(until)
        text += f"• {display} — осталось: <b>{left}</b>\n  📌 {reason}\n"
    await message.reply(text, parse_mode="HTML")


# ─────────────────────────────────────────────
# /activwarns
# ─────────────────────────────────────────────

@dp.message(Command("activwarns"))
async def cmd_activwarns(message: Message):
    if not is_allowed(message):
        return
    args_text = message.text.partition(" ")[2].strip()
    target = parse_target_only(args_text)
    chat_id = message.chat.id if not is_private(message) else ALLOWED_CHAT_ID
    user_id, name = await resolve_target(chat_id, target, message.reply_to_message)
    if not user_id:
        if target and target.lstrip("-").isdigit():
            user_id = int(target)
            name = str(user_id)
        else:
            return await message.reply("❌ Укажи пользователя: /activwarns @ник или ID")
    warns = db_get_warns_with_id(user_id)
    if not warns:
        return await message.reply(f"✅ У {format_display(name)} нет варнов.")
    text = f"⚠️ <b>Варны {format_display(name)} ({len(warns)}):</b>\n\n"
    for i, (wid, reason, created_at) in enumerate(warns, 1):
        dt = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M")
        text += f"{i}. {reason} — <i>{dt}</i>\n"
    text += f"\n💡 Снять варн: /unwarn <номер> @ник"
    await message.reply(text, parse_mode="HTML")


# ─────────────────────────────────────────────
# /unwarn
# ─────────────────────────────────────────────

@dp.message(Command("unwarn"))
async def cmd_unwarn(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return

    args_text = message.text.partition(" ")[2].strip()
    parts = args_text.split(maxsplit=1)

    if len(parts) < 2:
        return await message.reply(
            "⚠️ Использование: /unwarn <номер> @ник\n"
            "Пример: /unwarn 2 @user\n"
            "Номера варнов смотри в /activwarns @ник"
        )

    try:
        warn_num = int(parts[0])
    except ValueError:
        return await message.reply("❌ Укажи номер варна числом. Пример: /unwarn 2 @user")

    target = parts[1].lstrip("@")
    chat_id = message.chat.id if not is_private(message) else ALLOWED_CHAT_ID
    user_id, name = await resolve_target(chat_id, target)
    if not user_id:
        if target.lstrip("-").isdigit():
            user_id = int(target)
            name = str(user_id)
        else:
            return await message.reply("❌ Пользователь не найден.")

    warns = db_get_warns_with_id(user_id)
    if not warns:
        return await message.reply(f"✅ У {format_display(name)} нет варнов.")

    if warn_num < 1 or warn_num > len(warns):
        return await message.reply(f"❌ Варн №{warn_num} не существует. У пользователя {len(warns)} варн(ов).")

    warn_id, reason, created_at = warns[warn_num - 1]
    db_remove_warn_by_id(warn_id)

    remaining = len(warns) - 1
    await message.reply(
        f"✅ Варн №{warn_num} снят у {format_display(name)}\n"
        f"📌 Был за: {reason}\n"
        f"Осталось варнов: {remaining}",
        parse_mode="HTML",
    )
    await notify_user(user_id, f"✅ С тебя сняли варн №{warn_num}\n📌 Был за: {reason}\nОсталось варнов: {remaining}")


# ─────────────────────────────────────────────
# Личка — анонимные сообщения (должен быть последним!)
# ─────────────────────────────────────────────

@dp.message(F.chat.type == "private")
async def handle_anon_message(message: Message):
    if not message.from_user:
        return
    if message.from_user.id not in waiting_for_anon:
        return
    if message.text and message.text.startswith("/"):
        return
    waiting_for_anon.discard(message.from_user.id)
    try:
        await bot.copy_message(chat_id=ANON_CHANNEL_ID, from_chat_id=message.chat.id, message_id=message.message_id)
        await message.answer("✅ Сообщение анонимно отправлено в канал!")
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке: {e}")


# ─────────────────────────────────────────────
# Запуск
# ─────────────────────────────────────────────

async def check_expired():
    """Каждую минуту проверяет истёкшие муты и баны."""
    while True:
        await asyncio.sleep(60)
        now = int(time.time())
        mutes = db_get_mutes()
        for uid, uname, until, reason in mutes:
            if until is not None and until <= now:
                db_remove_mute(uid)
                name = f"@{uname}" if uname and not uname.lstrip("-").isdigit() else "пользователь"
                await notify_user(uid, f"🔊 {name}, вы теперь можете свободно общаться, но лучше следите за языком!")
        bans = db_get_bans()
        for uid, uname, until, reason in bans:
            if until is not None and until <= now:
                db_remove_ban(uid)
                name = f"@{uname}" if uname and not uname.lstrip("-").isdigit() else "пользователь"
                await notify_user(uid, f"✅ {name}, вы теперь можете свободно общаться, но лучше следите за языком!")


async def main():
    init_db()
    asyncio.create_task(check_expired())
    await dp.start_polling(bot, allowed_updates=["message", "chat_member", "my_chat_member"])

if __name__ == "__main__":
    asyncio.run(main())
