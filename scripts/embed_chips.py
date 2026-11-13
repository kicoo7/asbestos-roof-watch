"""DINOv2 ViT-S/14 embeddings (CLS + mean patch token, averaged over 4 rotations) for every roof chip.

-> data/processed/chip_embeddings.parquet (roof_id, e0..e767)
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import timm
import torch
from PIL import Image

import argparse

ROOT = Path(__file__).resolve().parents[1]
_ap = argparse.ArgumentParser()
_ap.add_argument("--chips", default=str(ROOT / "data/processed/chips"))
_ap.add_argument("--out", default=str(ROOT / "data/processed/chip_embeddings.parquet"))
_a = _ap.parse_args()
CHIPS, OUT = Path(_a.chips), Path(_a.out)
MODEL = "vit_small_patch14_dinov2.lvd142m"

dev = "mps" if torch.backends.mps.is_available() else "cpu"
model = timm.create_model(MODEL, pretrained=True, num_classes=0, img_size=224).eval().to(dev)
mean = torch.tensor([0.485, 0.456, 0.406], device=dev).view(1, 3, 1, 1)
std = torch.tensor([0.229, 0.224, 0.225], device=dev).view(1, 3, 1, 1)

ids = sorted(int(p.stem) for p in CHIPS.glob("*.jpg"))
print(f"{len(ids)} chips on {dev}", flush=True)
rows, t0, B = [], time.time(), 64


@torch.no_grad()
def embed(x):
    feats = []
    for k in range(4):  # roofs have no canonical orientation
        tok = model.forward_features(torch.rot90(x, k, dims=(2, 3)))
        feats.append(torch.cat([tok[:, 0], tok[:, model.num_prefix_tokens:].mean(1)], dim=1))
    return torch.stack(feats).mean(0)


for i in range(0, len(ids), B):
    batch = ids[i:i + B]
    x = np.stack([np.asarray(Image.open(CHIPS / f"{r}.jpg").convert("RGB")) for r in batch])
    x = torch.from_numpy(x).to(dev).permute(0, 3, 1, 2).float() / 255.0
    rows.append(embed((x - mean) / std).cpu().numpy())
    if (i // B) % 50 == 0:
        print(f"{i + len(batch)}/{len(ids)} {time.time()-t0:.0f}s", flush=True)
E = np.concatenate(rows)
df = pd.DataFrame(E, columns=[f"e{j}" for j in range(E.shape[1])]).astype("float32")
df.insert(0, "roof_id", ids)
df.to_parquet(OUT, index=False)
print("done", E.shape, f"{time.time()-t0:.0f}s")
