"""
tier_diff.py — compute Compare-modal-ready deltas between Budget/Balanced/Premium.

Given three priced BoMs (already produced by `compute_economics`), classify
every line in the higher tier as either:

  - 'add'      — SKU not present in lower tier, no same-category counterpart
  - 'upgrade'  — same-category SKU exists in lower tier with a different name
                 (e.g. 'Battery 5kWh' → 'Battery LFP 15kWh')

The frontend's Compare card uses the result to render
"Premium adds: …" + "↑ Battery: 7 kWh → 15 kWh" lists, so the customer
can see exactly *what* they're paying more for — not just the totals.

Implicit rows ('PV Module 450Wp', 'Inverter NkW') are skipped: the customer
already sees the kWp / panel-count delta in the Compare card's system_summary
section, so listing them again here is noise.
"""

from __future__ import annotations

from typing import TypedDict


# Categories where two SKUs in the same category but different names are an
# UPGRADE (showing as "from → to") rather than two unrelated lines. These are
# the categories where Reonic's installers historically pick exactly one SKU.
UPGRADE_CATEGORIES = {
    "BatteryStorage",
    "Heatpump",
    "AccessoryToInverter",       # Inverter / EMS swaps
    "WarmwaterStorage",          # 250L → 300L
    "HeatingStorage",            # 100L → 200L buffer
}


class TierAdd(TypedDict):
    part_name: str
    category: str
    line_total_eur: int


class TierUpgrade(TypedDict):
    from_part: str
    to_part: str
    category: str
    delta_eur: int


class TierDiff(TypedDict):
    adds: list[TierAdd]
    upgrades: list[TierUpgrade]
    delta_eur: int      # higher.capex − lower.capex (signed; usually positive)


def _real_lines(bom: list[dict]) -> list[dict]:
    """Drop implicit rows; the customer sees them via system_summary."""
    return [line for line in bom if not line.get("is_implicit", False)]


def _diff_pair(lower_bom: list[dict], higher_bom: list[dict]) -> TierDiff:
    """
    Build the {adds, upgrades, delta_eur} delta from `lower` → `higher`.

    Algorithm:
      1. Drop implicit rows from both sides.
      2. For each upgrade-category, pair leftover SKUs by index — first
         lower-only SKU pairs with first higher-only SKU, etc.
      3. Anything left in higher that hasn't been paired is an 'add'.
      4. delta_eur is the difference in line-total sums (≈ capex delta excluding
         implicit panel/inverter cost, which is already shown in system_summary).
    """
    lower = _real_lines(lower_bom)
    higher = _real_lines(higher_bom)

    lower_names = {line["part_name"] for line in lower}
    higher_names = {line["part_name"] for line in higher}

    only_lower = [line for line in lower if line["part_name"] not in higher_names]
    only_higher = [line for line in higher if line["part_name"] not in lower_names]

    upgrades: list[TierUpgrade] = []
    consumed_higher_names: set[str] = set()

    for category in UPGRADE_CATEGORIES:
        lo_in_cat = [l for l in only_lower if l.get("category") == category]
        hi_in_cat = [
            l for l in only_higher
            if l.get("category") == category and l["part_name"] not in consumed_higher_names
        ]
        for lo, hi in zip(lo_in_cat, hi_in_cat):
            delta = float(hi.get("line_total_eur", 0)) - float(lo.get("line_total_eur", 0))
            upgrades.append({
                "from_part": lo["part_name"],
                "to_part": hi["part_name"],
                "category": category,
                "delta_eur": int(round(delta)),
            })
            consumed_higher_names.add(hi["part_name"])

    adds: list[TierAdd] = [
        {
            "part_name": line["part_name"],
            "category": line.get("category", "Other"),
            "line_total_eur": int(round(float(line.get("line_total_eur", 0)))),
        }
        for line in only_higher
        if line["part_name"] not in consumed_higher_names
    ]

    # Capex delta excluding implicit rows so the number matches what the
    # Compare card lists (the implicit panel / inverter delta lives in the
    # system_summary section of the card, not in the "Premium adds" block).
    higher_total = sum(float(l.get("line_total_eur", 0)) for l in higher)
    lower_total = sum(float(l.get("line_total_eur", 0)) for l in lower)
    delta_eur = int(round(higher_total - lower_total))

    return {"adds": adds, "upgrades": upgrades, "delta_eur": delta_eur}


def compute_tier_diffs(
    budget: dict,
    balanced: dict,
    premium: dict,
) -> dict:
    """
    Build the Compare-card deltas:
      balanced_vs_budget   — what Balanced adds over Budget
      premium_vs_balanced  — what Premium adds over Balanced
      premium_vs_budget    — convenience aggregate (used by some UIs)

    Each value follows the TierDiff shape above.
    """
    return {
        "balanced_vs_budget": _diff_pair(budget["bom"], balanced["bom"]),
        "premium_vs_balanced": _diff_pair(balanced["bom"], premium["bom"]),
        "premium_vs_budget": _diff_pair(budget["bom"], premium["bom"]),
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    budget = {"bom": [
        {"part_name": "Battery 5kWh", "category": "BatteryStorage", "line_total_eur": 2800, "is_implicit": False},
        {"part_name": "Heat Pump 8kW", "category": "Heatpump", "line_total_eur": 7600, "is_implicit": False},
        {"part_name": "Install Inverter", "category": "InstallationFee", "line_total_eur": 400, "is_implicit": False},
    ]}
    balanced = {"bom": [
        {"part_name": "Battery LFP 10kWh", "category": "BatteryStorage", "line_total_eur": 5200, "is_implicit": False},
        {"part_name": "Heat Pump 8kW", "category": "Heatpump", "line_total_eur": 7600, "is_implicit": False},
        {"part_name": "Install Inverter", "category": "InstallationFee", "line_total_eur": 400, "is_implicit": False},
        {"part_name": "AC Surge Protection", "category": "InstallationFee", "line_total_eur": 250, "is_implicit": False},
        {"part_name": "Selective Circuit Breaker (SLS)", "category": "InstallationFee", "line_total_eur": 180, "is_implicit": False},
    ]}
    premium = {"bom": [
        {"part_name": "Battery LFP 15kWh", "category": "BatteryStorage", "line_total_eur": 7500, "is_implicit": False},
        {"part_name": "Heat Pump 8kW", "category": "Heatpump", "line_total_eur": 7600, "is_implicit": False},
        {"part_name": "Install Inverter", "category": "InstallationFee", "line_total_eur": 400, "is_implicit": False},
        {"part_name": "AC Surge Protection", "category": "InstallationFee", "line_total_eur": 250, "is_implicit": False},
        {"part_name": "Selective Circuit Breaker (SLS)", "category": "InstallationFee", "line_total_eur": 180, "is_implicit": False},
        {"part_name": "Sub-Distribution Board", "category": "Other", "line_total_eur": 600, "is_implicit": False},
        {"part_name": "Smart Guard 63A", "category": "Other", "line_total_eur": 280, "is_implicit": False},
        {"part_name": "Energy Management System", "category": "AccessoryToInverter", "line_total_eur": 1100, "is_implicit": False},
        {"part_name": "Power Optimizer 600W", "category": "AccessoryToModule", "line_total_eur": 65 * 30, "is_implicit": False},
    ]}

    diffs = compute_tier_diffs(budget, balanced, premium)
    print(json.dumps(diffs, indent=2))
