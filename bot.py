import os
import discord
from discord import app_commands
from dotenv import load_dotenv

# Loads DISCORD_TOKEN from the .env file
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "No Discord token found. Create a .env file with DISCORD_TOKEN=your_new_token"
    )

class ERLCBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Registers slash commands with Discord
        await self.tree.sync()

bot = ERLCBot()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print("Bot is online — press Ctrl+C to stop it.")

@bot.tree.command(name="ping", description="Check whether the bot is online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! Bot is online.")

@bot.tree.command(name="status", description="Check ER:LC bot status")
async def status(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Discord bot is running. ER:LC API setup comes next."
    )

@bot.tree.command(name="announce", description="Prepare an ER:LC announcement")
@app_commands.describe(message="The message you want announced in ER:LC")
async def announce(interaction: discord.Interaction, message: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Only server administrators can use this command.",
            ephemeral=True,
        )
        return

    # This prints the request in the terminal for now.
    # We will replace this with the ER:LC API request later.
    print(f"{interaction.user} requested announcement: {message}")

    await interaction.response.send_message(
        f"Announcement received: `{message}`",
        ephemeral=True,
    )

bot.run(TOKEN)