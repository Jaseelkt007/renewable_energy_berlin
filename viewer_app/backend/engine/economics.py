"""
economics.py — closed-form CapEx, autarky, payback, and CO₂ estimators.

Uses the canonical price catalog in `price_catalog.py` (single source of truth
for prices). Every BoM line returned to the frontend carries:
  - unit_price_eur, line_total_eur
  - cost_type ∈ {hardware, labor, service_fee, credit}
  - price_source (sourcing rationale, surfaced as tooltip)
  - price_confidence ∈ {high, medium, low}
  - is_implicit (true for synthesized rows: PV modules, inverter)

CapEx is the rounded sum of all line totals (subtotals_by_type also exposed).
Autarky / payback / CO₂ are HTW Berlin (Weniger 2014) closed forms over a
heat-pump-aware electricity demand.

Tariff assumptions (Germany, 2025):
- Electricity buy:  €0.32/kWh  (residential average)
- Electricity sell: €0.08/kWh  (EEG feed-in for systems <10 kWp)
- Gas:              €0.12/kWh
- Heat-pump COP:    3.3        (annual average for modern air-source)
- Grid CO₂:         0.40 kg/kWh (German mix, 2024)
"""

from __future__ import annotations

from typing import Optional

from .price_catalog import (
    INVERTER_PRICES,
    PV_MODULE_450WP,
    PriceEntry,
    lookup,
    size_inverter_kw,
)

# ---------------------------------------------------------------------------
# Tariff & physics constants
# ---------------------------------------------------------------------------
ELECTRICITY_BUY = 0.32        # €/kWh
ELECTRICITY_SELL = 0.08       # €/kWh
GAS_PRICE = 0.12              # €/kWh
HP_COP = 3.3                  # seasonal average
GRID_CO2_KG_PER_KWH = 0.40    # German mix
PANEL_WP = 450                # default module size

# Battery arbitrage savings (no-PV case): off-peak vs. peak spread
BATTERY_ARBITRAGE_EUR_PER_KWH_DAY = 0.15


# ---------------------------------------------------------------------------
# Per-line pricing — replaces the old estimate_capex()
# ---------------------------------------------------------------------------

def _line_total(entry: PriceEntry, qty: float) -> float:
    """per_panel SKUs scale linearly with quantity; fixed SKUs charge once."""
    if entry["scaling"] == "per_panel":
        return entry["price"] * qty
    return entry["price"] * max(1.0, qty)


def _priced_line_dict(
    base: dict,
    entry: PriceEntry,
    line_total: float,
    is_implicit: bool = False,
) -> dict:
    """Attach the 5 price fields to a BoM line dict."""
    return {
        **base,
        "unit_price_eur": entry["price"],
        "line_total_eur": line_total,
        "cost_type": entry["type"],
        "price_source": entry["source"],
        "price_confidence": entry["confidence"],
        "is_implicit": is_implicit,
    }


