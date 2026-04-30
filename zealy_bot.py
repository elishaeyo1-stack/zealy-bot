"""
Zealy Quest Tracker - Telegram Bot
-----------------------------------
Monitors Zealy communities for new quests and sends alerts to Telegram chats/groups.

Requirements:
    pip install python-telegram-bot aiohttp

Setup:
    1. Create a bot via @BotFather on Telegram → get your BOT_TOKEN
    2. Fill in BOT_TOKEN below (or set as env variable)
    3. Run: python zealy_bot.py
"""

import os
import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

# ─── CONFIG ───────────────────────────────────────────────────────────────────

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8760528784:AAFSLmZZGMT4LmoxjDQmaDTqt4Xc8D3_NRc")
DATA_FILE = "zealy_data.json"          # Persists tracked communities + seen quest IDs
DEFAULT_INTERVAL = 15                  # Default poll interval in minutes (users can change)
ZEALY_API = "https://api-v2.zealy.io/public/communities/{subdomain}/quests"

# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ─── PERSISTENCE ──────────────────────────────────────────────────────────────

def load_data() -> dict:
    """Load persisted data from disk."""
    if Path(DATA_FILE).exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return {}


def save_data(data: dict):
    """Save data to disk."""
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_chat_data(data: dict, chat_id: str) -> dict:
    """Get or create per-chat data structure."""
    if chat_id not in data:
        data[chat_id] = {
            "communities": {},   # subdomain → { api_key, seen_ids }
            "interval": DEFAULT_INTERVAL,
        }
    return data[chat_id]

# ─── ZEALY API ────────────────────────────────────────────────────────────────

async def fetch_quests(subdomain: str, api_key: str = "") -> list | None:
    """Fetch quests for a community. Returns list or None on error."""
    url = ZEALY_API.format(subdomain=subdomain)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if isinstance(result, list):
                        return result
                    return result.get("items") or result.get("quests") or []
                log.warning(f"Zealy API {subdomain} returned {resp.status}")
                return None
    except Exception as e:
        log.error(f"Error fetching {subdomain}: {e}")
        return None

# ─── FORMATTING ───────────────────────────────────────────────────────────────

TASK_EMOJI = {
    "twitter": "🐦", "twitterFollow": "🐦", "tweet": "🐦", "tweetReact": "🐦",
    "discord": "💬", "telegram": "✈️", "url": "🔗", "visitLink": "👁",
    "quiz": "❓", "text": "📝", "file": "📎", "api": "⚡",
    "invites": "👥", "poll": "📊", "opinion": "💭", "tiktok": "🎵",
    "onChain": "⛓", "partnership": "🤝",
}

def format_quest(quest: dict, subdomain: str) -> str:
    name = quest.get("name", "Unnamed Quest")
    tasks = quest.get("tasks", [])
    rewards = quest.get("rewards", [])
    recurrence = quest.get("recurrence", "once")
    published = quest.get("published", False)

    task_icons = " ".join(TASK_EMOJI.get(t.get("type", ""), "•") for t in tasks) or "🎯"
    xp = next((r["value"] for r in rewards if r.get("type") == "xp"), None)
    xp_str = f"  ⚡ *{xp} XP*" if xp else ""
    rec_str = f"  🔄 `{recurrence}`" if recurrence != "once" else ""
    status = "🟢" if published else "🟡"
    link = f"https://zealy.io/cw/{subdomain}/questboard"

    return (
        f"{status} *{name}*\n"
        f"{task_icons}{xp_str}{rec_str}\n"
        f"[View on Zealy]({link})"
    )


def format_new_quest_alert(quests: list, subdomain: str) -> str:
    count = len(quests)
    header = f"🚨 *{count} new quest{'s' if count > 1 else ''} dropped in* `{subdomain}`!\n\n"
    bodies = []
    for q in quests[:5]:  # cap at 5 per message
        bodies.append(format_quest(q, subdomain))
    msg = header + "\n\n─────────────\n\n".join(bodies)
    if count > 5:
        msg += f"\n\n_...and {count - 5} more. Check Zealy for all quests._"
    return msg

