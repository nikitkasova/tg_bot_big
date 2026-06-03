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
# Запретные слова
# ==============================
BANNED_WORDS = [
    "дс", "дискорд", "хохол",
    "гойда", "сво", "卐", "1488",
]

# ==============================
# AntiRaid защита
# ==============================
antiraid_enabled = False
antiraid_join_tracker: dict[int, list[float]] = defaultdict(list)  # user_id -> время заходов
ANTIRAID_JOIN_LIMIT = 5  # максимум заходов за 10 минут от одного юзера
ANTIRAID_WINDOW = 600  # 10 минут в секундах

# ==============================
# AntiLink
# ==============================
antilink_enabled = False
LINK_PATTERN = re.compile(r'(https?://|www\.)[a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,}(/\S*)?')

# ==============================
# Настройки чата (сохраняются в БД)
# ==============================
def init_settings_db():
    conn = sqlite3.connect("moderation.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_setting(key: str, default: str = "off") -> str:
    conn = sqlite3.connect("moderation.db")
    row = conn.execute("SELECT value FROM chat_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default

def set_setting(key: str, value: str):
    conn = sqlite3.connect("moderation.db")
    conn.execute("INSERT OR REPLACE INTO chat_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

# ==============================
# Логирование действий бота
# ==============================
LOG_CHANNEL_ID = None  # можно задать ID канала для логов

async def log_action(action: str, moderator: str, target: str, reason: str = "", details: str = ""):
    """Отправляет лог в канал (если настроен)"""
    if LOG_CHANNEL_ID:
        log_text = f"📋 <b>{action}</b>\n👮 Модератор: {moderator}\n👤 Цель: {target}"
        if reason:
            log_text += f"\n📌 Причина: {reason}"
        if details:
            log_text += f"\n📝 Детали: {details}"
        try:
            await bot.send_message(LOG_CHANNEL_ID, log_text, parse_mode="HTML")
        except Exception:
            pass

# ==============================
# Система уровней
# ==============================
EXP_PER_MESSAGE_MIN = 3
EXP_PER_MESSAGE_MAX = 8
EXP_COOLDOWN = 60  # секунд между начислением опыта
exp_cooldown_tracker: dict[int, float] = {}
DAILY_BONUS_EXP = 50  # ежедневный бонус

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
    """Суммарный опыт для достижения уровня."""
    if level <= 0:
        return 0
    return int(100 * (level ** 1.5))

def calculate_level(total_exp: int) -> tuple[int, int, int]:
    """Возвращает (level, exp_в_текущем_уровне, нужно_до_следующего)."""
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
# Ежедневный бонус (cooldown)
# ==============================
daily_bonus_tracker: dict[int, int] = {}  # user_id -> timestamp последнего бонуса
DAILY_COOLDOWN = 86400  # 24 часа

# ==============================
# Активность за 7 дней (для топа)
# ==============================
user_activity: dict[int, list[int]] = defaultdict(list)  # user_id -> [timestamp сообщений]
ACTIVITY_WINDOW = 7 * 86400  # 7 дней в секундах

def add_activity(user_id: int):
    """Добавляет активность пользователя"""
    now = int(time.time())
    user_activity[user_id].append(now)
    # Очищаем старые записи
    user_activity[user_id] = [t for t in user_activity[user_id] if now - t < ACTIVITY_WINDOW]

def get_weekly_activity(user_id: int) -> int:
    """Возвращает количество сообщений за последние 7 дней"""
    now = int(time.time())
    return sum(1 for t in user_activity.get(user_id, []) if now - t < ACTIVITY_WINDOW)

def get_weekly_top(limit: int = 10) -> list[tuple[int, int]]:
    """Возвращает топ пользователей по активности за 7 дней"""
    now = int(time.time())
    activity_count = {}
    for uid, timestamps in user_activity.items():
        count = sum(1 for t in timestamps if now - t < ACTIVITY_WINDOW)
        if count > 0:
            activity_count[uid] = count
    sorted_users = sorted(activity_count.items(), key=lambda x: x[1], reverse=True)
    return sorted_users[:limit]

# ==============================
# ПРАВИЛА
# ==============================
RULES_TEXT = """
📜 <b>ПРАВИЛА!</b>

<b>1.0</b> Угрозы — <i>1 день</i>
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
    # Таблица уровней
    c.execute("""
        CREATE TABLE IF NOT EXISTS levels (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            exp INTEGER DEFAULT 0
        )
    """)
    # Таблица истории наказаний
    c.execute("""
        CREATE TABLE IF NOT EXISTS punishments_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            action TEXT,
            reason TEXT,
            duration TEXT,
            moderator_id INTEGER,
            moderator_name TEXT,
            created_at INTEGER
        )
    """)
    conn.commit()
    conn.close()
    init_settings_db()

def add_punishment_history(user_id: int, username: str, action: str, reason: str, duration: str, moderator_id: int, moderator_name: str):
    """Добавляет запись в историю наказаний"""
    conn = sqlite3.connect("moderation.db")
    conn.execute(
        "INSERT INTO punishments_history (user_id, username, action, reason, duration, moderator_id, moderator_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, username, action, reason, duration, moderator_id, moderator_name, int(time.time()))
    )
    conn.commit()
    conn.close()

def get_punishment_history(user_id: int) -> list:
    """Возвращает историю наказаний пользователя"""
    conn = sqlite3.connect("moderation.db")
    rows = conn.execute(
        "SELECT action, reason, duration, moderator_name, created_at FROM punishments_history WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    return rows


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


# ── Levels ──
def db_get_user_exp(user_id: int) -> tuple[int, str, str]:
    """Возвращает (exp, username, full_name)."""
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
    """Добавляет опыт. Возвращает (старый_уровень, новый_уровень)."""
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

def parse_time_string(time_str: str) -> int:
    """Парсит строку времени типа '10м', '2ч', '1д' в секунды"""
    time_str = time_str.lower().strip()
    if time_str.endswith('м'):
        return int(time_str[:-1]) * 60
    elif time_str.endswith('ч'):
        return int(time_str[:-1]) * 3600
    elif time_str.endswith('д'):
        return int(time_str[:-1]) * 86400
    else:
        return 0

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
# Антиспам + фильтр слов + лимит букв + EXP + AntiLink
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
    if not message.from_user:
        return
    
    # Добавляем активность
    add_activity(message.from_user.id)
    
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
        # Запретные слова — мут 5 часов
        text_lower = message.text.lower()
        if any(re.search(r'(?<![а-яёa-z])' + re.escape(word) + r'(?![а-яёa-z])', text_lower) for word in BANNED_WORDS):
            mute_5h = 5 * 3600
            until_5h = message.date + timedelta(seconds=mute_5h)
            try:
                await message.delete()
                await bot.restrict_chat_member(chat_id=message.chat.id, user_id=user_id, permissions=NO_PERMS, until_date=until_5h)
                db_add_mute(user_id, username, int(until_5h.timestamp()), "запрещённое слово")
                add_punishment_history(user_id, username, "mute", "запрещённое слово", "5 ч", 0, "auto")
                await message.answer(f"🔇 <b>{name}</b> замьючен на 5 ч\n📌 Причина: запрещённое слово", parse_mode="HTML")
                await notify_user(user_id, f"🔇 Тебя замьютили на <b>5 ч</b>\n📌 Причина: использование запрещённого слова")
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
                add_punishment_history(user_id, username, "mute", f"превышен лимит {MAX_MESSAGE_LENGTH} символов", "1 ч", 0, "auto")
                await message.answer(f"🔇 <b>{name}</b> замьючен на 1 ч\n📌 Причина: сообщение превышает {MAX_MESSAGE_LENGTH} символов", parse_mode="HTML")
                await notify_user(user_id, f"🔇 Тебя замьютили на <b>1 ч</b>\n📌 Причина: сообщение превышает {MAX_MESSAGE_LENGTH} символов")
            except Exception:
                pass
            return

        # AntiLink — удаление ссылок
        antilink_enabled = get_setting("antilink", "off") == "on"
        if antilink_enabled and LINK_PATTERN.search(message.text):
            try:
                await message.delete()
                await message.answer(f"🔗 <b>{name}</b>, ссылки запрещены!", parse_mode="HTML")
                await notify_user(user_id, f"🔗 Ваше сообщение было удалено за содержащуюся ссылку.")
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
                add_punishment_history(user_id, username, "mute", "спам", "1 ч", 0, "auto")
                await message.answer(f"🔇 <b>{name}</b> замьючен на 1 ч\n📌 Причина: спам", parse_mode="HTML")
                await notify_user(user_id, f"🔇 Тебя замьютили на <b>1 ч</b>\n📌 Причина: спам")
            except Exception:
                pass
            return

    # ── Начисление опыта (с cooldown, для всех включая админов) ──
    last_exp = exp_cooldown_tracker.get(user_id, 0)
    if now - last_exp >= EXP_COOLDOWN:
        exp_gain = random.randint(EXP_PER_MESSAGE_MIN, EXP_PER_MESSAGE_MAX)
        old_lvl, new_lvl = db_add_exp(user_id, username, full_name, exp_gain)
        exp_cooldown_tracker[user_id] = now
        if new_lvl > old_lvl:
            lvl_name = get_level_name(new_lvl)
            await message.answer(
                f"🎉 <b>{name}</b> достиг нового уровня!\n"
                f"✨ Уровень <b>{new_lvl}</b> — {lvl_name}",
                parse_mode="HTML"
            )


# ─────────────────────────────────────────────
# Антирейд защита
# ─────────────────────────────────────────────

@dp.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def antiraid_check(event: ChatMemberUpdated):
    if event.chat.id != ALLOWED_CHAT_ID:
        return
    
    if get_setting("antiraid", "off") != "on":
        return
    
    user_id = event.new_chat_member.user.id
    now = time.time()
    
    # Проверяем не заходит ли один и тот же пользователь много раз
    antiraid_join_tracker[user_id] = [t for t in antiraid_join_tracker[user_id] if now - t < ANTIRAID_WINDOW]
    antiraid_join_tracker[user_id].append(now)
    
    if len(antiraid_join_tracker[user_id]) >= ANTIRAID_JOIN_LIMIT:
        # Подозрение на рейд — кикаем пользователя
        try:
            await bot.ban_chat_member(event.chat.id, user_id)
            await bot.unban_chat_member(event.chat.id, user_id, only_if_banned=True)
            await bot.send_message(event.chat.id, f"⚠️ Пользователь @{event.new_chat_member.user.username or user_id} заподозрен в рейде и был кикнут.")
        except Exception:
            pass


# ─────────────────────────────────────────────
# Приветствие
# ─────────────────────────────────────────────

@dp.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def welcome_new_member(event: ChatMemberUpdated):
    if event.chat.id != ALLOWED_CHAT_ID:
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
        "👻 <b>/activ</b> — анонимно в канал (личка)\n\n"
        "🏅 <b>/ранг</b> [@ник или ID] — уровень и опыт\n"
        "📊 <b>/profile</b> — твой профиль (XP + уровень)\n"
        "🎁 <b>/daily</b> — ежедневный бонус XP\n"
        "🔥 <b>/leaderweek</b> — топ активных за 7 дней\n\n"
        "🛡️ <b>Модерация:</b>\n"
        "🔇 <b>/mute</b> @ник 30 м [причина]\n"
        "🔊 <b>/unmute</b> @ник\n"
        "🔨 <b>/ban</b> @ник 24 ч [причина]\n"
        "🔨 <b>/ban</b> @ник perm [причина] — навсегда\n"
        "✅ <b>/unban</b> @ник\n"
        "👢 <b>/kick</b> @ник [причина]\n"
        "⚠️ <b>/warn</b> @ник [причина]\n"
        "🚫 <b>/ro</b> @ник [30 м] [причина]\n"
        "🔇 <b>/muteall</b> 10м — временно запрещает писать всем\n"
        "🧹 <b>/clear</b> 10 — удалить последние сообщения\n"
        "🚨 <b>/antiraid</b> on/off — защита от рейда\n"
        "🔗 <b>/antilink</b> on/off — удаление ссылок\n\n"
        "🆔 <b>/id</b> — узнать ID\n"
        "👮 <b>/whois</b> @ник — полная информация\n"
        "🧾 <b>/history</b> @ник — история наказаний\n\n"
        "📊 <b>/activmute</b> — кто в муте\n"
        "📊 <b>/activban</b> — кто в бане\n"
        "📊 <b>/activwarns</b> @ник — варны пользователя\n"
        "📜 <b>/logs</b> — последние действия бота\n\n"
        "🛠 <b>Только для админов:</b>\n"
        "➕ <b>/add lvl</b> @ник (число) — добавить уровни\n"
        "➕ <b>/add exp</b> @ник (число) — добавить опыт\n"
        "➖ <b>/fine lvl</b> @ник (число) — снять уровни\n"
        "➖ <b>/fine exp</b> @ник (число) — снять опыт\n"
        "⭐ <b>/setxp</b> @ник 500 — установить опыт\n\n"
        "🎲 <b>/roll</b> — случайное число 1-100\n"
        "😂 <b>/joke</b> — случайная шутка\n\n"
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

    # Отображаемое имя
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

    # Место в топе
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
# /profile — профиль пользователя
# ─────────────────────────────────────────────

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
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
    
    # Проверяем в муте/бане ли пользователь
    mute_status = "✅ Нет"
    ban_status = "✅ Нет"
    
    mutes = db_get_mutes()
    for uid, _, until, _ in mutes:
        if uid == user_id and (until is None or until > int(time.time())):
            mute_status = "🔇 В муте"
            break
    
    bans = db_get_bans()
    for uid, _, until, _ in bans:
        if uid == user_id and (until is None or until > int(time.time())):
            ban_status = "🔨 В бане"
            break
    
    warns_count = len(db_get_warns(user_id))
    
    await message.reply(
        f"📊 <b>Профиль {display_name}</b>\n\n"
        f"⭐ Уровень: <b>{level}</b> — {lvl_name}\n"
        f"✨ Опыт: <b>{exp}</b>\n"
        f"📊 Прогресс: {bar} <b>{current_in_level}/{need_next}</b>\n\n"
        f"⚠️ Варны: <b>{warns_count}</b>\n"
        f"🔇 Мут: {mute_status}\n"
        f"🔨 Бан: {ban_status}",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
# /leader — топ-30 по уровням
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

        # Отображаемое имя
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

    # Показываем место самого пользователя, если его нет в топ-30
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
# /leaderweek — топ за 7 дней
# ─────────────────────────────────────────────

@dp.message(Command("leaderweek"))
async def cmd_leaderweek(message: Message):
    if not is_allowed_chat(message):
        return
    
    top = get_weekly_top(limit=15)
    if not top:
        return await message.reply("📊 За последние 7 дней никто не писал сообщений!")
    
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["🔥 <b>ТОП АКТИВНЫХ ЗА 7 ДНЕЙ</b>\n"]
    
    for i, (user_id, count) in enumerate(top, start=1):
        exp, username, full_name = db_get_user_exp(user_id)
        if username and not username.lstrip('-').isdigit():
            display = f"@{username}"
        elif full_name:
            display = full_name
        else:
            display = f"id{user_id}"
        
        medal = medals.get(i, f"{i}.")
        lines.append(f"{medal} <b>{display}</b> — {count} сообщ.")
    
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
    
    user_id = message.from_user.id
    now = int(time.time())
    last_bonus = daily_bonus_tracker.get(user_id, 0)
    
    if now - last_bonus < DAILY_COOLDOWN:
        remaining = DAILY_COOLDOWN - (now - last_bonus)
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        return await message.reply(f"⏰ Ты уже получал ежедневный бонус!\nСледующий бонус через <b>{hours}ч {minutes}м</b>", parse_mode="HTML")
    
    # Начисляем бонус
    username = message.from_user.username or str(user_id)
    full_name = message.from_user.full_name or ""
    old_lvl, new_lvl = db_add_exp(user_id, username, full_name, DAILY_BONUS_EXP)
    daily_bonus_tracker[user_id] = now
    
    await message.reply(
        f"🎁 <b>Ежедневный бонус!</b>\n"
        f"✨ Ты получил <b>{DAILY_BONUS_EXP}</b> опыта!\n"
        f"⭐ Текущий уровень: <b>{new_lvl}</b> — {get_level_name(new_lvl)}",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
# /setxp — установить опыт пользователю (админ)
# ─────────────────────────────────────────────

@dp.message(Command("setxp"))
async def cmd_setxp(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    
    parts = message.text.strip().split()
    if len(parts) < 3:
        return await message.reply(
            "⚠️ Использование:\n"
            "/setxp @ник 500\n"
            "или ответь на сообщение: /setxp 500"
        )
    
    if message.reply_to_message and message.reply_to_message.from_user:
        try:
            amount = int(parts[1])
        except ValueError:
            return await message.reply("❌ Укажи число опыта")
        user_id = message.reply_to_message.from_user.id
        username = message.reply_to_message.from_user.username or str(user_id)
        full_name = message.reply_to_message.from_user.full_name or ""
    else:
        target = parts[1].lstrip("@")
        try:
            amount = int(parts[2])
        except ValueError:
            return await message.reply("❌ Укажи число опыта")
        user_id, username = await resolve_target(message.chat.id, target)
        if not user_id:
            return await message.reply("❌ Пользователь не найден.")
        full_name = ""
    
    if amount < 0:
        return await message.reply("❌ Опыт не может быть отрицательным.")
    
    db_set_user_exp(user_id, username, full_name, amount)
    new_level, _, _ = calculate_level(amount)
    
    await message.reply(
        f"⭐ <b>{format_display(username)}</b> установлен опыт <b>{amount}</b>\n"
        f"Уровень: <b>{new_level}</b> — {get_level_name(new_level)}",
        parse_mode="HTML"
    )
    await notify_user(user_id, f"⭐ Тебе установили {amount} опыта!\nУровень: {new_level} — {get_level_name(new_level)}")


# ─────────────────────────────────────────────
# /whois — полная информация о пользователе
# ─────────────────────────────────────────────

@dp.message(Command("whois"))
async def cmd_whois(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    
    args_text = message.text.partition(" ")[2].strip()
    target = parse_target_only(args_text)
    user_id, username = await resolve_target(message.chat.id, target, message.reply_to_message)
    
    if not user_id:
        return await message.reply("❌ Пользователь не найден.")
    
    # Получаем информацию
    exp, db_username, db_fullname = db_get_user_exp(user_id)
    uname = username or db_username
    fname = db_fullname
    
    level, _, _ = calculate_level(exp)
    lvl_name = get_level_name(level)
    
    # Мут/бан статус
    mute_status = "✅ Нет"
    ban_status = "✅ Нет"
    mute_info = ""
    ban_info = ""
    
    mutes = db_get_mutes()
    for uid, _, until, reason in mutes:
        if uid == user_id and (until is None or until > int(time.time())):
            left = fmt_time_left(until)
            mute_status = f"🔇 Да (ещё {left})"
            mute_info = f"\n📌 Причина: {reason}"
            break
    
    bans = db_get_bans()
    for uid, _, until, reason in bans:
        if uid == user_id and (until is None or until > int(time.time())):
            left = fmt_time_left(until)
            ban_status = f"🔨 Да ({left})"
            ban_info = f"\n📌 Причина: {reason}"
            break
    
    warns_count = len(db_get_warns(user_id))
    weekly_activity = get_weekly_activity(user_id)
    
    # Пытаемся получить username и имя из чата
    try:
        member = await bot.get_chat_member(message.chat.id, user_id)
        if member.user:
            uname = member.user.username or uname
            fname = member.user.full_name or fname
    except Exception:
        pass
    
    display = f"@{uname}" if uname and not uname.lstrip('-').isdigit() else (fname or f"id{user_id}")
    
    await message.reply(
        f"👤 <b>WHOIS: {display}</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📛 Имя: {fname or 'Не указано'}\n"
        f"🔖 Username: @{uname if uname else 'Нет'}\n\n"
        f"⭐ Уровень: <b>{level}</b> — {lvl_name}\n"
        f"✨ Опыт: <b>{exp}</b>\n"
        f"💬 Активность (7 дней): <b>{weekly_activity}</b> сообщ.\n\n"
        f"⚠️ Варны: <b>{warns_count}</b>\n"
        f"🔇 Мут: {mute_status}{mute_info}\n"
        f"🔨 Бан: {ban_status}{ban_info}",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
# /history — история наказаний
# ─────────────────────────────────────────────

@dp.message(Command("history"))
async def cmd_history(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    
    args_text = message.text.partition(" ")[2].strip()
    target = parse_target_only(args_text)
    user_id, username = await resolve_target(message.chat.id, target, message.reply_to_message)
    
    if not user_id:
        return await message.reply("❌ Пользователь не найден.")
    
    history = get_punishment_history(user_id)
    if not history:
        return await message.reply(f"✅ У {format_display(username)} нет истории наказаний.")
    
    lines = [f"🧾 <b>История наказаний {format_display(username)} ({len(history)})</b>\n"]
    for action, reason, duration, moderator, created_at in history[:20]:
        dt = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M")
        lines.append(f"• <b>{action.upper()}</b> | {dt}\n  👮 {moderator}\n  📌 {reason}")
        if duration and duration != "0":
            lines[-1] += f" | ⏱ {duration}"
    
    await message.reply("\n\n".join(lines), parse_mode="HTML")


# ─────────────────────────────────────────────
# /add lvl | /add exp — только для админов
# ─────────────────────────────────────────────

async def resolve_level_target(message: Message) -> tuple[int | None, str, str]:
    """
    Разрешает цель для команд уровня.
    Поддерживает: reply, @ник, ID, пусто (сам пользователь).
    Возвращает (user_id, username, full_name).
    """
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
    """
    Парсит аргументы /add и /fine.
    Форматы:
      /add lvl @ник 5
      /add lvl 5  (reply)
    Возвращает (user_id, username, full_name, amount).
    """
    parts = message.text.strip().split()
    # parts[0]=/add, parts[1]=lvl/exp, parts[2..]=аргументы
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

    if subcmd == "lvl":
        target_level = old_level + amount
        target_exp = exp_for_level(target_level)
        exp_to_add = target_exp - old_exp
        db_add_exp(uid, username or db_uname, full_name or db_fname, exp_to_add)
        actual_exp, _, _ = db_get_user_exp(uid)
        new_level, _, _ = calculate_level(actual_exp)
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

    if subcmd == "lvl":
        target_level = max(0, old_level - amount)
        target_exp = exp_for_level(target_level)
        db_set_user_exp(uid, username or db_uname, full_name or db_fname, target_exp)
        actual_exp, _, _ = db_get_user_exp(uid)
        new_level, _, _ = calculate_level(actual_exp)
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
    try:
        await bot.restrict_chat_member(chat_id=message.chat.id, user_id=user_id, permissions=NO_PERMS, until_date=until)
        db_add_mute(user_id, name, int(until.timestamp()), reason)
        duration_text = format_duration(seconds)
        add_punishment_history(user_id, name, "mute", reason, duration_text, message.from_user.id, message.from_user.full_name or str(message.from_user.id))
        await message.reply(f"🔇 <b>{format_display(name)}</b> замьючен на <b>{duration_text}</b>\n📌 Причина: {reason}", parse_mode="HTML")
        chat_name = message.chat.title or str(message.chat.id)
        await notify_user(user_id, f"🔇 Тебя замьютили в чате <b>{chat_name}</b> на <b>{duration_text}</b>\n📌 Причина: {reason}")
        await log_action("MUTE", message.from_user.full_name or str(message.from_user.id), format_display(name), reason, duration_text)
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# ─────────────────────────────────────────────
# /muteall — временный локдаун
# ─────────────────────────────────────────────

@dp.message(Command("muteall"))
async def cmd_muteall(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    
    args_text = message.text.partition(" ")[2].strip()
    if not args_text:
        return await message.reply("⚠️ Использование: /muteall 10м\nВарианты: 10м, 2ч, 1д")
    
    seconds = parse_time_string(args_text)
    if seconds == 0:
        return await message.reply("❌ Неверный формат времени. Пример: /muteall 10м")
    
    until = message.date + timedelta(seconds=seconds)
    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=message.chat.id,
            permissions=NO_PERMS,
            until_date=until
        )
        await message.reply(f"🔇 <b>Чат заморожен на {format_duration(seconds)}!</b>\nПисать никто не может.", parse_mode="HTML")
        await log_action("MUTEALL", message.from_user.full_name or str(message.from_user.id), "весь чат", "", format_duration(seconds))
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


# ─────────────────────────────────────────────
# /clear — очистка чата
# ─────────────────────────────────────────────

@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    
    args_text = message.text.partition(" ")[2].strip()
    if not args_text:
        return await message.reply("⚠️ Использование: /clear <кол-во>\nПример: /clear 10")
    
    try:
        count = int(args_text)
        if count <= 0 or count > 100:
            return await message.reply("❌ Количество должно быть от 1 до 100.")
    except ValueError:
        return await message.reply("❌ Укажи число.")
    
    deleted = 0
    async for msg in bot.get_chat_history(message.chat.id, limit=count + 1):
        if msg.message_id != message.message_id:
            try:
                await msg.delete()
                deleted += 1
            except Exception:
                pass
    
    await message.reply(f"🧹 Удалено <b>{deleted}</b> сообщений!", parse_mode="HTML")
    await log_action("CLEAR", message.from_user.full_name or str(message.from_user.id), f"{deleted} сообщений", "", "")


# ─────────────────────────────────────────────
# /antiraid on/off
# ─────────────────────────────────────────────

@dp.message(Command("antiraid"))
async def cmd_antiraid(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    
    args_text = message.text.partition(" ")[2].strip().lower()
    if args_text not in ("on", "off"):
        current = "включена" if get_setting("antiraid", "off") == "on" else "выключена"
        return await message.reply(f"🛡️ Антирейд защита сейчас <b>{current}</b>\nИспользование: /antiraid on/off", parse_mode="HTML")
    
    set_setting("antiraid", args_text)
    status = "включена ✅" if args_text == "on" else "выключена ❌"
    await message.reply(f"🛡️ Антирейд защита {status}", parse_mode="HTML")
    await log_action("ANTIRAID", message.from_user.full_name or str(message.from_user.id), args_text.upper(), "", "")


# ─────────────────────────────────────────────
# /antilink on/off
# ─────────────────────────────────────────────

@dp.message(Command("antilink"))
async def cmd_antilink(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    
    args_text = message.text.partition(" ")[2].strip().lower()
    if args_text not in ("on", "off"):
        current = "включена" if get_setting("antilink", "off") == "on" else "выключена"
        return await message.reply(f"🔗 Антиссылка сейчас <b>{current}</b>\nИспользование: /antilink on/off", parse_mode="HTML")
    
    set_setting("antilink", args_text)
    status = "включена ✅" if args_text == "on" else "выключена ❌"
    await message.reply(f"🔗 Антиссылка {status}", parse_mode="HTML")
    await log_action("ANTILINK", message.from_user.full_name or str(message.from_user.id), args_text.upper(), "", "")


# ─────────────────────────────────────────────
# /scan — проверка чата на подозрительные сообщения
# ─────────────────────────────────────────────

@dp.message(Command("scan"))
async def cmd_scan(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    
    await message.reply("🔍 <b>Начинаю сканирование чата...</b>", parse_mode="HTML")
    
    suspicious = []
    spam_users = defaultdict(int)
    
    async for msg in bot.get_chat_history(message.chat.id, limit=200):
        if msg.from_user and msg.from_user.is_bot:
            continue
        if msg.text:
            # Проверка на ссылки
            if LINK_PATTERN.search(msg.text) and not is_allowed(msg):
                suspicious.append(f"🔗 Ссылка от {msg.from_user.full_name}")
            # Проверка на длинные сообщения
            if len(msg.text) > 500:
                suspicious.append(f"📝 Длинное сообщение ({len(msg.text)} символов) от {msg.from_user.full_name}")
            # Проверка на капс (более 70% заглавных)
            caps_count = sum(1 for c in msg.text if c.isupper())
            if caps_count > len(msg.text) * 0.7 and len(msg.text) > 20:
                suspicious.append(f"🔊 Капс от {msg.from_user.full_name}")
            # Считаем сообщения для выявления спамеров
            spam_users[msg.from_user.id] += 1
    
    # Выявляем потенциальных спамеров
    for uid, count in spam_users.items():
        if count > 30:
            suspicious.append(f"⚠️ Потенциальный спамер (ID: {uid}) — {count} сообщений")
    
    if suspicious:
        text = "🔍 <b>Найдены подозрительные моменты:</b>\n\n" + "\n".join(suspicious[:15])
        await message.reply(text, parse_mode="HTML")
    else:
        await message.reply("✅ <b>Чат чист! Подозрительных сообщений не найдено.</b>", parse_mode="HTML")
    
    await log_action("SCAN", message.from_user.full_name or str(message.from_user.id), "чат проверен", "", "")


# ─────────────────────────────────────────────
# /logs — последние действия бота
# ─────────────────────────────────────────────

@dp.message(Command("logs"))
async def cmd_logs(message: Message):
    if not is_allowed_chat(message):
        return
    if not is_allowed(message):
        return
    
    # Последние действия из истории наказаний
    conn = sqlite3.connect("moderation.db")
    rows = conn.execute(
        "SELECT action, username, reason, moderator_name, created_at FROM punishments_history ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    conn.close()
    
    if not rows:
        return await message.reply("📜 История действий бота пуста.")
    
    lines = ["📜 <b>Последние действия бота:</b>\n"]
    for action, username, reason, moderator, created_at in rows:
        dt = datetime.fromtimestamp(created_at).strftime("%d.%m %H:%M")
        lines.append(f"• <b>{action.upper()}</b> | {dt}\n  👤 {format_display(username)}\n  👮 {moderator}\n  📌 {reason}")
    
    await message.reply("\n\n".join(lines[:10]), parse_mode="HTML")


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
            add_punishment_history(user_id, name, "ban", reason, "навсегда", message.from_user.id, message.from_user.full_name or str(message.from_user.id))
            await message.reply(f"🔨 <b>{format_display(name)}</b> забанен <b>навсегда</b>\n📌 Причина: {reason}", parse_mode="HTML")
            await notify_user(user_id, f"🔨 Тебя забанили в чате <b>{chat_name}</b> <b>навсегда</b>\n📌 Причина: {reason}")
            await log_action("BAN", message.from_user.full_name or str(message.from_user.id), format_display(name), reason, "навсегда")
        else:
            until = message.date + timedelta(seconds=seconds)
            await bot.ban_chat_member(chat_id=message.chat.id, user_id=user_id, until_date=until)
            db_add_ban(user_id, name, int(until.timestamp()), reason)
            duration_text = format_duration(seconds)
            add_punishment_history(user_id, name, "ban", reason, duration_text, message.from_user.id, message.from_user.full_name or str(message.from_user.id))
            await message.reply(f"🔨 <b>{format_display(name)}</b> забанен на <b>{duration_text}</b>\n📌 Причина: {reason}", parse_mode="HTML")
            await notify_user(user_id, f"🔨 Тебя забанили в чате <b>{chat_name}</b> на <b>{duration_text}</b>\n📌 Причина: {reason}")
            await log_action("BAN", message.from_user.full_name or str(message.from_user.id), format_display(name), reason, duration_text)
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
    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=True),
            use_independent_chat_permissions=False,
        )
        db_remove_mute(user_id)
        await message.reply(f"🔊 <b>{format_display(name)}</b> размьючен.", parse_mode="HTML")
        chat_name = message.chat.title or str(message.chat.id)
        await notify_user(user_id, f"🔊 Тебя размьютили в чате <b>{chat_name}</b>!")
        await log_action("UNMUTE", message.from_user.full_name or str(message.from_user.id), format_display(name), "", "")
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
    try:
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=user_id, only_if_banned=True)
        db_remove_ban(user_id)
        await message.reply(f"✅ <b>{format_display(name)}</b> разбанен.", parse_mode="HTML")
        chat_name = message.chat.title or str(message.chat.id)
        await notify_user(user_id, f"✅ Тебя разбанили в чате <b>{chat_name}</b>!")
        await log_action("UNBAN", message.from_user.full_name or str(message.from_user.id), format_display(name), "", "")
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
        add_punishment_history(user_id, name, "kick", reason, "", message.from_user.id, message.from_user.full_name or str(message.from_user.id))
        await message.reply(f"👢 <b>{format_display(name)}</b> кикнут.\n📌 Причина: {reason}", parse_mode="HTML")
        await log_action("KICK", message.from_user.full_name or str(message.from_user.id), format_display(name), reason, "")
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
    warns = db_get_warns(user_id)
    add_punishment_history(user_id, name, "warn", reason, "", message.from_user.id, message.from_user.full_name or str(message.from_user.id))
    await message.reply(
        f"⚠️ <b>{format_display(name)}</b>, ты получил предупреждение! (всего: {len(warns)})\n📌 Причина: {reason}",
        parse_mode="HTML",
    )
    chat_name = message.chat.title or str(message.chat.id)
    await notify_user(user_id, f"⚠️ Ты получил предупреждение в чате <b>{chat_name}</b>\n📌 Причина: {reason}\nВсего варнов: {len(warns)}")
    await log_action("WARN", message.from_user.full_name or str(message.from_user.id), format_display(name), reason, f"всего: {len(warns)}")


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
        add_punishment_history(user_id, name, "readonly", reason, format_duration(seconds) if seconds else "навсегда", message.from_user.id, message.from_user.full_name or str(message.from_user.id))
        await message.reply(f"🚫 <b>{format_display(name)}</b> переведён в режим чтения {duration_text}\n📌 Причина: {reason}", parse_mode="HTML")
        chat_name = message.chat.title or str(message.chat.id)
        await notify_user(user_id, f"🚫 Тебя перевели в режим чтения в чате <b>{chat_name}</b> {duration_text}\n📌 Причина: {reason}")
        await log_action("READONLY", message.from_user.full_name or str(message.from_user.id), format_display(name), reason, duration_text)
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
    await log_action("UNWARN", message.from_user.full_name or str(message.from_user.id), format_display(name), reason, f"снят варн #{warn_num}")


# ─────────────────────────────────────────────
# /roll — случайное число
# ─────────────────────────────────────────────

@dp.message(Command("roll"))
async def cmd_roll(message: Message):
    if not is_allowed_chat(message):
        return
    
    args = message.text.partition(" ")[2].strip()
    if args:
        try:
            max_num = int(args)
            if max_num < 2:
                max_num = 100
        except ValueError:
            max_num = 100
    else:
        max_num = 100
    
    result = random.randint(1, max_num)
    name = message.from_user.full_name if message.from_user else "Кто-то"
    await message.reply(f"🎲 <b>{name}</b> выбросил число <b>{result}</b> из {max_num}!", parse_mode="HTML")


# ─────────────────────────────────────────────
# /joke — случайная шутка
# ─────────────────────────────────────────────

JOKES = [
    "Почему программисты путают Хэллоуин и Рождество? Потому что 31 Oct = 25 Dec!",
    "Сколько программистов нужно, чтобы заменить лампочку? Ни одного — это аппаратная проблема!",
    "Что говорит один бит другому? — Ты меня дополняешь!",
    "Почему разработчики ненавидят открытые офисы? Потому что там слишком много тегов",
    "Что такое идеальный код? Тот, который не написан",
    "Почему админ носит тёмные очки? Чтобы солнце не мешало видеть синий экран смерти",
    "Как отличить хоббита от программиста? Хоббит с радостью идёт в горы, а программист — только в горы с ноутбуком",
    "Что такое 8 бит? Это 1 байт удачи!",
    "Почему программисты всегда холодные? Потому что на улице +0, а в комнате +10 (системы счисления)",
    "Как программист ловит рыбу? Он экземплярит класс Удочка и вызывает метод catch()",
]

@dp.message(Command("joke"))
async def cmd_joke(message: Message):
    if not is_allowed_chat(message):
        return
    
    joke = random.choice(JOKES)
    await message.reply(f"😂 {joke}", parse_mode="HTML")


# ─────────────────────────────────────────────
# Личка — анонимные сообщения
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
