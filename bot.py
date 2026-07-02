import asyncio
import datetime
import logging
import os
from collections import defaultdict
from datetime import datetime as dt
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Ljubljana")

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import claude_client
import sheets_client
import user_store

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

USER_PROFILE = {"gender": "male", "height_cm": 192, "weight_kg": 108}

# Comma-separated Telegram user IDs allowed to use this bot
_ALLOWED_IDS: set[int] = set()


def _load_allowed_ids() -> None:
    raw = os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "")
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            _ALLOWED_IDS.add(int(part))


def _is_allowed(update: Update) -> bool:
    if not _ALLOWED_IDS:
        return True  # no whitelist configured — open access
    return update.effective_user.id in _ALLOWED_IDS

WORKOUT_KEYWORDS = {
    "swam", "swim", "swimming", "ran", "run", "running", "jog", "jogged", "jogging",
    "cycled", "cycling", "bike", "biked", "biking", "walked", "walking", "hike", "hiked",
    "hiking", "gym", "workout", "exercise", "exercised", "trained", "training", "lifted",
    "lifting", "rowing", "rowed", "yoga", "pilates", "crossfit", "hr", "bpm",
    "heart rate", "minutes", "km", "miles", "laps", "reps", "sets", "pushups", "situps",
    "squats", "elliptical", "treadmill", "spinning",
}

_sheets_service = None


def get_sheets_service():
    return _sheets_service


def is_workout(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in WORKOUT_KEYWORDS)


def _progress_bar(current: int, total: int, width: int = 18) -> str:
    if total <= 0:
        return f"[{'░' * width}] —"
    ratio = min(current / total, 1.0)
    filled = round(ratio * width)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(ratio * 100)
    over = current > total
    flag = " ⚠️" if over else ""
    return f"[{bar}] {pct}%{flag}"


def _confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes, log it", callback_data="confirm_yes"),
        InlineKeyboardButton("Correct it", callback_data="confirm_correct"),
        InlineKeyboardButton("No, cancel", callback_data="confirm_no"),
    ]])


async def morning_reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text="Good morning! 🌅 Don't forget to log your breakfast.",
    )


async def evening_recap_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.user_id
    chat_id = context.job.chat_id
    try:
        text = await _build_today_summary(user_id)
    except Exception as e:
        text = f"Could not load daily recap: {e}"
    await context.bot.send_message(chat_id=chat_id, text=f"Evening recap 🌙\n\n{text}")


def _schedule_user_jobs(app, chat_id: int, user_id: int) -> None:
    # Remove existing jobs for this user to avoid duplicates
    for name in (f"morning_{user_id}", f"evening_{user_id}"):
        existing = app.job_queue.get_jobs_by_name(name)
        for job in existing:
            job.schedule_removal()

    app.job_queue.run_daily(
        morning_reminder_job,
        time=datetime.time(7, 0, tzinfo=TZ),
        chat_id=chat_id,
        user_id=user_id,
        name=f"morning_{user_id}",
    )
    app.job_queue.run_daily(
        evening_recap_job,
        time=datetime.time(21, 0, tzinfo=TZ),
        chat_id=chat_id,
        user_id=user_id,
        name=f"evening_{user_id}",
    )


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    await asyncio.to_thread(sheets_client.set_chat_id, _sheets_service, user_id, chat_id)
    _schedule_user_jobs(context.application, chat_id, user_id)
    await update.message.reply_text(
        "Welcome to Fatty — your personal calorie tracker!\n\n"
        "Just send me what you ate:\n"
        "  • Text: \"had a banana and coffee with milk\"\n"
        "  • Photo: send a picture of your meal\n"
        "  • Workout: \"swam 47 min, 2050m, avg HR 133\"\n\n"
        "Commands:\n"
        "  /goal 2200 — set your daily calorie goal\n"
        "  /today — today's intake, burned & remaining\n"
        "  /history 7 — last N days summary\n"
        "  /undo — remove your last logged entry"
    )


async def goal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /goal 2200")
        return
    goal = int(args[0])
    user_id = update.effective_user.id
    await asyncio.to_thread(sheets_client.set_user_goal, _sheets_service, user_id, goal)
    await update.message.reply_text(f"Daily goal set to {goal} kcal.")


