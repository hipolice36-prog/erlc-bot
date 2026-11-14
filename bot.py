import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import discord
import requests
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

SERVER_NAME = "New York City Roleplay"
GAME_CODE = "NYCRPIsFun"
API_URL = "https://api.erlc.gg/v2/server"
BANNER_PATH = Path(__file__).with_name("download.png")

DISCORD_SERVER_ID = os.getenv("DISCORD_SERVER_ID", "").strip()
ERLC_SERVER_KEY = os.getenv("ERLC_SERVER_KEY", "").strip()
SSU_PING_ROLE_ID = os.getenv("SSU_PING_ROLE_ID", "").strip()

join_query = urlencode({
    "placeId": "2534724415",
    "launchData": json.dumps({"psCode": GAME_CODE}, separators=(",", ":")),
})
DEFAULT_JOIN_URL = f"https://www.roblox.com/games/start?{join_query}"
ERLC_JOIN_URL = os.getenv("ERLC_JOIN_URL", "").strip() or DEFAULT_JOIN_URL


class ERLCBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        commands = await self.tree.sync()
        names = ", ".join(f"/{command.name}" for command in commands)
        print(f"Synced: {names}", flush=True)


bot = ERLCBot()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}", flush=True)


async def require_admin(interaction: discord.Interaction) -> bool:
    if (
        interaction.guild_id is not None
        and str(interaction.guild_id) == DISCORD_SERVER_ID
        and interaction.permissions.administrator
    ):
        return True

    await interaction.response.send_message(
        "Only administrators in the configured Discord server can use this.",
        ephemeral=True,
    )
    return False


async def require_join_link(interaction: discord.Interaction) -> bool:
    try:
        url = urlsplit(ERLC_JOIN_URL)
        if url.scheme == "https" and url.hostname:
            return True
    except ValueError:
        pass

    await interaction.response.send_message(
        "Set ERLC_JOIN_URL to an HTTPS link or remove it to use the built-in NYC link.",
        ephemeral=True,
    )
    return False


def join_button():
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="▶ Join Server", url=ERLC_JOIN_URL))
    return view


async def request_erlc(method, path="", **kwargs):
    return await asyncio.to_thread(
        requests.request,
        method,
        f"{API_URL}{path}",
        headers={"server-key": ERLC_SERVER_KEY},
        timeout=12,
        allow_redirects=False,
        **kwargs,
    )


def api_error(status_code):
    messages = {
        401: "ER:LC rejected the server key.",
        403: "ER:LC denied access. If /status works, check Trusted IP Addresses.",
        422: "ER:LC could not complete the request. Check whether the game server is online.",
        429: "ER:LC rate limit reached. Wait before trying again.",
    }
    return messages.get(status_code, f"ER:LC returned HTTP {status_code}.")


async def erlc_request(interaction: discord.Interaction, command=None):
    if not await require_admin(interaction):
        return

    if not ERLC_SERVER_KEY:
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
            response = await request_erlc("POST", "/command", json={"command": command})

        if response.status_code != 200:
            message = api_error(response.status_code)
        elif command is not None:
            message = "ER:LC confirmed the command ran."
        else:
            data = response.json()
            if isinstance(data, dict):
                name = data.get("Name", SERVER_NAME)
                current = data.get("CurrentPlayers", "?")
                maximum = data.get("MaxPlayers", "?")
                message = f"{name} | Players: {current}/{maximum}"
            else:
                message = "ER:LC returned an unreadable status response."
    except requests.exceptions.JSONDecodeError:
        message = "ER:LC returned an unreadable status response."
    except requests.RequestException:
        message = "Could not reach ER:LC. Check the game before retrying."

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


@bot.tree.command(description="Check whether the bot is online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! Bot is online.")


@bot.tree.command(description="Show ER:LC server status")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def status(interaction: discord.Interaction):
    await erlc_request(interaction)


@bot.tree.command(description="Run an in-game ER:LC command")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(command="Full in-game command, including the colon")
async def erlc(interaction: discord.Interaction, command: str):
    command = command.strip()
    if not command.startswith(":") or not command[1:].strip():
        await interaction.response.send_message(
            "Enter a full command, such as :h Hello!", ephemeral=True
        )
        return

    await erlc_request(interaction, command)


@bot.tree.command(description="Send an in-game ER:LC announcement")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def announce(interaction: discord.Interaction, message: str):
    message = message.strip()
    if not message:
        await interaction.response.send_message("Enter a message.", ephemeral=True)
        return

    await erlc_request(interaction, f":m {message}")


@bot.tree.command(description="Post a New York City Roleplay session announcement")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def ssu(interaction: discord.Interaction):
    if not await require_admin(interaction) or not await require_join_link(interaction):
        return

    started = int(discord.utils.utcnow().timestamp())
    embed = discord.Embed(
        title="Session Started!",
        description=(
            f"Our **{SERVER_NAME}** session is now live! "
            "Click **Join Server** below to hop in and start roleplaying.\n\n"
            f"Session started: <t:{started}:R>"
        ),
        color=discord.Color.blue(),
    )

    role = None
    if SSU_PING_ROLE_ID.isdecimal():
        role = interaction.guild.get_role(int(SSU_PING_ROLE_ID))
        if role == interaction.guild.default_role:
            role = None

    await interaction.response.send_message(
        content=role.mention if role else None,
        embed=embed,
        view=join_button(),
        allowed_mentions=discord.AllowedMentions(
            everyone=False, users=False, roles=[role] if role else False
        ),
    )


@bot.tree.command(description="Post the New York City Roleplay session info panel")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def sessioninfo(interaction: discord.Interaction):
    if not await require_admin(interaction) or not await require_join_link(interaction):
        return

    await interaction.response.defer(thinking=True)

    data = None
    if ERLC_SERVER_KEY:
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

    players = staff = queue = "Unavailable"
    updated = "Stats unavailable"

    if isinstance(data, dict):
        current = data.get("CurrentPlayers")
        maximum = data.get("MaxPlayers")
        if isinstance(current, int) and isinstance(maximum, int):
            players = f"{current} / {maximum}"

        staff = online_staff_count(data)
        queued_players = data.get("Queue")
        if isinstance(queued_players, list):
            queue = str(len(queued_players))

        updated = f"<t:{int(discord.utils.utcnow().timestamp())}:t>"

    info = discord.Embed(
        title=SERVER_NAME,
        description=(
            "**Session Information**\n"
            "Check who's in-game, see how many players are waiting, "
            "or use **Join Server** to hop into the session.\n\n"
            f"**Last updated:** {updated}"
        ),
        color=discord.Color.blue(),
    )
    info.add_field(name="In-game Players", value=players, inline=False)
    info.add_field(name="Online Staff", value=staff, inline=False)
    info.add_field(name="Queue", value=queue, inline=False)
    info.add_field(name="Game Code", value=f"`{GAME_CODE}`", inline=False)

    if BANNER_PATH.is_file():
        info.set_image(url=f"attachment://{BANNER_PATH.name}")
        await interaction.followup.send(
            embed=info,
            file=discord.File(BANNER_PATH, filename=BANNER_PATH.name),
            view=join_button(),
        )
    else:
        print(f"Session banner missing beside bot.py: {BANNER_PATH.name}", flush=True)
        await interaction.followup.send(embed=info, view=join_button())


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Set DISCORD_TOKEN in Railway Variables or your local .env.")
    bot.run(token)
