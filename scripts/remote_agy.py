"""Manage a reusable local AGY role and its Colab agent, without public ports."""
import argparse
import getpass
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
sys.stdout.reconfigure(encoding='utf-8',errors='replace')


def main():
    from main import load_config,load_dotenv
    from core.remote_agent import RemoteManager,DEFAULT_MODEL
    from core.role_memory import atomic_json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['setup','auth-check','login-start','login-stop','login-status','login-personal',
        'login-code','notify-login','send','poll','cancel','chat','review','status','local-turn'])
    p.add_argument('--message')
    p.add_argument('--request-id')
    p.add_argument('--model',default=DEFAULT_MODEL)
    p.add_argument('--timeout',type=int,default=21600,help='Explicit remote turn budget; default 6h, not an inactivity timer')
    p.add_argument('--wait',type=int,default=0,help='Maximum seconds to poll using code, not agent turns')
    p.add_argument('--reconnect',action='store_true',help='Explicitly reconnect a closed Colab browser transport')
    p.add_argument('--allow-command',action='append',default=[],help='Explicit native command permission for this request only')
    a = p.parse_args()
    load_dotenv(ROOT/'.env'); load_dotenv(ROOT/'config/.env')
    directory = ROOT/'storage/remote-agy/colab-personal'
    m = RemoteManager(ROOT,directory,load_config())
    if a.action == 'setup':
        connection = m.transport.cloud.colab_connect({'force':a.reconnect})
        # Never print bridge proxy tokens/URLs when reconnecting.
        print(json.dumps({'connection':connection.get('status','connected')}))
        if a.reconnect:
            pointer = directory/'control-cell.json'
            if pointer.exists(): pointer.rename(pointer.with_name('control-cell.'+uuid.uuid4().hex+'.json'))
        result = m.transport.deploy()
    elif a.action == 'auth-check': result = m.transport.auth_check()
    elif a.action == 'login-start':
        result = m.transport.login_start()
        # The authorization URL is for the owner; the public key is not useful UI.
        result.pop('public_key',None)
    elif a.action == 'login-status':
        result = m.transport.login_status(); result.pop('public_key',None)
    elif a.action == 'login-personal': result = m.transport.login_personal()
    elif a.action == 'login-stop': result = m.transport.rpc('_result=_agy_auth.stop()')
    elif a.action == 'login-code':
        if not sys.stdin.isatty(): raise SystemExit('Run interactively in your own PowerShell; do not paste the code into chat.')
        code = getpass.getpass('AGY authorization code (hidden): ')
        result = m.transport.login_code(code)
        del code
    elif a.action == 'notify-login':
        from gateways.discord_gateway import DiscordGateway,discover_playground_channels
        state = m.transport.login_status()
        url = state.get('authorization_url')
        if not url: raise SystemExit('No live login URL available; start/inspect login first')
        dedup = directory/'login-notice.json'
        import hashlib
        digest = hashlib.sha256(url.encode()).hexdigest()
        if dedup.exists() and json.loads(dedup.read_text()).get('url_hash') == digest:
            print('Already delivered this login request.'); return
        channels = discover_playground_channels(os.environ.get('DISCORD_BOT_TOKEN',''))
        channel = os.environ.get('DISCORD_TASK_PROPOSAL_CHANNEL_ID') or channels.get('task_proposal')
        if not channel: raise SystemExit('Task-proposal channel unavailable')
        message = ('Cần bạn đăng nhập AGY trên Colab CPU để thử hai agent giao tiếp. '
            'Windows AGY đã đăng nhập; Colab dùng phiên riêng, không sao chép credentials.\n'
            f'Mở liên kết đăng nhập chính thức (phiên đang chờ):\n<{url}>\n'
            'Sau khi Google trả authorization code, mở PowerShell trên máy hiện tại và chạy:\n'
            '```powershell\ncd D:\\Antigravity_playgournd\n.\\.conda\\python.exe scripts/remote_agy.py login-code\n```\n'
            'Nhập code vào prompt ẩn của terminal. Code được mã hóa tới Colab, không ghi vào notebook/log. '
            'Không gửi code hoặc token lên Discord/chat. Báo "đã đăng nhập AGY Colab" khi xong. '
            'Đang dùng CPU, không bật GPU hay chạy lại benchmark.')
        delivered = DiscordGateway(load_config().get('gateways',{}).get('discord',{})).send_message(message,channel)
        if delivered: atomic_json(dedup,{'url_hash':digest,'delivered':True,'at':time.time()})
        result = {'delivered':delivered}
    elif a.action == 'local-turn':
        if not a.message: p.error('--message required')
        r = m.local_turn('DISPATCH',a.message)
        result = {'state':'PREPARED','output':r.output,'session':r.structured_data}
    elif a.action in {'send','chat'}:
        if not a.message: p.error('--message required')
        # A single host-side exchange lock prevents interleaving local contexts.
        from core.role_memory import RoleMemory
        with RoleMemory(directory/'exchanges','remote_manager').locked():
            message = a.message
            existing = None
            if a.request_id and a.action == 'chat':
                try: existing = m.mailbox.get(a.request_id)
                except KeyError: pass
            if a.action == 'chat':
                a.request_id = a.request_id or uuid.uuid4().hex
                exchange_path = directory/'exchanges'/(a.request_id+'.json')
                if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}',a.request_id):
                    p.error('Invalid request ID')
                scope = {'user_message':a.message,'model':a.model,'timeout_seconds':a.timeout}
                if a.allow_command: scope['allowed_commands'] = a.allow_command
                if exchange_path.exists():
                    if json.loads(exchange_path.read_text(encoding='utf-8')) != scope:
                        p.error('Request ID already belongs to different chat instructions')
                elif existing:
                    p.error('No original chat scope for this request ID; inspect it using status/poll')
                else: atomic_json(exchange_path,scope)
            if a.action == 'chat' and not existing:
                r = m.local_turn('DISPATCH',message)
                message = r.output
            result = existing['result'] if existing else m.send(message,a.request_id,a.model,a.timeout,a.allow_command)
            if result is None: raise SystemExit('Pending local receipt; use send with the original payload or inspect the ledger')
            rid = result['request_id']
            print(json.dumps(result,ensure_ascii=False),flush=True)
            deadline = time.monotonic()+a.wait
            while result['state'] in {'QUEUED','RUNNING'} and time.monotonic()<deadline:
                time.sleep(min(3,max(0,deadline-time.monotonic())))
                result = m.poll(rid)
            if a.action == 'chat' and result['state'] == 'COMPLETED':
                review_path = directory/'reviews'/(rid+'.json')
                if review_path.exists(): saved = json.loads(review_path.read_text(encoding='utf-8'))
                else:
                    review = m.local_turn('REVIEW',a.message,result)
                    saved = {'request_id':rid,'output':review.output,'session':review.structured_data}
                    atomic_json(review_path,saved)
                result = dict(result,local_review=saved['output'],local_session=saved['session'].get('session_id'))
    elif a.action == 'poll':
        if not a.request_id: p.error('--request-id required')
        result = m.poll(a.request_id)
    elif a.action == 'cancel':
        if not a.request_id: p.error('--request-id required')
        result = m.cancel(a.request_id)
    elif a.action == 'review':
        if not a.request_id: p.error('--request-id required')
        row = m.mailbox.get(a.request_id)
        r = m.local_turn('REVIEW',a.message or row['payload']['message'],row['result'])
        result = {'output':r.output,'session':r.structured_data}
    else: result = {'messages':m.mailbox.recent()}
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__ == '__main__': main()
