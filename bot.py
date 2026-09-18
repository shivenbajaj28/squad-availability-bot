"""Squad availability bot for NUS Cricket.

Admin posts a fixture, everyone taps Yes / No / Maybe, the bot keeps a live
tally and chases whoever has not answered before the deadline.
"""

import html
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "")
TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Singapore"))
REMINDER_LEAD_HOURS = float(os.getenv("REMINDER_LEAD_HOURS", "24"))
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

ANSWERS = {"yes": "Yes", "no": "No", "maybe": "Maybe"}

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("squadbot")


# ----------------------------------------------------------------- helpers

def parse_when(text):
    """Accept '2026-09-19 14:00', '19/09/2026 14:00' or '19-09-2026 14:00'."""
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M", "%Y-%m-%d %H%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    raise ValueError(f"Could not read the date and time: {text}")


def pretty(dt_iso):
    dt = datetime.fromisoformat(dt_iso)
    return dt.strftime("%a %d %b, %H:%M")


def mention(user_id, name):
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


def display_name(user):
    return user.full_name or user.username or str(user.id)


async def is_admin(update, context):
    user = update.effective_user
    if user.id in ADMIN_IDS:
        return True
    chat = update.effective_chat
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        try:
            member = await chat.get_member(user.id)
            return member.status in ("creator", "administrator")
        except TelegramError:
            return False
    return False


def keyboard(fixture_id):
    row = [
        InlineKeyboardButton("Yes", callback_data=f"a:{fixture_id}:yes"),
        InlineKeyboardButton("No", callback_data=f"a:{fixture_id}:no"),
        InlineKeyboardButton("Maybe", callback_data=f"a:{fixture_id}:maybe"),
    ]
    return InlineKeyboardMarkup([row])


def fixture_text(fixture):
    """The live tally message that sits under the buttons."""
    rows = db.responses_for(fixture["id"])
    buckets = {"yes": [], "no": [], "maybe": []}
    for r in rows:
        buckets[r["answer"]].append(r["name"])

    deadline = datetime.fromisoformat(fixture["deadline_at"])
    closed = datetime.now(TZ) > deadline

    lines = [
        f"<b>{html.escape(fixture['name'])}</b>",
        f"Match: {pretty(fixture['match_at'])}",
        f"Reply by: {pretty(fixture['deadline_at'])}" + ("  (closed)" if closed else ""),
        "",
        f"Yes {len(buckets['yes'])}   No {len(buckets['no'])}   Maybe {len(buckets['maybe'])}",
    ]

    for key in ("yes", "no", "maybe"):
        if buckets[key]:
            names = ", ".join(html.escape(n) for n in buckets[key])
            lines.append(f"<b>{ANSWERS[key]}:</b> {names}")

    waiting = db.non_responders(fixture["id"], fixture["chat_id"])
    if waiting:
        names = ", ".join(html.escape(u["name"]) for u in waiting)
        lines.append(f"<b>No response ({len(waiting)}):</b> {names}")

    lines.append("")
    lines.append("Tap a button. You can change your answer any time before the deadline.")
    return "\n".join(lines)


