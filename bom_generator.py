"""
bom_generator.py — Strict Historical Mapping Engine (v3)
========================================================
Reonic Track / Big Hack Berlin

Generates a Bill of Materials (BoM) that STRICTLY mirrors real Reonic quotes.
Every part name, sizing rule, and inclusion decision is backed by frequency
analysis of the historical project_options_parts dataset (~10.7k option-1 rows,
1062 projects cross-referenced against projects_status_quo).

Zero hallucination tolerance: every string literal was verified against the
component_name column of the source CSVs.

v3 KEY FIXES (data-driven corrections):
    - REMOVED explicit Module row (only 1.9% of projects have it — PV hardware
      is bundled in Complete Packages or implicit)
    - REMOVED explicit Inverter hardware row (only 2.0% of projects)
    - REMOVED Energy Manager B from default (only 15.1%)
    - REMOVED APZ Field (8.4%), Sub-Distribution Board (10.6%),
      Equipotential Bonding (10.6%) from default — all below 20%
    - REMOVED Grid Registration (12.1%), Delivery to Site (11.9%),
      Site Setup / Safety (11.5%) — all below 20%
    - FIXED wallbox: only when has_ev=True (32.3% of EV owners get wallbox,
      vs 17% of non-EV — was incorrectly universal before)
    - FIXED battery catalog: added "Battery 7kWh" (11% prevalence, was missing)
      and "Battery 5kWh" (6.8%, different from "Battery LFP 5kWh")
    - Result: ~9-13 items per quote (median in real data: 11)

Methodology:
    - "Core" items (>40% among full-installation projects): always included
    - "Common" items (20-40%): included in the standard quote
    - "Conditional" items: included only when triggered by profile flags
    - Items below 20% prevalence are EXCLUDED from the default set

Usage:
    from bom_generator import generate_bill_of_materials
    bom = generate_bill_of_materials(customer_profile, max_roof_panels=30)
"""

from __future__ import annotations

import math
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════
# CATALOG — Every string is VERBATIM from project_options_parts.csv
# ═══════════════════════════════════════════════════════════════════════════

# ---------------------------------------------------------------------------
# PV Module spec (used for sizing calculations only — NOT emitted as a line
# item, because only 1.9% of projects have explicit Module rows).
# ---------------------------------------------------------------------------
PV_MODULE_WP = 450       # modal module_watt_peak in the data

# ---------------------------------------------------------------------------
# Batteries — Top SKUs by demand bracket.
#
# Revised cross-reference including "Battery 7kWh" (11%, was missing in v2)
# and "Battery 5kWh" (6.8%, a distinct SKU from "Battery LFP 5kWh").
#
# Demand brackets (median demand per SKU):
#   Battery 5kWh          : n=72,  median demand 3800 kWh
#   Battery 7kWh          : n=117, median demand 4500 kWh  ← most common mid-tier
#   Battery LFP 10kWh     : n=157, median demand 5000 kWh  ← most common overall
#   Battery LFP 15kWh     : n=35,  median demand 5000 kWh
# ---------------------------------------------------------------------------
BATTERY_CATALOG = {
    # kWh → (exact component_name, battery_capacity_kwh_value)
    5:  ("Battery 5kWh",         5000),     # 72 occurrences, median demand 3800
    7:  ("Battery 7kWh",         7000),     # 117 occurrences, median demand 4500
    10: ("Battery LFP 10kWh",   10000),     # 157 occurrences (most popular overall)
    15: ("Battery LFP 15kWh",   15000),     # 35 occurrences
}

# ---------------------------------------------------------------------------
# Wallbox — "Wallbox" is the most common generic name (95 occurrences, 8.9%)
# among the ~15 wallbox hardware SKUs.
# ---------------------------------------------------------------------------
WALLBOX_NAME    = "Wallbox"
WALLBOX_BRAND   = ""
WALLBOX_SPEED_W = 11000

# ---------------------------------------------------------------------------
# Heat Pumps — all Vaillant brand.  heatpump_nominal_power_kw in Watts.
# Only 8 explicit Heatpump rows exist across all option-1 data.
# These are ONLY quoted when heating_existing_type is fossil (Gas/Oil/Other).
# ---------------------------------------------------------------------------
HEATPUMP_CATALOG = [
    # (rated_kw, exact_name, nominal_watts)
    (5.5,  "Heat Pump 5.5kW 230V",  5000),     # 2 occurrences
    (7.5,  "Heat Pump 7.5kW 230V",  7000),     # 1 occurrence
    (10.5, "Heat Pump 10.5kW 400V", 10000),    # 3 occurrences (most common)
    (12.5, "Heat Pump 12.5kW 400V", 12000),    # 1 occurrence
]
HEATPUMP_BRAND = "Vaillant"

