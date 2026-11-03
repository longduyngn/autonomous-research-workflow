"""Portable stdlib-only checkpoints, usable on a VM or a mounted durable volume.

Application code supplies its real state files. No pickle/model is deserialized
here. A store on the ephemeral VM disk does NOT survive loss of that VM.
"""
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def artifact(root, name):
    rel=Path(name)
    if rel.is_absolute() or not rel.parts or '..' in rel.parts:
        raise ValueError('Checkpoint paths must be relative and contained')
    if any(p.startswith('.env') or p in {'.ssh','.aws','.kaggle','credentials.json','token.json','access_token'} for p in rel.parts):
        raise ValueError('Credentials are not checkpoint state')
    root=Path(root).resolve();path=root/rel
    for part in [path,*list(path.parents)[:len(rel.parts)-1]]:
        if part.is_symlink():
            raise ValueError('Checkpoint symlinks are not supported')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Checkpoint path escapes root')
    return path


class CheckpointStore:
    def __init__(self, directory):
        self.root=Path(directory).resolve()
        self.root.mkdir(parents=True,exist_ok=True)

    def publish(self, source, files, metadata):
        if not files or len(set(files)) != len(files):
            raise ValueError('Explicit unique state files are required')
        for field in ('code_sha256','data_sha256','config_sha256'):
            if not re.fullmatch('[0-9a-f]{64}',metadata.get(field,'')):
                raise ValueError('Checkpoint needs '+field)
        if type(metadata.get('step')) is not int or metadata['step'] < 0:
            raise ValueError('A nonnegative completed step is required')
        key=uuid.uuid4().hex;stage=self.root/('.incoming-'+key)
        stage.mkdir()
        entries=[]
        try:
            for name in files:
                src=artifact(source,name)
                if not src.is_file():raise ValueError('Missing checkpoint file: '+name)
                dst=artifact(stage/'files',name);dst.parent.mkdir(parents=True,exist_ok=True)
                with src.open('rb') as inp,dst.open('wb') as out:
                    shutil.copyfileobj(inp,out);out.flush();os.fsync(out.fileno())
                entries.append({'path':name,'bytes':dst.stat().st_size,'sha256':sha256(dst)})
            manifest={'version':1,'id':key,'created_ns':time.time_ns(),'metadata':metadata,'files':entries}
            with (stage/'manifest.json').open('w',encoding='utf8') as f:
                json.dump(manifest,f,indent=2);f.flush();os.fsync(f.fileno())
            # Rename publishes only after every file and the completion manifest exist.
            os.replace(stage,self.root/key)
            self.verify(key)
            return manifest
        except BaseException:
            # Keep interrupted staging evidence; readers ignore .incoming-*.
            raise

    def verify(self,key,expected=None):
        if not re.fullmatch('[0-9a-f]{32}',key):raise ValueError('Invalid checkpoint ID')
        directory=self.root/key
        if directory.is_symlink():raise ValueError('Invalid checkpoint directory')
        m=json.loads((directory/'manifest.json').read_text(encoding='utf8'))
        if m.get('id') != key or m.get('version') != 1 or not m.get('files'):
            raise ValueError('Incomplete checkpoint manifest')
        names=[]
        for entry in m['files']:
            path=artifact(directory/'files',entry['path']);names.append(entry['path'])
            if not path.is_file() or path.stat().st_size != entry['bytes'] or sha256(path) != entry['sha256']:
                raise ValueError('Corrupt checkpoint state: '+entry['path'])
        if len(set(names)) != len(names):raise ValueError('Duplicate checkpoint entries')
        for field,value in (expected or {}).items():
            if m['metadata'].get(field) != value:
                raise ValueError('Incompatible checkpoint: '+field)
        return m

    def list(self):
        valid=[]
        for path in self.root.iterdir():
            if re.fullmatch('[0-9a-f]{32}',path.name):
                try:valid.append(self.verify(path.name))
                except (ValueError,OSError,KeyError,TypeError):continue
        return sorted(valid,key=lambda m:m['created_ns'],reverse=True)

    def restore(self,key,destination,expected=None):
        m=self.verify(key,expected);destination=Path(destination)
        if destination.exists() or destination.is_symlink():
            raise ValueError('Restore into a fresh directory; never overwrite live work')
        destination.parent.mkdir(parents=True,exist_ok=True)
        stage=destination.with_name('.restore-'+uuid.uuid4().hex)
        stage.mkdir()
        for entry in m['files']:
            src=artifact(self.root/key/'files',entry['path']);dst=artifact(stage,entry['path'])
            dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
            if sha256(dst) != entry['sha256']:raise ValueError('Restore verification failed')
        os.replace(stage,destination)
        return m


class AutoCheckpoint:
    """Call at safe training boundaries with a consistent snapshot of state files."""
    def __init__(self,store,interval_seconds=300):
        if interval_seconds <= 0:raise ValueError('Checkpoint interval must be positive')
        self.store=store;self.interval=interval_seconds;self.last=None

    def save(self,source,files,metadata,force=False):
        now=time.monotonic()
        if not force and self.last is not None and now-self.last < self.interval:return None
        result=self.store.publish(source,files,metadata)
        self.last=now
        return result
