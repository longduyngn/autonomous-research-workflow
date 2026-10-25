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
    for guild in client.guilds:
        print(f"Guild: {guild.name} ({guild.id})")
        print(f"Me: {guild.me} (roles: {[r.name for r in guild.me.roles]})")
        for ch in guild.channels:
            perms = ch.permissions_for(guild.me)
            print(f"  Channel: #{ch.name} (type: {type(ch).__name__}, id: {ch.id}, can_view: {perms.view_channel}, can_send: {perms.send_messages})")
            if isinstance(ch, discord.TextChannel) and perms.send_messages:
                await ch.send("🚀 **Antigravity Autonomous Workspace is Online!**\nConnected via Python 3.11 `.conda` environment.")
                print(f"  --> Sent message to #{ch.name}!")
                break
            elif isinstance(ch, discord.TextChannel):
                # Try sending anyway in case permissions_for cached calculation was strict
                try:
                    await ch.send("🚀 **Antigravity Autonomous Workspace is Online!**\nConnected via Python 3.11 `.conda` environment.")
                    print(f"  --> Successfully forced message send to #{ch.name}!")
                    break
                except Exception as e:
                    print(f"  --> Failed sending to #{ch.name}: {e}")
    await client.close()

client.run(token)
