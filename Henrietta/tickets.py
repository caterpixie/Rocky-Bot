import discord
from discord import app_commands, ui
from discord.ui import View, Button, Modal, TextInput
import aiohttp
import io
import re
import os
from transcripting import export_ticket_to_html, cleanup_file, upload_transcript_to_r2
from datetime import datetime, timezone

# ============================================================
# CONFIGURATION
# ============================================================

EMBED_LOG_COLOR = "#FFC6D6"

TICKET_CHANNEL_ID = 1483328460213719123
SUPPORT_CATEGORY_ID = 1487175936230293747
LOG_CHANNEL_ID = 1487175733884354650

MOD_ROLE_IDS = {
    1534010789944627320,
    1486955771303301291
}

# For the embeds created in the individual ticket channels for the user
TICKET_TYPES = {
    "server-support": {
        "name_prefix": "support",
        "write_roles": [],  
        "view_roles": [],
        "title": "Server Support Ticket",
        "welcome": "Thanks for opening a server support ticket! Please describe your issue, and we will be with you shortly."
    },
    "mod-help": {
        "name_prefix": "mod-help",
        "write_roles": [],  
        "view_roles": [],
        "title": "Mod Help Ticket",
        "welcome": "Thank you for submitting a mod help ticket! Please describe your issue and I will be with you as soon as I can. Feel free to also ask in <#1483328022890287154> if I'm unavailable"
    },
    "other": {
        "name_prefix": "other",
        "write_roles": [],  
        "view_roles": [],
        "title": "Some Other Kinda Ticket",
        "welcome": "Thanks for opening a ticket! Please describe your issue, and we will be with you shortly."
    }
}

# For the ticket panel in the support channel
TICKET_PANEL = {
    "main_description": "Need to talk to staff about your mods, report a bug, or have some questions about the server?\n\nClick the button below to create a ticket!",      
    "button": {
        "label": "Create Ticket",
        "emoji": "📨",
        "style": discord.ButtonStyle.secondary
    },
    "color": "#FFC6D6"
}

# ============================================================
# BOT HOOKUP
# ============================================================

bot = None
def set_bot(bot_instance):
    global bot
    bot = bot_instance


def safe_avatar_url(user):
    return user.avatar.url if user.avatar else None

def is_mod(member: discord.Member) -> bool:
    return any(getattr(role, "id", None) in MOD_ROLE_IDS for role in getattr(member, "roles", []))

def can_interact_in_ticket(interaction: discord.Interaction) -> bool:
    perms = interaction.channel.permissions_for(interaction.user)
    return perms.send_messages

def get_ticket_meta(channel: discord.TextChannel) -> dict:
    """
    Reads opener + type from channel.topic.
    topic format: "ticket_opener_id=123; ticket_type=mod-help"
    """
    topic = channel.topic or ""
    opener_match = re.search(r"ticket_opener_id=(\d+)", topic)
    type_match = re.search(r"ticket_type=([a-z0-9\-]+)", topic)

    opener_id = int(opener_match.group(1)) if opener_match else None
    ticket_type = type_match.group(1) if type_match else None

    return {"opener_id": opener_id, "ticket_type": ticket_type}

async def user_has_open_ticket(guild: discord.Guild, user: discord.Member):
    category = guild.get_channel(SUPPORT_CATEGORY_ID)
    if not category:
        return None

    for channel in category.text_channels:
        meta = get_ticket_meta(channel)
        if meta["opener_id"] == user.id:
            return channel

    return None

async def log_ticket_open(
        guild: discord.Guild,
        user: discord.Member,
        channel: discord.TextChannel,
        ticket_type: str
    ):
        log_channel = guild.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return

        embed = discord.Embed(
            title="Ticket Opened",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="User/Creator", value=f"{user.mention}\n`{user}`")
        embed.add_field(name="Ticket Type", value=ticket_type.replace("-", " ").title())
        embed.set_footer(text=f"ID: {user.id}")
        embed.set_author(name=str(user), icon_url=safe_avatar_url(user))

        await log_channel.send(embed=embed)

async def get_ticket_participants(channel: discord.TextChannel) -> list[discord.User]:
    participants: dict[int, discord.User] = {}

    async for msg in channel.history(limit=None):
        author = msg.author
        if author.bot:
            continue

        participants[author.id] = author

    return list(participants.values())

async def dm_transcript_to_non_mod_participants(
    participants: list[discord.abc.User],
    guild: discord.Guild,
    channel: discord.TextChannel,
    transcript_url: str
):
    for user in participants:
        if user.bot:
            continue

        member = user if isinstance(user, discord.Member) else guild.get_member(user.id)
        if member is None:
            try:
                member = await guild.fetch_member(user.id)
            except Exception:
                continue

        if is_mod(member):
            continue

        try:
            dm_embed = discord.Embed(
                title=f"Your ticket in **{guild.name}** was closed",
                description="You can view the full transcript by clicking the link below.",
                color=discord.Color.from_str(EMBED_LOG_COLOR),
                timestamp=datetime.now(timezone.utc)
            )

            dm_embed.add_field(
                name="Ticket Name", 
                value=f"`{channel.name}`"
            )


            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="View Transcript", url=transcript_url))

            await user.send(embed=dm_embed, view=view) 

        except (discord.Forbidden, discord.HTTPException):
            continue

