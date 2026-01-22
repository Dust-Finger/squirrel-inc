import asyncio
import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

import database

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zuppa")

# Database Init
database.init_db()

# Discord Bot Setup
intents = discord.Intents.default()
# intents.messages = True # If we need to listen
bot = commands.Bot(command_prefix="!", intents=intents)

# Scheduler Setup
scheduler = AsyncIOScheduler()

async def check_reminders():
    """Background task to check DB for due reminders and send them."""
    try:
        pending = database.get_pending_reminders()
        # Ensure we compare against UTC because we store UTC now
        now = datetime.now(timezone.utc)
        
        # Backward compatibility: older records might be naive (no timezone info)
        # We assume those were server-local. But ideally we migrate.
        # For this logic, we'll try to treat everything as aware.
        
        for r in pending:
            # Parse stored time
            if isinstance(r['remind_time'], str):
                try:
                    # Attempt robust parsing
                    try:
                        remind_time = datetime.strptime(r['remind_time'], '%Y-%m-%d %H:%M:%S.%f%z')
                    except ValueError:
                        try:
                            remind_time = datetime.strptime(r['remind_time'], '%Y-%m-%d %H:%M:%S%z')
                        except ValueError:
                            # Fallback for naive strings (old data or simple str())
                            # Assume they are UTC if we are moving to UTC system
                            rt_naive = datetime.fromisoformat(str(r['remind_time']).replace('Z', '+00:00'))
                            if rt_naive.tzinfo is None:
                                remind_time = rt_naive.replace(tzinfo=timezone.utc)
                            else:
                                remind_time = rt_naive
                except ValueError:
                     continue # Skip garbage data
            else:
                 remind_time = r['remind_time']
            
            # If reminder is due
            # meaningful comparison requires both to be aware
            if remind_time <= now:
                logger.info(f"Sending reminder: {r['message']}")
                try:
                    if DISCORD_CHANNEL_ID:
                        channel = bot.get_channel(int(DISCORD_CHANNEL_ID))
                        if channel:
                            # Premium Squirrel Inc Formatting
                            embed = discord.Embed(
                                title="🐿️ Squirrel Inc Reminder!",
                                color=0x8B4513 # Saddle Brown
                            )
                            embed.add_field(name="Task", value=r['message'], inline=False)
                            
                            # Show Event Time if available
                            if r.get('event_time'):
                                # Try to parse if string, or use as is
                                et_display = r['event_time']
                                timestamp_str = ""
                                
                                try:
                                    if isinstance(et_display, str):
                                        # Handles '2023-10-27 14:30:00.000000' or similar
                                        try:
                                            et_obj = datetime.strptime(et_display, '%Y-%m-%d %H:%M:%S.%f')
                                        except ValueError:
                                             et_obj = datetime.strptime(et_display, '%Y-%m-%d %H:%M:%S')
                                    elif isinstance(et_display, datetime):
                                        et_obj = et_display
                                    else:
                                        et_obj = None

                                    if et_obj:
                                        ts = int(et_obj.timestamp())
                                        # <t:TIMESTAMP:F> = Full Date Time (Wednesday, ... 4:00 PM)
                                        # <t:TIMESTAMP:R> = Relative (in 30 minutes)
                                        timestamp_str = f"<t:{ts}:F> (<t:{ts}:R>)"
                                        embed.add_field(name="Time Due", value=timestamp_str, inline=True)
                                except Exception as e:
                                    logger.error(f"Error parsing event time for timestamp: {e}")
                                    # Fallback to string if strictly needed, or just omit
                                    embed.add_field(name="Time Due (Raw)", value=str(et_display), inline=True)

                            embed.set_footer(text="Powered by Squirrel Inc 🌰")
                            
                            # Send message with ping content + embed
                            await channel.send(content=f"<@{r['target_user']}>", embed=embed)
                        else:
                            logger.warning(f"Channel {DISCORD_CHANNEL_ID} not found.")
                    
                    database.mark_reminder_sent(r['id'])
                except Exception as e:
                    logger.error(f"Failed to send reminder {r['id']}: {e}")

    except Exception as e:
        logger.error(f"Error in check_reminders: {e}")

# FastAPI Lifestyle
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Zuppa System...")
    
    # Start Scheduler
    scheduler.add_job(check_reminders, 'interval', seconds=30)
    scheduler.start()
    
    # Start Discord Bot in background
    bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
    
    yield
    
    # Shutdown
    logger.info("Shutting down Zuppa System...")
    
    # Shutdown Scheduler
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler shut down.")
        
    # Shutdown Bot
    await bot.close()
    
    # Wait for bot task to finish if it's still running
    if not bot_task.done():
        try:
            bot_task.cancel()
            await bot_task
        except asyncio.CancelledError:
            pass
    logger.info("Discord Bot shut down.")

app = FastAPI(lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    logger.info('------')

@app.get("/")
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/reminders")
async def create_reminder(
    request: Request,
    message: str = Form(...),
    event_time: str = Form(...), # expecting ISO datetime-local string (Naive)
    offset_minutes: int = Form(...),
    target_user: str = Form(...),
    client_offset: int = Form(...) # Minutes from UTC
):
    try:
        # 1. Parse the "Wall Clock" time the user typed (e.g. 5:00 PM)
        # It has no timezone info attached yet.
        naive_et = datetime.strptime(event_time, "%Y-%m-%dT%H:%M")
        
        # 2. Convert to UTC
        # client_offset is minutes BEHIND UTC (usually).
        # Actually JS getTimezoneOffset() returns +min for West (US).
        # So: Local + Offset = UTC
        utc_et = naive_et + timedelta(minutes=client_offset)
        
        # Make it 'aware' so python knows it's UTC
        utc_et = utc_et.replace(tzinfo=timezone.utc)
        
        # 3. Calculate Remind Time (also UTC)
        utc_rt = utc_et - timedelta(minutes=offset_minutes)
        
        # Store as standard timestamps (or ISO strings)
        # We will save them as naive UTC strings to keep SQLite simple, or just rely on str()
        # Ideally, we format explicitly
        
        database.add_reminder(message, utc_rt, utc_et, target_user)
        logger.info(f"Created reminder for {target_user}. Event(UTC): {utc_et}")
    except ValueError as e:
        logger.error(f"Date parsing error: {e}")
    
    return RedirectResponse(url="/", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