async def _build_today_summary(user_id: int) -> str:
    today = dt.now(tz=TZ).strftime("%Y-%m-%d")
    rows = await asyncio.to_thread(sheets_client.read_recent_days, _sheets_service, 1)
    today_rows = [r for r in rows if len(r) >= 5 and r[0] == today]

    food_cal = 0
    burned_cal = 0
    lines = []
    for r in today_rows:
        entry_type = r[2] if len(r) > 2 else "?"
        item = r[3] if len(r) > 3 else "?"
        try:
            cal = int(r[4])
        except (ValueError, IndexError):
            cal = 0
        if entry_type == "Workout":
            burned_cal += abs(cal)
            lines.append(f"  🏃 {item}: {abs(cal)} kcal burned")
        else:
            food_cal += cal
            lines.append(f"  🍽 {item}: {cal} kcal")

    goal = await asyncio.to_thread(sheets_client.get_user_goal, _sheets_service, user_id)
    budget = (goal or 0) + burned_cal
    remaining = budget - food_cal

    parts = [f"Today ({today})"]
    if not today_rows:
        parts.append("Nothing logged yet.")
    else:
        parts.append("\n".join(lines))

    parts.append("")
    parts.append(f"Intake:  {food_cal} kcal")
    parts.append(f"Burned:  {burned_cal} kcal")

    if goal:
        parts.append(f"Budget:  {budget} kcal  (goal {goal} + {burned_cal} burned)")
        parts.append("")
        parts.append(_progress_bar(food_cal, budget))
        parts.append(f"{food_cal} / {budget} kcal — {remaining} remaining")
    else:
        parts.append("\nNo goal set — use /goal to set one.")

    return "\n".join(parts)


async def today_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    user_id = update.effective_user.id
    try:
        text = await _build_today_summary(user_id)
    except Exception as e:
        await update.message.reply_text(f"Error reading sheet: {e}")
        return
    await update.message.reply_text(text)


async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    args = context.args
    n_days = 7
    if args and args[0].isdigit():
        n_days = max(1, min(int(args[0]), 90))

    try:
        rows = await asyncio.to_thread(sheets_client.read_recent_days, _sheets_service, n_days)
    except Exception as e:
        await update.message.reply_text(f"Error reading sheet: {e}")
        return

    if not rows:
        await update.message.reply_text(f"No entries in the last {n_days} day(s).")
        return

    # Group by date
    by_date: dict[str, dict] = defaultdict(lambda: {"food": 0, "burned": 0})
    for r in rows:
        if len(r) < 5:
            continue
        date = r[0]
        entry_type = r[2] if len(r) > 2 else ""
        try:
            cal = int(r[4])
        except (ValueError, IndexError):
            cal = 0
        if entry_type == "Workout":
            by_date[date]["burned"] += abs(cal)
        else:
            by_date[date]["food"] += cal

    goal = await asyncio.to_thread(sheets_client.get_user_goal, _sheets_service, update.effective_user.id)

    lines = [f"Last {n_days} day(s) summary:"]
    for date in sorted(by_date.keys(), reverse=True):
        d = by_date[date]
        budget = (goal or 0) + d["burned"]
        net = d["food"] - d["burned"]
        bar = _progress_bar(d["food"], budget) if goal else ""
        entry = f"\n{date}\n  {bar}\n  Intake: {d['food']} kcal | Burned: {d['burned']} kcal | Net: {net} kcal"
        lines.append(entry)

    await update.message.reply_text("\n".join(lines))


