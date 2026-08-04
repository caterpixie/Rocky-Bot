import discord
from datetime import datetime, timezone

# ======================================
# CONFIGURATION
# ======================================

SERVER_ID = 1480390027593777287

LOG_CHANNEL_ID = 1486952263250477126
MESSAGE_LOG_CHANNEL_ID = 1486952344796270693
USER_LOG_CHANNEL_ID = 1486952230425722901

SCRIPTURE_CHANNEL_ID = 1486956103207096340
OFFICIAL_MOD_CHANNEL_ID = 1482168928045367349

MINOR_ROLE_ID = 1486955537286299700

# ======================================
# BOT HOOKUP
# ======================================

bot = None


def set_bot(bot_instance):
    global bot
    bot = bot_instance


def setup_logging(bot_instance: discord.Client):
    set_bot(bot_instance)

    @bot_instance.event
    async def on_member_join(user: discord.Member):
        if user.guild.id != SERVER_ID:
            return
        await log_member_join(user)

    @bot_instance.event
    async def on_member_remove(user: discord.Member):
        if user.guild.id != SERVER_ID:
            return
        await log_member_remove(user)

    @bot_instance.event
    async def on_message_delete(message: discord.Message):
        if message.guild is None or message.guild.id != SERVER_ID:
            return
        await log_message_delete(message)

    @bot_instance.event
    async def on_message_edit(before: discord.Message, after: discord.Message):
        if before.guild is None or before.guild.id != SERVER_ID:
            return
        await log_message_edit(before, after)

    @bot_instance.event
    async def on_member_update(before: discord.Member, after: discord.Member):
        if before.guild.id != SERVER_ID:
            return
        await log_member_update(before, after)

    @bot_instance.event
    async def on_voice_state_update(user: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if user.guild.id != SERVER_ID:
            return
        await log_voice_state_update(user, before, after)


async def log_event(channel_id: int, embed: discord.Embed):
    channel = bot.get_channel(channel_id)
    if channel:
        await channel.send(embed=embed)


# ================= ARRIVAL/DEPARTURE LOGS =================

async def log_member_join(user: discord.Member):
    now = datetime.now(timezone.utc)
    timestamp = int(user.created_at.timestamp())

    embed = discord.Embed(
        title="User Joined",
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
    icon_url = user.avatar.url if user.avatar else user.default_avatar.url

    embed.set_author(name=str(user), icon_url=icon_url)
    embed.add_field(name="User", value=user.mention)
    embed.add_field(name="Account Created", value=f"<t:{timestamp}:R>", inline=False)
    embed.set_footer(text=f"ID: {user.id}")
    embed.timestamp = now

    await log_event(LOG_CHANNEL_ID, embed)


async def log_member_remove(user: discord.Member):
    now = datetime.now(timezone.utc)
    roles = [role.mention for role in user.roles if role.name != "@everyone"]
    role_text = ", ".join(roles) if roles else "No roles"

    embed = discord.Embed(
        title="User Left",
        description=f"**Roles:** {role_text}",
        color=discord.Color.red()
    )
    embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
    icon_url = user.avatar.url if user.avatar else user.default_avatar.url

    embed.set_author(name=str(user), icon_url=icon_url)
    embed.set_footer(text=f"ID: {user.id}")
    embed.timestamp = now

    await log_event(LOG_CHANNEL_ID, embed)


# ================= MESSAGE LOGS =================

async def log_message_delete(message: discord.Message):

    now = datetime.now(timezone.utc)

    if message.author.bot:
        return

    embed = discord.Embed(
        description=f"**Message by {message.author.mention} deleted in {message.channel.mention}**\n{message.content}",
        color=discord.Color.red()
    )
    embed.set_author(name=str(message.author), icon_url=message.author.avatar.url)
    embed.timestamp = now

    # Log deleted images
    if message.attachments:
        image_urls = [
            attachment.url
            for attachment in message.attachments
            if attachment.content_type and "image" in attachment.content_type
        ]
        if image_urls:
            embed.add_field(name="Image(s)", value="\n".join(image_urls), inline=False)
            embed.set_image(url=image_urls[0])

    await log_event(MESSAGE_LOG_CHANNEL_ID, embed)


async def log_message_edit(before: discord.Message, after: discord.Message):

    now = datetime.now(timezone.utc)
    jump_url = f"https://discord.com/channels/{before.guild.id}/{before.channel.id}/{before.id}"

    if before.author.bot:
        return
    if before.guild is None:
        return
    if before.content == after.content:
        return

    embed = discord.Embed(
        description=(
            f"**Message by {before.author.mention} edited in {before.channel.mention}**\n"
            f"[Jump to message]({jump_url})\n\n"
            f"**Before:**\n{before.content}\n\n"
            f"**After:**\n{after.content}"
        ),
        color=discord.Color.orange()
    )
    icon_url = before.author.avatar.url if before.author.avatar else before.default_avatar.url
    embed.set_author(name=str(before.author), icon_url=icon_url)
    embed.timestamp = now

    await log_event(MESSAGE_LOG_CHANNEL_ID, embed)


# ================= USER LOGS =================

async def log_member_update(before: discord.Member, after: discord.Member):
    if before.bot:
        return

    now = datetime.now(timezone.utc)
    changes = []
    minor_added = False

    # Nickname change
    if before.nick != after.nick:
        changes.append(f"Nickname: `{before.nick}` → `{after.nick}`")

    # Role add/remove
    before_roles = set(before.roles)
    after_roles = set(after.roles)

    added_roles = after_roles - before_roles
    removed_roles = before_roles - after_roles

    if added_roles:
        changes.append(f"Roles Added: {', '.join(role.mention for role in added_roles)}")
        if any(role.id == MINOR_ROLE_ID for role in added_roles):
            minor_added = True

    if removed_roles:
        changes.append(f"Roles Removed: {', '.join(role.mention for role in removed_roles)}")

    if not changes:
        return

    embed = discord.Embed(
        title="User Updated",
        color=discord.Color.from_str("#99FCFF")
    )
    icon_url = before.avatar.url if before.avatar else before.default_avatar.url
    embed.set_author(name=str(before), icon_url=icon_url)
    embed.add_field(name="User", value=f"{before.mention} (`{before.id}`)")
    embed.add_field(name="Changes", value="\n".join(changes), inline=False)
    embed.timestamp = now

    await log_event(USER_LOG_CHANNEL_ID, embed)

    if minor_added:
        minor_embed = discord.Embed(
            title="Stinky Minor Alert",
            description=f"User {before.mention} has selected the -17 role. They have been permanently muted until they open a ticket.",
            color=discord.Color.red(),
        )
        minor_embed.set_footer(text=f"ID:{before.id}")
        mod_channel = bot.get_channel(OFFICIAL_MOD_CHANNEL_ID)
        if mod_channel:
            await mod_channel.send(content=role_mention, embed=minor_embed)


async def log_voice_state_update(user: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if user.bot:
        return

    now = datetime.now(timezone.utc)

    if before.channel != after.channel:
        if before.channel and not after.channel:
            embed = discord.Embed(
                description=f"{user.mention} left voice channel {before.channel.mention}",
                color=discord.Color.red()
            )
            embed.timestamp = now
            embed.set_author(name=str(user), icon_url=user.avatar.url)
            await log_event(USER_LOG_CHANNEL_ID, embed)

        elif after.channel and not before.channel:
            embed = discord.Embed(
                description=f"{user.mention} joined voice channel {after.channel.mention}",
                color=discord.Color.green()
            )
            embed.timestamp = now
            embed.set_author(name=str(user), icon_url=user.avatar.url)
            await log_event(USER_LOG_CHANNEL_ID, embed)

        elif before.channel and after.channel:
            embed = discord.Embed(
                description=f"{user.mention} moved from voice channel {before.channel.mention} to {after.channel.mention}",
                color=discord.Color.green()
            )
            embed.timestamp = now
            icon_url = user.avatar.url if user.avatar else user.default_avatar.url
            embed.set_author(name=str(user), icon_url=icon_url)
            await log_event(USER_LOG_CHANNEL_ID, embed)


