"""CPU matching comparison on a fixed, authored twelve-query demonstration set.

This is not merchant demand evidence, a user study or material-safety grading.
The direct selector remains the user's explicit choice, not a predicted label.
"""
from pathlib import Path
import argparse
import json
import os
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from core import SemanticMatcher

CASES = [
    ("cheap painted craft", "pine"),
    ("A small decoration I will cover in colour; keep the timber inexpensive.", "pine"),
    ("Softwood for practising cuts, no premium grain required.", "pine"),
    ("pine strips for a practice model", "pine"),
    ("Solid hardwood with attractive grain left on display.", "oak"),
    ("A furniture detail that keeps its natural appearance.", "oak"),
    ("oak for a decorative panel", "oak"),
    ("A durable wooden ornament rather than plywood.", "oak"),
    ("Laminated sheets for an accurate cabinet mockup.", "birch_plywood"),
    ("birch plywood strips for a small model", "birch_plywood"),
    ("A smooth stable panel for model-making.", "birch_plywood"),
    ("Stacked thin layers for a little storage box.", "birch_plywood"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    args = parser.parse_args()
    os.environ.pop("OFFCUTFIT_MODEL_DIR", None)
    lexical = SemanticMatcher()
    semantic = SemanticMatcher(model_dir=args.model_dir)
    rows = []
    start = time.perf_counter()
    for query, intended in CASES:
        baseline = lexical.suggest(query)["suggestions"]
        encoded = semantic.suggest(query)["suggestions"]
        rows.append({"query": query, "authored_intended_card": intended,
                     "tfidf_top": baseline[0]["material"],
                     "minilm_top": encoded[0]["material"],
                     "tfidf_scores": baseline, "minilm_scores": encoded})
    summary = {"scope": "AUTHORED_FIXED_DEMONSTRATION_QUERIES_NOT_INDEPENDENT_GENERALIZATION_OR_USER_STUDY",
               "rows": len(rows), "candidate_cards": 3,
               "direct_selection": "User chooses one of the same three cards; no automated ranking or user-time result claimed.",
               "lexical_baseline": "TF-IDF unigrams and bigrams over the same material descriptions",
               "tfidf_matches_authored_intention": sum(x["tfidf_top"] == x["authored_intended_card"] for x in rows),
               "minilm_matches_authored_intention": sum(x["minilm_top"] == x["authored_intended_card"] for x in rows),
               "ranking_seconds_excluding_model_load": round(time.perf_counter() - start, 3),
               "limits": "Author-set labels/queries and only three cards; no claims of timber suitability, demand, user benefit or unseen-query accuracy.",
               "comparisons": rows}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
