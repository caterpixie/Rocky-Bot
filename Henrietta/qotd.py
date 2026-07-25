import discord
from discord.ext import tasks
from discord import app_commands, ui
from datetime import datetime
from zoneinfo import ZoneInfo
import aiomysql

# =========================
# CONFIGURATION
# =========================
QOTD_CHANNEL_ID = 1530456978072141966
QOTD_ROLE_ID = 1530020464293056624

QOTD_MOD_CHANNEL_ID = 1482168928045367349

QOTD_MOD_ROLE_ID = None

# Do not change timezone, it needs to stay America/Chicago to work with PebbleHost's server. Time is 1 hour behind on EST
TIMEZONE_NAME = "America/Chicago" 
AUTO_POST_HOUR = 15          
AUTO_POST_MINUTE = 20      

THREAD_NAME = "Answers"
THREAD_AUTO_ARCHIVE_MINUTES = 1440

EMBED_COLOR = "FFC6D6"
QUEUE_PAGE_SIZE = 10

SUGGEST_BUTTON_LABEL = "Suggest a Question"

# =========================
# BOT HOOKUP
# =========================
bot = None


def set_bot(bot_instance):
    global bot
    bot = bot_instance

def _can_review(member: discord.Member) -> bool:
    if member.guild_permissions.manage_guild:
        return True
    if QOTD_MOD_ROLE_ID:
        return any(role.id == QOTD_MOD_ROLE_ID for role in member.roles)
    return False


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


# =========================
# QOTD SUGGESTIONS
# =========================

class SuggestQOTDModal(ui.Modal, title="Suggest a Question"):
    question = ui.TextInput(
        label="Your question",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        placeholder="What's your QOTD idea?",
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        mod_channel = guild.get_channel(QOTD_MOD_CHANNEL_ID) if QOTD_MOD_CHANNEL_ID else None
        if not mod_channel:
            await interaction.response.send_message(
                "Sorry, QOTD suggestions aren't set up right now. Try again later.",
                ephemeral=True,
            )
            return

        review_embed = discord.Embed(
            title="New QOTD Suggestion",
            description=str(self.question),
            color=discord.Color.from_str(EMBED_COLOR),
        )
        review_embed.set_footer(text=f"Suggested by {interaction.user} | {interaction.user.id}")

        review_message = await mod_channel.send(embed=review_embed, view=QOTDReviewView())

        async with bot.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO qotd_suggestions (guild_id, user_id, author, question, review_message_id)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        guild.id,
                        interaction.user.id,
                        interaction.user.name,
                        str(self.question),
                        review_message.id,
                    ),
                )

        await interaction.response.send_message(
            "Thanks! Your question was submitted for review.", ephemeral=True
        )


class SuggestQOTDView(ui.View):
    """Attached to every posted QOTD embed. Persistent across restarts."""

    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(
        label=SUGGEST_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        custom_id="qotd_suggest_open",
    )
    async def suggest(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(SuggestQOTDModal())


class DenyReasonModal(ui.Modal, title="Deny QOTD Suggestion"):
    reason = ui.TextInput(
        label="Reason (sent to the user)",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=False,
        placeholder="Optional note to the submitter",
    )

    def __init__(self, suggestion, review_message: discord.Message):
        super().__init__()
        self.suggestion = suggestion
        self.review_message = review_message

    async def on_submit(self, interaction: discord.Interaction):
        suggestion = self.suggestion
        reason_text = str(self.reason) if self.reason else None

        async with bot.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO qotd_suggestion_denials
                        (guild_id, user_id, denied_by_name, question, reason)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        suggestion["guild_id"],
                        suggestion["user_id"],
                        interaction.user.name,
                        suggestion["question"],
                        reason_text,
                    ),
                )
                await cur.execute("DELETE FROM qotd_suggestions WHERE id = %s", (suggestion["id"],))

        denied_embed = discord.Embed(
            title="QOTD Suggestion Denied",
            description=suggestion["question"],
            color=discord.Color.red(),
        )
        denied_embed.add_field(name="Denied by", value=interaction.user.mention, inline=False)
        if reason_text:
            denied_embed.add_field(name="Reason", value=reason_text, inline=False)
        await self.review_message.edit(embed=denied_embed, view=None)

        member = interaction.guild.get_member(suggestion["user_id"])
        if member:
            try:
                if reason_text:
                    await member.send(
                        f"Your QOTD suggestion wasn't approved: {reason_text}"
                    )
                else:
                    await member.send("Your QOTD suggestion wasn't approved.")
            except discord.Forbidden:
                pass

        await interaction.response.send_message("Suggestion denied.", ephemeral=True)


