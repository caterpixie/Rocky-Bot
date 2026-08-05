import discord
from datetime import datetime, timezone

# ======================================
# CONFIGURATION
# ======================================

SERVER_ID = 1480390027593777287
RULES_CHANNEL_ID = 1482168928045367346

EMBED_COLOR = "#FFC6D6"

INTRO_TEMPLATE = (
    "**Name/Nickname:**\n"
    "**Timezone:**\n"
    "**Pings/DMs okay?:**\n"
    "**Your favorite FoM NPC:**\n"
    "**Why you joined the server:**\n"
    "**What other games do you play?:**\n"
    "**Hobbies:**\n"
    "**Send a picture of your pet 🫴**\n"
)
# ======================================
# BOT HOOKUP
# ======================================

bot = None


def set_bot(bot_instance):
    global bot
    bot = bot_instance

def setup_welcome(bot_instance: discord.Client):
    set_bot(bot_instance)

    bot_instance.add_listener(on_member_join_welcome, "on_member_join")


# ======================================
# HANDLER
# ======================================

async def on_member_join_welcome(member: discord.Member):
    if member.guild.id != SERVER_ID:
        return

    rules_mention = f"<#{RULES_CHANNEL_ID}>" if RULES_CHANNEL_ID else "the rules channel"

    embed = discord.Embed(
        title=f"Welcome to {member.guild.name}, {member.display_name}!",
        description=(
            f"**Get your ass over to {rules_mention} and accept the rules** to unlock the rest of the server~\n\n"
            f"Then (if you wanna) you can use this template to introduce yourself to the server in <#1498057003220865126>! <:m_happi:1482560583890112694>\n"
        ),
        color=discord.Color.from_str(EMBED_COLOR),
    )
    embed.add_field(name="Intro Template", value=INTRO_TEMPLATE, inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Member #{member.guild.member_count}")

    try:
        await member.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        print(f"[welcome] Couldn't DM {member} — skipping.")
        return
