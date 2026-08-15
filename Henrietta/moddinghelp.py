import asyncio
import discord
from discord import app_commands, ui
import aiomysql

# ============================================================
# CONFIGURATION
# ============================================================

MENU_CHANNEL_ID = 1528271445799604315
STICKY_DEBOUNCE_SECONDS = 10

MENU_PANEL = {
    "title": "<:x_staricon:1523500147085021318> Mod Issue Self-Checkout",
    "description": "Pick an option in the dropdown below to display the debug steps for the issue you are experiencing.\n\nIf these steps don't fix your issue, please ping <@700767738997768344> or <#1483328460213719123>, and we'll be able to help!",
    "color": "#FFC6D6",
}

MENU_OPTIONS = {
    "gml-changed": {
        "label": "Game GML changed. Unable to install.",
        "response": {
            "embed": {
                "description": "This is a MOMI security feature, **not a bug with your mod**. Every time the game is patched and a GML seam is changed, MOMI will skip the installation of GML mods until MOMI can be updated.\n\n*To fix this issue:*\n  1. Download the latest version of MOMI that was updated AFTER the latest game patch.\n  2. Move your mods folder to another location (ex. your desktop) to not lose your mods\n  3. Go to ProgramFile (x86) > Steam > steamapps > common and delete the Fields of Mistria folder (this won't delete your save files, they're in a different location)\n  4. Reinstall the game through Steam",
                "color": discord.Color.from_str("#FFC6D6"),
            }
        },
    },
    "not-showing": {
        "label": "Mods aren't not showing up in game.",
        "response": {
            "embed": {
                "description": "After a patch, mods need to be uninstalled and reinstalled to work, since the assets folder changed.\n\n*To fix this issue:*\n  1. Uninstall all your mods\n  2. Delete the \"assets.back.zip\" folder from your FoM directory.\n  3. In Steam, go to Properties > Installed files > verify the integrity of your game.\n  3. Reinstall your mods through MOMI.",
                "color": discord.Color.from_str("#FFC6D6"),
            }
        },
    },
    "crash-with-error": {
        "label": "My game crashed with an error",
        "response": {
            "embed": {
                "description": "If your game crashed with an error, please post the following details:\n\n 1. A screenshot or copy of what the error was\n 2. When the crash occured (ie. on startup or after taking a specific action)\n 3. A full list of your mods (either a screenshot of your MOMI list or your mods folder)",
                "color": discord.Color.from_str("#FFC6D6"),
            }
        },
    },
    "crash-with-no-error": {
        "label": "My game crashed silently on startup",
        "response": {
            "embed": {
                "description": "If your game crashed on startup with no error, please post the following details:\n\n 1.  A full list of your mods (either a screenshot of your MOMI list or your mods folder)\n 2. Your terminal error log.\n\nYou can get your terminal log by following the steps below:\n 1. Go to ProgramFile (x86) > Steam > steamapps > common\n 2. Right click on the Fields of Mistria folder and click \"Open in Terminal\"\n 3. In the terminal, type in: \"powershell -noexit -Command \"& ./FieldsOfMistria.exe --debug-tools=true 2>&1 | Write-Host\"; %command%\"",
                "color": discord.Color.from_str("#FFC6D6"),
            }
        },
    },
    "invalid-save": {
        "label": "My save is saying it's invalid",
        "response": {
            "embed": {
                "description": "An invalid save is typically casued by a mod that changes your save data that was uninstalled.\n\nKnown mods that cause this issue:\n1. Better Storage (I believe actually any storage mods can cause this)\n2. Better Greenhouses\n\nThis issue can be fixed by installing the latest update of the mod. If you are trying to uninstall them, please load into your save with them installed, remove the greenhouses/chests, then uninstall the mod.",
                "color": discord.Color.from_str("#FFC6D6"),
            }
        },
    }
}

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


async def _clear_sticky_message_id(channel_id: int):
    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM sticky_messages WHERE channel_id = %s",
                (channel_id,),
            )

_pending_repost_tasks: dict[int, asyncio.Task] = {}


def _build_MENU_embed() -> discord.Embed:
    return discord.Embed(
        title=MENU_PANEL["title"],
        description=MENU_PANEL["description"],
        color=discord.Color.from_str(MENU_PANEL["color"]),
    )


async def _repost_sticky(channel: discord.TextChannel):
    old_message_id = await _get_sticky_message_id(channel.id)
    if old_message_id:
        try:
            old_message = await channel.fetch_message(old_message_id)
            await old_message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    new_message = await channel.send(embed=_build_MENU_embed(), view=MenuView())
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
    if message.channel.id != MENU_CHANNEL_ID:
        return

    if bot.user and message.author.id == bot.user.id:
        return

    existing_task = _pending_repost_tasks.get(message.channel.id)
    if existing_task and not existing_task.done():
        existing_task.cancel()

    _pending_repost_tasks[message.channel.id] = asyncio.create_task(
        _debounced_repost(message.channel)
    )

class MenuSelect(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=data["label"],
                value=key,
                description=data.get("description"),
                emoji=data.get("emoji"),
            )
            for key, data in MENU_OPTIONS.items()
        ]

        super().__init__(
            placeholder="Select a topic...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="menu:select", 
        )

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        config = MENU_OPTIONS.get(choice)

        if config is None:
            await interaction.response.send_message(
                "That option isn't available anymore.",
                ephemeral=True,
            )
            return

        response = config["response"]

        # Reset the select back to its placeholder so it can be picked again,
        # then send the actual answer as a separate ephemeral followup.
        await interaction.response.edit_message(view=MenuView())

        if isinstance(response, dict) and "embed" in response:
            embed = discord.Embed(**response["embed"])
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(str(response), ephemeral=True)


class MenuView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MenuSelect())

class ModHelpGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="modhelp", description="Sticky info dropdown menu commands")


modhelp_group = ModHelpGroup()


@modhelp_group.command(name="setup", description="Posts the sticky dropdown menu to the configured channel")
async def modhelp_setup(interaction: discord.Interaction):
    channel = interaction.guild.get_channel(MENU_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("Error: modding-help channel not found.", ephemeral=True)
        return

    old_message_id = await _get_sticky_message_id(channel.id)
    if old_message_id:
        try:
            old_message = await channel.fetch_message(old_message_id)
            await old_message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    new_message = await channel.send(embed=_build_MENU_embed(), view=MenuView())
    await _set_sticky_message_id(channel.id, new_message.id)

    await interaction.response.send_message("Info menu sent!", ephemeral=True)
