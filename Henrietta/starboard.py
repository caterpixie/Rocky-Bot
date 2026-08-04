import discord
from datetime import datetime, timezone

# =========================
# CONFIGURATION
# =========================

STARBOARD_CHANNEL_ID = 1486956103207096340
EXCLUDED_CHANNEL_IDS = [
]

STAR_EMOJIS = {"⭐"}
STAR_THRESHOLD = 3

EMBED_COLOR = "#FFC6D6"

# =========================
# BOT HOOKUP
# =========================

bot = None
starred_messages = {}


def set_bot(bot_instance):
    global bot
    bot = bot_instance


async def build_starboard_payload(message: discord.Message):
    embed = discord.Embed(
        description=(
            f"{message.content}\n\n[Jump to Message!]({message.jump_url})"
            if message.content
            else f"[No text]\n\n[Jump to Message!]({message.jump_url})"
        ),
        color=discord.Color.from_str(EMBED_COLOR),
    )

    embed.set_author(
        name=str(message.author),
        icon_url=message.author.display_avatar.url,
    )
    embed.timestamp = datetime.now(timezone.utc)

    files = []

    if message.attachments:
        attachment = message.attachments[0]

        spoiler = attachment.is_spoiler()
        file = await attachment.to_file(
            use_cached=True,
            spoiler=spoiler,
        )
        files.append(file)

        if not spoiler and attachment.content_type and attachment.content_type.startswith("image/"):
            embed.set_image(url=f"attachment://{file.filename}")

    return embed, files


def setup_starboard(bot_instance: discord.Client):
    set_bot(bot_instance)

    @bot_instance.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
        emoji = str(payload.emoji)

        if emoji not in STAR_EMOJIS:
            return

        if payload.channel_id in EXCLUDED_CHANNEL_IDS:
            return

        channel = bot.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(payload.channel_id)
            except discord.NotFound:
                return
            except discord.Forbidden:
                return
            except discord.HTTPException:
                return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return
        except discord.Forbidden:
            return
        except discord.HTTPException:
            return

        if message.channel.id == STARBOARD_CHANNEL_ID:
            return

        count = 0
        for reaction in message.reactions:
            if str(reaction.emoji) == emoji:
                count = reaction.count
                break

        if count < STAR_THRESHOLD:
            return

        starboard = bot.get_channel(STARBOARD_CHANNEL_ID)
        if starboard is None:
            try:
                starboard = await bot.fetch_channel(STARBOARD_CHANNEL_ID)
            except discord.NotFound:
                return
            except discord.Forbidden:
                return
            except discord.HTTPException:
                return

        embed, files = await build_starboard_payload(message)

        key = (message.id, emoji)

        if key in starred_messages:
            try:
                old_msg = await starboard.fetch_message(starred_messages[key])

                await old_msg.delete()

                new_msg = await starboard.send(
                    content=f"{emoji} {count}",
                    embed=embed,
                    files=files,
                )
                starred_messages[key] = new_msg.id

            except discord.NotFound:
                starred_messages.pop(key, None)

                new_msg = await starboard.send(
                    content=f"{emoji} {count}",
                    embed=embed,
                    files=files,
                )
                starred_messages[key] = new_msg.id

        else:
            new_msg = await starboard.send(
                content=f"{emoji} {count}",
                embed=embed,
                files=files,
            )
            starred_messages[key] = new_msg.id