# ---------------------------------------------------------------------------
# Roof-type dependent substructure & DC-install SKUs
#
# IMPORTANT: "Concrete Tile" ≠ "Concrete" and "Clay Tile" ≠ "Clay".
# These are separate roof types with different SKU names in the data.
# Quantities always scale 1:1 with panel count.
#
# DC Install for "concrete" and "clay" (non-tile) has angle variants in the
# data (<30°, >30°, no suffix).  We use the most common variant.
# ---------------------------------------------------------------------------
ROOF_TYPE_MAP = {
    # roof_type → (substructure_name, dc_install_name)
    "concrete_tile": ("Substructure Concrete Tile Roof", "DC Install Concrete Tile Roof"),   # 220 / 231
    "clay_tile":     ("Substructure Clay Tile Roof",     "DC Install Clay Tile Roof"),        # 123 / 124
    "concrete":      ("Substructure Concrete Roof",      "DC Install Concrete Roof"),         # 108 / 40
    "clay":          ("Substructure Clay Roof",           "DC Install Clay Roof"),             # 53 / 24
    "flat":          ("Substructure Flat Roof",           "DC Install Flat Roof"),             # 47 / 65
    "metal":         ("Substructure Metal Roof",          "DC Install Metal Roof"),            # 21 / 25
}
DEFAULT_ROOF_TYPE = "concrete_tile"     # most common (220 of 1262 substructure rows)


# ═══════════════════════════════════════════════════════════════════════════
# SIZING LOGIC
# ═══════════════════════════════════════════════════════════════════════════

def size_pv(annual_demand_kwh: float, max_panels: int) -> tuple[int, float]:
    """
    Return (panel_count, system_kwp) capped by the roof constraint.
    Ratio: ~1.8 kWp per MWh of annual demand (median from historical data).
    """
    target_kwp = 1.8 * (annual_demand_kwh / 1000.0)
    target_panels = max(1, round(target_kwp * 1000 / PV_MODULE_WP))
    panels = min(target_panels, max_panels)
    kwp = round(panels * PV_MODULE_WP / 1000.0, 2)
    return panels, kwp


def size_inverter(pv_kwp: float) -> int:
    """
    Select inverter kW from the REAL sizing rule observed in the data.

    Historical pattern (16 cross-referenced projects):
        PV ≤ 8.8 kWp  → 5 kW   (DC-coupled, inverter < array)
        PV 8.9–15 kWp → 10 kW
        PV > 15 kWp   → 20 kW
    """
    if pv_kwp <= 8.8:
        return 5
    elif pv_kwp <= 15.0:
        return 10
    else:
        return 20


def size_battery(annual_demand_kwh: float) -> int:
    """
    Select battery kWh from the demand-correlated bracket.

    Revised brackets using ALL top battery SKUs (including Battery 7kWh
    which was missing in v2):
        ≤ 3500 kWh  → 5 kWh   (Battery 5kWh,      median demand 3800)
        3501–5000    → 7 kWh   (Battery 7kWh,       median demand 4500)
        5001–7000    → 10 kWh  (Battery LFP 10kWh,  median demand 5000)
        > 7000       → 15 kWh  (Battery LFP 15kWh,  median demand 5000)
    """
    if annual_demand_kwh <= 3500:
        return 5
    elif annual_demand_kwh <= 5000:
        return 7
    elif annual_demand_kwh <= 7000:
        return 10
    else:
        return 15


def size_heatpump(profile: dict) -> Optional[tuple[float, str, int]]:
    """
    Return (rated_kw, exact_name, nominal_watts) if warranted, else None.

    Triggered ONLY for fossil heating: Gas, Oil, OtherNonRenewable.
    (100% of the 8 heat-pump rows in option-1 data are on fossil projects.)
    """
    heating_type = (profile.get("heating_existing_type") or "").strip()
    if heating_type.lower() not in {"gas", "oil", "othernonrenewable"}:
        return None

    heat_demand_wh = profile.get("heating_existing_heating_demand_wh")
    if heat_demand_wh and heat_demand_wh > 0:
        kw_peak = (heat_demand_wh / 1000.0) / 2000.0
    else:
        sqm = profile.get("house_size_sqm") or 140
        kw_peak = sqm * 0.05

    for rated_kw, name, watts in HEATPUMP_CATALOG:
        if rated_kw >= kw_peak:
            return rated_kw, name, watts
    return HEATPUMP_CATALOG[-1]


