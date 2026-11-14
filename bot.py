import asyncio
import json
import os
from datetime import time
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import discord
import requests
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

SERVER_NAME = "New York City Roleplay"
GAME_CODE = "NYCRPIsFun"
API_URL = "https://api.erlc.gg/v2/server"
BANNER_PATH = Path(__file__).with_name("download.png")
PANEL_MARKER = "NYCRP • Daily Session Info"

GUILD_ID = os.getenv("DISCORD_SERVER_ID", "").strip()
SERVER_KEY = os.getenv("ERLC_SERVER_KEY", "").strip()
PANEL_CHANNEL_ID = os.getenv("SESSIONINFO_CHANNEL_ID", "").strip()
PANEL_MESSAGE_ID = os.getenv("SESSIONINFO_MESSAGE_ID", "").strip()
PING_ROLE_ID = os.getenv("SSU_PING_ROLE_ID", "").strip()

join_query = urlencode({
    "placeId": "2534724415",
    "launchData": json.dumps({"psCode": GAME_CODE}, separators=(",", ":")),
})
JOIN_URL = (
    os.getenv("ERLC_JOIN_URL", "").strip()
    or f"https://www.roblox.com/games/start?{join_query}"
)

panel_lock = asyncio.Lock()
cached_panel_id = None


class ERLCBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        commands = await self.tree.sync()
        print("Synced:", ", ".join(f"/{cmd.name}" for cmd in commands), flush=True)
        daily_sessioninfo.start()


bot = ERLCBot()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}", flush=True)


async def require_admin(interaction):
    if (
        interaction.guild_id is not None
        and str(interaction.guild_id) == GUILD_ID
        and interaction.permissions.administrator
    ):
        return True

    await interaction.response.send_message(
        "Only administrators in the configured Discord server can use this.",
        ephemeral=True,
    )
    return False


def join_button():
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="▶ Join Server", url=JOIN_URL))
    return view


async def request_erlc(method, path="", **kwargs):
    return await asyncio.to_thread(
        requests.request,
        method,
        f"{API_URL}{path}",
        headers={"server-key": SERVER_KEY},
        timeout=12,
        allow_redirects=False,
        **kwargs,
    )


