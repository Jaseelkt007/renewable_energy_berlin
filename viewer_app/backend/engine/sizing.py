"""
sizing.py — demand-anchored target sizes for PV / battery / heat pump per mode.

Replaces the previous "fill the roof" behavior. Targets are computed in the
pipeline BEFORE the LLM call and passed in as part of the prompt, so the
LLM has explicit guidance instead of falling back to max_panels.

Rules of thumb are validated against:
- HTW Berlin Weniger (2014, updated 2024) — kWp/MWh-demand band 1.0–2.0
- HTW Berlin Stromspeicher-Inspektion 2024 — battery 1.0–1.5 kWh per kWp PV
- Reonic's own 580 historical option-1 projects:
    median kWp/MWh = 1.86  ·  median kWh-battery/kWp = 0.93
    floor of ~7 kWp even for low-demand customers (install fixed costs)

All three modes obey the floor; they differ only in the multiplier above it.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Mode multipliers — calibrated against HTW + Reonic data
# ---------------------------------------------------------------------------

# kWp per MWh of total annual electricity demand (incl. heat-pump load).
# Median real Reonic project sits at 1.86. We anchor:
# - Budget at 1.0 (cover demand exactly — fastest payback, smallest CapEx)
# - Balanced at 1.5 (HTW Berlin orthodox optimum, slightly below corpus median)
# - Premium at 2.0 (~90th percentile of Reonic's corpus, max self-sufficiency)
PV_KWP_PER_MWH = {
    "budget":   1.0,   # cover demand exactly; fastest payback
    "balanced": 1.5,   # HTW optimum; mainstream installer choice
    "premium":  2.0,   # max self-sufficiency; near-roof-fill for big roofs
}

# Minimum kWp regardless of demand — fixed install costs (scaffolding, planning,
# inverter) need at least this much PV to amortize sensibly. Mirrors Reonic's
# bottom-quintile customers who landed at ~7 kWp even for 2.7 MWh demand.
MIN_KWP_FLOOR = 7.0

# kWh battery per kWp PV (HTW recommends 1.0–1.5; Reonic median 0.93).
BATTERY_KWH_PER_KWP = {
    "budget":   0.6,   # ~5 kWh on a 9 kWp system
    "balanced": 0.9,   # ~10 kWh on a 11 kWp system (matches Reonic median)
    "premium":  1.2,   # ~15 kWh on a 12.5 kWp system (HTW upper band)
}

BATTERY_CATALOG_KWH = [5, 7, 10, 15]   # snap-to-tier list (matches PRICE_CATALOG)
HP_CATALOG_KW = [5.5, 7.5, 8.0, 10.5, 12.5]
PANEL_KWP = 0.45                        # 450 Wp default module

# Heat-pump sizing: full-load-hours method.
# A typical German single-family heating season runs ~2,000 h at design load.
# Round UP to next catalog tier — undersized HPs blow the warranty.
HP_FULL_LOAD_HOURS = 2000.0

# HP coefficient of performance — used to compute the HP's electrical demand
# so PV target can absorb it.
HP_COP_PLANNING = 3.3

# Heating types that imply a heat-pump retrofit.
FOSSIL_HEATING = {"Gas", "Oil", "OtherNonRenewable"}


# ---------------------------------------------------------------------------
# Target computers
# ---------------------------------------------------------------------------

def hp_implied_by_profile(profile: dict) -> bool:
    """True when the customer's existing heating is fossil → recommend HP."""
    heating = (profile.get("heating_existing_type") or "").strip()
    return heating in FOSSIL_HEATING


def target_hp_kw(heat_demand_kwh: float) -> float:
    """
    Pick the smallest HP catalog tier that covers the design load.
    Returns 0.0 if heat_demand_kwh <= 0 (no HP needed).
    """
    if heat_demand_kwh <= 0:
        return 0.0
    needed = heat_demand_kwh / HP_FULL_LOAD_HOURS
    for tier in HP_CATALOG_KW:
        if tier >= needed:
            return tier
    return HP_CATALOG_KW[-1]   # cap at the largest catalog tier


def target_kwp(
    base_demand_kwh: float,
    heat_demand_kwh: float,
    mode: str,
    has_hp: bool,
) -> float:
    """
    Demand-anchored PV target with a 7 kWp floor for fixed-cost amortization.

    When an HP will be installed, the HP's electrical load (heat_demand_kwh / COP)
    is added to the base electricity demand so the PV is sized to absorb it.
    """
    if mode not in PV_KWP_PER_MWH:
        mode = "balanced"

    hp_electric_kwh = (heat_demand_kwh / HP_COP_PLANNING) if has_hp else 0.0
    total_demand_mwh = (base_demand_kwh + hp_electric_kwh) / 1000.0

    sized = total_demand_mwh * PV_KWP_PER_MWH[mode]
    return max(MIN_KWP_FLOOR, sized)