# ═══════════════════════════════════════════════════════════════════════════
# BOM LINE ITEM BUILDER
# ═══════════════════════════════════════════════════════════════════════════

def _line(
    category: str,
    part_name: str,
    quantity: float,
    unit: str = "pcs",
    *,
    technology: str = "solar",
    line_item_function: str = "default",
    brand: str = "",
    module_watt_peak: Optional[float] = None,
    inverter_power_kw: Optional[float] = None,
    wb_charging_speed_kw: Optional[float] = None,
    heatpump_nominal_power_kw: Optional[float] = None,
) -> dict:
    """Build one BoM line item matching the historical schema."""
    row = {
        "category": category,
        "part_name": part_name,
        "quantity": quantity,
        "unit": unit,
        "technology": technology,
        "line_item_function": line_item_function,
        "brand": brand,
    }
    if module_watt_peak is not None:
        row["module_watt_peak"] = module_watt_peak
    if inverter_power_kw is not None:
        row["inverter_power_kw"] = inverter_power_kw
    if wb_charging_speed_kw is not None:
        row["wb_charging_speed_kw"] = wb_charging_speed_kw
    if heatpump_nominal_power_kw is not None:
        row["heatpump_nominal_power_kw"] = heatpump_nominal_power_kw
    return row


# ═══════════════════════════════════════════════════════════════════════════
# MAIN BOM GENERATOR
# ═══════════════════════════════════════════════════════════════════════════

