import calendar
import re
import traceback
import warnings
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, available_timezones

import aiomysql
import discord
from discord import app_commands
from discord.ext import tasks

# =========================
# CONFIGURATION
# =========================
BIRTHDAY_CHANNEL_ID = 1568804992675086438       
BIRTHDAY_ROLE_ID = 1568805784685650032         
BIRTHDAY_PING_ROLE_ID = 1568805784685650032      
BIRTHDAY_MOD_ROLE_ID = 1486955771303301291    

EMBED_COLOR = "#FFC6D6"
ROLE_DURATION = timedelta(hours=24)

THREAD_AUTO_ARCHIVE_MINUTES = 1440

DEFAULT_TIME = "12:00 AM"
COMMON_TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Paris",
    "Asia/Tokyo",
    "Australia/Sydney",
    "UTC",
]

# =========================
# BOT HOOKUP
# =========================
bot = None


def set_bot(bot_instance):
    global bot
    bot = bot_instance
    for name, value in (
        ("BIRTHDAY_CHANNEL_ID", BIRTHDAY_CHANNEL_ID),
        ("BIRTHDAY_ROLE_ID", BIRTHDAY_ROLE_ID),
    ):
        if not value:
            print(f"[BIRTHDAY] {name} is not set in birthday.py, so that part is disabled.")


def _can_manage(member: discord.Member) -> bool:
    if member.guild_permissions.manage_guild:
        return True
    if BIRTHDAY_MOD_ROLE_ID:
        return any(role.id == BIRTHDAY_MOD_ROLE_ID for role in member.roles)
    return False


# =========================
# DATABASE SETUP
# =========================
# birthdays.last_announced_at, birthday_roles.expires_at are naive UTC datetimes.
TABLES = [
    """
    CREATE TABLE IF NOT EXISTS birthdays (
        guild_id BIGINT UNSIGNED NOT NULL,
        user_id BIGINT UNSIGNED NOT NULL,
        month TINYINT UNSIGNED NOT NULL,
        day TINYINT UNSIGNED NOT NULL,
        hour TINYINT UNSIGNED NOT NULL DEFAULT 0,
        minute TINYINT UNSIGNED NOT NULL DEFAULT 0,
        timezone VARCHAR(64) NOT NULL,
        birth_year SMALLINT UNSIGNED NULL,
        show_age BOOLEAN NOT NULL DEFAULT FALSE,
        last_announced_at DATETIME NULL,
        PRIMARY KEY (guild_id, user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS birthday_gifs (
        id INT NOT NULL AUTO_INCREMENT,
        guild_id BIGINT UNSIGNED NOT NULL,
        url VARCHAR(500) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uq_birthday_gif (guild_id, url)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS birthday_roles (
        guild_id BIGINT UNSIGNED NOT NULL,
        user_id BIGINT UNSIGNED NOT NULL,
        expires_at DATETIME NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


async def ensure_tables():
    """Creates the birthday tables if they do not exist yet."""
    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  
                for statement in TABLES:
                    await cur.execute(statement)


# =========================
# TIME HELPERS
# =========================
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_db(dt: datetime) -> datetime:
    """Aware datetime -> naive UTC datetime for MySQL."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


_TZ_LOOKUP = {name.lower(): name for name in available_timezones()}
_TZ_NAMES = sorted(_TZ_LOOKUP.values())

_TIME_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m?\.?\s*$|^\s*(\d{1,2}):(\d{2})\s*$", re.I)


def resolve_timezone(text: str) -> Optional[str]:
    """Returns the canonical IANA name for user input, or None if it is not a valid timezone."""
    return _TZ_LOOKUP.get(text.strip().replace(" ", "_").lower())


def parse_time(text: str) -> Optional[tuple[int, int]]:
    """Accepts '14:30', '9:00 AM', '9am', '9 pm'. Returns (hour, minute) in 24h time, or None."""
    match = _TIME_RE.match(text)
    if not match:
        return None
    if match.group(4) is not None: 
        hour, minute = int(match.group(4)), int(match.group(5))
        meridiem = None
    else:
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        meridiem = match.group(3).lower()
    if minute > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "p" else 0)
    elif hour > 23:
        return None
    return hour, minute


