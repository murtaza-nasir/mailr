"""Shared training utilities for the per-user learned models (tier 2).

Inputs are the message embedding (384-d) concatenated with the cheap feature vector. Models
are scikit-learn logistic regressions, small enough to train in seconds on CPU, stored as
joblib bytes in ClassifierModel with held-out metrics.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mailr.intel.features import FEATURES
from mailr.models import EMBED_DIM, ClassifierModel

log = logging.getLogger("mailr.learn")

_CACHE: dict[tuple[int, str], tuple[Any, Any]] = {}  # (user, target) -> (trained_at, model)


def row_vector(embedding, feats: list[float]) -> np.ndarray:
    emb = np.zeros(EMBED_DIM, dtype=np.float32) if embedding is None else np.asarray(embedding, dtype=np.float32)
    return np.concatenate([emb, np.asarray(feats, dtype=np.float32) * 0.5])


@dataclass
class Trained:
    model: Any
    metrics: dict[str, Any]


def train_binary(X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None) -> Trained | None:
    n_pos, n_neg = int(y.sum()), int(len(y) - y.sum())
    if n_pos < 5 or n_neg < 5:
        return None
    w = np.ones(len(y)) if w is None else w
    metrics: dict[str, Any] = {"n": len(y), "positives": n_pos, "negatives": n_neg}
    if n_pos >= 8 and n_neg >= 8:
        Xtr, Xte, ytr, yte, wtr, _ = train_test_split(X, y, w, test_size=0.25, stratify=y, random_state=7)
        m = LogisticRegression(max_iter=400, C=2.0, class_weight="balanced")
        m.fit(Xtr, ytr, sample_weight=wtr)
        pred = m.predict_proba(Xte)[:, 1] >= 0.5
        tp = int(((pred == 1) & (yte == 1)).sum())
        fp = int(((pred == 1) & (yte == 0)).sum())
        fn = int(((pred == 0) & (yte == 1)).sum())
        metrics.update(precision=tp / (tp + fp) if tp + fp else 0.0, recall=tp / (tp + fn) if tp + fn else 0.0,
                       held_out=len(yte), held_out_positives=int(yte.sum()))
    else:
        metrics.update(precision=None, recall=None, held_out=0)
    model = LogisticRegression(max_iter=400, C=2.0, class_weight="balanced")
    model.fit(X, y, sample_weight=w)
    return Trained(model, metrics)


def train_multiclass(X: np.ndarray, y: list[str], w: np.ndarray | None = None) -> Trained | None:
    classes = sorted(set(y))
    if len(classes) < 2 or len(y) < 10:
        return None
    ya = np.asarray(y)
    w = np.ones(len(y)) if w is None else w
    metrics: dict[str, Any] = {"n": len(y), "classes": {c: int((ya == c).sum()) for c in classes}}
    counts = [int((ya == c).sum()) for c in classes]
    if min(counts) >= 3 and len(y) >= 20:
        Xtr, Xte, ytr, yte, wtr, _ = train_test_split(X, ya, w, test_size=0.25, stratify=ya, random_state=7)
        m = LogisticRegression(max_iter=600, C=2.0, class_weight="balanced")
        m.fit(Xtr, ytr, sample_weight=wtr)
        metrics["accuracy"] = float((m.predict(Xte) == yte).mean())
        metrics["held_out"] = len(yte)
    model = LogisticRegression(max_iter=600, C=2.0, class_weight="balanced")
    model.fit(X, ya, sample_weight=w)
    return Trained(model, metrics)


def dumps(model: Any) -> bytes:
    buf = io.BytesIO()
    joblib.dump({"model": model, "features": FEATURES, "dim": EMBED_DIM}, buf)
    return buf.getvalue()


def loads(blob: bytes, what: str = "model") -> Any | None:
    try:
        d = joblib.load(io.BytesIO(blob))
    except Exception as e:  # noqa: BLE001
        # The model stops applying until it is retrained: say which one.
        log.warning("%s: stored model does not load, not applied until retrained: %s", what, e)
        return None
    if d.get("features") != FEATURES or d.get("dim") != EMBED_DIM:
        return None  # trained on an older feature layout; retrain
    return d["model"]


async def save(db: AsyncSession, user_id: int, target: str, t: Trained, auto_apply: bool = False) -> None:
    from datetime import UTC, datetime

    stmt = insert(ClassifierModel).values(user_id=user_id, target=target, blob=dumps(t.model),
                                          metrics=t.metrics, auto_apply=auto_apply, trained_at=datetime.now(UTC))
    stmt = stmt.on_conflict_do_update(index_elements=["user_id", "target"],
                                      set_={"blob": stmt.excluded.blob, "metrics": stmt.excluded.metrics,
                                            "auto_apply": stmt.excluded.auto_apply,
                                            "trained_at": stmt.excluded.trained_at})
    await db.execute(stmt)
    _CACHE.pop((user_id, target), None)


async def load(db: AsyncSession, user_id: int, target: str) -> tuple[Any, ClassifierModel] | None:
    row = (await db.execute(select(ClassifierModel).where(ClassifierModel.user_id == user_id,
                                                          ClassifierModel.target == target))).scalar_one_or_none()
    if row is None:
        return None
    cached = _CACHE.get((user_id, target))
    if cached and cached[0] == row.trained_at:
        return cached[1], row
    model = loads(row.blob, f"user {user_id}, {target}")
    if model is None:
        return None
    _CACHE[(user_id, target)] = (row.trained_at, model)
    return model, row