async def undo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    user_id = update.effective_user.id
    last_row = user_store.get_last_row(user_id)
    if last_row is None:
        await update.message.reply_text("Nothing to undo.")
        return
    try:
        await asyncio.to_thread(sheets_client.delete_row, _sheets_service, last_row)
    except Exception as e:
        await update.message.reply_text(f"Error deleting row: {e}")
        return
    user_store.set_last_row(user_id, None)
    await update.message.reply_text("Last entry removed.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    text = update.message.text
    user_id = update.effective_user.id

    # If the user is correcting a previous estimate, handle that first
    existing = user_store.pending.get(user_id)
    if existing and existing.get("awaiting_correction"):
        thinking_msg = await update.message.reply_text("Revising estimate...")
        try:
            result = await claude_client.correct_estimate(
                existing["original_description"],
                existing["claude_result"],
                text,
            )
        except Exception as e:
            await thinking_msg.edit_text(f"Error revising estimate: {e}")
            return
        items_str = "\n".join(
            f"  • {i['name']}: {i['calories']} kcal" for i in result["items"]
        )
        existing.update({
            "item": ", ".join(i["name"] for i in result["items"])[:120],
            "calories": result["total"],
            "notes": result["notes"],
            "display_text": (
                f"{items_str}\n"
                f"Total: {result['total']} kcal\n"
                f"Notes: {result['notes']}"
            ),
            "claude_result": result,
            "awaiting_correction": False,
        })
        user_store.pending[user_id] = existing
        await thinking_msg.edit_text(
            f"Revised estimate:\n{existing['display_text']}\n\nLog this?",
            reply_markup=_confirmation_keyboard(),
        )
        return

    thinking_msg = await update.message.reply_text("Thinking...")

    try:
        if is_workout(text):
            result = await claude_client.estimate_workout(text, USER_PROFILE)
            entry = {
                "type": "Workout",
                "item": result["activity"],
                "calories": -abs(result["calories_burned"]),
                "notes": result["notes"],
                "display_text": (
                    f"Workout: {result['activity']}\n"
                    f"Duration: {result['duration_min']} min\n"
                    f"Calories burned: {result['calories_burned']} kcal\n"
                    f"Notes: {result['notes']}"
                ),
                "original_description": text,
                "claude_result": result,
                "awaiting_correction": False,
            }
        else:
            result = await claude_client.estimate_food(text)
            items_str = "\n".join(
                f"  • {i['name']}: {i['calories']} kcal" for i in result["items"]
            )
            entry = {
                "type": "Food",
                "item": text[:120],
                "calories": result["total"],
                "notes": result["notes"],
                "display_text": (
                    f"{items_str}\n"
                    f"Total: {result['total']} kcal\n"
                    f"Notes: {result['notes']}"
                ),
                "original_description": text,
                "claude_result": result,
                "awaiting_correction": False,
            }
    except Exception as e:
        _log_exception("text_handler estimate", e)
        await thinking_msg.edit_text(f"Error estimating: {type(e).__name__}: {e}")
        return

    user_store.pending[user_id] = entry
    await thinking_msg.edit_text(
        f"{entry['display_text']}\n\nLog this?",
        reply_markup=_confirmation_keyboard(),
    )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    user_id = update.effective_user.id
    thinking_msg = await update.message.reply_text("Analyzing your photo...")

    try:
        photo = update.message.photo[-1]  # largest resolution
        file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await file.download_as_bytearray())
        result = await claude_client.estimate_food_from_photo(image_bytes, "image/jpeg")
    except Exception as e:
        await thinking_msg.edit_text(f"Error analyzing photo: {e}")
        return

    items_str = "\n".join(
        f"  • {i['name']}: {i['calories']} kcal" for i in result["items"]
    )
    entry = {
        "type": "Food",
        "item": ", ".join(i["name"] for i in result["items"])[:120],
        "calories": result["total"],
        "notes": result["notes"],
        "display_text": (
            f"{items_str}\n"
            f"Total: {result['total']} kcal\n"
            f"Notes: {result['notes']}"
        ),
        "original_description": "food from photo",
        "claude_result": result,
        "awaiting_correction": False,
    }

    user_store.pending[user_id] = entry
    await thinking_msg.edit_text(
        f"{entry['display_text']}\n\nLog this?",
        reply_markup=_confirmation_keyboard(),
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # must answer quickly to remove loading spinner

    user_id = query.from_user.id

    if query.data == "confirm_yes":
        entry = user_store.pending.pop(user_id, None)
        if entry is None:
            await query.edit_message_text("Session expired. Please resend your message.")
            return

        now = dt.now(tz=TZ)
        row = [
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M"),
            entry["type"],
            entry["item"],
            entry["calories"],
            entry["notes"],
        ]
        try:
            row_idx = await asyncio.to_thread(sheets_client.append_row, _sheets_service, row)
        except Exception as e:
            await query.edit_message_text(f"Error writing to sheet: {e}")
            return

        user_store.set_last_row(user_id, row_idx)

        verb = "burned" if entry["type"] == "Workout" else "logged"
        cal_abs = abs(entry["calories"])
        await query.edit_message_text(
            f"Logged! {cal_abs} kcal {verb}.\n\n"
            f"Use /today to see your daily summary."
        )

    elif query.data == "confirm_correct":
        entry = user_store.pending.get(user_id)
        if entry is None:
            await query.edit_message_text("Session expired. Please resend your message.")
            return
        entry["awaiting_correction"] = True
        await query.edit_message_text(
            "What should I correct? Just tell me (e.g. \"only 2 pieces, not 3\"):"
        )

    elif query.data == "confirm_no":
        user_store.pending.pop(user_id, None)
        await query.edit_message_text("Cancelled, nothing logged.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception", exc_info=context.error)


def _log_exception(label: str, e: Exception) -> None:
    logger.error("%s: %s: %s", label, type(e).__name__, e, exc_info=True)


def main() -> None:
    load_dotenv()
    _load_allowed_ids()

    global _sheets_service

    # Support both a file path (local dev) and raw JSON content (Railway/Render)
    sa_content = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT")
    if sa_content:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write(sa_content)
        tmp.close()
        json_path = tmp.name
    else:
        json_path = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

    _sheets_service = sheets_client.build_service(json_path)
    logger.info("Google Sheets service initialized.")

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("goal", goal_handler))
    app.add_handler(CommandHandler("today", today_handler))
    app.add_handler(CommandHandler("history", history_handler))
    app.add_handler(CommandHandler("undo", undo_handler))

    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_error_handler(error_handler)

    # Schedule daily jobs for all previously registered users
    for uid_str, cid in sheets_client.get_chat_ids(_sheets_service):
        _schedule_user_jobs(app, cid, int(uid_str))
        logger.info("Scheduled daily jobs for user %s", uid_str)

    logger.info("Bot started. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