def generate_bill_of_materials(
    customer_profile: dict,
    max_roof_panels: int,
    roof_type: str = DEFAULT_ROOF_TYPE,
) -> list[dict]:
    """
    Produce a historically-accurate BoM from a customer profile.

    The output mirrors the typical 10-14 line item structure observed in
    real Reonic quotes.  Items are only included when their prevalence
    among full-installation projects exceeds 20%, OR when explicitly
    triggered by a customer profile flag (e.g. has_ev → wallbox).

    NOTE: Explicit Module and Inverter hardware rows are NOT emitted.
    Only 1.9% and 2.0% of projects list these as separate line items;
    the hardware is typically bundled in Complete Packages or priced
    implicitly through the installation and service fees.

    Args:
        customer_profile: dict from projects_status_quo.
            Required: energy_demand_wh.
            Optional: has_ev, has_wallbox, heating_existing_type,
                      heating_existing_heating_demand_wh, house_size_sqm.
        max_roof_panels: hard cap from the Solar API / roof analysis.
        roof_type: key from ROOF_TYPE_MAP. Defaults to "concrete_tile".

    Returns:
        List of dicts with: category, part_name, quantity, unit, technology,
        line_item_function, brand, and relevant technical fields.
    """
    bom: list[dict] = []

    # ── Derive sizing from profile ──
    annual_kwh = (customer_profile.get("energy_demand_wh") or 0) / 1000.0
    panels, pv_kwp = size_pv(annual_kwh, max_roof_panels)
    inv_kw = size_inverter(pv_kwp)
    batt_kwh = size_battery(annual_kwh)
    hp = size_heatpump(customer_profile)

    # Wallbox: only when customer explicitly has an EV.
    # Data shows 32.3% of has_ev=True projects get wallbox items,
    # vs only 17% of has_ev=False.  The non-EV 17% are likely upsells
    # or future-proofing — we don't generate those by default.
    has_ev = customer_profile.get("has_ev")
    # Guard against string "False" from CSV reads
    if isinstance(has_ev, str):
        has_ev = has_ev.lower() in ("true", "1", "yes")
    else:
        has_ev = bool(has_ev)

    sub_name, dc_name = ROOF_TYPE_MAP.get(
        roof_type, ROOF_TYPE_MAP[DEFAULT_ROOF_TYPE]
    )

    # ==================================================================
    # ROOF INSTALLATION — Substructure + DC Install + Scaffolding
    # These are the qty-scaled items (qty = panel count).
    # Present in ~45-50% of all projects; ~100% co-occurrence among
    # full-installation projects.
    # ==================================================================

    # Substructure — roof-type dependent, qty = panel count
    # Evidence: 1262 total rows across all subtypes
    bom.append(_line("ModuleFrameConstruction", sub_name, panels))

    # DC Install — roof-type dependent, qty = panel count
    bom.append(_line("AccessoryToModule", dc_name, panels))

    # Scaffolding Setup & Removal — 45.5%, qty = panel count
    bom.append(_line("ModuleFrameConstruction", "Scaffolding Setup & Removal", panels))

    # ==================================================================
    # INSTALLATION FEES — Core items (>40% among scaffold-projects)
    # ==================================================================

    # Install Battery Storage — 56.7% overall, 94.2% among scaffold projects
    bom.append(_line("InstallationFee", "Install Battery Storage", 1))

    # Install Inverter — 50.9% overall, 91.7% among scaffold projects
    bom.append(_line("InstallationFee", "Install Inverter", 1))

    # Planning & Consulting — 54.4% overall, 99% among scaffold projects
    bom.append(_line("InstallationFee", "Planning & Consulting", 1))

    # Meter Cabinet Repair — 30.4% overall, 54.7% among scaffold projects
    bom.append(_line("InstallationFee", "Meter Cabinet Repair", 1))

    # AC Surge Protection — 24.4% overall, 46.2% among scaffold projects
    bom.append(_line("InstallationFee", "AC Surge Protection", 1))

    # ==================================================================
    # BATTERY — sized by demand bracket
    # ==================================================================

    batt_name, batt_cap = BATTERY_CATALOG[batt_kwh]
    bom.append(_line(
        "BatteryStorage", batt_name, 1,
        technology="ses",
        inverter_power_kw=batt_cap,
    ))

    # ==================================================================
    # SERVICE FEES — Core items
    # ==================================================================

    # Travel & Logistics Flat Rate — 54.4% overall, 98.3% among scaffold
    bom.append(_line("ServiceFee", "Travel & Logistics Flat Rate", 1))

    # All-Inclusive Package B — 39.4% overall, 68.9% among scaffold
    # (vs "All-Inclusive Package" at 15.6% — B is the current standard)
    bom.append(_line("ServiceFee", "All-Inclusive Package B", 1))

    # Optional Solar Credit — 29.5% overall, 51.8% among scaffold
    bom.append(_line("ServiceFee", "Optional Solar Credit", 1))

    # Selective Circuit Breaker (SLS) — 23.8% overall, 44.5% among scaffold
    bom.append(_line("Other", "Selective Circuit Breaker (SLS)", 1))

    # ==================================================================
    # WALLBOX — Only when has_ev is True
    # ==================================================================

    if has_ev:
        # Install Wallbox — 20.8% overall; most common wallbox fee
        bom.append(_line("InstallationFee", "Install Wallbox", 1, technology="wallbox"))

        # Wallbox hardware — "Wallbox" is the most common generic SKU (95 occ, 8.9%)
        bom.append(_line(
            "Wallbox", WALLBOX_NAME, 1,
            technology="wallbox",
            brand=WALLBOX_BRAND,
            wb_charging_speed_kw=WALLBOX_SPEED_W,
        ))

    # ==================================================================
    # HEATPUMP — Only when fossil heating (Gas/Oil/OtherNonRenewable)
    # ==================================================================

    if hp is not None:
        hp_kw, hp_name, hp_watts = hp

        bom.append(_line(
            "Heatpump", hp_name, 1,
            technology="heatpump", line_item_function="heatpump",
            brand=HEATPUMP_BRAND, heatpump_nominal_power_kw=hp_watts,
        ))

        # Heat Pump Hydraulic Station — 7 rows, co-occurs with every HP
        bom.append(_line(
            "AccessoryToHeatpump", "Heat Pump Hydraulic Station", 1,
            technology="heatpump", line_item_function="heatpump",
        ))

        # Smart Heating Controller — 7 rows, co-occurs with every HP
        bom.append(_line(
            "AccessoryToHeatpump", "Smart Heating Controller", 1,
            technology="heatpump", line_item_function="heatpump",
        ))

        # Hot Water Storage 300L — 4 rows (most common warm-water tank)
        bom.append(_line(
            "WarmwaterStorage", "Hot Water Storage 300L", 1,
            technology="heatpump", line_item_function="warmwaterStorage",
        ))

        # Buffer Storage 200L — 4 rows (most common heating buffer)
        bom.append(_line(
            "HeatingStorage", "Buffer Storage 200L", 1,
            technology="heatpump", line_item_function="heatingStorage",
        ))

        # Heat Pump Installation Compact B — 4 rows, standard HP install fee
        bom.append(_line(
            "InstallationFee", "Heat Pump Installation Compact B", 1,
            technology="heatpump", line_item_function="heatpump",
        ))

        # Garden Work Small B — 4 rows, co-occurs with HP install
        bom.append(_line(
            "InstallationFee", "Garden Work Small B", 1,
            technology="heatpump", line_item_function="heatpump",
        ))

    return bom


