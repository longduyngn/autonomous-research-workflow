import asyncio
import os
import sys
from pathlib import Path

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
env_file = WORKSPACE_ROOT / ".env"
token = ""
for line in env_file.read_text(encoding="utf-8").splitlines():
    if line.startswith("DISCORD_BOT_TOKEN="):
        token = line.split("=", 1)[1].strip()

import discord

intents = discord.Intents.default()
intents.guilds = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    vc = client.get_channel(1563576559414026304)
    if vc:
        try:
            msg = await vc.send(
                "🚀 **Antigravity Autonomous Workspace is Online!**\n\n"
                "• **Environment**: Python 3.11 (`.conda`)\n"
                "• **Bot**: `assistance#6702`\n"
                "• **Multi-Agent Loop**: `Researcher` ➔ `Operator` ➔ `Validator` ➔ `Communicator`\n\n"
                "*Note: To allow the bot to chat in `#chung`, grant the `@assistance` role 'View Channel' and 'Send Messages' permissions.*"
            )
            print(f"[+] Successfully sent message to channel #{vc.name} (id: {vc.id})!")
        except Exception as e:
            print(f"[!] Error sending to #{vc.name}: {e}")
    await client.close()

client.run(token)