def price_bom(
    bom: list[dict],
    panels: int,
    pv_kwp: float,
) -> tuple[list[dict], dict[str, float], float]:
    """
    Walk a BoM and return:
      - priced_lines:      input rows + price fields, with implicit PV module
                           and inverter rows PREPENDED when panels > 0
      - subtotals_by_type: {hardware, labor, service_fee, credit}
      - capex_eur:         rounded to nearest €100

    Implicit rows account for hardware bundled in Reonic packages but absent
    from the LLM's BoM (panel modules + cabling, inverter unit). Without them
    the breakdown total would be lower than the real installed price.
    """
    priced: list[dict] = []
    subtotals = {"hardware": 0.0, "labor": 0.0, "service_fee": 0.0, "credit": 0.0}

    # ─── Implicit per-panel module + cabling (only when there are panels) ───
    if panels > 0:
        module_total = PV_MODULE_450WP["price"] * panels
        priced.append(_priced_line_dict(
            {
                "part_name": "PV Module 450Wp",
                "quantity": panels,
                "category": "ModuleFrameConstruction",
                "technology": "solar",
                "rationale": "Implicit per-panel hardware: module + cabling + small parts.",
            },
            PV_MODULE_450WP,
            module_total,
            is_implicit=True,
        ))
        subtotals[PV_MODULE_450WP["type"]] += module_total

        # ─── Implicit inverter unit (the LLM's "Install Inverter" line is labor only) ───
        inv_kw = size_inverter_kw(pv_kwp)
        inv_entry = INVERTER_PRICES[inv_kw]
        priced.append(_priced_line_dict(
            {
                "part_name": f"Inverter {inv_kw}kW",
                "quantity": 1,
                "category": "AccessoryToInverter",
                "technology": "solar",
                "rationale": f"Implicit inverter unit, sized for {pv_kwp:.1f} kWp PV.",
            },
            inv_entry,
            inv_entry["price"],
            is_implicit=True,
        ))
        subtotals[inv_entry["type"]] += inv_entry["price"]

    # ─── Real BoM lines ───
    for line in bom:
        name = line.get("part_name", "")
        qty = float(line.get("quantity", 1))
        category = line.get("category")
        entry = lookup(name, category)
        line_total = _line_total(entry, qty)
        priced.append(_priced_line_dict(line, entry, line_total, is_implicit=False))
        subtotals[entry["type"]] += line_total

    raw_total = sum(subtotals.values())
    capex_eur = float(round(raw_total / 100) * 100)
    return priced, subtotals, capex_eur


# ---------------------------------------------------------------------------
# Autarky (HTW Berlin Weniger 2014 — empirical curve)
# ---------------------------------------------------------------------------

def estimate_autarky(pv_kwp: float, battery_kwh: float, annual_kwh: float) -> float:
    """
    Degree of self-sufficiency as a fraction (0..1).

    HTW Berlin empirical model (single-family German home, H0 load profile):
      base = clip(0.30 + 0.20 * (kWp_per_MWh - 1.0), 0.0, 0.55)
      battery_boost = clip(0.25 * kWh_per_kWp, 0.0, 0.30)
      autarky = clip(base + battery_boost, 0.0, 0.85)

    Reference: Weniger, J. et al. (2014).
    Economics of Residential PV-Battery Systems in the Self-Consumption Age.
    """
    if annual_kwh <= 0 or pv_kwp <= 0:
        return 0.0

    pv_per_mwh = pv_kwp / (annual_kwh / 1000.0)
    base = max(0.0, min(0.30 + 0.20 * (pv_per_mwh - 1.0), 0.55))

    if pv_kwp > 0:
        bat_per_kwp = battery_kwh / pv_kwp
        battery_boost = max(0.0, min(0.25 * bat_per_kwp, 0.30))
    else:
        battery_boost = 0.0

    return round(min(base + battery_boost, 0.85), 3)


# ---------------------------------------------------------------------------
# Payback + CO₂
# ---------------------------------------------------------------------------

def _hp_fuel_savings(hp_replaces_gas_kwh: float) -> float:
    """Annual € saved by switching gas/oil heating to a heat pump."""
    if hp_replaces_gas_kwh <= 0:
        return 0.0
    # Gas cost (replaced) minus electricity needed for HP at COP 3.3
    return hp_replaces_gas_kwh * (GAS_PRICE - ELECTRICITY_BUY / HP_COP)


def estimate_payback(
    capex: float,
    autarky: float,
    annual_kwh: float,
    hp_replaces_gas_kwh: float = 0,
    battery_kwh_for_arbitrage: float = 0,
    has_pv: bool = True,
) -> float:
    """
    Simple payback (years) = CapEx / annual savings.

    Annual savings sources:
    - PV self-consumption avoids buy-price grid imports
    - PV surplus earns feed-in tariff (assumes 50% of non-self-consumed is exported)
    - HP replaces fossil heating (if applicable)
    - In no-PV case: battery does dynamic-tariff arbitrage
    """
    pv_savings = autarky * annual_kwh * ELECTRICITY_BUY if has_pv else 0.0
    feed_in = (1.0 - autarky) * 0.5 * annual_kwh * ELECTRICITY_SELL if has_pv else 0.0
    hp_savings = max(0.0, _hp_fuel_savings(hp_replaces_gas_kwh))
    arbitrage = (
        battery_kwh_for_arbitrage * 365 * BATTERY_ARBITRAGE_EUR_PER_KWH_DAY
        if not has_pv else 0.0
    )

    annual_savings = pv_savings + feed_in + hp_savings + arbitrage
    if annual_savings <= 0:
        return 99.9
    return round(capex / annual_savings, 1)


