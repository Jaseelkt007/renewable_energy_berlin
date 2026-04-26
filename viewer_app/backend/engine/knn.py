"""
knn.py — kNN retrieval engine over historical Reonic projects.

Given a customer profile, returns the k most similar past projects with their
full Bill-of-Materials (option_number=1 only). Supports mode filtering for
Budget / Balanced / Premium variants.

Key data realities discovered during Phase 0 inspection:
- house_size_sqm, num_inhabitants are 99.6% NaN — UNUSABLE
- heating_existing_type is 88% NaN — fill with "Unknown"
- Only energy_demand_wh has 100% coverage as a continuous signal
- 858 projects have option-1 BoMs that intersect with status_quo

Module-level state is initialized lazily on first call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import OneHotEncoder, StandardScaler


# ---------------------------------------------------------------------------
# Paths — resolve from this file's location (works regardless of cwd)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATUS_QUO_CSV = _REPO_ROOT / "projects_status_quo.csv"
_PARTS_CSV = _REPO_ROOT / "project_options_parts.csv"


# ---------------------------------------------------------------------------
# Feature engineering choices (data-driven, see Phase 0 inspection)
# ---------------------------------------------------------------------------
CONTINUOUS_FEATURES = [
    "energy_demand_wh",                       # 100% coverage, log1p-transformed
    "heating_existing_heating_demand_wh",     # 12% coverage, impute 0
]
BOOL_FEATURES = [
    "has_ev",
    "has_solar",
    "has_storage",
    "has_wallbox",
]
CATEGORICAL_FEATURES = [
    "heating_existing_type",                  # fill NaN → "Unknown"
]

# Heating types observed in data (rest mapped to "Unknown"):
HEATING_TYPES_OBSERVED = ["Heatpump", "Gas", "Oil", "OtherNonRenewable", "Unknown"]


# ---------------------------------------------------------------------------
# Hardcoded NO-PV archetypes — used when max_panels == 0 (heritage, no roof).
# The historical corpus is dominated by PV projects, so kNN over the corpus
# returns PV-heavy neighbors even when the customer cannot install PV.
# These two archetypes ground the LLM in plausible HP+battery-only quotes.
# ---------------------------------------------------------------------------
def _archetype_bom(parts: list[tuple[str, str, str, float]]) -> list[dict]:
    """Helper: build a BoM list-of-dicts matching the corpus schema."""
    return [
        {
            "project_id": "_no_pv_archetype",
            "technology": tech,
            "line_item_function": "default",
            "component_type": cat,
            "component_name": name,
            "component_brand": "",
            "quantity": qty,
            "quantity_units": "pcs",
            "module_watt_peak": None,
            "inverter_power_kw": None,
            "battery_capacity_kwh": None,
            "wb_charging_speed_kw": None,
            "heatpump_nominal_power_kw": None,
        }
        for tech, cat, name, qty in parts
    ]


NO_PV_ARCHETYPES: list[dict] = [
    {
        "project_id": "_no_pv_archetype_heritage_hp_battery",
        "distance": 0.0,
        "line_count": 9,
        "bom": _archetype_bom([
            ("heatpump", "Heatpump",            "Heat Pump 10.5kW 400V",            1.0),
            ("heatpump", "AccessoryToHeatpump", "Heat Pump Hydraulic Station",      1.0),
            ("heatpump", "AccessoryToHeatpump", "Smart Heating Controller",         1.0),
            ("heatpump", "WarmwaterStorage",    "Hot Water Storage 300L",           1.0),
            ("heatpump", "HeatingStorage",      "Buffer Storage 200L",              1.0),
            ("heatpump", "InstallationFee",     "Heat Pump Installation Compact B", 1.0),
            ("heatpump", "InstallationFee",     "Garden Work Small B",              1.0),
            ("ses",      "BatteryStorage",      "Battery LFP 10kWh",                1.0),
            ("ses",      "InstallationFee",     "Install Battery Storage",          1.0),
        ]),
    },
    {
        "project_id": "_no_pv_archetype_small_hp_only",
        "distance": 0.5,
        "line_count": 6,
        "bom": _archetype_bom([
            ("heatpump", "Heatpump",            "Heat Pump 5.5kW 230V",             1.0),
            ("heatpump", "AccessoryToHeatpump", "Heat Pump Hydraulic Station",      1.0),
            ("heatpump", "AccessoryToHeatpump", "Smart Heating Controller",         1.0),
            ("heatpump", "WarmwaterStorage",    "Hot Water Storage 250L",           1.0),
            ("heatpump", "InstallationFee",     "Heat Pump Installation Compact B", 1.0),
            ("heatpump", "InstallationFee",     "Garden Work Small B",              1.0),
        ]),
    },
]


# ---------------------------------------------------------------------------
# Module-level state — initialized once on first call to get_similar_projects
# ---------------------------------------------------------------------------
_state: dict[str, Any] = {"initialized": False}


def _load_and_index() -> None:
    """One-time load of CSVs, fit preprocessor, build kNN index, group BoMs."""
    if _state["initialized"]:
        return

    # Load status_quo (customer profiles)
    sq = pd.read_csv(_STATUS_QUO_CSV)

    # Load parts; filter to option_number == 1 (the historically-chosen variant)
    parts = pd.read_csv(_PARTS_CSV)
    opt1 = parts[parts["option_number"] == 1].copy()

    # Group parts by project_id → list of dicts
    bom_lookup: dict[str, list[dict]] = {}
    for pid, group in opt1.groupby("project_id"):
        bom_lookup[pid] = group.drop(columns=["option_id", "option_number"]).to_dict("records")

    # Keep only projects that have a BoM (intersection)
    valid_pids = set(sq["project_id"]) & set(bom_lookup.keys())
    sq = sq[sq["project_id"].isin(valid_pids)].reset_index(drop=True)

    # Drop rows with energy_demand_wh missing or 0
    sq = sq[(sq["energy_demand_wh"].notna()) & (sq["energy_demand_wh"] > 0)].reset_index(drop=True)

    # Build feature matrix
    X_cont, X_bool, X_cat = _build_feature_blocks(sq)

    scaler = StandardScaler()
    X_cont_scaled = scaler.fit_transform(X_cont)

    ohe = OneHotEncoder(
        categories=[HEATING_TYPES_OBSERVED],
        handle_unknown="ignore",
        sparse_output=False,
    )
    X_cat_encoded = ohe.fit_transform(X_cat)

    X = np.hstack([X_cont_scaled, X_bool.astype(float), X_cat_encoded])

    # Fit kNN index — over-fetch so post-filtering by mode still yields k results
    nn = NearestNeighbors(n_neighbors=min(50, len(sq)), metric="euclidean")
    nn.fit(X)

    # Per-project line counts (for mode filtering)
    line_counts = {pid: len(bom_lookup[pid]) for pid in sq["project_id"]}
    counts_sorted = sorted(line_counts.values())
    p30 = counts_sorted[int(len(counts_sorted) * 0.30)]
    p70 = counts_sorted[int(len(counts_sorted) * 0.70)]

    _state.update({
        "initialized": True,
        "sq": sq,
        "scaler": scaler,
        "ohe": ohe,
        "nn": nn,
        "X": X,
        "bom_lookup": bom_lookup,
        "line_counts": line_counts,
        "p30_count": p30,
        "p70_count": p70,
        "project_ids": sq["project_id"].tolist(),
    })


def _build_feature_blocks(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Extract continuous (log1p), boolean, and categorical feature matrices."""
    cont = df[CONTINUOUS_FEATURES].copy()
    cont["heating_existing_heating_demand_wh"] = cont["heating_existing_heating_demand_wh"].fillna(0)
    cont = np.log1p(cont.to_numpy(dtype=float))

    bool_arr = df[BOOL_FEATURES].fillna(False).astype(bool).to_numpy()

    cat = df[CATEGORICAL_FEATURES].fillna("Unknown")
    # Map any unobserved values to "Unknown" so OHE doesn't drop them silently
    cat = cat.map(lambda v: v if v in HEATING_TYPES_OBSERVED else "Unknown")
    cat_arr = cat.to_numpy()

    return cont, bool_arr, cat_arr