# ═══════════════════════════════════════════════════════════════════════════
# TEST HARNESS
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    # ── Test Case 1: Mid-range household with EV + Gas heating ──
    test1 = {
        "project_id": "test-ev-gas",
        "energy_demand_wh": 6_500_000,
        "has_ev": False,
        "heating_existing_type": "Gas",
        "heating_existing_heating_demand_wh": 18_000_000,
        "house_size_sqm": 160,
    }

    # ── Test Case 2: Small household, no EV, no heating ──
    test2 = {
        "project_id": "test-small",
        "energy_demand_wh": 2_500_000,
        "has_ev": False,
        "heating_existing_type": None,
    }

    # ── Test Case 3: Large household, EV, oil heating ──
    test3 = {
        "project_id": "test-large-oil",
        "energy_demand_wh": 12_000_000,
        "has_ev": True,
        "heating_existing_type": "Oil",
        "heating_existing_heating_demand_wh": 30_000_000,
        "house_size_sqm": 220,
    }

    for label, profile, roof_cap in [
        ("MID-RANGE (6500 kWh, EV, Gas)", test1, 30),
        ("SMALL (2500 kWh, no EV)",       test2, 20),
        ("LARGE (12000 kWh, EV, Oil)",    test3, 50),
    ]:
        annual = profile["energy_demand_wh"] / 1000.0
        panels, kwp = size_pv(annual, roof_cap)
        inv = size_inverter(kwp)
        batt = size_battery(annual)
        hp = size_heatpump(profile)

        print("=" * 105)
        print(f"  TEST: {label}")
        print("=" * 105)
        print(f"  Demand: {annual:.0f} kWh | Roof cap: {roof_cap} | "
              f"PV: {panels}p × {PV_MODULE_WP}Wp = {kwp} kWp | "
              f"Inv: {inv}kW | Batt: {batt}kWh | "
              f"HP: {hp[0]:.1f}kW" if hp else
              f"  Demand: {annual:.0f} kWh | Roof cap: {roof_cap} | "
              f"PV: {panels}p × {PV_MODULE_WP}Wp = {kwp} kWp | "
              f"Inv: {inv}kW | Batt: {batt}kWh | HP: None")

        bom = generate_bill_of_materials(profile, roof_cap)

        print(f"\n{'#':>3}  {'TECH':<10} {'CATEGORY':<32} {'QTY':>5}  PART NAME")
        print("-" * 105)
        for i, row in enumerate(bom, 1):
            print(f"{i:>3}  {row['technology']:<10} {row['category']:<32} "
                  f"{row['quantity']:>5}  {row['part_name']}")
        print("-" * 105)
        print(f"  Total line items: {len(bom)}\n")

    # ── CSV Validation ──
    print("=" * 105)
    print("  DATA VALIDATION")
    print("=" * 105)
    try:
        import pandas as pd
        import os
        for p1, p2 in [
            ("project_options_parts.csv", "project_options_1_parts.csv"),
        ]:
            if os.path.exists(p1):
                parts = pd.concat([pd.read_csv(p1), pd.read_csv(p2)], ignore_index=True)
                real_names = set(parts["component_name"].dropna().unique())
                # Collect all part names across all three test cases
                all_bom_names = set()
                for profile, cap in [(test1, 30), (test2, 20), (test3, 50)]:
                    for row in generate_bill_of_materials(profile, cap):
                        all_bom_names.add(row["part_name"])
                unknown = all_bom_names - real_names
                if unknown:
                    print(f"  FAIL — {len(unknown)} names not in CSV:")
                    for n in sorted(unknown):
                        print(f"    x {n}")
                else:
                    print(f"  PASS — All {len(all_bom_names)} unique part names verified.")
                break   
        else:
            print("  CSV files not found — skipping validation.")
    except ImportError:
        print("  pandas not installed — skipping validation.")
