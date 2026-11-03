"""Explicitly authorized Discord integration probe: real messages, simulated ingress.

Never authenticates as a user. Test commands remain bot-authored on Discord;
only their in-memory Message author is substituted for testing the allowlisted
human command handler. The ordinary listener ignores the bot-authored originals.
"""
import argparse
import asyncio
import copy
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from main import build_system, load_config, load_dotenv
from gateways.discord_bot import DiscordBotRunner

BASE = ROOT / "storage/full-run-20261102"


async def submit(trial="initial"):
    import discord
    config = load_config()
    db, _, orchestrator, _, _, _ = build_system(config)
    allowed = sorted(x.strip() for x in os.environ.get("DISCORD_ALLOWED_USER_IDS", "").split(",") if x.strip())
    assert allowed, "An authorized human ID is required"
    channel_id = os.environ.get("DISCORD_CHANNEL_ID", "")
    assert channel_id.isdigit(), "An explicit existing Discord channel is required"
    def enqueue(prompt, channel, user, event_key="", input_files=None):
        return orchestrator.submit_task(prompt, source_channel=channel, source_user=user,
            event_key=event_key, input_files=input_files, source_path=str(BASE / ("project_codex" if trial == "codex" else "project")))
    runner = DiscordBotRunner(os.environ["DISCORD_BOT_TOKEN"], enqueue,
                              lambda: {"tasks": db.get_all_tasks(10)})
    bot = runner.create_bot()
    evidence = {"simulation": "Bot-authored Discord commands; authorized-human Message identity simulated locally only",
                "started_at_unix": time.time(), "tasks": [], "probes": {}}
    evidence_path = BASE / (f"discord-ingress-{trial}.json" if trial != "initial" else "discord-ingress.json")
    assert not evidence_path.exists(), "A prior submission exists; inspect its task IDs rather than enqueueing duplicates"
    async with bot:
        await bot.login(os.environ["DISCORD_BOT_TOKEN"])
        channel = await bot.fetch_channel(int(channel_id))
        human = discord.User(state=bot._connection, data={"id": allowed[0], "username": "authorized-integration-simulation", "discriminator": "0", "bot": False, "avatar": None})
        commands = [
            ("main", "!task E2E full workflow verification: simulated authorized command (this visible message is bot-authored). Read inputs/request.md and implement that entire nontrivial experiment using the exact supplied inputs/experiment_spec.json, train.csv and test.csv. Use the mixed configured agents, real Colab T4, three milestones, all six seeded trainings, independent checks, artifact export and verified shutdown. Exercise the two controlled LOCAL final-check faults without retraining. No helper agents or messages. Finish with measured results and output paths.",
             [BASE / "supplied" / name for name in ("request.md", "experiment_spec.json", "train.csv", "test.csv")]),
            ("csv_audit", "!task --provider codex E2E queue/isolation probe: simulated authorized command (bot-authored original). Read inputs/train.csv and inputs/test.csv using Python stdlib. Implement audit_csv.py producing csv_audit.json with row count, class counts, mean x1/x2 and min/max per file. Write check_audit.py which independently recomputes and asserts every saved value; run it. Use a direct research plan with the executable check [\"{python}\",\"check_audit.py\"]. Preserve scripts/results and a concise audit report. Do not use cloud, send messages or spawn helpers.",
             [BASE / "supplied/train.csv", BASE / "supplied/test.csv"]),
            ("numerics", "!task --provider codex E2E queued numerical-library probe: simulated authorized command (bot-authored original). Implement numerics.py with stable sigmoid(x), softplus(x) and binary_logistic_loss(logit,label) using only Python math; support extreme finite logits plus/minus 1000 without overflow. Implement unittest checks of finiteness, symmetry, labels 0/1, known values at 0, and finite-difference derivative versus sigmoid(x)-label. Save actual test output and README. Use a direct research plan with executable check [\"{python}\",\"-m\",\"unittest\",\"discover\",\"-v\"]. No cloud, helper agents or messaging.", [])
        ]
        if trial == "codex":
            commands = [("codex_colab", "!task --provider codex E2E second sequential Colab T4 trial (bot-authored original; authorized ingress simulated locally). The first Antigravity T4 has been verified stopped with zero sessions. Read inputs/request.md and the supplied specification. Review the supplied train.py/local_checks.py, then perform exactly one fresh full Colab T4 execution of all six seeded trainings using the controller transport helper. Export new weights/predictions/curves, independently validate and verify shutdown. No copied results, no concurrent Colab GPU, no helper agents or messaging. Final reports must use this new run ID and actual measurements.",
                        [BASE / "supplied_codex" / name for name in ("request.md", "experiment_spec.json", "train.csv", "test.csv")])]
        if trial == "kaggle":
            commands = [("kaggle_cpu", "!task E2E Kaggle CPU lifecycle probe (bot-authored original; authorized ingress simulated locally). Read inputs/kaggle_spec.json. Create monte_carlo.ipynb using only Python stdlib: seeded random.Random, 100 batches of 10000 independent uniform (x,y) points in [0,1]^2, count x*x+y*y<=1, save batch_counts.csv with batch_index,inside,total and mc_result.json with seed,batches,draws_per_batch,total_points,total_inside,pi_estimate,standard_error. Also save environment.json with actual platform, Python version and cwd. Submit ONCE to Kaggle with hardware CPU, no GPU and no Colab. Use a direct research_plan backend kaggle with executable check [\"{python}\",\"verify_mc.py\"]. Implement verify_mc.py locally before submission: after outputs download under artifacts/kaggle/JOB_ID, independently regenerate all 1000000 draws and verify every batch, all aggregate fields, pi_estimate=4*inside/total, standard_error=4*sqrt(p*(1-p)/N), and abs(pi_estimate-math.pi)<.02. Do not execute the notebook locally as a substitute. Return WAIT_FOR_JOB: actual_32_character_job_id so the controller polls and exports before validation. After completion the communicator reports actual measured result and paths. Use a new generated slug, never overwrite unrelated kernels. No helpers or messaging tools.",
                        [BASE / "supplied_kaggle/kaggle_spec.json"])]
        for tag, content, files in commands:
            message = await channel.send(content, files=[discord.File(p) for p in files], allowed_mentions=discord.AllowedMentions.none())
            original_author = str(message.author.id)
            message.author = human
            await bot.on_message(message)
            with db._connect() as connection:
                row = connection.execute("SELECT id FROM tasks WHERE event_key=?", (f"discord:{message.id}",)).fetchone()
            assert row is not None, "Production command handler did not enqueue the message"
            task_id = row[0]
            task = db.get_task(task_id)
            evidence["tasks"].append({"tag": tag, "task_id": task_id, "message_id": str(message.id),
                "channel_id": str(channel.id), "original_author_bot_id": original_author,
                "attachments": [{"filename": a.filename, "size": a.size} for a in message.attachments],
                "input_snapshots": json.loads(task.inputs_json), "status_at_submission": task.status})
            db.log_event("DISCORD_SIMULATED_INGRESS", "integration:explicit-user-test",
                "Real bot-authored command/attachments processed with a locally simulated authorized human identity",
                task_id=task_id, payload={"message_id": str(message.id), "tag": tag, "original_author_is_bot": True})
            evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            print(json.dumps({"submitted": tag, "task_id": task_id, "message_id": str(message.id)}), flush=True)
            if tag == "main":
                before = len(db.get_all_tasks(10000))
                await bot.on_message(message)
                assert len(db.get_all_tasks(10000)) == before
                evidence["probes"]["duplicate_request_ignored"] = True
                for key, author, text in (("unauthorized_ignored", discord.User(state=bot._connection, data={"id": "1", "username": "unauthorized-test", "discriminator": "0", "bot": False, "avatar": None}), "!task This must not be queued"),
                                          ("approval_reply_not_task", human, "approve 0123456789ab")):
                    probe = copy.copy(message)
                    probe.id += 123 if key == "unauthorized_ignored" else 124
                    probe.author, probe.content = author, text
                    await bot.on_message(probe)
                    assert len(db.get_all_tasks(10000)) == before
                    evidence["probes"][key] = True
        ids = [t["task_id"] for t in evidence["tasks"]]
        deadline = time.monotonic() + (15 if trial == "initial" else 0)
        samples = []
        while time.monotonic() < deadline:
            tasks = [db.get_task(i) for i in ids]
            sample = [{"id": t.id, "status": t.status, "owned": bool(t.lease_owner)} for t in tasks]
            samples.append(sample)
            if sum(bool(t.lease_owner) for t in tasks) == 2 and any(t.status == "PENDING" and not t.lease_owner for t in tasks):
                evidence["probes"]["two_workers_with_overflow_queued"] = True
                break
            await asyncio.sleep(.2)
        evidence["queue_samples"] = samples
        status = await channel.send("!status", allowed_mentions=discord.AllowedMentions.none())
        status.author = human
        await bot.on_message(status)
        evidence["probes"]["status_command_executed"] = True
        evidence["status_message_id"] = str(status.id)
    evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence["probes"], indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Explicitly post the authorized test commands")
    parser.add_argument("--trial", choices=["initial", "codex", "kaggle"], default="initial")
    args = parser.parse_args()
    if not args.execute:
        parser.error("Use --execute only for an authorized live Discord test")
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "config/.env")
    asyncio.run(submit(args.trial))


if __name__ == "__main__":
    main()
