import discord
from discord.ext import tasks
from discord import app_commands, ui
from datetime import datetime
from zoneinfo import ZoneInfo
import aiomysql

# =========================
# CONFIGURATION
# =========================
QOTD_CHANNEL_ID = 
QOTD_ROLE_ID = 

# Do not change timezone, it needs to stay America/Chicago to work with PebbleHost's server. Time is 1 hour behind on CST
TIMEZONE_NAME = "America/Chicago" 
AUTO_POST_HOUR = 15          
AUTO_POST_MINUTE = 20      

THREAD_NAME = "Answers"
THREAD_AUTO_ARCHIVE_MINUTES = 1440

EMBED_COLOR = ""
QUEUE_PAGE_SIZE = 10

# =========================
# BOT HOOKUP
# =========================
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
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
        await message.create_thread(name=THREAD_NAME, auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES)