def _profile_to_vector(profile: dict) -> np.ndarray:
    """Transform a customer profile into the feature vector used by the kNN index."""
    row = {
        "energy_demand_wh": profile.get("energy_demand_wh") or 4_500_000,
        "heating_existing_heating_demand_wh": profile.get("heating_existing_heating_demand_wh") or 0,
        "has_ev": bool(profile.get("has_ev", False)),
        "has_solar": bool(profile.get("has_solar", False)),
        "has_storage": bool(profile.get("has_storage", False)),
        "has_wallbox": bool(profile.get("has_wallbox", False)),
        "heating_existing_type": profile.get("heating_existing_type") or "Unknown",
    }
    df = pd.DataFrame([row])
    cont, bool_arr, cat_arr = _build_feature_blocks(df)
    cont_scaled = _state["scaler"].transform(cont)
    cat_encoded = _state["ohe"].transform(cat_arr)
    return np.hstack([cont_scaled, bool_arr.astype(float), cat_encoded])


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def get_similar_projects(
    profile: dict,
    k: int = 5,
    filter_mode: str = "balanced",
    no_pv: bool = False,
) -> list[dict]:
    """
    Return the k most similar historical projects with their full BoMs.

    Args:
        profile: customer profile dict. Required: energy_demand_wh.
                 Optional: has_ev, has_solar, has_storage, has_wallbox,
                 heating_existing_type, heating_existing_heating_demand_wh.
        k: number of neighbors to return.
        filter_mode: one of "budget" | "balanced" | "premium".
            - budget:   only return neighbors with bottom-30% line count
            - balanced: no filter (use raw nearest)
            - premium:  only return neighbors with top-30% line count
        no_pv: if True, return hardcoded HP+battery archetypes instead of
               kNN over the (PV-dominated) corpus. Use when max_panels == 0.

    Returns:
        List of dicts: {project_id, distance, line_count, bom}.
    """
    if no_pv:
        return NO_PV_ARCHETYPES[:k]

    _load_and_index()

    q = _profile_to_vector(profile)
    distances, indices = _state["nn"].kneighbors(q, n_neighbors=min(50, len(_state["sq"])))
    distances, indices = distances[0], indices[0]

    p30 = _state["p30_count"]
    p70 = _state["p70_count"]
    line_counts = _state["line_counts"]
    project_ids = _state["project_ids"]
    bom_lookup = _state["bom_lookup"]

    results = []
    for idx, dist in zip(indices, distances):
        pid = project_ids[idx]
        lc = line_counts[pid]

        if filter_mode == "budget" and lc > p30:
            continue
        if filter_mode == "premium" and lc < p70:
            continue

        results.append({
            "project_id": pid,
            "distance": float(dist),
            "line_count": lc,
            "bom": bom_lookup[pid],
        })
        if len(results) >= k:
            break

    return results


