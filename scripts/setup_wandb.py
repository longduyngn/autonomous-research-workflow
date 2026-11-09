"""Host-only W&B login check and explicitly requested Discord handoff."""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check(online=False):
    if not importlib.util.find_spec('wandb'):
        return {'status': 'sdk_missing'}
    import wandb
    try:
        # Public API, noninteractive; SDK resolves credentials internally.
        if not wandb.login(prompt=False, verify=online, timeout=10):
            return {'status': 'login_required', 'sdk_version': wandb.__version__}
        if not online:
            return {'status': 'credentials_present_unverified', 'sdk_version': wandb.__version__}
        viewer = wandb.Api(timeout=10).viewer
        return {'status': 'authenticated', 'username': viewer.username, 'sdk_version': wandb.__version__}
    except Exception as exc:
        return {'status': 'auth_or_network_error', 'error_type': type(exc).__name__}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['check', 'login', 'notify-discord'])
    p.add_argument('--online', action='store_true')
    args = p.parse_args()
    from main import load_dotenv, load_config
    load_dotenv(ROOT/'.env')
    load_dotenv(ROOT/'config/.env')
    if args.action == 'login':
        if not sys.stdin.isatty():
            raise SystemExit('Run this command in your own interactive terminal; never paste a key in chat.')
        import wandb
        wandb.login(relogin=True)
        print(json.dumps(check(True)))
    elif args.action == 'check':
        print(json.dumps(check(args.online)))
    else:
        from gateways.discord_gateway import DiscordGateway, discover_playground_channels
        from core.role_memory import atomic_json
        receipt = ROOT/'storage/verification-local/wandb-login-discord.json'
        if receipt.exists() and json.loads(receipt.read_text())['delivered']:
            print('Login instructions already delivered; no duplicate ping.')
            return
        cfg = load_config()
        channels = discover_playground_channels(os.environ.get('DISCORD_BOT_TOKEN', ''))
        channel = os.environ.get('DISCORD_TASK_PROPOSAL_CHANNEL_ID') or channels.get('task_proposal')
        if not channel:
            raise SystemExit('Task-proposal channel unavailable; no message sent.')
        message = (
            'Cần bạn đăng nhập W&B để bật theo dõi tiến độ online. SDK đã cài trên máy.\n'
            'Mở PowerShell trên máy của bạn và chạy:\n'
            '```powershell\ncd D:\\Antigravity_playgournd\n'
            '.\\.conda\\python.exe scripts/setup_wandb.py login\n```\n'
            'Đăng nhập tại https://wandb.ai/authorize và nhập API key vào prompt của terminal. '
            'Không gửi API key vào Discord/chat hoặc ghi vào source code.\n'
            'Sau đó chạy `.\\.conda\\python.exe scripts/setup_wandb.py check --online` '
            'và báo lại username/team (entity), project muốn dùng. '
            'Chưa chọn thì project mặc định là antigravity-research. '
            'Colab/Kaggle cần thêm secret WANDB_API_KEY trong secret store của nền tảng; '
            'đăng nhập trên Windows không tự đăng nhập VM. '
            'Trong lúc chờ, tiến độ vẫn lưu local; chưa có smoke test online được xác nhận.'
        )
        sent = DiscordGateway(cfg.get('gateways', {}).get('discord', {})).send_message(message, channel)
        atomic_json(receipt, {'delivered': sent, 'channel': channel, 'status': check(False)})
        print(json.dumps({'delivered': sent, 'channel': channel}))
        if not sent:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
