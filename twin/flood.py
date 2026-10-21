"""Connected coastal flooding by priority-flood.

For every cell, the lowest still-water level (CGVD2013) at which the sea can
reach it over the terrain. Unlike a bathtub threshold, low ground behind a
dyke or ridge only floods once the water tops that barrier. This is a static
planning screen, not a hydrodynamic model: no waves, duration or drainage.

Barnes, Lehman & Mulla (2014), "Priority-flood: an optimal depression-filling
and watershed-labeling algorithm for digital elevation models".
"""
from __future__ import annotations

import heapq

import numpy as np


def seeds(ground: np.ndarray, sea_max: float = 0.5) -> np.ndarray:
    """Open-water cells on the grid edge: where the sea enters the model."""
    edge = np.zeros(ground.shape, bool)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    return edge & (ground <= sea_max)


def fill_level(ground: np.ndarray, seed_mask: np.ndarray) -> np.ndarray:
    """Minimum water level needed for the sea (entering at `seed_mask`) to reach each cell."""
    h, w = ground.shape
    g = ground.ravel().astype("float64")
    level = np.full(g.size, np.inf)
    done = np.zeros(g.size, bool)
    heap = [(g[i], i) for i in np.flatnonzero(seed_mask.ravel())]
    for _, i in heap:
        level[i] = g[i]
        done[i] = True
    heapq.heapify(heap)
    while heap:
        lv, i = heapq.heappop(heap)
        r, c = divmod(i, w)
        for j, ok in ((i - w, r > 0), (i + w, r < h - 1), (i - 1, c > 0), (i + 1, c < w - 1)):
            if ok and not done[j]:
                done[j] = True
                level[j] = max(lv, g[j])
                heapq.heappush(heap, (level[j], j))
    return level.reshape(h, w).astype("float32")
