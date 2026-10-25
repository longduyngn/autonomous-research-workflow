import asyncio
import os
import sys
from pathlib import Path

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

# Load .env
env_file = WORKSPACE_ROOT / ".env"
token = ""
target_channel_id = os.environ.get("DISCORD_CHANNEL_ID", "").strip()

if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DISCORD_BOT_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("DISCORD_CHANNEL_ID="):
            target_channel_id = line.split("=", 1)[1].strip().strip('"').strip("'")

if not token:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

if not token:
    print("[!] Error: DISCORD_BOT_TOKEN not found in .env or environment.")
    sys.exit(1)

import discord

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True

client = discord.Client(intents=intents)

custom_message = (
    "🚀 **Antigravity Autonomous Workspace is Online!**\n\n"
    "• **Environment**: Python 3.11 (`.conda` virtual environment)\n"
    "• **Hybrid Multi-Agent Pipeline**: `Researcher` ➔ `Operator` ➔ `Validator` ➔ `Communicator`\n"
    "• **Watchers**: Internal Workspace Monitor + External World Monitor active\n"
    "• **State Store**: SQLite (`storage/events.db`) & Task logs (`storage/logs/`)\n\n"
    "Ready to receive tasks!"
)

if len(sys.argv) > 1:
    custom_message = " ".join(sys.argv[1:])

@client.event
async def on_ready():
    print(f"[+] Successfully logged into Discord as: {client.user} (ID: {client.user.id})")
    print(f"[+] Connected to {len(client.guilds)} guild(s):")
    sent = False

    # 1. If a specific channel ID was configured, attempt that first
    if target_channel_id:
        try:
            cid = int(target_channel_id)
            target_ch = client.get_channel(cid)
            if target_ch:
                guild = target_ch.guild
                perms = target_ch.permissions_for(guild.me)
                print(f"[+] Configured channel #{target_ch.name} (ID: {cid}) in guild '{guild.name}': view_channel={perms.view_channel}, send_messages={perms.send_messages}")
                if perms.view_channel and perms.send_messages:
                    await target_ch.send(custom_message)
                    print(f"[+] Message delivered to configured channel #{target_ch.name} (ID: {cid})!")
                    sent = True
                else:
                    print(f"[!] Configured channel #{target_ch.name} (ID: {cid}) lacks required permissions (view_channel={perms.view_channel}, send_messages={perms.send_messages}).")
            else:
                print(f"[!] Configured channel ID {cid} not found in client cache.")
        except ValueError:
            print(f"[!] Invalid DISCORD_CHANNEL_ID '{target_channel_id}'.")

    # 2. Otherwise discover any messageable channel across all connected guilds
    if not sent:
        for guild in client.guilds:
            print(f"    - Guild: '{guild.name}' (ID: {guild.id})")
            # In Discord, TextChannel, VoiceChannel (with text chat), StageChannel, and Thread are all messageable
            messageable_channels = [
                ch for ch in guild.channels
                if isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread))
            ]
            for channel in messageable_channels:
                perms = channel.permissions_for(guild.me)
                ch_type = type(channel).__name__
                if perms.view_channel and perms.send_messages:
                    print(f"      -> Sending announcement to #{channel.name} (Type: {ch_type}, ID: {channel.id})...")
                    try:
                        await channel.send(custom_message)
                        print(f"[+] Message delivered to #{channel.name} in '{guild.name}'!")
                        sent = True
                        break
                    except Exception as e:
                        print(f"      [!] Failed to send to #{channel.name}: {e}")
                else:
                    print(f"      -> Skipping #{channel.name} (Type: {ch_type}, ID: {channel.id}): view_channel={perms.view_channel}, send_messages={perms.send_messages}")
            if sent:
                break

    if not sent:
        print("[!] Warning: Could not find a messageable channel with send permissions in any server.")
        print(f"    To fix, ensure the bot's role has 'View Channel' and 'Send Messages' permissions in at least one channel.")
        print(f"    Invite URL for this bot: https://discord.com/oauth2/authorize?client_id={client.user.id}&permissions=277077026880&scope=bot")

    await client.close()

try:
    client.run(token)
except Exception as e:
    print(f"[!] Discord connection error: {e}")
    sys.exit(1)