# ─── COMMANDS ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Zealy Quest Tracker*\n\n"
        "I watch Zealy communities and alert you when new quests drop.\n\n"
        "*Commands:*\n"
        "/add `subdomain` — Start tracking a community\n"
        "/add `subdomain` `api_key` — Track a private community\n"
        "/remove `subdomain` — Stop tracking\n"
        "/list — Show tracked communities\n"
        "/check — Check for new quests right now\n"
        "/interval `minutes` — Set poll interval (e.g. `/interval 10`)\n"
        "/status — Show bot status\n"
        "/help — Show this message"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    args = ctx.args

    if not args:
        await update.message.reply_text(
            "Usage: `/add subdomain` or `/add subdomain api_key`",
            parse_mode="Markdown"
        )
        return

    subdomain = args[0].lower().strip()
    api_key = args[1] if len(args) > 1 else ""

    msg = await update.message.reply_text(f"🔍 Checking `{subdomain}`...", parse_mode="Markdown")

    quests = await fetch_quests(subdomain, api_key)
    if quests is None:
        await msg.edit_text(
            f"❌ Could not reach `{subdomain}`. Check the subdomain and try again.",
            parse_mode="Markdown"
        )
        return

    data = load_data()
    chat = get_chat_data(data, chat_id)

    seen_ids = [q["id"] for q in quests if "id" in q]
    chat["communities"][subdomain] = {
        "api_key": api_key,
        "seen_ids": seen_ids,
        "added_at": datetime.utcnow().isoformat(),
    }
    save_data(data)

    await msg.edit_text(
        f"✅ Now tracking *{subdomain}*\n"
        f"Found *{len(quests)}* existing quests (marked as seen).\n"
        f"You'll be notified when new ones drop! 🎯",
        parse_mode="Markdown"
    )


async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not ctx.args:
        await update.message.reply_text("Usage: `/remove subdomain`", parse_mode="Markdown")
        return

    subdomain = ctx.args[0].lower().strip()
    data = load_data()
    chat = get_chat_data(data, chat_id)

    if subdomain not in chat["communities"]:
        await update.message.reply_text(f"❓ `{subdomain}` is not being tracked.", parse_mode="Markdown")
        return

    del chat["communities"][subdomain]
    save_data(data)
    await update.message.reply_text(f"🗑 Stopped tracking *{subdomain}*.", parse_mode="Markdown")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    chat = get_chat_data(data, chat_id)
    communities = chat["communities"]

    if not communities:
        await update.message.reply_text(
            "You're not tracking any communities yet.\nUse `/add subdomain` to start.",
            parse_mode="Markdown"
        )
        return

    interval = chat.get("interval", DEFAULT_INTERVAL)
    lines = [f"📋 *Tracked Communities* (polling every {interval}min)\n"]
    for sub, info in communities.items():
        count = len(info.get("seen_ids", []))
        key_str = " 🔑" if info.get("api_key") else ""
        lines.append(f"• `{sub}`{key_str} — {count} quests seen")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    chat = get_chat_data(data, chat_id)
    communities = chat["communities"]

    if not communities:
        await update.message.reply_text("No communities tracked. Use `/add subdomain` first.", parse_mode="Markdown")
        return

    msg = await update.message.reply_text("🔍 Checking all communities...", parse_mode="Markdown")
    found_any = False

    for subdomain, info in communities.items():
        quests = await fetch_quests(subdomain, info.get("api_key", ""))
        if quests is None:
            await update.message.reply_text(f"⚠️ Could not reach `{subdomain}`.", parse_mode="Markdown")
            continue

        seen = set(info.get("seen_ids", []))
        new_quests = [q for q in quests if q.get("id") and q["id"] not in seen]

        if new_quests:
            found_any = True
            # Update seen IDs
            info["seen_ids"] = list(seen | {q["id"] for q in quests})
            save_data(data)
            alert = format_new_quest_alert(new_quests, subdomain)
            await update.message.reply_text(alert, parse_mode="Markdown", disable_web_page_preview=True)
        else:
            info["seen_ids"] = list({q["id"] for q in quests if "id" in q})
            save_data(data)

    if not found_any:
        await msg.edit_text("✅ All up to date — no new quests found.", parse_mode="Markdown")
    else:
        await msg.delete()


