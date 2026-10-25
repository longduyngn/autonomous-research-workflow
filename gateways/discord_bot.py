"""Bidirectional Discord Bot Gateway.

Allows users to control the autonomous workspace from Discord:
- Mention the bot: `@assistance <your task instructions>`
- Command prefix: `!task <your task instructions>`
- Direct Message (DM): Send any instruction directly to the bot in DM
- `!status`: Check active and recent tasks in SQLite
- `!help`: Show available bot commands

When a task completes, the Communicator sends the verified response directly
back to the Discord channel or DM where the request originated.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    import discord
    from discord.ext import commands
    HAS_DISCORD_PY = True
except ImportError:
    HAS_DISCORD_PY = False


class DiscordBotRunner:
    """Manages the Discord Bot connection, listens for tasks, and dispatches them to the workspace."""

    def __init__(
        self,
        bot_token: str,
        enqueue_task_fn: Callable[[str, str, str], Any],
        get_status_fn: Callable[[], Dict[str, Any]],
    ) -> None:
        self.bot_token = bot_token
        self.enqueue_task_fn = enqueue_task_fn
        self.get_status_fn = get_status_fn

    def run(self) -> None:
        if not HAS_DISCORD_PY:
            print("[!] Error: discord.py is not installed in the active environment.")
            return

        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.dm_messages = True

        bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

        @bot.event
        async def on_ready() -> None:
            print(f"[+] Discord Bot connected as: {bot.user} (ID: {bot.user.id})")
            print(f"[+] Ready to receive tasks via:")
            print(f"    1. `!task <prompt>` in any accessible channel")
            print(f"    2. `@assistance <prompt>` mention in any channel")
            print(f"    3. Direct Message (DM) to {bot.user.name}")
            bot.loop.create_task(heartbeat_loop())

        async def heartbeat_loop() -> None:
            hb_path = Path("storage/bot_heartbeat.json")
            hb_path.parent.mkdir(parents=True, exist_ok=True)
            import json, time
            while not bot.is_closed():
                try:
                    data = {
                        "status": "listening",
                        "bot_user": f"{bot.user.name}#{bot.user.discriminator}",
                        "bot_id": str(bot.user.id),
                        "guilds": [g.name for g in bot.guilds],
                        "pid": os.getpid(),
                        "timestamp": time.time(),
                    }
                    hb_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                except Exception:
                    pass
                await asyncio.sleep(5)

        @bot.event
        async def on_message(message: discord.Message) -> None:
            # Never respond to other bots (or ourselves)
            if message.author.bot:
                return

            channel_id = str(message.channel.id)
            user_tag = str(message.author)
            content = message.content.strip()

            # 1. Direct Message (DM) to the bot: treat entire text as task
            is_dm = isinstance(message.channel, discord.DMChannel)

            # 2. Bot Mentioned: `@assistance <prompt>`
            is_mentioned = bot.user in message.mentions

            if is_dm and not content.startswith("!"):
                await handle_incoming_prompt(message.channel, content, user_tag)
                return

            if is_mentioned and not content.startswith("!"):
                # Strip out the mention tag (e.g. `<@1563577856318119956>`)
                clean_prompt = content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
                if clean_prompt:
                    await handle_incoming_prompt(message.channel, clean_prompt, user_tag)
                    return

            # Process registered bot commands (!task, !status, !help)
            await bot.process_commands(message)

        async def handle_incoming_prompt(channel: Any, prompt: str, user_tag: str) -> None:
            task = self.enqueue_task_fn(prompt, "discord", str(channel.id))
            await channel.send(
                f"📥 **Task #{task.id} Forwarded to Workspace**\n"
                f"> **Prompt**: {prompt[:400]}\n"
                f"⚙️ Ephemeral pipeline spawned: `Researcher` ➔ `Operator` ➔ `Validator` ➔ `Communicator`...\n"
                f"Verified results will be posted here once complete."
            )

        @bot.command(name="task")
        async def cmd_task(ctx: commands.Context, *, prompt: str) -> None:
            await handle_incoming_prompt(ctx.channel, prompt, str(ctx.author))

        @bot.command(name="status")
        async def cmd_status(ctx: commands.Context) -> None:
            data = self.get_status_fn()
            tasks = data.get("tasks", [])
            lines = []
            if not tasks:
                lines.append("No active or recent tasks.")
            for t in tasks[:6]:
                status_emoji = "✅" if t.status == "COMPLETED" else "⏳" if t.status in ("RESEARCHING", "OPERATING", "VALIDATING") else "⚠️"
                lines.append(f"{status_emoji} **Task #{t.id:03d}** `[{t.status}]`: {t.title[:55]}")

            embed = discord.Embed(
                title="Antigravity Workspace Status",
                description="\n".join(lines),
                color=0x2ECC71,
            )
            embed.set_footer(text=f"Database: storage/events.db")
            await ctx.send(embed=embed)

        @bot.command(name="help")
        async def cmd_help(ctx: commands.Context) -> None:
            embed = discord.Embed(
                title="Antigravity Autonomous Workspace - Discord Control",
                description="Control the multi-agent coding framework directly from Discord.",
                color=0x3498DB,
            )
            embed.add_field(
                name="💬 Submitting a Task",
                value=(
                    "• `!task <your instructions>`\n"
                    "• `@assistance <your instructions>`\n"
                    "• Or send a Direct Message (DM) to this bot."
                ),
                inline=False,
            )
            embed.add_field(
                name="📊 Status & Monitoring",
                value="• `!status` - Show recent tasks and workspace pipeline state.",
                inline=False,
            )
            await ctx.send(embed=embed)

        bot.run(self.bot_token)