class QOTDReviewView(ui.View):
    """Attached to suggestion review messages in the mod channel. Persistent across restarts."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _fetch_suggestion(self, message_id: int):
        async with bot.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM qotd_suggestions WHERE review_message_id = %s",
                    (message_id,),
                )
                return await cur.fetchone()

    @ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="qotd_review_approve")
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        if not _can_review(interaction.user):
            await interaction.response.send_message(
                "You don't have permission to review QOTD suggestions.", ephemeral=True
            )
            return

        suggestion = await self._fetch_suggestion(interaction.message.id)
        if not suggestion:
            await interaction.response.send_message(
                "This suggestion has already been reviewed.", ephemeral=True
            )
            return

        async with bot.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO qotds (guild_id, question, author, is_published, image_url)
                    VALUES (%s, %s, %s, FALSE, NULL)
                    """,
                    (suggestion["guild_id"], suggestion["question"], suggestion["author"]),
                )
                await cur.execute("DELETE FROM qotd_suggestions WHERE id = %s", (suggestion["id"],))

        approved_embed = discord.Embed(
            title="QOTD Suggestion Approved",
            description=suggestion["question"],
            color=discord.Color.green(),
        )
        approved_embed.add_field(name="Approved by", value=interaction.user.mention, inline=False)
        await interaction.response.edit_message(embed=approved_embed, view=None)

        member = interaction.guild.get_member(suggestion["user_id"])
        if member:
            try:
                await member.send("Your QOTD suggestion was approved and added to the queue!")
            except discord.Forbidden:
                pass

    @ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="qotd_review_deny")
    async def deny(self, interaction: discord.Interaction, button: ui.Button):
        if not _can_review(interaction.user):
            await interaction.response.send_message(
                "You don't have permission to review QOTD suggestions.", ephemeral=True
            )
            return

        suggestion = await self._fetch_suggestion(interaction.message.id)
        if not suggestion:
            await interaction.response.send_message(
                "This suggestion has already been reviewed.", ephemeral=True
            )
            return

        await interaction.response.send_modal(DenyReasonModal(suggestion, interaction.message))


def register_persistent_views(bot_instance):
    """Call once, e.g. in on_ready/setup_hook, so buttons survive restarts."""
    bot_instance.add_view(SuggestQOTDView())
    bot_instance.add_view(QOTDReviewView())


# =========================
# COMMANDS
# =========================

class QOTDGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="qotd", description="Manage QOTDs")


qotd_group = QOTDGroup()


@qotd_group.command(name="add", description="Adds a QOTD to the queue")
async def add_qotd(interaction: discord.Interaction, question: str, image: discord.Attachment = None):
    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO qotds (guild_id, question, author, is_published, image_url)
                VALUES (%s, %s, %s, FALSE, %s)
                """,
                (interaction.guild.id, question, interaction.user.name, image.url if image else None),
            )
    await interaction.response.send_message(f"Submitted QOTD: {question}", ephemeral=True)


@qotd_group.command(name="post", description="Manually post QOTD to the QOTD channel and create a thread")
async def post_qotd(interaction: discord.Interaction):
    channel = interaction.guild.get_channel(QOTD_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("QOTD channel not found.", ephemeral=True)
        return

    async with bot.pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT * FROM qotds
                WHERE guild_id = %s AND is_published = FALSE
                ORDER BY id ASC
                LIMIT 1
                """,
                (interaction.guild.id,),
            )
            record = await cur.fetchone()
            if not record:
                await interaction.response.send_message("No QOTD in queue, slut", ephemeral=True)
                return

            await cur.execute("UPDATE qotds SET is_published = TRUE WHERE id = %s", (record["id"],))

            await cur.execute(
                """
                SELECT COUNT(*) AS count FROM qotds
                WHERE guild_id = %s AND is_published = FALSE
                """,
                (interaction.guild.id,),
            )
            count = (await cur.fetchone())["count"]

    embed = discord.Embed(
        title="Question of the Day",
        description=record["question"],
        color=discord.Color.from_str(EMBED_COLOR),
    )
    if record.get("image_url"):
        embed.set_image(url=record["image_url"])
    embed.set_footer(text=f"| Author: {record['author']} | {count} QOTDs left in queue |")

    message = await channel.send(
        content=f"<@&{QOTD_ROLE_ID}>",
        embed=embed,
        view=SuggestQOTDView(),
        allowed_mentions=discord.AllowedMentions(roles=True),
    )

    await interaction.response.send_message("QOTD posted and thread created.", ephemeral=True)
    await message.create_thread(name=THREAD_NAME, auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES)


