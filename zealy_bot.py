import os
import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = "8760528784:AAFSLmZZGMT4LmoxjDQmaDTqt4Xc8D3_NRc"
DATA_FILE = "zealy_data.json"
DEFAULT_INTERVAL = 15
ZEALY_API = "https://api-v2.zealy.io/public/communities/{subdomain}/quests"

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

def load_data():
    if Path(DATA_FILE).exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_chat_data(data, chat_id):
    if chat_id not in data:
        data[chat_id] = {"communities": {}, "interval": DEFAULT_INTERVAL}
    return data[chat_id]

async def fetch_quests(subdomain, api_key=""):
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
                return None
    except Exception as e:
        log.error(f"Error fetching {subdomain}: {e}")
        return None

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Zealy Quest Tracker*\n\n"
        "I watch Zealy communities and alert you when new quests drop.\n\n"
        "*Commands:*\n"
        "/add subdomain — Start tracking\n"
        "/remove subdomain — Stop tracking\n"
        "/list — Show tracked communities\n"
        "/check — Check for new quests now\n"
        "/interval 10 — Set poll interval in minutes\n"
        "/status — Show bot status"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not ctx.args:
        await update.message.reply_text("Usage: /add subdomain", parse_mode="Markdown")
        return
    subdomain = ctx.args[0].lower().strip()
    api_key = ctx.args[1] if len(ctx.args) > 1 else ""
    msg = await update.message.reply_text(f"🔍 Checking `{subdomain}`...", parse_mode="Markdown")
    quests = await fetch_quests(subdomain, api_key)
    if quests is None:
        await msg.edit_text(f"❌ Could not reach `{subdomain}`. Check and try again.", parse_mode="Markdown")
        return
    data = load_data()
    chat = get_chat_data(data, chat_id)
    chat["communities"][subdomain] = {"api_key": api_key, "seen_ids": [q["id"] for q in quests if "id" in q], "added_at": datetime.utcnow().isoformat()}
    save_data(data)
    await msg.edit_text(f"✅ Now tracking *{subdomain}*\nFound *{len(quests)}* existing quests.\nYou'll be notified when new ones drop! 🎯", parse_mode="Markdown")

async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not ctx.args:
        await update.message.reply_text("Usage: /remove subdomain")
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
    if not chat["communities"]:
        await update.message.reply_text("No communities tracked yet. Use /add subdomain")
        return
    interval = chat.get("interval", DEFAULT_INTERVAL)
    lines = [f"📋 *Tracked Communities* (every {interval}min)\n"]
    for sub, info in chat["communities"].items():
        lines.append(f"• `{sub}` — {len(info.get('seen_ids', []))} quests seen")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    chat = get_chat_data(data, chat_id)
    if not chat["communities"]:
        await update.message.reply_text("No communities tracked. Use /add subdomain first.")
        return
    msg = await update.message.reply_text("🔍 Checking all communities...")
    found_any = False
    for subdomain, info in chat["communities"].items():
        quests = await fetch_quests(subdomain, info.get("api_key", ""))
        if quests is None:
            continue
        seen = set(info.get("seen_ids", []))
        new_quests = [q for q in quests if q.get("id") and q["id"] not in seen]
        if new_quests:
            found_any = True
            info["seen_ids"] = list(seen | {q["id"] for q in quests})
            save_data(data)
            text = f"🚨 *{len(new_quests)} new quest(s) in* `{subdomain}`!\n\n"
            for q in new_quests[:5]:
                text += f"🎯 *{q.get('name', 'Unnamed')}*\n"
            await update.message.reply_text(text, parse_mode="Markdown")
    if not found_any:
        await msg.edit_text("✅ No new quests found.")

async def cmd_interval(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Usage: /interval 10")
        return
    minutes = max(1, int(ctx.args[0]))
    data = load_data()
    chat = get_chat_data(data, chat_id)
    chat["interval"] = minutes
    save_data(data)
    schedule_poll_job(ctx.application, chat_id, minutes)
    await update.message.reply_text(f"⏱ Poll interval set to *{minutes} minutes*.", parse_mode="Markdown")

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    chat = get_chat_data(data, chat_id)
    await update.message.reply_text(f"📊 *Bot Status*\n\nCommunities: *{len(chat['communities'])}*\nInterval: *{chat.get('interval', DEFAULT_INTERVAL)} min*", parse_mode="Markdown")

async def poll_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    data = load_data()
    chat = data.get(str(chat_id))
    if not chat:
        return
    for subdomain, info in chat["communities"].items():
        quests = await fetch_quests(subdomain, info.get("api_key", ""))
        if quests is None:
            continue
        seen = set(info.get("seen_ids", []))
        new_quests = [q for q in quests if q.get("id") and q["id"] not in seen]
        if new_quests:
            info["seen_ids"] = list(seen | {q["id"] for q in quests})
            save_data(data)
            text = f"🚨 *{len(new_quests)} new quest(s) in* `{subdomain}`!\n\n"
            for q in new_quests[:5]:
                text += f"🎯 *{q.get('name', 'Unnamed')}*\n"
            try:
                await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
            except Exception as e:
                log.error(f"Failed to send alert: {e}")
        else:
            info["seen_ids"] = list({q["id"] for q in quests if "id" in q})
            save_data(data)

def schedule_poll_job(app, chat_id, interval_minutes):
    job_name = f"poll_{chat_id}"
    for job in app.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()
    app.job_queue.run_repeating(poll_job, interval=interval_minutes * 60, first=interval_minutes * 60, chat_id=int(chat_id), name=job_name)

async def on_startup(app):
    data = load_data()
    for chat_id, chat in data.items():
        if chat.get("communities"):
            schedule_poll_job(app, chat_id, chat.get("interval", DEFAULT_INTERVAL))

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("interval", cmd_interval))
    app.add_handler(CommandHandler("status", cmd_status))
    log.info("🤖 Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
