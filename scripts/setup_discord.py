"""Discover/create workflow role channels and update only channel-ID env keys."""
import argparse
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from main import load_dotenv
from core.discord_channels import discover,update_env


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--create',action='store_true');p.add_argument('--apply-env',action='store_true')
    p.add_argument('--guild',default='');p.add_argument('--env-file',type=Path,default=ROOT/'.env')
    args=p.parse_args()
    load_dotenv(ROOT/'.env');load_dotenv(ROOT/'config/.env')
    token=os.environ.get('DISCORD_BOT_TOKEN','')
    if not token:raise SystemExit('Discord credential is not configured')
    mapping=discover(token,args.guild or os.environ.get('DISCORD_GUILD_ID',''),args.create)
    if args.apply_env:update_env(args.env_file,mapping)
    state=ROOT/'storage/discord-channels.json';state.parent.mkdir(exist_ok=True,parents=True)
    state.write_text(json.dumps(mapping,indent=2),encoding='utf8')
    print(json.dumps(mapping,indent=2))


if __name__=='__main__':main()
