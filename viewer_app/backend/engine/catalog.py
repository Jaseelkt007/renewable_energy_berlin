"""
catalog.py — SKU catalog loader.

Loads the closed set of valid component_name values from the historical parts CSV.
Used to: (a) constrain LLM output, (b) validate Pydantic responses,
(c) build a compact catalog representation for the prompt.

290 unique SKUs in the corpus (option_number=1 only).
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PARTS_CSV = _REPO_ROOT / "project_options_parts.csv"


@lru_cache(maxsize=1)
def _load() -> dict:
    """One-time load: returns dict with sku_set, category_map, tech_map, popularity."""
    pp = pd.read_csv(_PARTS_CSV)
    opt1 = pp[pp["option_number"] == 1].copy()
    opt1 = opt1.dropna(subset=["component_name"])

    total_projects = opt1["project_id"].nunique()

    # Most-common category and technology per SKU (modes)
    cat_map = (
        opt1.groupby("component_name")["component_type"]
        .agg(lambda s: s.mode().iat[0] if len(s.mode()) else "Other")
        .to_dict()
    )
    tech_map = (
        opt1.groupby("component_name")["technology"]
        .agg(lambda s: s.mode().iat[0] if len(s.mode()) else "solar")
        .to_dict()
    )
    popularity = (
        opt1.groupby("component_name")["project_id"].nunique() / total_projects
    ).to_dict()

    sku_names = sorted(cat_map.keys())
    return {
        "sku_set": set(sku_names),
        "sku_names": sku_names,
        "category_map": cat_map,
        "tech_map": tech_map,
        "popularity": popularity,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_valid_sku(name: str) -> bool:
    return name in _load()["sku_set"]


def category_for(name: str) -> str:
    return _load()["category_map"].get(name, "Other")


def technology_for(name: str) -> str:
    return _load()["tech_map"].get(name, "solar")


def popularity_for(name: str) -> float:
    """Fraction of projects (0..1) that include this SKU."""
    return _load()["popularity"].get(name, 0.0)


def all_sku_names() -> list[str]:
    return _load()["sku_names"]


def catalog_for_prompt(min_popularity: float = 0.0) -> str:
    """
    Compact representation of the catalog for the LLM prompt.
    Grouped by technology, sorted by popularity.
    """
    data = _load()
    by_tech: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for name in data["sku_names"]:
        if data["popularity"][name] < min_popularity:
            continue
        tech = data["tech_map"][name]
        by_tech[tech].append((data["popularity"][name], name))

    lines = []
    for tech in sorted(by_tech.keys()):
        items = sorted(by_tech[tech], key=lambda t: -t[0])
        lines.append(f"\n[{tech.upper()}]")
        for pop, name in items:
            lines.append(f"  {name}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(f"Total SKUs: {len(all_sku_names())}")
    print(f"\nSample valid: {is_valid_sku('Battery LFP 10kWh')}")
    print(f"Sample invalid: {is_valid_sku('Made-Up Battery 99kWh')}")
    print(f"\nCategory for 'Battery LFP 10kWh': {category_for('Battery LFP 10kWh')}")
    print(f"Popularity of 'Install Battery Storage': {popularity_for('Install Battery Storage'):.1%}")
    print("\n--- First 30 lines of catalog_for_prompt ---")
    print("\n".join(catalog_for_prompt().split("\n")[:30]))
