import asyncio
import discord
from discord import app_commands, ui
import aiomysql
from datetime import datetime, timezone

# ============================================================
# CONFIGURATION
# ============================================================

ART_CHANNEL_ID = 1483327961141739571
STICKY_DEBOUNCE_SECONDS = 3

ART_PANEL = {
    "title": "<:x_staricon:1523500147085021318> Share Your Art",
    "description": "Click the button below to submit your art! You can post up to 10 images at once along with a descripton.\n\nOnce submitted, it'll be posted here with its own thread to keep discussions to their own post only.",
    "color": "#FFC6D6",
}

ART_MAX_IMAGES = 10
THREAD_AUTO_ARCHIVE_MINUTES = 1440 

# ============================================================
# BOT HOOKUP
# ============================================================

bot = None
def set_bot(bot_instance):
    global bot
    bot = bot_instance
    bot.add_listener(_on_message_for_sticky, "on_message")


async def _get_sticky_message_id(channel_id: int) -> int | None:
    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT message_id FROM sticky_messages WHERE channel_id = %s",
                (channel_id,),
            )
            row = await cur.fetchone()
            return row[0] if row else None


async def _set_sticky_message_id(channel_id: int, message_id: int):
    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO sticky_messages (channel_id, message_id)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE message_id = VALUES(message_id)
                """,
                (channel_id, message_id),
            )

_pending_repost_tasks: dict[int, asyncio.Task] = {}


def _build_art_panel_embed() -> discord.Embed:
    return discord.Embed(
        title=ART_PANEL["title"],
        description=ART_PANEL["description"].format(max_images=ART_MAX_IMAGES),
        color=discord.Color.from_str(ART_PANEL["color"]),
    )


async def _repost_sticky(channel: discord.TextChannel):
    old_message_id = await _get_sticky_message_id(channel.id)
    if old_message_id:
        try:
            old_message = await channel.fetch_message(old_message_id)
            await old_message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    new_message = await channel.send(embed=_build_art_panel_embed(), view=ArtPanelView())
    await _set_sticky_message_id(channel.id, new_message.id)


async def _debounced_repost(channel: discord.TextChannel):
    try:
        await asyncio.sleep(STICKY_DEBOUNCE_SECONDS)
        await _repost_sticky(channel)
    except asyncio.CancelledError:
        pass
    finally:
        _pending_repost_tasks.pop(channel.id, None)


async def _on_message_for_sticky(message: discord.Message):
    if message.channel.id != ART_CHANNEL_ID:
        return
    if message.author.id == bot.user.id and message.components:
        return

    existing_task = _pending_repost_tasks.get(message.channel.id)
    if existing_task and not existing_task.done():
        existing_task.cancel()

    _pending_repost_tasks[message.channel.id] = asyncio.create_task(
        _debounced_repost(message.channel)
    )

class ArtModal(ui.Modal, title="Submit Your Art"):
    def __init__(self):
        super().__init__()
        self.description_input = ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder="Anything you want the masses to know",
            required=False,
            max_length=1000,
        )
        self.image_upload = ui.FileUpload(required=True, min_values=1, max_values=ART_MAX_IMAGES)

        self.add_item(ui.Label(text="Description (optional)", component=self.description_input))
        self.add_item(
            ui.Label(
                text=f"Artwork Image{'s' if ART_MAX_IMAGES > 1 else ''} (up to {ART_MAX_IMAGES})",
                component=self.image_upload,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        attachments = self.image_upload.values

        image_attachments = [
            a for a in attachments if a.content_type and a.content_type.startswith("image/")
        ]

        if not image_attachments:
            await interaction.response.send_message(
                "No valid images were attached. Click **Submit Art** again to retry.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        art_channel = bot.get_channel(ART_CHANNEL_ID) or interaction.channel
        gallery_url = f"https://art-submission.local/{interaction.id}"
        files = []
        embeds = []

        for index, attachment in enumerate(image_attachments):
            file = await attachment.to_file(filename=f"art_{index}_{attachment.filename}")
            files.append(file)

            if index == 0:
                embed = discord.Embed(
                    description=self.description_input.value or None,
                    color=discord.Color.from_str(ART_PANEL["color"]),
                    url=gallery_url,
                )
                embed.set_author(
                    name=interaction.user.display_name,
                    icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
                )
            else:
                embed = discord.Embed(url=gallery_url)

            embed.set_image(url=f"attachment://{file.filename}")
            embeds.append(embed)

        posted_message = await art_channel.send(embeds=embeds, files=files)

        try:
            thread = await posted_message.create_thread(
                name="Discussion",
                auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

        await interaction.followup.send(
            f"Your art has been posted! {posted_message.jump_url}",
            ephemeral=True,
        )


class ArtSubmitButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="Submit Art",
            style=discord.ButtonStyle.secondary,
            custom_id="art:submit",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ArtModal())


class ArtPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ArtSubmitButton())

class ArtGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="art", description="Art channel sticky panel commands")


art_group = ArtGroup()


@art_group.command(name="setup", description="Posts the sticky art submission panel to the configured channel")
async def art_setup(interaction: discord.Interaction):
    channel = interaction.guild.get_channel(ART_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("Error: art channel not found.", ephemeral=True)
        return

    old_message_id = await _get_sticky_message_id(channel.id)
    if old_message_id:
        try:
            old_message = await channel.fetch_message(old_message_id)
            await old_message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    new_message = await channel.send(embed=_build_art_panel_embed(), view=ArtPanelView())
    await _set_sticky_message_id(channel.id, new_message.id)

    await interaction.response.send_message("Art panel sent!", ephemeral=True)