def validate_date(month: int, day: int, year: Optional[int]) -> Optional[str]:
    """Returns an error message, or None if the date is valid."""
    if year is None:
        if day > calendar.monthrange(2024, month)[1]:  
            return f"{calendar.month_name[month]} doesn't have {day} days."
        return None
    today = _utcnow().date()
    if year < 1900 or year > today.year:
        return f"Birth year must be between 1900 and {today.year}."
    if day > calendar.monthrange(year, month)[1]:
        return f"{calendar.month_name[month]} {year} doesn't have {day} days."
    if date(year, month, day) > today:
        return "That birth date is in the future."
    return None


def _occurrence(row: dict, year: int) -> datetime:
    """The moment the birthday starts in the given (local) year, as an aware datetime.
    Feb 29 birthdays are celebrated on Feb 28 in non-leap years."""
    day = row["day"]
    if row["month"] == 2 and day == 29 and not calendar.isleap(year):
        day = 28
    return datetime(year, row["month"], day, row["hour"], row["minute"], tzinfo=ZoneInfo(row["timezone"]))


def _current_window(row: dict, now: datetime) -> Optional[tuple[datetime, int]]:
    """If the 24h birthday window is open at `now`, returns (start, local_year), else None."""
    local_year = now.astimezone(ZoneInfo(row["timezone"])).year
    for year in (local_year, local_year - 1):  
        start = _occurrence(row, year)
        if start <= now < start + ROLE_DURATION:
            return start, year
    return None


def _next_occurrence(row: dict, now: datetime) -> datetime:
    local_year = now.astimezone(ZoneInfo(row["timezone"])).year
    for year in (local_year, local_year + 1):
        start = _occurrence(row, year)
        if start > now:
            return start
    return _occurrence(row, local_year + 2)


def _age_for(row: dict, year: int) -> Optional[int]:
    if row.get("show_age") and row.get("birth_year"):
        return year - row["birth_year"]
    return None


def _ordinal(n: int) -> str:
    """1 -> 1st, 2 -> 2nd, 3 -> 3rd, 11 -> 11th, 21 -> 21st, 112 -> 112th"""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _format_time(hour: int, minute: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {suffix}"


# =========================
# RANDOM GIF
# =========================
async def _random_gif(guild_id: int) -> Optional[str]:
    """Returns a random GIF url from the server's list, or None if the list is empty."""
    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT url FROM birthday_gifs WHERE guild_id = %s ORDER BY RAND() LIMIT 1", (guild_id,)
            )
            row = await cur.fetchone()
            return row[0] if row else None


