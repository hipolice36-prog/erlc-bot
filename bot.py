import asyncio
import os

import discord
import requests
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://api.erlc.gg/v2/server"
DISCORD_SERVER_ID = os.getenv("DISCORD_SERVER_ID", "").strip()
ERLC_SERVER_KEY = os.getenv("ERLC_SERVER_KEY", "").strip()
SSU_PING_ROLE_ID = os.getenv("SSU_PING_ROLE_ID", "").strip()

DEFAULT_JOIN_URL = (
    "https://www.roblox.com/games/start?placeId=2534724415"
    "&launchData=%7B%22psCode%22%3A%22joeyisfun%22%7D"
)
ERLC_JOIN_URL = os.getenv("ERLC_JOIN_URL", "").strip() or DEFAULT_JOIN_URL


class ERLCBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        commands = await self.tree.sync()
        print("Synced:", ", ".join(f"/{cmd.name}" for cmd in commands), flush=True)


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
    headers = {"server-key": ERLC_SERVER_KEY}

    try:
        if command is None:
            response = await asyncio.to_thread(
                requests.get,
                API_URL,
                headers=headers,
                timeout=12,
                allow_redirects=False,
            )
        else:
            response = await asyncio.to_thread(
                requests.post,
                f"{API_URL}/command",
                headers=headers,
                json={"command": command},
                timeout=12,
                allow_redirects=False,
            )
    except requests.RequestException:
        await interaction.followup.send(
            "Could not reach ER:LC. Check the game before retrying.",
            ephemeral=True,
        )
        return

    code = response.status_code
    if code == 200 and command is None:
        try:
            data = response.json()
            name = data.get("Name", "ER:LC")
            current = data.get("CurrentPlayers", "?")
            maximum = data.get("MaxPlayers", "?")
            message = f"{name} | Players: {current}/{maximum}"
        except (ValueError, AttributeError):
            message = "ER:LC returned an unreadable status response."
    elif code == 200:
        message = "ER:LC confirmed the command ran."
    elif code == 401:
        message = "ER:LC rejected the server key."
    elif code == 403:
        message = "ER:LC denied access. If /status works, check Trusted IP Addresses."
    elif code == 429:
        message = "ER:LC rate limit reached. Wait before trying again."
    elif code == 422:
        message = "ER:LC could not complete the request. Check if the game server is online."
    else:
        message = f"ER:LC returned HTTP {code}."

    await interaction.followup.send(
        message,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


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
            "Enter a full command, such as :h Hello!",
            ephemeral=True,
        )
        return

    await erlc_request(interaction, command)


@bot.tree.command(description="Send an in-game ER:LC announcement")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def announce(interaction: discord.Interaction, message: str):
    await erlc_request(interaction, f":m {message.strip()}")


@bot.tree.command(description="Post an ER:LC session-start announcement")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def ssu(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    if not ERLC_JOIN_URL.startswith("https://"):
        await interaction.response.send_message(
            "Set ERLC_JOIN_URL to an HTTPS link or clear it to use the built-in link.",
            ephemeral=True,
        )
        return

    started = int(discord.utils.utcnow().timestamp())
    embed = discord.Embed(
        title="Session Started!",
        description=f"Join up for top tier roleplay.\n\nSession started: <t:{started}:R>",
        color=discord.Color.green(),
    )

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="▶ Join Server", url=ERLC_JOIN_URL))

    role = None
    if SSU_PING_ROLE_ID.isdecimal():
        role = interaction.guild.get_role(int(SSU_PING_ROLE_ID))
        if role == interaction.guild.default_role:
            role = None

    await interaction.response.send_message(
        content=role.mention if role else None,
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=[role] if role else False,
        ),
    )


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Set DISCORD_TOKEN in Railway Variables or your local .env.")
    bot.run(token)
