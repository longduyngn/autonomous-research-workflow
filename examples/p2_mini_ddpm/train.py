"""Mini-DDPM (CIFAR-10, a few epochs) with resumable checkpoints on the shared store.

Workflow smoke, not a paper reproduction: it exercises submit -> progress -> checkpoint -> resume ->
light results. Usage (Colab/Kaggle/local with torch):

  python train.py --steps 2000 --ckpt-every 250                       # CIFAR-10 via torchvision
  python train.py --smoke --steps 8 --ckpt-every 4                    # synthetic 8x8 data, CPU, seconds

Environment (set by the controller's job adapter):
  WORKFLOW_CHECKPOINT_URI   shared store root for this logical run (rclone:gdrive:... or a Drive mount path)
  WORKFLOW_EXPERIMENT_JOB_ID / WORKFLOW_JOB_REQUEST_KEY   identity for progress lines
"""
import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checkpointing import SharedStore, progress, save_local  # noqa: E402


class TinyEps(nn.Module):
    """Small conv noise predictor with a sinusoidal time embedding."""

    def __init__(self, channels=3, width=64):
        super().__init__()
        self.time = nn.Sequential(nn.Linear(32, width), nn.SiLU(), nn.Linear(width, width))
        self.inp = nn.Conv2d(channels, width, 3, padding=1)
        self.mid = nn.Sequential(nn.GroupNorm(8, width), nn.SiLU(), nn.Conv2d(width, width, 3, padding=1),
                                 nn.GroupNorm(8, width), nn.SiLU(), nn.Conv2d(width, width, 3, padding=1))
        self.out = nn.Conv2d(width, channels, 3, padding=1)

    def forward(self, x, t):
        half = 16
        freqs = torch.exp(-math.log(10000) * torch.arange(half, device=x.device) / half)
        emb = torch.cat([torch.sin(t[:, None] * freqs), torch.cos(t[:, None] * freqs)], dim=1)
        h = self.inp(x) + self.time(emb)[:, :, None, None]
        return self.out(h + self.mid(h))


def data(smoke, batch):
    if smoke:
        generator = torch.Generator().manual_seed(0)
        images = torch.rand(256, 3, 8, 8, generator=generator) * 2 - 1
        return images, batch
    import torchvision
    from torchvision import transforms
    dataset = torchvision.datasets.CIFAR10('data', train=True, download=True,
                                           transform=transforms.Compose([transforms.ToTensor(),
                                                                         transforms.Normalize([0.5] * 3, [0.5] * 3)]))
    return torch.stack([dataset[i][0] for i in range(len(dataset))]), batch


def rng_state():
    return {'python': random.getstate(), 'numpy': np.random.get_state(), 'torch': torch.get_rng_state()}


def set_rng(state):
    random.setstate(state['python'])
    np.random.set_state(state['numpy'])
    torch.set_rng_state(state['torch'])


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--steps', type=int, default=2000)
    parser.add_argument('--batch', type=int, default=128)
    parser.add_argument('--ckpt-every', type=int, default=250)
    parser.add_argument('--timesteps', type=int, default=200)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--work', default='work')
    args = parser.parse_args()

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    job_id = os.environ.get('WORKFLOW_EXPERIMENT_JOB_ID', 'local')
    store = SharedStore(os.environ.get('WORKFLOW_CHECKPOINT_URI') or f'file:{Path(args.work) / "store"}')
    work = Path(args.work)
    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)

    images, batch = data(args.smoke, args.batch)
    model = TinyEps().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-4)
    betas = torch.linspace(1e-4, 0.02, args.timesteps, device=device)
    alpha_bar = torch.cumprod(1 - betas, dim=0)
    step, cursor, order, losses = 0, 0, torch.randperm(len(images)).tolist(), []

    latest = store.latest()
    if latest:  # resume from the newest published checkpoint of this logical run
        local = store.restore(latest, 'state.pt', work / 'restore' / 'state.pt')
        state = torch.load(local, map_location=device, weights_only=False)
        model.load_state_dict(state['model']); optimizer.load_state_dict(state['optimizer'])
        set_rng(state['rng']); step, cursor, order, losses = state['step'], state['cursor'], state['order'], state['losses']
        print(f'resumed from {latest["checkpoint_id"]} at step {step}', flush=True)

    while step < args.steps:
        if cursor + batch > len(order):
            order, cursor = torch.randperm(len(images)).tolist(), 0
        x0 = images[order[cursor:cursor + batch]].to(device)
        cursor += batch
        t = torch.randint(0, args.timesteps, (x0.shape[0],), device=device)
        noise = torch.randn_like(x0)
        a = alpha_bar[t][:, None, None, None]
        loss = F.mse_loss(model((a.sqrt() * x0 + (1 - a).sqrt() * noise), t.float()), noise)
        optimizer.zero_grad(); loss.backward(); optimizer.step()
        step += 1
        losses.append(float(loss))
        if step % max(1, args.ckpt_every) == 0 or step == args.steps:
            state = {'model': model.state_dict(), 'optimizer': optimizer.state_dict(), 'rng': rng_state(),
                     'step': step, 'cursor': cursor, 'order': order, 'losses': losses}
            path = save_local(state, work / 'ckpt' / 'state.pt', saver=torch.save)
            metrics = work / 'ckpt' / 'metrics.json'
            metrics.write_text(json.dumps({'step': step, 'loss': losses[-1], 'loss_curve': losses[-200:]}), encoding='utf-8')
            store.publish(f'ck-{step:07d}', [path, metrics], {
                'logical_run_id': os.environ.get('WORKFLOW_JOB_REQUEST_KEY', 'mini-ddpm'), 'job_id': job_id,
                'global_step': step, 'data_cursor': cursor, 'checkpoint_schema': 'mini-ddpm-v1',
                'kinds': {'metrics.json': 'metrics'}, 'light': ('metrics.json',)})
            progress(job_id, step, args.steps, loss=losses[-1])
    progress(job_id, step, args.steps, state='COMPLETED', loss=losses[-1] if losses else float('nan'))


if __name__ == '__main__':
    main()