# =========================
# ANNOUNCEMENT
# =========================
def _build_embed(member: discord.abc.User, age: Optional[int], gif_url: Optional[str], is_test: bool) -> discord.Embed:
    if age is not None:
        first_line = f"Today is {member.mention}'s {_ordinal(age)} birthday!"
    else:
        first_line = f"Today is {member.mention}'s birthday!"
    description = f"{first_line}\n\nLeave a wish in the thread and react to celebrate!"

    embed = discord.Embed(
        title=f"🎂 Happy Birthday, {member.display_name}!",
        description=description,
        color=discord.Color.from_str(EMBED_COLOR),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if gif_url:
        embed.set_image(url=gif_url)
    embed.set_footer(text="Test announcement" if is_test else "Happy birthday!")
    return embed


async def _send_announcement(
    guild: discord.Guild, member: discord.Member, age: Optional[int], is_test: bool
) -> discord.Message:
    channel = guild.get_channel(BIRTHDAY_CHANNEL_ID) if BIRTHDAY_CHANNEL_ID else None
    if channel is None:
        raise RuntimeError("Birthday channel is not configured or was not found.")

    gif_url = await _random_gif(guild.id)
    embed = _build_embed(member, age, gif_url, is_test)

    parts = []
    roles = []
    if BIRTHDAY_PING_ROLE_ID:
        parts.append(f"<@&{BIRTHDAY_PING_ROLE_ID}>")
        roles.append(discord.Object(id=BIRTHDAY_PING_ROLE_ID))
    parts.append(member.mention)

    message = await channel.send(
        content=" ".join(parts),
        embed=embed,
        allowed_mentions=discord.AllowedMentions(users=[member], roles=roles),
    )

    try:
        await message.create_thread(
            name=f"Birthday wishes for {member.display_name}"[:100],
            auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
        )
    except discord.HTTPException as e:
        print(f"[BIRTHDAY] Could not create wishes thread: {e}")

    for emoji in ("🎂", "🎉"):
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            break

    return message


# =========================
# SCHEDULER
# =========================
async def _fetch_member(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    member = guild.get_member(user_id)
    if member:
        return member
    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        return None


async def _announce_due():
    now = _utcnow()
    async with bot.pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM birthdays")
            rows = await cur.fetchall()

    for row in rows:
        try:
            guild = bot.get_guild(row["guild_id"])
            if not guild:
                continue
            window = _current_window(row, now)
            if not window:
                continue
            start, year = window
            start_db = _to_db(start)
            if row["last_announced_at"] and row["last_announced_at"] >= start_db:
                continue


            async with bot.pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        UPDATE birthdays SET last_announced_at = %s
                        WHERE guild_id = %s AND user_id = %s
                          AND (last_announced_at IS NULL OR last_announced_at < %s)
                        """,
                        (start_db, row["guild_id"], row["user_id"], start_db),
                    )
                    if cur.rowcount == 0:
                        continue

            member = await _fetch_member(guild, row["user_id"])
            if member is None:
                continue

            await _grant_role(guild, member, start + ROLE_DURATION)
            await _send_announcement(guild, member, _age_for(row, year), is_test=False)
        except Exception:
            print(f"[BIRTHDAY] Failed to announce for user {row.get('user_id')}:")
            traceback.print_exc()


async def _grant_role(guild: discord.Guild, member: discord.Member, expires_at: datetime):
    role = guild.get_role(BIRTHDAY_ROLE_ID) if BIRTHDAY_ROLE_ID else None
    if role is None:
        return
    try:
        await member.add_roles(role, reason="Birthday")
    except discord.HTTPException as e:
        print(f"[BIRTHDAY] Could not give birthday role to {member.id}: {e}")
        return
    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO birthday_roles (guild_id, user_id, expires_at) VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE expires_at = VALUES(expires_at)
                """,
                (guild.id, member.id, _to_db(expires_at)),
            )


async def _expire_roles():
    async with bot.pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM birthday_roles WHERE expires_at <= %s", (_to_db(_utcnow()),))
            rows = await cur.fetchall()

    for row in rows:
        keep_row = False
        try:
            guild = bot.get_guild(row["guild_id"])
            role = guild.get_role(BIRTHDAY_ROLE_ID) if guild and BIRTHDAY_ROLE_ID else None
            if guild and role:
                member = await _fetch_member(guild, row["user_id"])
                if member and role in member.roles:
                    await member.remove_roles(role, reason="Birthday over")
        except discord.Forbidden:
            print(f"[BIRTHDAY] Missing permission to remove the birthday role from {row['user_id']}.")
        except discord.HTTPException as e:
            print(f"[BIRTHDAY] Could not remove birthday role from {row['user_id']}, will retry: {e}")
            keep_row = True
        if not keep_row:
            async with bot.pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "DELETE FROM birthday_roles WHERE guild_id = %s AND user_id = %s",
                        (row["guild_id"], row["user_id"]),
                    )


@tasks.loop(minutes=1)
async def birthday_loop():
    for step in (_expire_roles, _announce_due):
        try:
            await step()
        except Exception:
            print(f"[BIRTHDAY] {step.__name__} failed:")
            traceback.print_exc()


@birthday_loop.before_loop
async def _before_birthday_loop():
    await bot.wait_until_ready()


# =========================
# COMMANDS
# =========================
class BirthdayGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="birthday", description="Birthday announcements")


birthday_group = BirthdayGroup()


async def timezone_autocomplete(interaction: discord.Interaction, current: str):
    needle = current.strip().replace(" ", "_").lower()
    if not needle:
        return [app_commands.Choice(name=n, value=n) for n in COMMON_TIMEZONES]
    starts = [n for n in _TZ_NAMES if n.lower().startswith(needle)]
    contains = [n for n in _TZ_NAMES if needle in n.lower() and n not in starts]
    return [app_commands.Choice(name=n, value=n) for n in (starts + contains)[:25]]