def get_corpus_stats() -> dict:
    """Diagnostic — useful for the demo to say 'grounded in N projects'."""
    _load_and_index()
    return {
        "n_projects": len(_state["sq"]),
        "n_features": _state["X"].shape[1],
        "median_bom_size": int(np.median(list(_state["line_counts"].values()))),
        "p30_threshold": _state["p30_count"],
        "p70_threshold": _state["p70_count"],
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("Corpus stats:", json.dumps(get_corpus_stats(), indent=2))

    # Mid-range test profile from PHASE_PLAN.md §3.5
    test_profile = {
        "energy_demand_wh": 6_500_000,
        "has_ev": True,
        "heating_existing_type": "Gas",
        "heating_existing_heating_demand_wh": 18_000_000,
    }

    for mode in ["budget", "balanced", "premium"]:
        print(f"\n{'=' * 80}\nMode: {mode}\n{'=' * 80}")
        neighbors = get_similar_projects(test_profile, k=5, filter_mode=mode)
        for i, n in enumerate(neighbors, 1):
            sample_parts = [p["component_name"] for p in n["bom"][:5]]
            print(f"  {i}. project={n['project_id'][:12]}…  "
                  f"dist={n['distance']:.3f}  lines={n['line_count']:>2}  "
                  f"sample={sample_parts}")
