import discord
from discord import app_commands, ui
from datetime import datetime, timezone

from tickets import TICKET_CHANNEL_ID

# ============================================================
# CONFIGURATION
# ============================================================

MODHELP_LOG_CHANNEL_ID = 1487175733884354650

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
    "missing-mist": {
        "label": "Getting the error: Missing mist data",
        "response": {
            "embed": {
                "description": "This error is caused by files left over from a previous update. In order to fix it, please make sure to delete the old folder for the mod (in your mods folder) before installing the newest version.",
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

header_file = discord.File("modhelp-header.png", filename="modhelp-header.png")

async def _log_menu_selection(interaction: discord.Interaction, option_key: str, option_label: str):
    if not MODHELP_LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(MODHELP_LOG_CHANNEL_ID)
    if channel is None:
        return

    user = interaction.user
    icon_url = user.display_avatar.url if user.display_avatar else None

    embed = discord.Embed(
        title="Mod Help Menu Used",
        description=f"{user.mention} selected **{option_label}**",
        color=discord.Color.from_str("#FFC6D6"),
    )
    embed.set_author(name=str(user), icon_url=icon_url)
    embed.set_footer(text=f"User ID: {user.id}")
    embed.timestamp = datetime.now(timezone.utc)

    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass



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
        await _log_menu_selection(interaction, choice, config["label"])
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
        super().__init__(name="modhelp", description="Mod issue self-checkout dropdown commands")


modhelp_group = ModHelpGroup()


@modhelp_group.command(name="setup", description="Posts the mod issue self-checkout dropdown to the ticket channel")
async def modhelp_setup(interaction: discord.Interaction):
    channel = interaction.guild.get_channel(TICKET_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("Error: Ticket channel not found.", ephemeral=True)
        return

    title_embed = discord.Embed(
        title=MENU_PANEL["title"],
        description=MENU_PANEL["description"],
        color=discord.Color.from_str(MENU_PANEL["color"]),
    )
    title_embed.set_image(url="attachment://modhelp_header.png")

    await channel.send(embed=title_embed, view=MenuView())
    await interaction.response.send_message("Mod issue self-checkout menu sent!", ephemeral=True)
