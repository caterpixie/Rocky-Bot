import discord
from discord import app_commands, ui
from discord.ui import View, Button, Modal, TextInput
import os
import json
import aiomysql
from datetime import timezone
from zoneinfo import ZoneInfo

# ============================================================
# CONFIGURATION
# ============================================================

CONFESSION_CHANNEL_ID = 1496373203986157838
CONFESSION_APPROVAL_CHANNEL_ID = 1482168928045367349
CONFESSION_LOGS_CHANNEL_ID = 1496371813259939891

COUNTER_FILE = "confession_counter.txt"
LATEST_CONFESSION_FILE = "latest_confession.txt"
PENDING_CONFESSIONS_FILE = "pending_confessions.json"

DENIAL_LOG_TIMEZONE = "America/Toronto"  
DENIAL_LOG_TZ_LABEL = "EST"            

COLOR_CONFESSION = "#FFC6D6"
COLOR_REPLY = "#CC718A"
COLOR_DENIAL_LOG = "#FFC6D6"

# ============================================================
# BOT HOOKUP
# ============================================================

bot = None
def set_bot(bot_instance):
    global bot
    bot = bot_instance

class Pages(ui.View):
    def __init__(self, embeds):
        super().__init__(timeout=None)
        self.embeds = embeds
        self.current_page = 0
        self.update_buttons()

    def update_buttons(self):
        self.children[0].disabled = self.current_page == 0
        self.children[1].disabled = self.current_page == len(self.embeds) - 1

    @ui.button(label="Previous", style=discord.ButtonStyle.primary)
    async def previous_page(self, interaction: discord.Interaction, button: ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: ui.Button):
        if self.current_page < len(self.embeds) - 1:
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)


def get_next_confession_number():
    if not os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE, "w") as f:
            f.write("1")
            return 1

    with open(COUNTER_FILE, "r+") as f:
        number = int(f.read().strip())
        f.seek(0)
        f.write(str(number + 1))
        f.truncate()
        return number

def get_latest_confession_id():
    if os.path.exists(LATEST_CONFESSION_FILE):
        with open(LATEST_CONFESSION_FILE, "r") as f:
            try:
                return int(f.read().strip())
            except ValueError:
                return None
    return None

def set_latest_confession_id(message_id):
    with open(LATEST_CONFESSION_FILE, "w") as f:
        f.write(str(message_id))

def log_pending_confession(message_id, data):
    try:
        if os.path.exists(PENDING_CONFESSIONS_FILE):
            with open(PENDING_CONFESSIONS_FILE, "r") as f:
                pending = json.load(f)
        else:
            pending = {}

        pending[str(message_id)] = data

        with open(PENDING_CONFESSIONS_FILE, "w") as f:
            json.dump(pending, f, indent=4)
        print(f"[LOG] Saved pending confession {message_id}")
    except Exception as e:
        print(f"[ERROR] Logging pending confession: {e}")

def remove_pending_confession(message_id):
    try:
        if os.path.exists(PENDING_CONFESSIONS_FILE):
            with open(PENDING_CONFESSIONS_FILE, "r") as f:
                pending = json.load(f)
            if str(message_id) in pending:
                del pending[str(message_id)]
                with open(PENDING_CONFESSIONS_FILE, "w") as f:
                    json.dump(pending, f, indent=4)
    except Exception as e:
        print(f"[ERROR] Removing pending confession: {e}")

def safe_avatar_url(user):
    return user.avatar.url if user.avatar else None