def target_panels(
    profile: dict,
    mode: str,
    max_panels: int,
) -> int:
    """
    Convert the kWp target to a panel count, capped at the roof maximum.
    Respects the no-PV case (max_panels == 0) by returning 0.
    """
    if max_panels <= 0:
        return 0
    base_kwh = float(profile.get("energy_demand_wh") or 0) / 1000.0
    heat_kwh = float(profile.get("heating_existing_heating_demand_wh") or 0) / 1000.0
    has_hp = hp_implied_by_profile(profile)
    kwp = target_kwp(base_kwh, heat_kwh, mode, has_hp)
    panels = math.ceil(kwp / PANEL_KWP)
    return max(1, min(panels, max_panels))


def target_battery_kwh(kwp: float, mode: str) -> int:
    """
    Battery size snapped to the closest catalog tier (5 / 7 / 10 / 15 kWh).
    Tied to kWp via the HTW Berlin 1.0–1.5 kWh-per-kWp recommendation.
    """
    if kwp <= 0:
        return 0
    if mode not in BATTERY_KWH_PER_KWP:
        mode = "balanced"
    raw = kwp * BATTERY_KWH_PER_KWP[mode]
    return int(min(BATTERY_CATALOG_KWH, key=lambda c: abs(c - raw)))


# ---------------------------------------------------------------------------
# One-shot helper: compute everything the prompt needs
# ---------------------------------------------------------------------------

def compute_targets(profile: dict, mode: str, max_panels: int) -> dict:
    """
    Returns the full sizing target bundle for a given (profile, mode, max_panels).

    {
      "panels":       int,
      "kwp":          float,
      "battery_kwh":  int,
      "hp_kw":        float,    # 0.0 when fossil heating is absent
      "has_hp":       bool,
      "rule_summary": str,      # human-readable, used inside the prompt
    }
    """
    base_kwh = float(profile.get("energy_demand_wh") or 0) / 1000.0
    heat_kwh = float(profile.get("heating_existing_heating_demand_wh") or 0) / 1000.0
    has_hp = hp_implied_by_profile(profile)

    panels = target_panels(profile, mode, max_panels)
    kwp = round(panels * PANEL_KWP, 2)
    battery = target_battery_kwh(kwp, mode) if max_panels > 0 else 0
    hp = target_hp_kw(heat_kwh) if has_hp else 0.0

    multiplier = PV_KWP_PER_MWH.get(mode, PV_KWP_PER_MWH["balanced"])
    rule_summary = (
        f"PV sized to ~{multiplier:.1f}× annual demand "
        f"(base {base_kwh:.0f} kWh + HP load {heat_kwh / HP_COP_PLANNING:.0f} kWh) "
        f"= {kwp:.1f} kWp / {panels} panels (floor {MIN_KWP_FLOOR:.0f} kWp; "
        f"roof cap {max_panels}). "
        f"Battery sized at {BATTERY_KWH_PER_KWP.get(mode, 0.9):.1f} kWh/kWp = {battery} kWh."
    )

    return {
        "panels": panels,
        "kwp": kwp,
        "battery_kwh": battery,
        "hp_kw": hp,
        "has_hp": has_hp,
        "rule_summary": rule_summary,
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    profiles = [
        ("4500 kWh / Gas / 22 MWh-heat / 33-panel roof",
         {"energy_demand_wh": 4_500_000, "heating_existing_type": "Gas",
          "heating_existing_heating_demand_wh": 22_000_000}, 33),
        ("9000 kWh / Oil / 28 MWh-heat / 50-panel roof",
         {"energy_demand_wh": 9_000_000, "heating_existing_type": "Oil",
          "heating_existing_heating_demand_wh": 28_000_000}, 50),
        ("3000 kWh / no heating / 30-panel roof",
         {"energy_demand_wh": 3_000_000, "heating_existing_type": "",
          "heating_existing_heating_demand_wh": 0}, 30),
        ("Heritage / max_panels=0",
         {"energy_demand_wh": 5_000_000, "heating_existing_type": "Gas",
          "heating_existing_heating_demand_wh": 22_000_000}, 0),
    ]

    for label, profile, mp in profiles:
        print("─" * 72)
        print(label)
        print("─" * 72)
        for mode in ("budget", "balanced", "premium"):
            t = compute_targets(profile, mode, mp)
            print(f"  {mode:8s}: panels={t['panels']:>3}  kWp={t['kwp']:>5.1f}  "
                  f"bat={t['battery_kwh']:>2}kWh  hp={t['hp_kw']:>4.1f}kW  has_hp={t['has_hp']}")
        print()
