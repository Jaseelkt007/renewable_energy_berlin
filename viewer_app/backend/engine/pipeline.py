"""
pipeline.py — three-layer engine orchestration.

Single function (design_system) that runs:
  Layer A: bom_generator.py rules (deterministic baseline + fallback)
  Layer B: kNN retrieval over historical projects (evidence)
  Layer C: Gemini designer with constrained JSON output

Returns a unified dict ready for JSON serialization to the frontend.

Includes in-memory caching keyed on (profile, max_panels, mode, overrides).
Critical for the refinement UX: sliders shouldn't trigger 3 LLM calls per second.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

# Allow `python -m viewer_app.backend.engine.pipeline` from repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from bom_generator import generate_bill_of_materials  # noqa: E402

from .economics import compute_economics  # noqa: E402
from .knn import get_corpus_stats, get_similar_projects  # noqa: E402
from .llm import BomResponse, MODEL_DESIGN, MODEL_REFINE, call_llm  # noqa: E402

log = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Confidence threshold — tuned during smoke testing.
# kNN distances on our 11-d feature space typically range 0.3 (very similar)
# to 3.0 (very different). 1.5 is the empirical "still relevant" boundary.
# ---------------------------------------------------------------------------
HIGH_CONFIDENCE_DIST = 1.0
MEDIUM_CONFIDENCE_DIST = 1.8


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------
_cache: dict[str, dict] = {}
_CACHE_MAX = 256


def _cache_key(profile: dict, max_panels: int, mode: str, overrides: Optional[dict]) -> str:
    payload = json.dumps(
        [profile, max_panels, mode, overrides or {}],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _confidence_label(distance: float) -> str:
    if distance <= HIGH_CONFIDENCE_DIST:
        return "high"
    if distance <= MEDIUM_CONFIDENCE_DIST:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def design_system(
    profile: dict,
    max_panels: int,
    mode: str = "balanced",
    overrides: Optional[dict] = None,
    use_refine_model: bool = False,
) -> dict:
    """
    Produce a complete BoM design for one customer + roof + mode.

    Args:
        profile: customer profile dict (keys: energy_demand_wh, has_ev, etc.)
        max_panels: roof capacity. 0 means PV is unavailable (heritage building).
        mode: "budget" | "balanced" | "premium"
        overrides: optional dict of refinement constraints
                   {battery_kwh, include_hp, include_wallbox, include_surge}
        use_refine_model: if True, use cheaper/faster gemini-3.1-flash-lite
                          (recommended for refinement re-runs)

    Returns:
        {
          "bom": [...],
          "system_summary": {...},
          "confidence": "high" | "medium" | "low",
          "neighbors_used": int,
          "mode": str,
          "notes": str,
          "cached": bool
        }
    """
    key = _cache_key(profile, max_panels, mode, overrides)
    if key in _cache:
        cached = dict(_cache[key])
        cached["cached"] = True
        log.info("Cache HIT for mode=%s", mode)
        return cached

    # ── Layer A: rules (always run; serves as both baseline and fallback) ──
    try:
        rule_bom = generate_bill_of_materials(profile, max_panels) if max_panels > 0 else []
    except Exception as e:
        log.warning("Rule engine failed: %s", e)
        rule_bom = []

    # ── Layer B: kNN (with no-PV archetype fallback when max_panels == 0) ──
    try:
        neighbors = get_similar_projects(
            profile, k=5, filter_mode=mode, no_pv=(max_panels == 0)
        )
        first_dist = neighbors[0]["distance"] if neighbors else 99.0
        # Archetypes have artificial distance 0; treat as "high" confidence
        confidence = _confidence_label(first_dist)
    except Exception as e:
        log.warning("kNN failed: %s", e)
        neighbors = []
        confidence = "low"

    # ── Layer C: Gemini designer (with rule fallback inside call_llm) ──
    model = MODEL_REFINE if use_refine_model else MODEL_DESIGN
    response: BomResponse = call_llm(
        profile=profile,
        max_panels=max_panels,
        mode=mode,
        neighbors=neighbors,
        rule_bom=rule_bom,
        overrides=overrides,
        model=model,
    )

    bom_dump = [b.model_dump() for b in response.bom]
    summary_dump = response.system_summary.model_dump()
    economics = compute_economics(bom_dump, summary_dump, profile)

    # Promote the priced BoM (with implicit PV module + inverter rows
    # prepended, and unit_price_eur / line_total_eur / cost_type / price_source
    # / price_confidence / is_implicit attached to every line) out of the
    # economics dict and into the top-level response. The frontend renders
    # these prices verbatim — no client-side pricer.
    priced_bom = economics.pop("priced_bom")

    result = {
        "bom": priced_bom,
        "system_summary": summary_dump,
        "economics": economics,
        "confidence": confidence,
        "neighbors_used": len(neighbors),
        "mode": mode,
        "notes": response.notes,
        "cached": False,
    }

    # ── Bounded LRU-ish cache (drop oldest by insertion order if over capacity) ──
    if len(_cache) >= _CACHE_MAX:
        oldest_key = next(iter(_cache))
        _cache.pop(oldest_key, None)
    _cache[key] = result

    return result


def cache_stats() -> dict:
    return {"size": len(_cache), "max": _CACHE_MAX}


def clear_cache() -> int:
    n = len(_cache)
    _cache.clear()
    return n


def corpus_stats() -> dict:
    return get_corpus_stats()


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time

    profile = {
        "energy_demand_wh": 6_500_000,
        "has_ev": True,
        "heating_existing_type": "Gas",
        "heating_existing_heating_demand_wh": 18_000_000,
    }

    print("Corpus:", json.dumps(corpus_stats(), indent=2))

    print("\n--- Cold call (Balanced) ---")
    t0 = time.time()
    r1 = design_system(profile, max_panels=25, mode="balanced")
    print(f"  Time: {time.time() - t0:.1f}s | cached={r1['cached']} | "
          f"confidence={r1['confidence']} | lines={len(r1['bom'])} | "
          f"PV={r1['system_summary']['pv_kwp']}kWp")

    print("\n--- Cache hit (same call) ---")
    t0 = time.time()
    r2 = design_system(profile, max_panels=25, mode="balanced")
    print(f"  Time: {(time.time() - t0) * 1000:.1f}ms | cached={r2['cached']}")

    print("\n--- Different mode (Budget) ---")
    t0 = time.time()
    r3 = design_system(profile, max_panels=25, mode="budget")
    print(f"  Time: {time.time() - t0:.1f}s | cached={r3['cached']} | "
          f"lines={len(r3['bom'])} | "
          f"battery={r3['system_summary']['battery_kwh']}kWh")