async def _get_birthday(guild_id: int, user_id: int):
    async with bot.pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM birthdays WHERE guild_id = %s AND user_id = %s", (guild_id, user_id)
            )
            return await cur.fetchone()


@birthday_group.command(name="set", description="Set your birthday, announcement time and timezone")
@app_commands.describe(
    month="Month of your birthday",
    day="Day of your birthday",
    tz_name="Your timezone, e.g. America/New_York",
    time="When the announcement goes out, e.g. 9:00 AM or 14:30 (default: midnight)",
    year="Optional birth year, needed if you want your age shown",
    show_age="Show your age in the announcement (needs a birth year)",
)
@app_commands.rename(tz_name="timezone")
@app_commands.choices(month=[app_commands.Choice(name=calendar.month_name[i], value=i) for i in range(1, 13)])
@app_commands.autocomplete(tz_name=timezone_autocomplete)
async def set_birthday(
    interaction: discord.Interaction,
    month: app_commands.Choice[int],
    day: app_commands.Range[int, 1, 31],
    tz_name: str,
    time: str = DEFAULT_TIME,
    year: Optional[int] = None,
    show_age: Optional[bool] = None,
):
    tz = resolve_timezone(tz_name)
    if tz is None:
        await interaction.response.send_message(
            "I don't recognise that timezone. Start typing a city or region (like `America/New_York`) and pick one from the list.",
            ephemeral=True,
        )
        return

    parsed = parse_time(time)
    if parsed is None:
        await interaction.response.send_message(
            "I couldn't read that time. Try something like `9:00 AM`, `9pm` or `14:30`.", ephemeral=True
        )
        return
    hour, minute = parsed

    existing = await _get_birthday(interaction.guild.id, interaction.user.id)
    final_year = year if year is not None else (existing["birth_year"] if existing else None)
    final_show_age = show_age if show_age is not None else (bool(existing["show_age"]) if existing else False)

    error = validate_date(month.value, day, final_year)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    if final_show_age and final_year is None:
        await interaction.response.send_message(
            "To show your age I need your birth year. Add the `year` option.", ephemeral=True
        )
        return

    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO birthdays (guild_id, user_id, month, day, hour, minute, timezone, birth_year, show_age)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    month = VALUES(month), day = VALUES(day), hour = VALUES(hour), minute = VALUES(minute),
                    timezone = VALUES(timezone), birth_year = VALUES(birth_year), show_age = VALUES(show_age)
                """,
                (
                    interaction.guild.id, interaction.user.id, month.value, day, hour, minute,
                    tz, final_year, final_show_age,
                ),
            )

    row = await _get_birthday(interaction.guild.id, interaction.user.id)
    now = _utcnow()

    announce_now = False
    window = _current_window(row, now)
    if window:
        start, window_year = window
        previous = existing["last_announced_at"] if existing else None
        recently_announced = previous is not None and previous > _to_db(now - ROLE_DURATION)
        async with bot.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE birthdays SET last_announced_at = %s
                    WHERE guild_id = %s AND user_id = %s
                      AND (last_announced_at IS NULL OR last_announced_at < %s)
                    """,
                    (_to_db(start), interaction.guild.id, interaction.user.id, _to_db(start)),
                )
                announce_now = cur.rowcount == 1 and not recently_announced

    next_start = _next_occurrence(row, now)
    age_text = "shown" if final_show_age else "hidden"
    extra = "\nYour birthday is happening right now, so you're getting the birthday role and an announcement! 🎂" if announce_now else ""
    await interaction.response.send_message(
        f"Saved! Your birthday is **{calendar.month_name[month.value]} {day}** at "
        f"**{_format_time(hour, minute)}** ({tz}). Your age is **{age_text}**.\n"
        f"Next announcement: <t:{int(next_start.timestamp())}:F>{extra}",
        ephemeral=True,
    )

    if announce_now:
        try:
            await _grant_role(interaction.guild, interaction.user, now + ROLE_DURATION)
            await _send_announcement(interaction.guild, interaction.user, _age_for(row, window_year), is_test=False)
        except Exception:
            print(f"[BIRTHDAY] Failed to announce immediately for user {interaction.user.id}:")
            traceback.print_exc()