def estimate_co2(
    autarky: float,
    annual_kwh: float,
    hp_replaces_gas_kwh: float = 0,
    has_pv: bool = True,
) -> float:
    """Annual tonnes of CO₂ avoided."""
    pv_avoided = autarky * annual_kwh * GRID_CO2_KG_PER_KWH if has_pv else 0.0
    # Gas combustion ~0.20 kg/kWh; HP electricity ~0.40 kg/kWh / COP
    if hp_replaces_gas_kwh > 0:
        gas_emit = hp_replaces_gas_kwh * 0.20
        hp_emit = (hp_replaces_gas_kwh / HP_COP) * GRID_CO2_KG_PER_KWH
        hp_avoided = gas_emit - hp_emit
    else:
        hp_avoided = 0.0
    total_kg = pv_avoided + max(0.0, hp_avoided)
    return round(total_kg / 1000.0, 2)


# ---------------------------------------------------------------------------
# Top-level convenience: compute all metrics in one call
# ---------------------------------------------------------------------------

def compute_economics(
    bom: list[dict],
    system_summary: dict,
    profile: dict,
) -> dict:
    """
    Build the full economics dict for the API response.

    When HP is present, total annual electricity demand = base + HP electricity
    (heat demand / COP). Autarky is computed on this total so PV savings scale
    correctly with the larger load.

    Returns dict with: capex_eur, subtotals_by_type, priced_bom, autarky_pct,
    payback_years, co2_saved_t_per_year, annual_savings_eur, assumptions.

    The caller (pipeline.design_system) is expected to PROMOTE `priced_bom`
    out of this dict and into the top-level response — see pipeline.py.
    """
    panels = int(system_summary.get("panels", 0))
    pv_kwp = float(system_summary.get("pv_kwp", 0.0))
    battery_kwh = float(system_summary.get("battery_kwh", 0.0))
    hp_kw = float(system_summary.get("hp_kw", 0.0))

    base_kwh = float(profile.get("energy_demand_wh") or 0) / 1000.0
    heat_demand_kwh = float(profile.get("heating_existing_heating_demand_wh") or 0) / 1000.0

    has_pv = pv_kwp > 0
    has_hp = hp_kw > 0
    hp_replaces_gas_kwh = heat_demand_kwh if has_hp else 0.0
    hp_electric_kwh = (hp_replaces_gas_kwh / HP_COP) if has_hp else 0.0

    # Total electricity load against which autarky is computed
    total_kwh = base_kwh + hp_electric_kwh

    # ─── Pricing — single source of truth ─────────────────────────────────
    priced_lines, subtotals, capex = price_bom(bom, panels, pv_kwp)

    autarky = estimate_autarky(pv_kwp, battery_kwh, total_kwh) if has_pv else 0.0
    payback = estimate_payback(
        capex=capex,
        autarky=autarky,
        annual_kwh=total_kwh,
        hp_replaces_gas_kwh=hp_replaces_gas_kwh,
        battery_kwh_for_arbitrage=battery_kwh,
        has_pv=has_pv,
    )
    co2 = estimate_co2(autarky, total_kwh, hp_replaces_gas_kwh, has_pv=has_pv)

    # Annual savings (for display)
    pv_savings = autarky * total_kwh * ELECTRICITY_BUY if has_pv else 0.0
    feed_in = (1.0 - autarky) * 0.5 * total_kwh * ELECTRICITY_SELL if has_pv else 0.0
    hp_savings = max(0.0, _hp_fuel_savings(hp_replaces_gas_kwh))
    arbitrage = (battery_kwh * 365 * BATTERY_ARBITRAGE_EUR_PER_KWH_DAY) if not has_pv else 0.0
    annual_savings = round(pv_savings + feed_in + hp_savings + arbitrage)

    return {
        "capex_eur": int(capex),
        "subtotals_by_type": {k: int(round(v)) for k, v in subtotals.items()},
        "priced_bom": priced_lines,
        "autarky_pct": int(round(autarky * 100)),
        "payback_years": payback,
        "co2_saved_t_per_year": co2,
        "annual_savings_eur": int(annual_savings),
        "assumptions": {
            "electricity_buy_eur_kwh": ELECTRICITY_BUY,
            "electricity_sell_eur_kwh": ELECTRICITY_SELL,
            "gas_eur_kwh": GAS_PRICE,
            "hp_cop": HP_COP,
            "grid_co2_kg_per_kwh": GRID_CO2_KG_PER_KWH,
            "source": "HTW Berlin self-consumption curves; 2025 German residential tariffs",
        },
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("─" * 70)
    print("MID-RANGE BALANCED (6500 kWh, EV, Gas, 25 panels, LFP 10kWh, HP 10.5kW)")
    print("─" * 70)
    bom = [
        {"part_name": "Substructure Concrete Tile Roof", "quantity": 25, "category": "ModuleFrameConstruction", "technology": "solar"},
        {"part_name": "DC Install Concrete Tile Roof", "quantity": 25, "category": "AccessoryToModule", "technology": "solar"},
        {"part_name": "Scaffolding Setup & Removal", "quantity": 25, "category": "InstallationFee", "technology": "solar"},
        {"part_name": "Install Inverter", "quantity": 1, "category": "InstallationFee", "technology": "solar"},
        {"part_name": "Battery LFP 10kWh", "quantity": 1, "category": "BatteryStorage", "technology": "ses"},
        {"part_name": "Install Battery Storage", "quantity": 1, "category": "InstallationFee", "technology": "ses"},
        {"part_name": "Heat Pump 10.5kW 400V", "quantity": 1, "category": "Heatpump", "technology": "heatpump"},
        {"part_name": "Heat Pump Hydraulic Station", "quantity": 1, "category": "AccessoryToHeatpump", "technology": "heatpump"},
        {"part_name": "Smart Heating Controller", "quantity": 1, "category": "AccessoryToHeatpump", "technology": "heatpump"},
        {"part_name": "Hot Water Storage 300L", "quantity": 1, "category": "WarmwaterStorage", "technology": "heatpump"},
        {"part_name": "Buffer Storage 200L", "quantity": 1, "category": "HeatingStorage", "technology": "heatpump"},
        {"part_name": "Heat Pump Installation Compact B", "quantity": 1, "category": "InstallationFee", "technology": "heatpump"},
        {"part_name": "Garden Work Small B", "quantity": 1, "category": "InstallationFee", "technology": "heatpump"},
        {"part_name": "Wallbox", "quantity": 1, "category": "Wallbox", "technology": "wallbox"},
        {"part_name": "Install Wallbox", "quantity": 1, "category": "InstallationFee", "technology": "wallbox"},
        {"part_name": "Planning & Consulting", "quantity": 1, "category": "ServiceFee", "technology": "solar"},
        {"part_name": "Travel & Logistics Flat Rate", "quantity": 1, "category": "ServiceFee", "technology": "solar"},
        {"part_name": "All-Inclusive Package B", "quantity": 1, "category": "ServiceFee", "technology": "solar"},
        {"part_name": "AC Surge Protection", "quantity": 1, "category": "InstallationFee", "technology": "solar"},
        {"part_name": "Meter Cabinet Repair", "quantity": 1, "category": "InstallationFee", "technology": "solar"},
        {"part_name": "Selective Circuit Breaker (SLS)", "quantity": 1, "category": "InstallationFee", "technology": "solar"},
        {"part_name": "Optional Solar Credit", "quantity": 1, "category": "Other", "technology": "solar"},
    ]
    summary = {"pv_kwp": 11.25, "panels": 25, "battery_kwh": 10, "hp_kw": 10.5, "wallbox_count": 1}
    profile = {"energy_demand_wh": 6_500_000, "heating_existing_heating_demand_wh": 18_000_000}
    out = compute_economics(bom, summary, profile)
    print(json.dumps({k: v for k, v in out.items() if k != "priced_bom"}, indent=2))
    print("\nFirst 4 priced lines:")
    for line in out["priced_bom"][:4]:
        print(f"  {line['part_name']:40s} × {line['quantity']:>3}  =  €{line['line_total_eur']:>8,.2f}  [{line['cost_type']}]")
    print(f"\nLine count: {len(out['priced_bom'])}")
    print(f"Sum check : €{sum(l['line_total_eur'] for l in out['priced_bom']):,.2f}  (capex_eur €{out['capex_eur']:,})")

    print("\n" + "─" * 70)
    print("USER'S ACTUAL PROFILE (4500 kWh, no EV, Gas, 33 panels, LFP 15kWh, HP 8kW)")
    print("─" * 70)
    bom_user = [
        {"part_name": "Substructure Concrete Tile Roof", "quantity": 33, "category": "ModuleFrameConstruction", "technology": "solar"},
        {"part_name": "DC Install Concrete Tile Roof", "quantity": 33, "category": "AccessoryToModule", "technology": "solar"},
        {"part_name": "Scaffolding Setup & Removal", "quantity": 33, "category": "InstallationFee", "technology": "solar"},
        {"part_name": "Install Inverter", "quantity": 1, "category": "InstallationFee", "technology": "solar"},
        {"part_name": "Battery LFP 15kWh", "quantity": 1, "category": "BatteryStorage", "technology": "ses"},
        {"part_name": "Install Battery Storage", "quantity": 1, "category": "InstallationFee", "technology": "ses"},
        {"part_name": "Heat Pump 8kW", "quantity": 1, "category": "Heatpump", "technology": "heatpump"},
        {"part_name": "Heat Pump Hydraulic Station", "quantity": 1, "category": "AccessoryToHeatpump", "technology": "heatpump"},
        {"part_name": "Smart Heating Controller", "quantity": 1, "category": "AccessoryToHeatpump", "technology": "heatpump"},
        {"part_name": "Hot Water Storage 300L", "quantity": 1, "category": "WarmwaterStorage", "technology": "heatpump"},
        {"part_name": "Buffer Storage 200L", "quantity": 1, "category": "HeatingStorage", "technology": "heatpump"},
        {"part_name": "Heat Pump Installation Compact B", "quantity": 1, "category": "InstallationFee", "technology": "heatpump"},
        {"part_name": "Garden Work Small B", "quantity": 1, "category": "InstallationFee", "technology": "heatpump"},
        {"part_name": "AC Surge Protection", "quantity": 1, "category": "InstallationFee", "technology": "solar"},
        {"part_name": "Selective Circuit Breaker (SLS)", "quantity": 1, "category": "InstallationFee", "technology": "solar"},
    ]
    summary_user = {"pv_kwp": 14.85, "panels": 33, "battery_kwh": 15, "hp_kw": 8.0, "wallbox_count": 0}
    profile_user = {"energy_demand_wh": 4_500_000, "heating_existing_heating_demand_wh": 22_000_000}
    out_user = compute_economics(bom_user, summary_user, profile_user)
    print(f"capex_eur          = €{out_user['capex_eur']:,}")
    print(f"subtotals          = {out_user['subtotals_by_type']}")
    print(f"subtotal sum       = €{sum(out_user['subtotals_by_type'].values()):,}")
    print(f"line_total sum     = €{sum(l['line_total_eur'] for l in out_user['priced_bom']):,.2f}")
    print(f"autarky / payback  = {out_user['autarky_pct']}% / {out_user['payback_years']}y")
