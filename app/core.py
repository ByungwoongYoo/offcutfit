"""OffcutFit: fit a small cut list to individually priced remnant bars.

Fixtures are invented demo inventory, not a merchant's stock or demand data.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import threading


CATALOG = [
    {"id": "P1", "material": "pine", "length_mm": 1200, "width_mm": 90, "thickness_mm": 18, "price_cents": 620},
    {"id": "P2", "material": "pine", "length_mm": 850, "width_mm": 90, "thickness_mm": 18, "price_cents": 440},
    {"id": "P3", "material": "pine", "length_mm": 600, "width_mm": 90, "thickness_mm": 18, "price_cents": 350},
    {"id": "O1", "material": "oak", "length_mm": 1300, "width_mm": 90, "thickness_mm": 18, "price_cents": 1450},
    {"id": "O2", "material": "oak", "length_mm": 900, "width_mm": 90, "thickness_mm": 18, "price_cents": 990},
    {"id": "B1", "material": "birch_plywood", "length_mm": 1220, "width_mm": 90, "thickness_mm": 18, "price_cents": 950},
    {"id": "B2", "material": "birch_plywood", "length_mm": 750, "width_mm": 90, "thickness_mm": 18, "price_cents": 580},
]
MATERIALS = {
    "pine": "Pine. Softwood for a painted light indoor craft, practice project, inexpensive model, small plant stand.",
    "oak": "Oak. Hardwood with visible natural grain for a durable furniture component or decorative solid wood piece.",
    "birch_plywood": "Birch plywood. Layered stable sheet cut into strips for a model cabinet, smooth shelf, or precise craft.",
}


class SemanticMatcher:
    """Local MiniLM embeddings when explicitly configured; TF-IDF otherwise.

    Neither mode infers load ratings, food safety, outdoor suitability, or dimensions.
    """
    def __init__(self, model_dir=None):
        self.lock = threading.Lock()
        self.labels = list(MATERIALS)
        self.mode = "tfidf-cpu"
        self.model_dir = model_dir or os.environ.get("OFFCUTFIT_MODEL_DIR")
        if self.model_dir:
            import torch
            from transformers import AutoTokenizer, AutoModel
            self.torch = torch
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_dir, local_files_only=True)
            self.model = AutoModel.from_pretrained(self.model_dir, local_files_only=True, trust_remote_code=False)
            self.model.eval()
            self.mode = "minilm-cpu"
            self.vectors = self._encode(list(MATERIALS.values()))
        else:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self.vectorizer = TfidfVectorizer(ngram_range=(1, 2))
            self.vectors = self.vectorizer.fit_transform(MATERIALS.values())

    def _encode(self, texts):
        t = self.torch
        with self.lock, t.no_grad():
            tokens = self.tokenizer(texts, padding=True, truncation=True, max_length=128, return_tensors="pt")
            hidden = self.model(**tokens).last_hidden_state
            mask = tokens["attention_mask"].unsqueeze(-1).float()
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            return t.nn.functional.normalize(pooled, p=2, dim=1)

    def suggest(self, query):
        if not isinstance(query, str) or not 3 <= len(query) <= 1000:
            raise ValueError("Describe the project in 3–1000 characters.")
        if self.mode == "minilm-cpu":
            sims = (self._encode([query]) @ self.vectors.T)[0].tolist()
        else:
            sims = (self.vectorizer.transform([query]) @ self.vectors.T).toarray()[0].tolist()
        ranked = sorted(zip(self.labels, sims), key=lambda x: (-x[1], x[0]))
        return {"model": self.mode, "suggestions": [{"material": m, "score": round(s, 4), "description": MATERIALS[m]} for m, s in ranked]}


def plan_cut(pieces_mm, material, max_cents=100000, kerf_mm=3, catalog=None, search_limit=100000):
    """Exact enumeration for this bounded demo: one merchant, up to 12 pieces.

    A remaining tail requires a full saw kerf. An exact final fit needs no cut.
    Width and thickness are fixed to 90x18mm in this prototype. No joining pieces.
    """
    if material not in MATERIALS:
        raise ValueError("Choose one of the offered materials.")
    if not isinstance(pieces_mm, list) or not 1 <= len(pieces_mm) <= 12:
        raise ValueError("Enter 1–12 piece lengths.")
    if any(type(x) is not int or not 1 <= x <= 3000 for x in pieces_mm):
        raise ValueError("Each length must be an integer from 1 to 3000 mm.")
    if type(max_cents) is not int or not 1 <= max_cents <= 100000:
        raise ValueError("Budget must be 1–100000 cents.")
    if type(kerf_mm) is not int or not 0 <= kerf_mm <= 10:
        raise ValueError("Kerf must be 0–10 mm.")
    stock = [s for s in copy.deepcopy(catalog if catalog is not None else CATALOG) if s["material"] == material and s["width_mm"] == 90 and s["thickness_mm"] == 18]
    stock.sort(key=lambda s: (s["price_cents"], s["id"]))
    if len(stock) > 16:
        raise ValueError("This demo supports at most 16 eligible remnants.")
    remaining = [s["length_mm"] for s in stock]
    assigned = [[] for _ in stock]
    ordered = sorted(pieces_mm, reverse=True)
    best = None
    visited = set()
    interrupted = False

    def search(n, cost):
        nonlocal best, interrupted
        if interrupted:
            return
        if cost > max_cents or (best is not None and cost > best[0][0]):
            return
        state = (n, tuple(remaining))
        if state in visited:
            return
        if len(visited) >= search_limit:
            interrupted = True
            return
        visited.add(state)
        if n == len(ordered):
            chosen = [{**stock[i], "cuts_mm": assigned[i][:], "tail_mm": remaining[i], "kerf_loss_mm": stock[i]["length_mm"] - remaining[i] - sum(assigned[i])} for i in range(len(stock)) if assigned[i]]
            key = (cost, sum(c["length_mm"] for c in chosen) - sum(ordered), len(chosen), tuple(c["id"] for c in chosen))
            if best is None or key < best[0]:
                best = (key, chosen)
            return
        piece = ordered[n]
        for i, bar in enumerate(stock):
            used = piece if piece == remaining[i] else piece + kerf_mm
            if used > remaining[i]:
                continue
            first = not assigned[i]
            remaining[i] -= used
            assigned[i].append(piece)
            search(n + 1, cost + (bar["price_cents"] if first else 0))
            assigned[i].pop()
            remaining[i] += used

    search(0, 0)
    if best is None:
        reason = "Search limit reached before finding a complete plan. Try fewer pieces; infeasibility has not been established." if interrupted else "No complete cut plan fits this stock and budget. No payment order created."
        return {"feasible": False, "search_complete": not interrupted, "reason": reason}
    return {"feasible": True, "search_complete": not interrupted, "lowest_cost_proven": not interrupted, "states_visited": len(visited), "material": material, "cross_section_mm": [90, 18], "pieces_mm": pieces_mm, "kerf_mm": kerf_mm, "price_cents": best[0][0], "currency": "USD", "bars": best[1], "retained_tail_mm": sum(x["tail_mm"] for x in best[1]), "kerf_loss_mm": sum(x["kerf_loss_mm"] for x in best[1]), "inventory_kind": "synthetic demonstration", "tail_owner": "buyer", "price_scope": "whole remnants including demo cutting; collection only; no additional demo tax or fees"}


def quote_hash(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def paypal_payload(plan):
    if not plan.get("feasible"):
        raise ValueError("A complete cut plan is required.")
    return {"intent": "CAPTURE", "purchase_units": [{"custom_id": quote_hash(plan), "description": "OffcutFit cut-to-length demo material", "amount": {"currency_code": plan["currency"], "value": f"{plan['price_cents'] / 100:.2f}"}}]}
