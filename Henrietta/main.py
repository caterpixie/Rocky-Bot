import os
import json

from dotenv import load_dotenv
load_dotenv()

import discord
from discord.ext import commands
import aiomysql

from confessions import (
    confession_group,
    reply_to_confession_context,
    set_bot as set_confessions_bot,
    ConfessionInteractionView,
    ApprovalView,
)
from starboard import setup_starboard
from mod import set_bot as set_warn_bot, mod_group
from log import setup_logging
from uwu import set_bot as set_uwu_bot, uwu
from triggers import set_bot as set_trigger_bot
from automod import setup_automod
from welcome import setup_welcome
from tickets import (
    ticket_group,
    set_bot as set_ticket_bot,
    TicketPanelView,
    CloseTicketView,
    ConfirmCloseView,
)
from qotd import (
    qotd_group,
    set_bot as set_qotd_bot,
    register_persistent_views as register_qotd_views,
    auto_post_qotd,
)


# =========================
# CONFIG
# =========================

GUILD_ID = 1480390027593777287
CONFESSION_APPROVAL_CHANNEL = 1482168928045367349


# =========================
# HELPERS
# =========================

async def restore_pending_confessions(bot: commands.Bot):
    if CONFESSION_APPROVAL_CHANNEL == 0:
        print("[RESTORE] CONFESSION_APPROVAL_CHANNEL is not set. Skipping restore.")
        return

    if not os.path.exists("pending_confessions.json"):
        print("[RESTORE] No pending confessions to restore.")
        return

    try:
        with open("pending_confessions.json", "r", encoding="utf-8") as f:
            pending = json.load(f)
    except Exception as e:
        print(f"[RESTORE ERROR] Could not load pending_confessions.json: {e}")
        return

    try:
        channel = await bot.fetch_channel(CONFESSION_APPROVAL_CHANNEL)
    except Exception as e:
        print(f"[RESTORE ERROR] Could not fetch approval channel: {e}")
        return

    for msg_id, data in pending.items():
        try:
            message = await channel.fetch_message(int(msg_id))
            submitter = await bot.fetch_user(data["submitter_id"])

            view = ApprovalView(
                confession_text=data["confession_text"],
                submitter=submitter,
                confession_number=data["confession_number"],
                type=data["type"],
                reply_to_message_id=data.get("reply_to_message_id"),
            )
            await message.edit(view=view)
            print(f"[RESTORE] Restored view for message {msg_id}")
        except Exception as e:
            print(f"[RESTORE ERROR] Could not restore view for message {msg_id}: {e}")


# =========================
# BOT
# =========================

class Client(commands.Bot):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pool: aiomysql.Pool | None = None

    async def setup_hook(self):
        # MySQL pool
        self.pool = await aiomysql.create_pool(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", 3306)),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            db=os.getenv("DB_NAME"),
            autocommit=True,
        )

        # Module bot hookups
        set_warn_bot(self)
        set_ticket_bot(self)
        set_confessions_bot(self)
        set_uwu_bot(self)
        set_qotd_bot(self)

        # Persistent views
        self.add_view(TicketPanelView())
        self.add_view(CloseTicketView())
        self.add_view(ConfirmCloseView())
        self.add_view(ConfessionInteractionView(self))
        register_qotd_views(self)

        # Restore confession approval buttons on restart
        await restore_pending_confessions(self)

        # Slash commands
        self.tree.add_command(mod_group)
        self.tree.add_command(ticket_group)
        self.tree.add_command(confession_group)
        self.tree.add_command(reply_to_confession_context)
        self.tree.add_command(uwu)
        self.tree.add_command(qotd_group)

        #TEMP
        #self.tree.clear_commands(guild=None)
        #await self.tree.sync()

        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)

        setup_starboard(self)

        if not auto_post_qotd.is_running():
            auto_post_qotd.start()

        print(f"Synced commands to guild {GUILD_ID}: {[cmd.name for cmd in synced]}")

    async def on_ready(self):
        print(f"Logged on as {self.user} (ID: {self.user.id})")
        print("Bot is ready.")


# =========================
# STARTUP
# =========================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = Client(
    command_prefix="?",
    intents=intents,
)

setup_logging(bot)
setup_automod(bot)
setup_welcome(bot) 
set_trigger_bot(bot)

bot.run(os.getenv("DISCORD_TOKEN"))