async def erlc_reply(interaction, command=None):
    if not await require_admin(interaction):
        return
    if not SERVER_KEY:
        await interaction.response.send_message(
            "Set ERLC_SERVER_KEY in Railway Variables.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        if command is None:
            response = await request_erlc("GET")
        else:
            response = await request_erlc("POST", "/command", json={"command": command})

        if response.status_code == 200 and command is not None:
            message = "ER:LC confirmed the command ran."
        elif response.status_code == 200:
            data = response.json()
            message = (
                f"{data.get('Name', SERVER_NAME)} | "
                f"Players: {data.get('CurrentPlayers', '?')}/{data.get('MaxPlayers', '?')}"
            )
        elif response.status_code == 403:
            message = "ER:LC denied access. If /status works, check Trusted IP Addresses."
        elif response.status_code == 429:
            message = "ER:LC rate limit reached. Wait before trying again."
        else:
            message = f"ER:LC returned HTTP {response.status_code}."
    except requests.exceptions.JSONDecodeError:
        message = "ER:LC returned unreadable server data."
    except requests.RequestException:
        message = "Could not reach ER:LC. Check the game before retrying."
    except AttributeError:
        message = "ER:LC returned unreadable server data."

    await interaction.followup.send(
        message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
    )


def online_staff_count(data):
    players = data.get("Players")
    staff = data.get("Staff")
    if not isinstance(players, list) or not isinstance(staff, dict):
        return "Unavailable"

    staff_ids = set()
    owner_id = data.get("OwnerId")
    if owner_id is not None:
        staff_ids.add(str(owner_id))

    co_owners = data.get("CoOwnerIds")
    if isinstance(co_owners, list):
        staff_ids.update(str(user_id) for user_id in co_owners)

    for rank in ("Admins", "Mods", "Helpers"):
        roster = staff.get(rank)
        if isinstance(roster, dict):
            staff_ids.update(str(user_id) for user_id in roster)

    online_ids = set()
    for player in players:
        if isinstance(player, dict):
            _, _, user_id = str(player.get("Player", "")).rpartition(":")
            if user_id.isdecimal():
                online_ids.add(user_id)

    return str(len(staff_ids & online_ids))


async def build_session_panel():
    players = staff = queue = "Unavailable"
    updated = "Stats unavailable"
    data = None

    if SERVER_KEY:
        try:
            response = await request_erlc(
                "GET", params={"Players": "true", "Staff": "true", "Queue": "true"}
            )
            if response.status_code == 200:
                data = response.json()
            else:
                print(f"Session stats returned HTTP {response.status_code}", flush=True)
        except (requests.RequestException, ValueError):
            print("Could not load ER:LC session stats.", flush=True)

    if isinstance(data, dict):
        current = data.get("CurrentPlayers")
        maximum = data.get("MaxPlayers")
        if isinstance(current, int) and isinstance(maximum, int):
            players = f"{current} / {maximum}"

        staff = online_staff_count(data)
        queued = data.get("Queue")
        if isinstance(queued, list):
            queue = str(len(queued))

        updated = f"<t:{int(discord.utils.utcnow().timestamp())}:f>"

    embed = discord.Embed(
        title=SERVER_NAME,
        description=(
            "**Session Information**\n"
            "Check who's in-game, see how many players are waiting, "
            "or click **Join Server** to hop into the session.\n\n"
            f"**Last updated:** {updated}"
        ),
        color=discord.Color.blue(),
    )
    embed.add_field(name="In-game Players", value=players, inline=False)
    embed.add_field(name="Online Staff", value=staff, inline=False)
    embed.add_field(name="Queue", value=queue, inline=False)
    embed.add_field(name="Game Code", value=f"`{GAME_CODE}`", inline=False)
    embed.set_footer(text=PANEL_MARKER)

    if BANNER_PATH.is_file():
        embed.set_image(url=f"attachment://{BANNER_PATH.name}")

    return embed


def is_session_panel(message):
    return (
        message.author.id == bot.user.id
        and bool(message.embeds)
        and message.embeds[0].footer.text == PANEL_MARKER
    )


async def publish_session_panel():
    global cached_panel_id

    if not PANEL_CHANNEL_ID.isdecimal():
        return "Set SESSIONINFO_CHANNEL_ID to your Discord text channel ID."

    if not JOIN_URL.startswith("https://"):
        return "Set ERLC_JOIN_URL to an HTTPS link or remove it to use the built-in NYC link."

    async with panel_lock:
        try:
            channel = await bot.fetch_channel(int(PANEL_CHANNEL_ID))
            if not isinstance(channel, discord.TextChannel) or str(channel.guild.id) != GUILD_ID:
                return "SESSIONINFO_CHANNEL_ID must be a text channel in your configured Discord server."

            panel = None
            message_id = cached_panel_id
            if message_id is None and PANEL_MESSAGE_ID.isdecimal():
                message_id = int(PANEL_MESSAGE_ID)

            if message_id is not None:
                try:
                    candidate = await channel.fetch_message(message_id)
                    if is_session_panel(candidate):
                        panel = candidate
                except discord.NotFound:
                    pass

            if panel is None:
                async for candidate in channel.history(limit=100):
                    if is_session_panel(candidate):
                        panel = candidate
                        break

            embed = await build_session_panel()
            files = []
            if BANNER_PATH.is_file():
                files.append(discord.File(BANNER_PATH, filename=BANNER_PATH.name))
            else:
                print(f"Session banner missing: {BANNER_PATH.name}", flush=True)

            if panel is None:
                panel = await channel.send(embed=embed, files=files, view=join_button())
            else:
                panel = await panel.edit(embed=embed, attachments=files, view=join_button())

            cached_panel_id = panel.id
            print(f"Session panel message ID: {panel.id}", flush=True)
            return None
        except discord.HTTPException as error:
            print(f"Could not publish the session panel: {error}", flush=True)
            return "Couldn't update the panel. Check the bot's permissions in the panel channel."


@tasks.loop(time=time(hour=9, tzinfo=ZoneInfo("America/New_York")))
async def daily_sessioninfo():
    error = await publish_session_panel()
    if error:
        print(error, flush=True)


@daily_sessioninfo.before_loop
async def post_panel_on_startup():
    await bot.wait_until_ready()
    await daily_sessioninfo.coro()


@bot.tree.command(description="Check whether the bot is online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! Bot is online.")


@bot.tree.command(description="Show ER:LC server status")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def status(interaction: discord.Interaction):
    await erlc_reply(interaction)


@bot.tree.command(description="Run an in-game ER:LC command")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def erlc(interaction: discord.Interaction, command: str):
    command = command.strip()
    if not command.startswith(":") or not command[1:].strip():
        await interaction.response.send_message(
            "Enter a full command, such as :h Hello!", ephemeral=True
        )
        return
    await erlc_reply(interaction, command)


@bot.tree.command(description="Send an in-game ER:LC announcement")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def announce(interaction: discord.Interaction, message: str):
    message = message.strip()
    if not message:
        await interaction.response.send_message("Enter a message.", ephemeral=True)
        return
    await erlc_reply(interaction, f":m {message}")


@bot.tree.command(description="Refresh the New York City Roleplay session panel")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def sessioninfo(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    error = await publish_session_panel()
    await interaction.followup.send(error or "Session panel updated.", ephemeral=True)


@bot.tree.command(description="Announce a session start and refresh the panel")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def ssu(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    if not JOIN_URL.startswith("https://"):
        await interaction.response.send_message(
            "Set ERLC_JOIN_URL to an HTTPS link or remove it to use the built-in NYC link.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    started = int(discord.utils.utcnow().timestamp())
    embed = discord.Embed(
        title="Session Started!",
        description=(
            f"Our **{SERVER_NAME}** session is now live! "
            "Click **Join Server** to hop in.\n\n"
            f"Session started: <t:{started}:R>"
        ),
        color=discord.Color.blue(),
    )

    role = None
    if PING_ROLE_ID.isdecimal():
        role = interaction.guild.get_role(int(PING_ROLE_ID))
        if role == interaction.guild.default_role:
            role = None

    try:
        await interaction.channel.send(
            content=role.mention if role else None,
            embed=embed,
            view=join_button(),
            allowed_mentions=discord.AllowedMentions(
                everyone=False, users=False, roles=[role] if role else False
            ),
        )
    except discord.HTTPException:
        await interaction.followup.send(
            "I couldn't post the announcement in this channel. Check my channel permissions.",
            ephemeral=True,
        )
        return

    error = await publish_session_panel()
    await interaction.followup.send(
        f"Announcement posted. {error or 'Session panel updated.'}",
        ephemeral=True,
    )


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Set DISCORD_TOKEN in Railway Variables or your local .env.")
    bot.run(token)
