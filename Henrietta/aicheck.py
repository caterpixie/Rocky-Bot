import re
import discord
from discord import app_commands
from discord.ext import commands
import aiomysql

from tickets import is_mod  # reuse the same staff role check as tickets.py

# ============================================================
# CONFIGURATION
# ============================================================

EMBED_COLOR_STAFF = "#FFC6D6"

# ============================================================
# BOT HOOKUP
# ============================================================

bot = None

def set_bot(bot_instance):
    global bot
    bot = bot_instance
    bot.add_command(ai_check)

NEXUS_URL_RE = re.compile(r"nexusmods\.com/fieldsofmistria/mods/(\d+)", re.IGNORECASE)

def extract_nexus_mod(text: str):
    match = NEXUS_URL_RE.search(text or "")
    if not match:
        return None

    mod_id = int(match.group(1))
    canonical_url = f"https://www.nexusmods.com/fieldsofmistria/mods/{mod_id}"
    return mod_id, canonical_url

async def _get_entry(guild_id: int, mod_id: int):
    async with bot.pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM ai_mods WHERE guild_id = %s AND mod_id = %s",
                (guild_id, mod_id),
            )
            return await cur.fetchone()


async def _add_entry(guild_id, mod_id, mod_url, ai_type, alternative_url, notes, added_by):
    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO ai_mods
                    (guild_id, mod_id, mod_url, ai_type, alternative_url, notes, added_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    mod_url = VALUES(mod_url),
                    ai_type = VALUES(ai_type),
                    alternative_url = VALUES(alternative_url),
                    notes = VALUES(notes),
                    added_by = VALUES(added_by)
                """,
                (guild_id, mod_id, mod_url, ai_type, alternative_url, notes, added_by),
            )


async def _remove_entry(guild_id, mod_id) -> int:
    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM ai_mods WHERE guild_id = %s AND mod_id = %s",
                (guild_id, mod_id),
            )
            return cur.rowcount


async def _set_alt(guild_id, mod_id, alternative_url) -> int:
    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE ai_mods SET alternative_url = %s WHERE guild_id = %s AND mod_id = %s",
                (alternative_url, guild_id, mod_id),
            )
            return cur.rowcount


async def _list_entries(guild_id: int, limit: int = 25):
    async with bot.pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM ai_mods WHERE guild_id = %s ORDER BY created_at DESC LIMIT %s",
                (guild_id, limit),
            )
            return await cur.fetchall()


@commands.command(name="ai", help="Check whether a Nexus Mods link is flagged for AI use")
async def ai_check(ctx: commands.Context, *, link: str = None):
    if not ctx.guild:
        return

    if not link:
        await ctx.reply(
            "Usage: `?ai <nexus mods link>`",
            mention_author=False,
        )
        return

    parsed = extract_nexus_mod(link)
    if not parsed:
        await ctx.reply(
            "Please paste the full Nexus URL.",
            mention_author=False,
        )
        return

    mod_id, canonical_url = parsed
    entry = await _get_entry(ctx.guild.id, mod_id)

    if entry is None:
        embed = discord.Embed(
            description=f"[This mod]({canonical_url}) isn't on the AI-use list. Please still make sure to check Nexus tags and the description.",
            color=discord.Color.green(),
        )
        await ctx.reply(embed=embed, mention_author=False)
        return

    if entry["ai_type"] == "ai_media":
        embed = discord.Embed(
            title="AI media used",
            description=f"[This mod]({canonical_url}) uses (or used at some point) AI-generated media either in the game or on the Nexus page.",
            color=discord.Color.gold(),
        )
    else:
        embed = discord.Embed(
            title="Heavy AI code used",
            description=f"[This mod]({canonical_url}) heavily uses AI code and/or was made in a way that may compromise your save if uninstalled. Please download and use with caution as it may not be fully functional.",
            color=discord.Color.red(),
        )

    if entry.get("notes"):
        embed.add_field(name="Notes", value=entry["notes"], inline=False)

    if entry.get("alternative_url"):
        embed.add_field(
            name="Alternative available",
            value=f"Here's an alternative link that does something similar: {entry['alternative_url']}",
            inline=False,
        )

    await ctx.reply(embed=embed, mention_author=False)

class AiGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="ai", description="Manage the AI-mod flag list")

ai_group = AiGroup()

def _staff_check(interaction: discord.Interaction) -> bool:
    return isinstance(interaction.user, discord.Member) and is_mod(interaction.user)


@ai_group.command(name="add", description="Flag a Nexus Mods link as using AI (staff only)")
@app_commands.describe(
    link="The Nexus Mods link to flag",
    type="Whether AI was used in code/logic (risky) or media",
    alternative="Optional: an alternative link that does the same thing",
    notes="Optional: extra context",
)
@app_commands.choices(type=[
    app_commands.Choice(name="AI heavily used (code/logic, could brick save)", value="ai_used"),
    app_commands.Choice(name="AI media used", value="ai_media"),
])
async def ai_add(
    interaction: discord.Interaction,
    link: str,
    type: app_commands.Choice[str],
    alternative: str = None,
    notes: str = None,
):
    if not _staff_check(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return

    parsed = extract_nexus_mod(link)
    if not parsed:
        await interaction.response.send_message("Plz verify this is a Nexus link", ephemeral=True)
        return

    alt_canonical = None
    if alternative:
        alt_parsed = extract_nexus_mod(alternative)
        if not alt_parsed:
            await interaction.response.send_message(
                "Plz verify this is a Nexus link", ephemeral=True
            )
            return
        alt_canonical = alt_parsed[1]

    mod_id, canonical_url = parsed

    await _add_entry(
        interaction.guild.id, mod_id, canonical_url,
        type.value, alt_canonical, notes, interaction.user.id,
    )

    embed = discord.Embed(
        title="Mod flagged",
        description=f"[{canonical_url}]({canonical_url}) flagged as **{type.name}**.",
        color=discord.Color.from_str(EMBED_COLOR_STAFF),
    )
    if alt_canonical:
        embed.add_field(name="Alternative", value=alt_canonical, inline=False)
    if notes:
        embed.add_field(name="Notes", value=notes, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@ai_group.command(name="remove", description="Remove a mod from the AI-flag list")
@app_commands.describe(link="The Nexus Mods link to unflag")
async def ai_remove(interaction: discord.Interaction, link: str):
    if not _staff_check(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return

    parsed = extract_nexus_mod(link)
    if not parsed:
        await interaction.response.send_message("Plz verify this is a Nexus link", ephemeral=True)
        return

    mod_id, canonical_url = parsed
    rows = await _remove_entry(interaction.guild.id, mod_id)

    if rows:
        await interaction.response.send_message(f"Removed {canonical_url} from the AI-flag list.", ephemeral=True)
    else:
        await interaction.response.send_message("That mod wasn't on the list.", ephemeral=True)


@ai_group.command(name="setalt", description="Add or update the alternative link for a flagged mod")
@app_commands.describe(
    link="The already-flagged Nexus Mods link",
    alternative="A non-AI alternative link that does the same thing",
)
async def ai_setalt(interaction: discord.Interaction, link: str, alternative: str):
    if not _staff_check(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return

    parsed = extract_nexus_mod(link)
    alt_parsed = extract_nexus_mod(alternative)
    if not parsed or not alt_parsed:
        await interaction.response.send_message("Plz verify this is a Nexus link", ephemeral=True)
        return

    mod_id, canonical_url = parsed
    alt_canonical = alt_parsed[1]
    rows = await _set_alt(interaction.guild.id, mod_id, alt_canonical)

    if rows:
        await interaction.response.send_message(
            f"Alternative set for {canonical_url}: {alt_canonical}", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "That mod isn't on the AI-flag list yet", ephemeral=True
        )


@ai_group.command(name="list", description="List mods currently flagged for AI use")
async def ai_list(interaction: discord.Interaction):
    if not _staff_check(interaction):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
        return

    entries = await _list_entries(interaction.guild.id)
    if not entries:
        await interaction.response.send_message("No mods are currently flagged.", ephemeral=True)
        return

    lines = []
    for e in entries:
        tag = "ai used" if e["ai_type"] == "ai_used" else "ai media"
        alt = f" (alt: {e['alternative_url']})" if e["alternative_url"] else ""
        lines.append(f"• [{tag}] {e['mod_url']}{alt}")

    embed = discord.Embed(
        title="AI-flagged mods",
        description="\n".join(lines)[:4000],
        color=discord.Color.from_str(EMBED_COLOR_STAFF),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
