import asyncio
import discord
from discord import app_commands, ui
import aiomysql

# ============================================================
# CONFIGURATION
# ============================================================

ART_CHANNEL_ID = 1483327961141739571
STICKY_DEBOUNCE_SECONDS = 3

WRITING_CHANNEL_ID = 1538412707471040574
WRITING_MAX_FILES = 1
WRITING_MAX_TEXT_LENGTH = 4000

WRITING_COLORS = {
    "own": "#FFC6D6",    
    "share": "#CC718A",  
}

WRITING_PANEL = {
    "title": "<:x_staricon:1523500147085021318> Share Some Writing/Fics!",
    "description": "Click the button below to submit writing! You can paste raw text, drop a link, or attach a file.\n\nYou can either post your own stuff, or a fic you wanna share with the class.",
    "color": "#FFC6D6",
}

ART_PANEL = {
    "title": "<:x_staricon:1523500147085021318> Share Your Art!",
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


def _build_writing_panel_embed() -> discord.Embed:
    return discord.Embed(
        title=WRITING_PANEL["title"],
        description=WRITING_PANEL["description"],
        color=discord.Color.from_str(WRITING_PANEL["color"]),
    )


# Maps a sticky-panel channel ID to the (embed builder, view factory) used to repost it.
# Populated after the view classes are defined below (see bottom of file).
_STICKY_PANELS: dict[int, tuple] = {}


async def _repost_sticky(channel: discord.TextChannel):
    panel = _STICKY_PANELS.get(channel.id)
    if not panel:
        return
    embed_builder, view_factory = panel

    old_message_id = await _get_sticky_message_id(channel.id)
    if old_message_id:
        try:
            old_message = await channel.fetch_message(old_message_id)
            await old_message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    new_message = await channel.send(embed=embed_builder(), view=view_factory())
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
    if message.channel.id not in _STICKY_PANELS:
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
        self.spoiler_select = ui.Select(
            options=[
                discord.SelectOption(label="No these are SFW", value="no", default=True),
                discord.SelectOption(label="Yes! One of these pictures has dick and/or ball", value="yes"),
            ],
            min_values=1,
            max_values=1,
        )

        self.add_item(ui.Label(text="Description (optional)", component=self.description_input))
        self.add_item(
            ui.Label(
                text=f"Artwork Image{'s' if ART_MAX_IMAGES > 1 else ''} (up to {ART_MAX_IMAGES})",
                component=self.image_upload,
            )
        )
        self.add_item(ui.Label(text="Spoiler these images?", component=self.spoiler_select))

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

        is_spoiler = bool(self.spoiler_select.values) and self.spoiler_select.values[0] == "yes"
        files = [
            await attachment.to_file(filename=f"art_{index}_{attachment.filename}", spoiler=is_spoiler)
            for index, attachment in enumerate(image_attachments)
        ]

        embed = discord.Embed(
            description=self.description_input.value or None,
            color=discord.Color.from_str(ART_PANEL["color"]),
        )
        embed.set_author(
            name=f"Posted by {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
        )
        posted_message = await art_channel.send(embed=embed, files=files)

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


def _looks_like_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


async def send_writing_embed(
    author: discord.abc.User,
    channel: discord.abc.Messageable,
    *,
    text: str | None = None,
    attachments: list[discord.Attachment] | None = None,
    origin: str = "own",
) -> discord.Message:
  ), and controls the embed color per WRITING_COLORS.
    text = (text or "").strip()
    attachments = attachments or []

    if not text and not attachments:
        raise ValueError("send_writing_embed requires text and/or attachments")

    color_hex = WRITING_COLORS.get(origin, WRITING_COLORS["own"])
    embed = discord.Embed(color=discord.Color.from_str(color_hex))
    embed.set_author(
        name=f"Posted by {author.display_name}",
        icon_url=author.display_avatar.url if author.display_avatar else None,
    )

    if text:
        if _looks_like_url(text):
            embed.add_field(name="Link", value=text, inline=False)
        else:
            embed.description = text[:4096]

    files = [
        await attachment.to_file(filename=f"writing_{index}_{attachment.filename}")
        for index, attachment in enumerate(attachments)
    ]

    return await channel.send(embed=embed, files=files or discord.utils.MISSING)


class WritingModal(ui.Modal, title="Submit Your Writing"):
    def __init__(self):
        super().__init__()
        self.text_input = ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder="Paste your writing, or a link to it (leave blank if attaching a file)",
            required=False,
            max_length=WRITING_MAX_TEXT_LENGTH,
        )
        self.file_upload = ui.FileUpload(required=False, min_values=0, max_values=WRITING_MAX_FILES)
        self.origin_select = ui.Select(
            options=[
                discord.SelectOption(label="This is my own writing", value="own", default=True),
                discord.SelectOption(label="I'm just sharing something", value="share"),
            ],
            min_values=1,
            max_values=1,
        )

        self.add_item(ui.Label(text="Text or Link (optional)", component=self.text_input))
        self.add_item(
            ui.Label(
                text=f"File{'s' if WRITING_MAX_FILES > 1 else ''} (optional)",
                component=self.file_upload,
            )
        )
        self.add_item(ui.Label(text="What is this?", component=self.origin_select))

    async def on_submit(self, interaction: discord.Interaction):
        text_value = self.text_input.value or ""
        attachments = self.file_upload.values

        if not text_value.strip() and not attachments:
            await interaction.response.send_message(
                "You need to provide either text, a link, or a file. Click **Submit Writing** again to retry.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        writing_channel = bot.get_channel(WRITING_CHANNEL_ID) or interaction.channel

        origin = self.origin_select.values[0] if self.origin_select.values else "own"

        posted_message = await send_writing_embed(
            interaction.user,
            writing_channel,
            text=text_value,
            attachments=attachments,
            origin=origin,
        )

        await interaction.followup.send(
            f"Your writing has been posted! {posted_message.jump_url}",
            ephemeral=True,
        )


class WritingSubmitButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="Submit Writing",
            style=discord.ButtonStyle.secondary,
            custom_id="writing:submit",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(WritingModal())


class WritingPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(WritingSubmitButton())


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

_STICKY_PANELS[ART_CHANNEL_ID] = (_build_art_panel_embed, ArtPanelView)
_STICKY_PANELS[WRITING_CHANNEL_ID] = (_build_writing_panel_embed, WritingPanelView)


@art_group.command(name="setup", description="Posts the sticky art submission panel to the configured channel")
async def art_setup(interaction: discord.Interaction):
    channel = interaction.guild.get_channel(ART_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("Error: art channel not found.", ephemeral=True)
        return

    await _repost_sticky(channel)
    await interaction.response.send_message("Art panel sent!", ephemeral=True)


@art_group.command(name="setup-writing", description="Posts the sticky writing submission panel to the configured channel")
async def art_setup_writing(interaction: discord.Interaction):
    channel = interaction.guild.get_channel(WRITING_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("Error: writing channel not found.", ephemeral=True)
        return

    await _repost_sticky(channel)
    await interaction.response.send_message("Writing panel sent!", ephemeral=True)


@art_group.command(name="share-writing", description="Submit writing (link, text, or file) to the writing channel")
async def art_share_writing(interaction: discord.Interaction):
    await interaction.response.send_modal(WritingModal())
