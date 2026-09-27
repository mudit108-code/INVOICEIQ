"""
InvoiceIQ — Price Lookup Engine
--------------------------------
Loads the price catalog from CSV or Excel and resolves each AI-extracted
service phrase against it using fuzzy string matching.

Hard rule: if no confident match is found, the line is flagged NEEDS_REVIEW
and NO price is fabricated. A human must supply the price manually before
the invoice can be approved.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Optional

import pandas as pd

REQUIRED_COLUMNS = ["service_id", "service_name", "category", "unit",
                    "unit_price_inr", "tax_rate_percent", "status"]

CONFIDENT_THRESHOLD = 0.62   # auto-match above this score
POSSIBLE_THRESHOLD = 0.40    # show as suggestion between this and confident


@dataclass
class PriceMatch:
    query: str
    matched: bool
    service_id: Optional[str] = None
    service_name: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    tax_rate: Optional[float] = None
    confidence: float = 0.0
    status: str = "NEEDS_REVIEW"  # MATCHED | SUGGESTED | NEEDS_REVIEW
    suggestions: Optional[List[dict]] = None


def load_catalog(path: str) -> pd.DataFrame:
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path, sheet_name=0)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Catalog is missing required columns: {missing}")

    df["status"] = df["status"].astype(str).str.lower()
    df = df[df["status"] == "active"].copy()
    df["unit_price_inr"] = pd.to_numeric(df["unit_price_inr"], errors="coerce")
    df["tax_rate_percent"] = pd.to_numeric(df["tax_rate_percent"], errors="coerce").fillna(0)
    df = df.dropna(subset=["unit_price_inr"])
    return df.reset_index(drop=True)


def _similarity(a: str, b: str) -> float:
    a, b = a.lower().strip(), b.lower().strip()
    ratio = SequenceMatcher(None, a, b).ratio()
    # token overlap boost — helps "logo design" match "Logo Design (Basic)"
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if a_tokens and b_tokens:
        overlap = len(a_tokens & b_tokens) / max(1, len(a_tokens))
        ratio = max(ratio, 0.5 * ratio + 0.5 * overlap)
    return ratio


def match_service(query: str, catalog: pd.DataFrame, top_n: int = 3,
                   predicted_category: Optional[str] = None) -> PriceMatch:
    scored = []
    for _, row in catalog.iterrows():
        score = _similarity(query, row["service_name"])
        # Small boost when the NLP engine's predicted category agrees with the
        # catalog row's category — helps break ties between similarly-worded
        # services in different categories (e.g. "design" vs "web design").
        if predicted_category and str(row.get("category", "")).strip() == predicted_category:
            score = min(1.0, score + 0.08)
        scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return PriceMatch(query=query, matched=False, status="NEEDS_REVIEW", suggestions=[])

    best_score, best_row = scored[0]
    suggestions = [
        {
            "service_id": r["service_id"],
            "service_name": r["service_name"],
            "unit_price": float(r["unit_price_inr"]),
            "score": round(s, 2),
        }
        for s, r in scored[:top_n]
    ]

    if best_score >= CONFIDENT_THRESHOLD:
        return PriceMatch(
            query=query,
            matched=True,
            service_id=best_row["service_id"],
            service_name=best_row["service_name"],
            category=best_row["category"],
            unit=best_row["unit"],
            unit_price=float(best_row["unit_price_inr"]),
            tax_rate=float(best_row["tax_rate_percent"]),
            confidence=round(best_score, 2),
            status="MATCHED",
            suggestions=suggestions,
        )
    elif best_score >= POSSIBLE_THRESHOLD:
        return PriceMatch(
            query=query,
            matched=False,
            confidence=round(best_score, 2),
            status="SUGGESTED",
            suggestions=suggestions,
        )
    else:
        return PriceMatch(query=query, matched=False, confidence=round(best_score, 2),
                           status="NEEDS_REVIEW", suggestions=suggestions)


def resolve_all(queries: List[str], catalog: pd.DataFrame,
                 categories: Optional[List[Optional[str]]] = None) -> List[PriceMatch]:
    if categories is None:
        categories = [None] * len(queries)
    return [match_service(q, catalog, predicted_category=c) for q, c in zip(queries, categories)]