@qotd_group.command(name="view", description="View the list of upcoming QOTDs")
async def view_queue(interaction: discord.Interaction):
    async with bot.pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT * FROM qotds
                WHERE guild_id = %s AND is_published = FALSE
                ORDER BY id ASC
                """,
                (interaction.guild.id,),
            )
            records = await cur.fetchall()

    if not records:
        await interaction.response.send_message("QOTD queue empty, fill her up~", ephemeral=True)
        return

    pages = []
    for i in range(0, len(records), QUEUE_PAGE_SIZE):
        chunk = records[i : i + QUEUE_PAGE_SIZE]
        description = "\n".join(
            f"**{idx}.** {entry['question']}"
            for idx, entry in enumerate(chunk, start=i + 1)
        )
        embed = discord.Embed(title="Question of the Day Queue", description=description)
        embed.set_footer(
            text=f"Page {i // QUEUE_PAGE_SIZE + 1}/{(len(records) - 1) // QUEUE_PAGE_SIZE + 1}"
        )
        pages.append(embed)

    view = Pages(pages)
    await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)


@qotd_group.command(name="delete", description="Deletes a QOTD by index")
async def delete_qotd(interaction: discord.Interaction, index: int):
    async with bot.pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT id, question, author FROM qotds
                WHERE guild_id = %s AND is_published = FALSE
                ORDER BY id ASC
                """,
                (interaction.guild.id,),
            )
            records = await cur.fetchall()

            if index < 1 or index > len(records):
                await interaction.response.send_message("Index invalid", ephemeral=True)
                return

            target = records[index - 1]
            await cur.execute("DELETE FROM qotds WHERE id = %s", (target["id"],))

    await interaction.response.send_message(
        f'Removed QOTD #{index}: "{target["question"]}" by {target["author"]}',
        ephemeral=True,
    )


@tasks.loop(minutes=1)
async def auto_post_qotd():
    now = datetime.now(ZoneInfo(TIMEZONE_NAME))
    if now.hour != AUTO_POST_HOUR or now.minute != AUTO_POST_MINUTE:
        return

    for guild in bot.guilds:
        qotd_channel = guild.get_channel(QOTD_CHANNEL_ID)
        if not qotd_channel:
            continue

        async with bot.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT * FROM qotds
                    WHERE guild_id = %s AND is_published = FALSE
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (guild.id,),
                )
                record = await cur.fetchone()
                if not record:
                    continue

                await cur.execute("UPDATE qotds SET is_published = TRUE WHERE id = %s", (record["id"],))

                await cur.execute(
                    """
                    SELECT COUNT(*) AS count FROM qotds
                    WHERE guild_id = %s AND is_published = FALSE
                    """,
                    (guild.id,),
                )
                count = (await cur.fetchone())["count"]

        embed = discord.Embed(
            title="Question of the Day",
            description=record["question"],
            color=discord.Color.from_str(EMBED_COLOR),
        )
        if record.get("image_url"):
            embed.set_image(url=record["image_url"])
        embed.set_footer(text=f"| Author: {record['author']} | {count} QOTDs left in queue |")

        message = await qotd_channel.send(
            content=f"<@&{QOTD_ROLE_ID}>",
            embed=embed,
            view=SuggestQOTDView(),
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
        await message.create_thread(name=THREAD_NAME, auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES)
