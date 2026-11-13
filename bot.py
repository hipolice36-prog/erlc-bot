import asyncio
import os

import discord
import requests
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://api.erlc.gg/v2/server"
SERVER_KEY = os.getenv("ERLC_SERVER_KEY", "").strip()
DISCORD_SERVER_ID = os.getenv("DISCORD_SERVER_ID", "").strip()


class ERLCBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()


bot = ERLCBot()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}", flush=True)


@bot.tree.command(description="Check whether the bot is online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! Bot is online.")


async def erlc_request(interaction: discord.Interaction, command=None):
    if (
        interaction.guild_id is None
        or str(interaction.guild_id) != DISCORD_SERVER_ID
        or not interaction.permissions.administrator
    ):
        await interaction.response.send_message(
            "Only administrators in the configured Discord server can use this.",
            ephemeral=True,
        )
        return

    if not SERVER_KEY:
        await interaction.response.send_message(
            "Set ERLC_SERVER_KEY in Railway Variables and deploy.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    headers = {"server-key": SERVER_KEY}

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
            players = data.get("CurrentPlayers", "?")
            maximum = data.get("MaxPlayers", "?")
            message = f"{name} | Players: {players}/{maximum}"
        except (ValueError, AttributeError):
            message = "ER:LC returned an unreadable status response."
    elif code == 200:
        message = "ER:LC confirmed the command ran."
    elif code in (401, 403):
        message = f"ER:LC denied access ({code}). Check the server key and API permissions."
    elif code == 429:
        message = "ER:LC rate limit reached. Wait before trying again."
    elif code == 422:
        message = "ER:LC could not complete the request. Check whether the private server is online."
    else:
        message = f"ER:LC returned HTTP {code}."

    await interaction.followup.send(
        message,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.tree.command(description="Show ER:LC server status")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def status(interaction: discord.Interaction):
    await erlc_request(interaction)


@bot.tree.command(description="Run an in-game command on your ER:LC server")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(command="Full ER:LC command, including the colon")
async def erlc(interaction: discord.Interaction, command: str):
    command = command.strip()
    if not command.startswith(":") or not command[1:].strip():
        await interaction.response.send_message(
            "Enter a full ER:LC command, such as :h Hello!",
            ephemeral=True,
        )
        return

    await erlc_request(interaction, command)


@bot.tree.command(description="Send an in-game ER:LC announcement")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def announce(interaction: discord.Interaction, message: str):
    await erlc_request(interaction, f":m {message.strip()}")


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Set DISCORD_TOKEN in Railway Variables or your local .env.")

    bot.run(token)
