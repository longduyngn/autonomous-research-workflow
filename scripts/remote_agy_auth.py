"""Imported only in the Colab kernel: interactive OAuth, no credential exports."""
import base64
import os
import re
import select
import subprocess
import time

_proc = None
_master = None
_screen = ''
_key = None
_submitted = False


def start(executable):
    global _proc, _master, _screen, _key, _submitted
    if _proc is not None and _proc.poll() is None:
        return status()
    import pty
    from cryptography.hazmat.primitives.asymmetric import rsa
    _key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    _screen, _submitted = '', False
    _master, slave = pty.openpty()
    # Use the documented remote OAuth flow instead of a browser inside the VM.
    env = dict(os.environ, SSH_CONNECTION='127.0.0.1 1 127.0.0.1 22',TERM='xterm-256color')
    _proc = subprocess.Popen([executable],stdin=slave,stdout=slave,stderr=slave,
                             cwd='/content/agy-worker',env=env,start_new_session=True)
    os.close(slave)
    time.sleep(1)
    return status()


def status():
    global _screen
    from cryptography.hazmat.primitives import serialization
    if _proc is None:
        return {'state':'NOT_STARTED'}
    for _ in range(40):
        if not select.select([_master],[],[],0.03)[0]: break
        try: data = os.read(_master,65536)
        except OSError: break
        if not data: break
        _screen = (_screen+data.decode('utf-8',errors='replace'))[-40000:]
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',_screen)
    urls = re.findall(r'https://[^\s\x00-\x20\x7f<>]+',text)
    url = next((u.rstrip(')') for u in reversed(urls)
                if u.startswith(('https://accounts.google.com/','https://auth.antigravity.google/','https://antigravity.google/'))),None)
    words = ('Personal','Google','Business','sign in','Sign in','authorization','code','Enter','browser')
    hint = ' | '.join(w for w in words if w in text)
    return {'state':'RUNNING' if _proc.poll() is None else 'EXITED',
            'authorization_url':url if not _submitted else None,'hint':hint,
            'public_key':_key.public_key().public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
            'input_submitted':_submitted,
            'keyring_error':any(marker in text.lower() for marker in (
                'failed to save','failed to store','secret service is not available','dbus-launch: not found'))}


def submit(ciphertext):
    global _submitted, _screen
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives import hashes
    if _proc is None or _proc.poll() is not None:
        raise RuntimeError('No interactive login is running')
    code = _key.decrypt(base64.b64decode(ciphertext),padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    if not 1 <= len(code) <= 190 or b'\n' in code or b'\r' in code:
        raise ValueError('Invalid authorization code')
    os.write(_master,code+b'\r')
    _submitted = True
    _screen = ''
    del code
    return {'state':'INPUT_SENT','plaintext_logged':False}


def choose_personal():
    if _proc is None or _proc.poll() is not None:
        raise RuntimeError('Login is not running')
    os.write(_master,b'\r')
    return {'state':'PERSONAL_OPTION_CONFIRMED'}


def stop():
    global _proc, _master, _screen, _key
    if _proc is not None and _proc.poll() is None:
        import signal
        os.killpg(_proc.pid,signal.SIGTERM)
    if _master is not None: os.close(_master)
    _proc, _master, _key, _screen = None, None, None, ''
    return {'state':'STOPPED'}