@birthday_group.command(name="age", description="Choose whether your age appears in your birthday announcement")
@app_commands.describe(show="True to show your age, false to hide it")
async def set_age_visibility(interaction: discord.Interaction, show: bool):
    row = await _get_birthday(interaction.guild.id, interaction.user.id)
    if not row:
        await interaction.response.send_message("You haven't set a birthday yet. Use `/birthday set` first.", ephemeral=True)
        return
    if show and not row["birth_year"]:
        await interaction.response.send_message(
            "I don't have your birth year yet. Run `/birthday set` again with the `year` option.", ephemeral=True
        )
        return

    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE birthdays SET show_age = %s WHERE guild_id = %s AND user_id = %s",
                (show, interaction.guild.id, interaction.user.id),
            )
    await interaction.response.send_message(
        "Your age will be shown in your announcement." if show else "Your age will be hidden in your announcement.",
        ephemeral=True,
    )


@birthday_group.command(name="view", description="View your saved birthday, or someone else's date")
@app_commands.describe(member="Whose birthday to look up (defaults to you)")
async def view_birthday(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    target = member or interaction.user
    row = await _get_birthday(interaction.guild.id, target.id)
    if not row:
        who = "You haven't" if target.id == interaction.user.id else f"{target.display_name} hasn't"
        await interaction.response.send_message(f"{who} set a birthday.", ephemeral=True)
        return

    date_text = f"{calendar.month_name[row['month']]} {row['day']}"
    if target.id != interaction.user.id:
        await interaction.response.send_message(f"{target.display_name}'s birthday is **{date_text}**.", ephemeral=True)
        return

    next_start = _next_occurrence(row, _utcnow())
    year_text = row["birth_year"] if row["birth_year"] else "not set"
    await interaction.response.send_message(
        f"**Date:** {date_text}\n"
        f"**Announcement time:** {_format_time(row['hour'], row['minute'])} ({row['timezone']})\n"
        f"**Birth year:** {year_text}\n"
        f"**Show age:** {'yes' if row['show_age'] else 'no'}\n"
        f"**Next announcement:** <t:{int(next_start.timestamp())}:F>",
        ephemeral=True,
    )


@birthday_group.command(name="remove", description="Delete your saved birthday")
async def remove_birthday(interaction: discord.Interaction):
    async with bot.pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM birthdays WHERE guild_id = %s AND user_id = %s",
                (interaction.guild.id, interaction.user.id),
            )
            removed = cur.rowcount
    await interaction.response.send_message(
        "Your birthday was removed." if removed else "You didn't have a birthday saved.", ephemeral=True
    )


@birthday_group.command(name="test", description="Post a test birthday announcement (mods only)")
@app_commands.describe(member="Who the test announcement is for")
async def test_announcement(interaction: discord.Interaction, member: discord.Member):
    if not _can_manage(interaction.user):
        await interaction.response.send_message("You don't have permission to send test announcements.", ephemeral=True)
        return
    if not BIRTHDAY_CHANNEL_ID or interaction.guild.get_channel(BIRTHDAY_CHANNEL_ID) is None:
        await interaction.response.send_message(
            "The birthday channel isn't configured. Set `BIRTHDAY_CHANNEL_ID` in birthday.py.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    age = None
    row = await _get_birthday(interaction.guild.id, member.id)
    if row:
        local_year = _utcnow().astimezone(ZoneInfo(row["timezone"])).year
        age = _age_for(row, local_year)

    try:
        message = await _send_announcement(interaction.guild, member, age, is_test=True)
    except discord.Forbidden:
        await interaction.followup.send("I'm missing permissions to post in the birthday channel.", ephemeral=True)
        return
    await interaction.followup.send(
        f"Test announcement sent: {message.jump_url}\n"
        "It pinged the birthday role and the member, but no birthday role was assigned.",
        ephemeral=True,
    )