async def refresh_message(context, fixture):
    if not fixture["message_id"]:
        return
    try:
        await context.bot.edit_message_text(
            chat_id=fixture["chat_id"],
            message_id=fixture["message_id"],
            text=fixture_text(fixture),
            reply_markup=keyboard(fixture["id"]),
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as exc:
        # "message is not modified" is harmless and common
        log.debug("edit skipped: %s", exc)


# ---------------------------------------------------------------- commands

HELP = (
    "<b>Squad availability bot</b>\n\n"
    "/fixture - post a new fixture (admins, in the squad group)\n"
    "    <code>/fixture NUS vs NTU | 2026-09-19 14:00 | 2026-09-18 20:00</code>\n"
    "    match name | match time | reply-by deadline\n"
    "/squad - show who is in, out, unsure and silent\n"
    "/help - this message\n\n"
    "DM me /start once so I can chase you privately instead of in the group."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.effective_chat.type == ChatType.PRIVATE:
        db.mark_dm_ok(user.id)
        await update.message.reply_text(
            "You are set. I will message you here when a fixture needs your answer.\n\n"
            "If nothing happens, make sure you have sent at least one message in the "
            "squad group so I know you are in the squad.",
        )
    else:
        await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)


async def cmd_fixture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await update.message.reply_text("Run /fixture inside the squad group.")
        return

    if not await is_admin(update, context):
        await update.message.reply_text("Only group admins can post a fixture.")
        return

    raw = update.message.text.partition(" ")[2]
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    if len(parts) < 2:
        await update.message.reply_text(
            "Format:\n<code>/fixture NUS vs NTU | 2026-09-19 14:00 | 2026-09-18 20:00</code>\n"
            "The third part (reply-by deadline) is optional and defaults to the match time.",
            parse_mode=ParseMode.HTML,
        )
        return

    name = parts[0]
    try:
        match_at = parse_when(parts[1])
        deadline_at = parse_when(parts[2]) if len(parts) > 2 else match_at
    except ValueError as exc:
        await update.message.reply_text(f"{exc}\nUse YYYY-MM-DD HH:MM or DD/MM/YYYY HH:MM.")
        return

    fixture_id = db.create_fixture(chat.id, name, match_at, deadline_at, update.effective_user.id)
    fixture = db.get_fixture(fixture_id)

    sent = await context.bot.send_message(
        chat_id=chat.id,
        text=fixture_text(fixture),
        reply_markup=keyboard(fixture_id),
        parse_mode=ParseMode.HTML,
    )
    db.set_fixture_message(fixture_id, sent.message_id)
    try:
        await context.bot.pin_chat_message(chat.id, sent.message_id, disable_notification=True)
    except TelegramError:
        pass

    schedule_reminder(context.application, db.get_fixture(fixture_id))


async def cmd_squad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fixture = db.latest_fixture(update.effective_chat.id)
    if not fixture:
        await update.message.reply_text("No fixture posted yet. Use /fixture to start one.")
        return
    await update.message.reply_text(fixture_text(fixture), parse_mode=ParseMode.HTML)


# --------------------------------------------------------------- callbacks

async def on_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, fixture_id, answer = query.data.split(":")
    fixture_id = int(fixture_id)
    fixture = db.get_fixture(fixture_id)

    if not fixture:
        await query.answer("That fixture is gone.", show_alert=True)
        return

    deadline = datetime.fromisoformat(fixture["deadline_at"])
    if datetime.now(TZ) > deadline:
        await query.answer("Responses closed for this fixture.", show_alert=True)
        return

    user = query.from_user
    db.remember_user(user.id, fixture["chat_id"], display_name(user), user.username)
    db.set_response(fixture_id, user.id, display_name(user), answer)

    await query.answer(f"Marked {ANSWERS[answer]}")
    await refresh_message(context, fixture)


# ------------------------------------------------------------------ roster

async def observe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Anyone who speaks in the group is part of the squad roster."""
    chat = update.effective_chat
    user = update.effective_user
    if not user or user.is_bot:
        return
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        db.remember_user(user.id, chat.id, display_name(user), user.username)


async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    for member in update.message.new_chat_members or []:
        if not member.is_bot:
            db.remember_user(member.id, chat.id, display_name(member), member.username)


# --------------------------------------------------------------- reminders

def reminder_time(fixture):
    deadline = datetime.fromisoformat(fixture["deadline_at"])
    when = deadline - timedelta(hours=REMINDER_LEAD_HOURS)
    earliest = datetime.now(TZ) + timedelta(minutes=2)
    return max(when, earliest)


def schedule_reminder(application, fixture):
    if fixture["reminded"]:
        return
    when = reminder_time(fixture)
    if when > datetime.fromisoformat(fixture["deadline_at"]):
        return
    application.job_queue.run_once(
        send_reminder,
        when=when,
        data={"fixture_id": fixture["id"]},
        name=f"reminder:{fixture['id']}",
    )
    log.info("Reminder for fixture %s scheduled at %s", fixture["id"], when)


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    fixture_id = context.job.data["fixture_id"]
    fixture = db.get_fixture(fixture_id)
    if not fixture or fixture["reminded"]:
        return

    waiting = db.non_responders(fixture_id, fixture["chat_id"])
    db.mark_reminded(fixture_id)
    if not waiting:
        log.info("Fixture %s: everyone answered, no reminder needed", fixture_id)
        return

    text = (
        f"Quick one: are you playing <b>{html.escape(fixture['name'])}</b> "
        f"on {pretty(fixture['match_at'])}?\n"
        f"Answers close {pretty(fixture['deadline_at'])}. "
        "Tap Yes, No or Maybe on the pinned message in the squad group."
    )

    unreachable = []
    for user in waiting:
        try:
            await context.bot.send_message(user["user_id"], text, parse_mode=ParseMode.HTML)
        except TelegramError:
            unreachable.append(user)

    if unreachable:
        names = ", ".join(mention(u["user_id"], u["name"]) for u in unreachable)
        await context.bot.send_message(
            fixture["chat_id"],
            f"Still waiting on: {names}\nAnswers close {pretty(fixture['deadline_at'])}.",
            parse_mode=ParseMode.HTML,
        )

    log.info(
        "Fixture %s: reminded %s players (%s could not be DMed)",
        fixture_id, len(waiting), len(unreachable),
    )


async def on_startup(application):
    """Redis-free persistence: rebuild pending reminder jobs after a restart."""
    for fixture in db.pending_reminders():
        schedule_reminder(application, fixture)


# -------------------------------------------------------------------- main

def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    db.init()

    application = Application.builder().token(TOKEN).post_init(on_startup).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("fixture", cmd_fixture))
    application.add_handler(CommandHandler("squad", cmd_squad))
    application.add_handler(CallbackQueryHandler(on_answer, pattern=r"^a:\d+:(yes|no|maybe)$"))
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members)
    )
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, observe), group=1
    )

    log.info("Bot running. Reminder lead time: %s hours", REMINDER_LEAD_HOURS)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