async def record_denial_event(
    guild_id: int,
    user_id: int,
    confession_text: str,
    denied_by_name: str,
    reason: str | None
) -> int:
    """
    Inserts a NEW denial event row with a precise interaction timestamp (NOW()).
    Returns the user's total number of denial events  insert.
    """
    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            # Insert one row per denial (append-only)
            await cur.execute(
                """
                INSERT INTO confession_denials (guild_id, user_id, denied_by_name, confession_text, reason)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (guild_id, user_id, denied_by_name, confession_text, reason)
            )

            # Count total denials for this user in this guild
            await cur.execute(
                """
                SELECT COUNT(*) FROM confession_denials
                WHERE guild_id = %s AND user_id = %s
                """,
                (guild_id, user_id)
            )
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 1

class ConfessionInteractionView(View):
    def __init__(self, bot_instance):
        super().__init__(timeout=None)
        self.bot = bot_instance

    @discord.ui.button(label="Submit a Confession!", style=discord.ButtonStyle.primary, custom_id="confession_submit")
    async def submit_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(ConfessionSubmitModal())

    @discord.ui.button(label="Reply", style=discord.ButtonStyle.secondary, custom_id="confession_reply")
    async def reply_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(ConfessionReplyModal(interaction.message.id))

class ConfessionSubmitModal(Modal, title="Submit a Confession"):
    confession = TextInput(label="Your Confession", style=discord.TextStyle.paragraph, required=True)
    author_name = TextInput(
        label="Author Name (optional)",
        style=discord.TextStyle.short,
        required=False,
        max_length=256,
        placeholder="Leave blank to stay fully anonymous",
    )

    async def on_submit(self, interaction: discord.Interaction):
        confession_number = get_next_confession_number()
        approval_channel = interaction.guild.get_channel(CONFESSION_APPROVAL_CHANNEL_ID)
        author_name_value = str(self.author_name) if str(self.author_name) else None

        embed = discord.Embed(
            title=f"Confession Awaiting Review (#{confession_number})",
            description=f"\"{self.confession.value}\"",
            colour=discord.Color.from_str(COLOR_CONFESSION)
        )
        embed.add_field(name="User", value=f"||{interaction.user.name} (`{interaction.user.id}`)||")
        if author_name_value:
            embed.add_field(name="Author Name", value=author_name_value, inline=False)

        view = ApprovalView(self.confession.value, interaction.user, confession_number, author_name=author_name_value)
        approval_message = await approval_channel.send(embed=embed, view=view)

        log_pending_confession(approval_message.id, {
            "confession_text": self.confession.value,
            "submitter_id": interaction.user.id,
            "submitter_name": interaction.user.name,
            "confession_number": confession_number,
            "type": "confession",
            "reply_to_message_id": None,
            "author_name": author_name_value
        })

        await interaction.response.send_message("Your confession has been submitted!", ephemeral=True)

class ConfessionReplyModal(Modal, title="Reply to a Confession"):
    reply = TextInput(label="Your Reply", style=discord.TextStyle.paragraph, required=True)
    author_name = TextInput(
        label="Author Name (optional)",
        style=discord.TextStyle.short,
        required=False,
        max_length=256,
        placeholder="Leave blank to stay fully anonymous",
    )

    def __init__(self, original_message_id: int):
        super().__init__()
        self.original_message_id = original_message_id

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.guild.get_channel(CONFESSION_CHANNEL_ID)

        try:
            await channel.fetch_message(self.original_message_id)
        except discord.NotFound:
            await interaction.response.send_message("Original confession not found.", ephemeral=True)
            return

        confession_number = get_next_confession_number()
        approval_channel = interaction.guild.get_channel(CONFESSION_APPROVAL_CHANNEL_ID)
        author_name_value = str(self.author_name) if str(self.author_name) else None

        embed = discord.Embed(
            title=f"Reply Awaiting Review (#{confession_number})",
            description=f"\"{self.reply.value}\"",
            color=discord.Color.from_str(COLOR_REPLY)
        )
        embed.add_field(name="User", value=f"||{interaction.user.name} (`{interaction.user.id}`)||")
        if author_name_value:
            embed.add_field(name="Author Name", value=author_name_value, inline=False)
        embed.add_field(
            name="Original Message",
            value=f"[Jump to message](https://discord.com/channels/{interaction.guild.id}/{channel.id}/{self.original_message_id})",
            inline=False
        )

        view = ApprovalView(
            confession_text=self.reply.value,
            submitter=interaction.user,
            confession_number=confession_number,
            type="reply",
            reply_to_message_id=self.original_message_id,
            author_name=author_name_value
        )
        approval_message = await approval_channel.send(embed=embed, view=view)

        log_pending_confession(approval_message.id, {
            "confession_text": self.reply.value,
            "submitter_id": interaction.user.id,
            "submitter_name": interaction.user.name,
            "confession_number": confession_number,
            "type": "reply",
            "reply_to_message_id": self.original_message_id,
            "author_name": author_name_value
        })
        await interaction.response.send_message("Your reply has been submitted!", ephemeral=True)

class ApprovalView(View):
    def __init__(self, confession_text, submitter, confession_number, type="confession", reply_to_message_id=None, author_name=None):
        super().__init__(timeout=None)
        self.confession_text = confession_text
        self.confession_number = confession_number
        self.submitter = submitter
        self.type = type
        self.reply_to_message_id = reply_to_message_id
        self.author_name = author_name

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success, custom_id="approval_approve")
    async def approve(self, interaction: discord.Interaction, button: Button):
        channel = interaction.guild.get_channel(CONFESSION_CHANNEL_ID)
        logchannel = interaction.guild.get_channel(CONFESSION_LOGS_CHANNEL_ID)

        if self.type == "reply":
            jump_url = f"https://discord.com/channels/{interaction.guild.id}/{channel.id}/{self.reply_to_message_id}"
            embed = discord.Embed(
                title=f"Anonymous Reply (#{self.confession_number})",
                description=f"\"{self.confession_text}\"\n",
                color=discord.Color.from_str(COLOR_REPLY)
            )
            embed.add_field(name="Original Confession", value=f"[Jump to confession]({jump_url})", inline=False)
        else:
            embed = discord.Embed(
                title=f"Anonymous Confession (#{self.confession_number})",
                description=f"\"{self.confession_text}\"",
                color=discord.Color.from_str(COLOR_CONFESSION)
            )

        if self.author_name:
            embed.set_author(name=self.author_name)

        new_message = await channel.send(embed=embed, view=ConfessionInteractionView(bot))

        # Remove buttons from previous confession
        last_message_id = get_latest_confession_id()
        if last_message_id:
            try:
                old_message = await channel.fetch_message(last_message_id)
                await old_message.edit(view=None)
            except discord.NotFound:
                pass

        set_latest_confession_id(new_message.id)

        await interaction.response.send_message("Approved and posted!", ephemeral=True)
        remove_pending_confession(interaction.message.id)
        await interaction.message.delete()

        logembed = discord.Embed(
            title=f"{'Reply' if self.type == 'reply' else 'Confession'} Approved (#{self.confession_number})",
            description=f"\"{self.confession_text}\"",
            color=discord.Color.green()
        )
        logembed.add_field(name="User", value=f"||{self.submitter.name} (`{self.submitter.id}`)||")
        if self.author_name:
            logembed.add_field(name="Author Name", value=self.author_name, inline=False)
        logembed.add_field(name="Approved By", value=f"{interaction.user.mention}", inline=False)

        await logchannel.send(embed=logembed)

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger, custom_id="approval_deny")
    async def deny(self, interaction: discord.Interaction, button: Button):
        logchannel = interaction.guild.get_channel(CONFESSION_LOGS_CHANNEL_ID)

        # Attempt to DM the submitter
        try:
            embed = discord.Embed(
                title="Your Denied Confession",
                description=f"\"{self.confession_text}\"",
                color=discord.Color.red()
            )
            await self.submitter.send(
                "Your confession has been denied.",
                embed=embed
            )
        except discord.Forbidden:
            pass  # User has DMs closed

        await interaction.response.send_message("Confession denied.", ephemeral=True)
        remove_pending_confession(interaction.message.id)
        await interaction.message.delete()

        logembed = discord.Embed(
            title=f"Confession Denied (#{self.confession_number})",
            description=f"\"{self.confession_text}\"",
            color=discord.Color.red()
        )
        logembed.add_field(name="User", value=f"||{self.submitter.name} (`{self.submitter.id}`)||")
        logembed.add_field(name="Denied By", value=f"{interaction.user.mention}", inline=False)

        await logchannel.send(embed=logembed)

    @discord.ui.button(label="💬 Deny with Reason", style=discord.ButtonStyle.danger, custom_id="approval_denyreason")
    async def deny_with_reason(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(DenyReasonModal(
            self.submitter,
            self.confession_text,
            interaction.guild,
            self.confession_number
        ))

class DenyReasonModal(Modal, title="Deny Confession with Reason"):
    reason = TextInput(label="Reason", placeholder="Why is this being denied?", required=True)

    def __init__(self, submitter, confession_text, guild, confession_number):
        super().__init__()
        self.submitter = submitter
        self.confession_text = confession_text
        self.confession_number = confession_number
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction):
        logchannel = interaction.guild.get_channel(CONFESSION_LOGS_CHANNEL_ID)
        embed = discord.Embed(
            title="Your Denied Confession",
            description=f"\"{self.confession_text}\"",
            color=discord.Color.red()
        )
        try:
            await self.submitter.send(
                f"Your confession was denied.\n**Reason:**\n{self.reason.value}",
                embed=embed
            )
        except discord.Forbidden:
            pass  # DMs are closed

        total_denials = await record_denial_event(
            guild_id=interaction.guild.id,
            user_id=self.submitter.id,
            confession_text=self.confession_text,
            denied_by_name=interaction.user.name,
            reason=self.reason.value
        )

        await interaction.response.send_message(
            "Confession has been denied with reason.\n"
            f"This user now has **{total_denials}** denied confession(s).",
            ephemeral=True
        )

        remove_pending_confession(interaction.message.id)
        await interaction.message.delete()

        logembed = discord.Embed(
            title=f"Confession Denied (#{self.confession_number})",
            description=f"\"{self.confession_text}\"",
            color=discord.Color.red()
        )
        logembed.add_field(name="User", value=f"||{self.submitter.name} (`{self.submitter.id}`)||")
        logembed.add_field(name="Denied By", value=f"{interaction.user.mention}", inline=False)
        logembed.add_field(name="Reason", value=f"{self.reason.value}", inline=False)
        await logchannel.send(embed=logembed)

class ConfessionGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="confession", description="Confession Commands")

confession_group = ConfessionGroup()

@confession_group.command(name="submit", description="Post a confession")
async def submit_confession(interaction: discord.Interaction, confession: str, author_name: str = None):
    confession_number = get_next_confession_number()

    approval_channel = interaction.guild.get_channel(CONFESSION_APPROVAL_CHANNEL_ID)
    if not approval_channel:
        await interaction.response.send_message("Confessions channel not found.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"Confession Awaiting Review (#{confession_number})",
        description=f"\"{confession}\"",
        colour=discord.Color.from_str(COLOR_CONFESSION)
    )
    embed.add_field(name="User", value=f"||{interaction.user.name} (`{interaction.user.id}`)||")
    if author_name:
        embed.add_field(name="Author Name", value=author_name, inline=False)

    view = ApprovalView(confession, interaction.user, confession_number, author_name=author_name)
    approval_message = await approval_channel.send(embed=embed, view=view)

    log_pending_confession(approval_message.id, {
        "confession_text": confession,
        "submitter_id": interaction.user.id,
        "submitter_name": interaction.user.name,
        "confession_number": confession_number,
        "type": "confession",
        "reply_to_message_id": None,
        "author_name": author_name
    })
    await interaction.response.send_message("Confession submitted!", ephemeral=True)

@confession_group.command(name="reply", description="Reply to a confession")
async def reply_to_confession(interaction: discord.Interaction, message_link: str):
    try:
        parts = message_link.strip().split("/")
        if len(parts) < 3:
            raise ValueError("Invalid link format")

        channel_id = int(parts[-2])
        message_id = int(parts[-1])
    except (ValueError, IndexError):
        await interaction.response.send_message("Invalid message link. Please make sure it's a valid Discord message URL.", ephemeral=True)
        return

    channel = interaction.guild.get_channel(channel_id)
    if not channel:
        await interaction.response.send_message("Could not find the channel from that link.", ephemeral=True)
        return

    try:
        await channel.fetch_message(message_id)
    except discord.NotFound:
        await interaction.response.send_message("Message not found. Make sure the link is from this server.", ephemeral=True)
        return

    await interaction.response.send_modal(ConfessionReplyModal(message_id))

@confession_group.command(name="denials", description="Displays a user's past denied confessions")
async def denial_log(interaction: discord.Interaction, user: discord.Member):
    async with bot.pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT denied_by_name, confession_text, reason, timestamp
                FROM confession_denials
                WHERE guild_id = %s AND user_id = %s
                ORDER BY timestamp DESC
                """,
                (interaction.guild.id, user.id)
            )
            records = await cur.fetchall()

    if not records:
        await interaction.response.send_message(f"{user.name} has no denied confessions logged.", ephemeral=True)
        return

    per_page = 10
    pages = []
    log_tz = ZoneInfo(DENIAL_LOG_TIMEZONE)

    for i in range(0, len(records), per_page):
        chunk = records[i:i+per_page]
        description = "\n".join(
            (
                f"**Moderator:** {entry['denied_by_name']}\n"
                f"**Confession:** {entry['confession_text']}\n"
                f"**Reason:** {entry['reason'] or '—'}\n"
                f"*<t:{int(entry['timestamp'].replace(tzinfo=timezone.utc).astimezone(log_tz).timestamp())}:f> {DENIAL_LOG_TZ_LABEL}*\n"
            )
            for entry in chunk
        )

        embed = discord.Embed(
            title=f"{len(records)} denied confession(s) for {user}:",
            description=description,
            color=discord.Color.from_str(COLOR_DENIAL_LOG)
        )
        embed.set_footer(text=f"Page {i//per_page + 1}/{(len(records)-1)//per_page + 1}")
        embed.set_author(name=str(user), icon_url=safe_avatar_url(user))
        pages.append(embed)

    view = Pages(pages)
    await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)

@app_commands.context_menu(name="Reply to Confession")
async def reply_to_confession_context(interaction: discord.Interaction, message: discord.Message):
    await interaction.response.send_modal(ConfessionReplyModal(message.id))