async def log_ticket_close(
    guild: discord.Guild,
    closed_by: discord.Member,
    channel: discord.TextChannel,
    opened_by: discord.Member | None,
    ticket_type: str | None,
    transcript_url: str,
    participants: list[discord.Member] | None = None
):
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        return

    embed = discord.Embed(
        title="Ticket Closed",
        color=discord.Color.from_str(EMBED_LOG_COLOR),
        timestamp=datetime.now(timezone.utc)
    )

    embed.add_field(
        name="Closed By",
        value=f"{closed_by.mention}\n`{closed_by}`",
        inline=False
    )

    embed.add_field(
        name="Ticket Type",
        value=(ticket_type or "Unknown").replace("-", " ").title(),
        inline=True
    )

    if participants:
        formatted = [
            f"{m.mention} (`{m.id}`)\n"
            for m in participants[:20]
        ]

        mentions = " ".join(formatted)

        if len(participants) > 20:
            mentions += f"\n+ {len(participants) - 20} more"

        if len(mentions) > 1024:
            mentions = mentions[:1020] + "..."

        embed.add_field(
            name="Participants",
            value=mentions,
            inline=False
        )
    else:
        embed.add_field(
            name="Participants",
            value="No one interacted with this ticket.",
            inline=False
        )
    embed.set_author(name=str(closed_by), icon_url=safe_avatar_url(closed_by))

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="View Transcript", url=transcript_url))

    await log_channel.send(embed=embed, view=view)

async def dm_user_ticket_attention(user: discord.abc.User, guild: discord.Guild, channel: discord.TextChannel):
    try:
        embed = discord.Embed(
            title="A ticket needs your attention",
            description=(
                f"A moderator added you to a ticket in **{guild.name}**.\n\n"
            ),
            color=discord.Color.from_str(EMBED_LOG_COLOR),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Open Ticket", value=f"https://discord.com/channels/{guild.id}/{channel.id}")

        await user.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


class TicketGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="ticket", description="Ticket Commands")

class TicketTypeSelect(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Server Support", value="server-support"),
            discord.SelectOption(label="Mod Help", value="mod-help"),
            discord.SelectOption(label="Other", value="other")
        ]

        super().__init__(
            placeholder="What are you opening a ticket about?",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        author = interaction.user

        existing = await user_has_open_ticket(guild, author)
        if existing:
            await interaction.response.send_message(
                f"You already have an open ticket: {existing.mention}\n"
                "Please close it before creating a new one.",
                ephemeral=True
            )
            return

        ticket_type = self.values[0]
        config = TICKET_TYPES[ticket_type]

        category = guild.get_channel(SUPPORT_CATEGORY_ID)
        if not category:
            await interaction.response.send_message("Error: Support category not found.", ephemeral=True)
            return
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            author: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True),
        }

        for role_id in config["write_roles"]:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True
                )

        for role_id in config["view_roles"]:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=False,
                    read_message_history=True
                )

        safe_name = author.name.lower().replace(" ", "-")
        channel_name = f"{config['name_prefix']}-{safe_name}"[:90]
        
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"ticket_opener_id={author.id}; ticket_type={ticket_type}"
        )

        await interaction.response.send_message(
            f"Your ticket has been created: {ticket_channel.mention}",
            ephemeral=True
        )

        embed = discord.Embed(
            title=f"{config['title']}",
            description=f"{author.mention}, {config['welcome']}",
            color=discord.Color.from_str("#D71919")
        )
        await ticket_channel.send(
            embed=embed,
            view=CloseTicketView()
        )

        await log_ticket_open(
            guild=guild,
            user=author,
            channel=ticket_channel,
            ticket_type=ticket_type
        )
       
class AddUserByIDModal(ui.Modal, title="Add user to ticket (ID only)"):
    user_id = ui.TextInput(
        label="User ID",
        placeholder="123456789012345678",
        required=True,
        min_length=15,
        max_length=25
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel: discord.TextChannel = interaction.channel
        guild: discord.Guild = interaction.guild

        perms = channel.permissions_for(interaction.user)
        if not perms.send_messages:
            await interaction.response.send_message(
                "Only ticket participants can do this.",
                ephemeral=True
            )
            return

        if not isinstance(interaction.user, discord.Member) or not is_mod(interaction.user):
            await interaction.response.send_message(
                "Only staff can do this.",
                ephemeral=True
            )
            return

        raw = str(self.user_id.value).strip()
        if not raw.isdigit():
            await interaction.response.send_message(
                "That doesn’t look like a valid user ID.",
                ephemeral=True
            )
            return

        uid = int(raw)

        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except discord.NotFound:
                await interaction.response.send_message(
                    "That user isn’t in this server.",
                    ephemeral=True
                )
                return
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I don’t have permission to fetch members.",
                    ephemeral=True
                )
                return
            except discord.HTTPException:
                await interaction.response.send_message(
                    "Failed to fetch that user. Try again.",
                    ephemeral=True
                )
                return

        if member.bot:
            await interaction.response.send_message(
                "You can’t add a bot to a ticket.",
                ephemeral=True
            )
            return

        if channel.permissions_for(member).view_channel:
            await interaction.response.send_message(
                "That user already has access to this ticket.",
                ephemeral=True
            )
            return

        await channel.set_permissions(
            member,
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            attach_files=True,
            embed_links=True
        )

        await interaction.response.send_message(
            f"Added {member.mention} to {channel.mention}.",
            ephemeral=True
        )
       
        await dm_user_ticket_attention(member, guild, channel)
   
class TicketPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None) 
        self.add_item(OpenTicketButton())

class TicketTypeView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect())

class OpenTicketButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="Create Ticket",
            style=discord.ButtonStyle.secondary,
            emoji="📨",
            custom_id="ticket:open" 
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Choose a ticket type:",
            view=TicketTypeView(),
            ephemeral=True
        )

class CloseTicketView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def _can_use_ticket_buttons(self, interaction: discord.Interaction) -> bool:
        perms = interaction.channel.permissions_for(interaction.user)
        return perms.send_messages

    @ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.secondary,
        emoji="🔒",
        custom_id="ticket:close"
    )
    async def close_ticket(self, interaction: discord.Interaction, button: ui.Button):
        if not self._can_use_ticket_buttons(interaction):
            await interaction.response.send_message(
                "Only ticket participants can use this.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Are you sure you want to close this ticket?",
            view=ConfirmCloseView()
        )

    @ui.button(
        label="Add User",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket:add_user_id"
    )
    async def add_user(self, interaction: discord.Interaction, button: ui.Button):
        if not self._can_use_ticket_buttons(interaction):
            await interaction.response.send_message(
                "Only ticket participants can use this.",
                ephemeral=True
            )
            return

        if not isinstance(interaction.user, discord.Member) or not is_mod(interaction.user):
            await interaction.response.send_message(
                "Only staff can use this.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(AddUserByIDModal())
   
class ConfirmCloseView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Confirm", style=discord.ButtonStyle.primary, custom_id="ticket:confirm_close")
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        channel = interaction.channel
        guild = interaction.guild
        closed_by = interaction.user

        perms = channel.permissions_for(interaction.user)
        if not perms.send_messages:
            await interaction.response.send_message(
                "Only ticket participants can confirm closing.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.followup.send("Closing ticket and generating transcript...", ephemeral=True)

            participants = await get_ticket_participants(channel)

            meta = get_ticket_meta(channel)
            opener_id = meta["opener_id"]
            ticket_type = meta["ticket_type"]

            opened_by = None
            if opener_id:
                opened_by = guild.get_member(opener_id)
                if opened_by is None:
                    try:
                        opened_by = await guild.fetch_member(opener_id)
                    except Exception:
                        opened_by = None

            transcript_path = None
            transcript_path, slug = await export_ticket_to_html(channel)
            transcript_url = await upload_transcript_to_r2(transcript_path, slug)

            await log_ticket_close(
                guild=guild,
                closed_by=closed_by,
                channel=channel,
                opened_by=opened_by,
                ticket_type=ticket_type,
                transcript_url=transcript_url,
                participants=participants
            )

            await dm_transcript_to_non_mod_participants(
                participants=participants,
                guild=guild,
                channel=channel,
                transcript_url=transcript_url
            )

            if transcript_path:
                cleanup_file(transcript_path)

            await channel.delete(reason="Ticket closed")

        except Exception as e:
            try:
                await interaction.followup.send(f"Ticket close failed: `{e}`", ephemeral=True)
            except Exception:
                pass
       
ticket_group = TicketGroup()

@ticket_group.command(name="setup", description="Sends the embed to the ticket channel")
async def embed_setup(interaction: discord.Interaction):
    ticket_embed_channel = interaction.guild.get_channel(TICKET_CHANNEL_ID)
    if not ticket_embed_channel:
        await interaction.response.send_message("Error: Ticket/support channel not found.", ephemeral=True)
        return
       
    header_file = discord.File("ticket-header.png", filename="ticket-header.png")

    title_embed = discord.Embed(
        color=discord.Color.from_str(TICKET_PANEL["color"])
    )
    title_embed.set_image(url="attachment://ticket_header.png")

    main_embed = discord.Embed(
        description=TICKET_PANEL["main_description"],
        color=discord.Color.from_str(TICKET_PANEL["color"])
    )

    await ticket_embed_channel.send(file=header_file)

    await ticket_embed_channel.send(embed=main_embed, view=TicketPanelView())

    await interaction.response.send_message("Ticket panel sent!", ephemeral=True)
