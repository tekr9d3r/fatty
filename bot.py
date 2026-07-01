import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime

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


def _confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes, log it", callback_data="confirm_yes"),
        InlineKeyboardButton("Correct it", callback_data="confirm_correct"),
        InlineKeyboardButton("No, cancel", callback_data="confirm_no"),
    ]])


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /goal 2200")
        return
    goal = int(args[0])
    user_store.set_goal(update.effective_user.id, goal)
    await update.message.reply_text(f"Daily goal set to {goal} kcal.")


async def today_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        rows = await asyncio.to_thread(sheets_client.read_recent_days, _sheets_service, 1)
    except Exception as e:
        await update.message.reply_text(f"Error reading sheet: {e}")
        return

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

    goal = user_store.get_goal(user_id)
    budget = (goal or 0) + burned_cal
    remaining = budget - food_cal

    header = f"Today ({today})\n"
    if not today_rows:
        header += "Nothing logged yet.\n"
    else:
        header += "\n".join(lines) + "\n"

    header += f"\nIntake:  {food_cal} kcal"
    header += f"\nBurned:  {burned_cal} kcal"
    if goal:
        header += f"\nGoal:    {goal} kcal"
        header += f"\nBudget:  {budget} kcal"
        header += f"\nLeft:    {remaining} kcal"
    else:
        header += "\n\nNo goal set — use /goal to set one."

    await update.message.reply_text(header)


async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    lines = [f"Last {n_days} day(s) summary:"]
    for date in sorted(by_date.keys(), reverse=True):
        d = by_date[date]
        net = d["food"] - d["burned"]
        lines.append(
            f"\n{date}\n"
            f"  Intake: {d['food']} kcal | Burned: {d['burned']} kcal | Net: {net} kcal"
        )

    await update.message.reply_text("\n".join(lines))


async def undo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        await thinking_msg.edit_text(f"Error estimating: {e}")
        return

    user_store.pending[user_id] = entry
    await thinking_msg.edit_text(
        f"{entry['display_text']}\n\nLog this?",
        reply_markup=_confirmation_keyboard(),
    )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

        now = datetime.now()
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


def main() -> None:
    load_dotenv()

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

    logger.info("Bot started. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
