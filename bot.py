import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import discord
import requests
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

SERVER_NAME = "New York City Roleplay"
GAME_CODE = "NYCRPIsFun"
API_URL = "https://api.erlc.gg/v2/server"
BANNER = Path(__file__).with_name("download.png")
PANEL_MARKER = "NYCRP • Daily Session Info"

GUILD_ID = os.getenv("DISCORD_SERVER_ID", "").strip()
SERVER_KEY = os.getenv("ERLC_SERVER_KEY", "").strip()
CHANNEL_ID = (
    os.getenv("SESSIONINFO_CHANNEL_ID", "").strip() or "1570994447280378048"
)
MESSAGE_ID = os.getenv("SESSIONINFO_MESSAGE_ID", "").strip()
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
panel_id = None


class ERLCBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        commands = await self.tree.sync()
        print("Synced:", ", ".join(cmd.name for cmd in commands), flush=True)
        refresh_panel.start()


bot = ERLCBot()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}", flush=True)


async def require_admin(interaction):
    if str(interaction.guild_id) == GUILD_ID and interaction.permissions.administrator:
        return True

    await interaction.response.send_message(
        "Only administrators in the configured Discord server can use this.",
        ephemeral=True,
    )
    return False


def valid_join_url():
    try:
        url = urlsplit(JOIN_URL)
        return url.scheme == "https" and bool(url.hostname)
    except ValueError:
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
            "Set ERLC_SERVER_KEY in Railway Variables or your local .env.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        if command is None:
            response = await request_erlc("GET")
        else:
            response = await request_erlc(
                "POST", "/command", json={"command": command}
            )

        if response.status_code == 200 and command is not None:
            reply = "ER:LC confirmed the command ran."
        elif response.status_code == 200:
            data = response.json()
            reply = (
                f"{data.get('Name', SERVER_NAME)} | "
                f"Players: {data.get('CurrentPlayers', '?')}/{data.get('MaxPlayers', '?')}"
            )
        elif response.status_code == 403:
            reply = "ER:LC denied access. If /status works, check Trusted IP Addresses."
        elif response.status_code == 429:
            reply = "ER:LC rate limit reached. Wait before trying again."
        else:
            reply = f"ER:LC returned HTTP {response.status_code}."
    except (ValueError, AttributeError):
        reply = "ER:LC returned unreadable server data."
    except requests.RequestException:
        reply = "Could not reach ER:LC. Check the game before retrying."

    await interaction.followup.send(
        reply, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
    )


def count_online_staff(data):
    players = data.get("Players")
    staff = data.get("Staff")
    if not isinstance(players, list) or not isinstance(staff, dict):
        return "Unavailable"

    staff_ids = set()
    if data.get("OwnerId") is not None:
        staff_ids.add(str(data["OwnerId"]))

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
            _, separator, user_id = str(player.get("Player", "")).rpartition(":")
            if separator and user_id.isdecimal():
                online_ids.add(user_id)

    return str(len(staff_ids & online_ids))


async def build_panel():
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

        staff = count_online_staff(data)
        queued = data.get("Queue")
        if isinstance(queued, list):
            queue = str(len(queued))

        updated = f"<t:{int(discord.utils.utcnow().timestamp())}:T>"

    embed = discord.Embed(
        title=SERVER_NAME,
        description=(
            "**Session Information**\n"
            "Check who's in-game or click **Join Server** to hop into the session.\n\n"
            f"**Last updated:** {updated}"
        ),
        color=discord.Color.blue(),
    )
    embed.add_field(name="In-game Players", value=players, inline=False)
    embed.add_field(name="Online Staff", value=staff, inline=False)
    embed.add_field(name="Queue", value=queue, inline=False)
    embed.add_field(name="Game Code", value=f"`{GAME_CODE}`", inline=False)
    embed.set_footer(text=PANEL_MARKER)

    if BANNER.is_file():
        embed.set_image(url=f"attachment://{BANNER.name}")

    return embed


def is_panel(message):
    return (
        message.author.id == bot.user.id
        and bool(message.embeds)
        and message.embeds[0].footer.text == PANEL_MARKER
    )


async def publish_panel():
    global panel_id

    if not CHANNEL_ID.isdecimal():
        return "Set SESSIONINFO_CHANNEL_ID to a Discord text channel ID."
    if not valid_join_url():
        return "Set ERLC_JOIN_URL to an HTTPS link or remove it to use the NYC link."

    async with panel_lock:
        try:
            channel = await bot.fetch_channel(int(CHANNEL_ID))
            if not isinstance(channel, discord.TextChannel) or str(channel.guild.id) != GUILD_ID:
                return "The panel channel must be in the server set by DISCORD_SERVER_ID."

            message = None
            target_id = panel_id
            if target_id is None and MESSAGE_ID.isdecimal():
                target_id = int(MESSAGE_ID)

            if target_id is not None:
                try:
                    candidate = await channel.fetch_message(target_id)
                    if is_panel(candidate):
                        message = candidate
                except discord.NotFound:
                    pass

            if message is None:
                async for candidate in channel.history(limit=100):
                    if is_panel(candidate):
                        message = candidate
                        break

            embed = await build_panel()
            if message is None:
                files = [discord.File(BANNER)] if BANNER.is_file() else []
                message = await channel.send(embed=embed, files=files, view=join_button())
                print(f"Session panel message ID: {message.id}", flush=True)
            else:
                attachments = []
                if BANNER.is_file():
                    attachments = [
                        item for item in message.attachments if item.filename == BANNER.name
                    ]
                    if not attachments:
                        attachments = [discord.File(BANNER)]

                message = await message.edit(
                    embed=embed, attachments=attachments, view=join_button()
                )

            panel_id = message.id
            return None

        except discord.HTTPException as error:
            print(f"Session panel update failed: {error}", flush=True)
            return "Couldn't update the panel. Check Railway logs and channel permissions."


@tasks.loop(minutes=1)
async def refresh_panel():
    error = await publish_panel()
    if error:
        print(error, flush=True)


@refresh_panel.before_loop
async def wait_for_discord():
    await bot.wait_until_ready()


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
    error = await publish_panel()
    await interaction.followup.send(error or "Session panel updated.", ephemeral=True)


@bot.tree.command(description="Announce a session start and refresh the panel")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def ssu(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return
    if not valid_join_url():
        await interaction.response.send_message(
            "Set ERLC_JOIN_URL to an HTTPS link or remove it to use the NYC link.",
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
            "I couldn't post the announcement. Check my permissions in this channel.",
            ephemeral=True,
        )
        return

    error = await publish_panel()
    await interaction.followup.send(
        f"Announcement posted. {error or 'Session panel updated.'}",
        ephemeral=True,
    )


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Set DISCORD_TOKEN in Railway Variables or your local .env.")
    bot.run(token)