async def cmd_interval(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Usage: `/interval 10` (minutes, min 1)", parse_mode="Markdown")
        return

    minutes = max(1, int(ctx.args[0]))
    data = load_data()
    chat = get_chat_data(data, chat_id)
    chat["interval"] = minutes
    save_data(data)

    # Reschedule jobs for this chat
    schedule_poll_job(ctx.application, chat_id, minutes)

    await update.message.reply_text(
        f"⏱ Poll interval set to *{minutes} minute{'s' if minutes != 1 else ''}*.",
        parse_mode="Markdown"
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    chat = get_chat_data(data, chat_id)
    interval = chat.get("interval", DEFAULT_INTERVAL)
    count = len(chat["communities"])

    await update.message.reply_text(
        f"📊 *Bot Status*\n\n"
        f"Communities tracked: *{count}*\n"
        f"Poll interval: *{interval} min*\n"
        f"Total chats: *{len(data)}*",
        parse_mode="Markdown"
    )

# ─── POLLING JOB ──────────────────────────────────────────────────────────────

async def poll_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: check for new quests across all communities for this chat."""
    chat_id = context.job.chat_id
    data = load_data()
    chat = data.get(str(chat_id))
    if not chat:
        return

    for subdomain, info in chat["communities"].items():
        quests = await fetch_quests(subdomain, info.get("api_key", ""))
        if quests is None:
            log.warning(f"Poll failed for {subdomain} (chat {chat_id})")
            continue

        seen = set(info.get("seen_ids", []))
        new_quests = [q for q in quests if q.get("id") and q["id"] not in seen]

        if new_quests:
            info["seen_ids"] = list(seen | {q["id"] for q in quests})
            save_data(data)
            alert = format_new_quest_alert(new_quests, subdomain)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=alert,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
                log.info(f"Sent {len(new_quests)} new quest alerts for {subdomain} → chat {chat_id}")
            except Exception as e:
                log.error(f"Failed to send alert to {chat_id}: {e}")
        else:
            # Keep seen_ids fresh
            info["seen_ids"] = list({q["id"] for q in quests if "id" in q})
            save_data(data)


def schedule_poll_job(app: Application, chat_id: str, interval_minutes: int):
    """Remove old job for this chat and schedule a new one."""
    job_name = f"poll_{chat_id}"
    current = app.job_queue.get_jobs_by_name(job_name)
    for job in current:
        job.schedule_removal()
    app.job_queue.run_repeating(
        poll_job,
        interval=interval_minutes * 60,
        first=interval_minutes * 60,
        chat_id=int(chat_id),
        name=job_name,
    )
    log.info(f"Scheduled poll job for chat {chat_id} every {interval_minutes}min")

# ─── STARTUP ──────────────────────────────────────────────────────────────────

async def on_startup(app: Application):
    """On bot start, restore poll jobs for all existing chats."""
    data = load_data()
    for chat_id, chat in data.items():
        if chat.get("communities"):
            interval = chat.get("interval", DEFAULT_INTERVAL)
            schedule_poll_job(app, chat_id, interval)
            log.info(f"Restored poll job for chat {chat_id} ({interval}min)")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    if BOT_TOKEN == "8760528784:AAFSLmZZGMT4LmoxjDQmaDTqt4Xc8D3_NRc":
        print("❌ Please set your BOT_TOKEN in the script or via TELEGRAM_BOT_TOKEN env variable.")
        return

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("interval", cmd_interval))
    app.add_handler(CommandHandler("status", cmd_status))

    log.info("🤖 Zealy Quest Tracker bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
