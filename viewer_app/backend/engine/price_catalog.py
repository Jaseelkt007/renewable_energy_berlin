"""
price_catalog.py — single source of truth for SKU prices.

Seeded from componentPrices.ts (German-market 2025/2026, with sourcing notes).
Every line in /api/design responses carries unit_price_eur, line_total_eur,
cost_type, and price_source — pulled from this file.

Pricing conventions:
- All prices are EUR, net of VAT.
  Residential PV is 0% VAT in Germany (§12 III UstG); other items already
  reflect typical retail.
- scaling="per_panel" → BoM line emits quantity = panel_count, line_total = price × qty.
- scaling="fixed"     → line_total = price × max(1, qty).
- type ∈ {"hardware", "labor", "service_fee", "credit"}.
- credit prices are negative (e.g. -500 for "Optional Solar Credit").
- confidence ∈ {"high", "medium", "low"} — surfaced in tooltips.

⚠️ This is the canonical catalog. The frontend's componentPrices.ts is a
   build-time mirror (Installation Estimator only). Keep them in sync via
   `GET /api/catalog` + a frontend codegen step.
"""

from __future__ import annotations

from typing import Literal, TypedDict

CostType = Literal["hardware", "labor", "service_fee", "credit"]
Scaling = Literal["per_panel", "fixed"]
Confidence = Literal["high", "medium", "low"]


class PriceEntry(TypedDict):
    price: float
    range: tuple[float, float]
    scaling: Scaling
    type: CostType
    confidence: Confidence
    source: str


# Implicit per-panel module + cabling. The BoM does NOT contain a "PV Module"
# line; we synthesize one based on system_summary.panels.
PV_MODULE_450WP: PriceEntry = {
    "price": 100,
    "range": (75, 130),
    "scaling": "per_panel",
    "type": "hardware",
    "confidence": "medium",
    "source": (
        "pvXchange Aug-2025 mainstream TOPCon ≈ €0.10/Wp wholesale; "
        "450Wp × ~€0.22/Wp typical EU-residential installer cost."
    ),
}

# Implicit inverter, picked by tier from system_summary.pv_kwp.
INVERTER_PRICES: dict[int, PriceEntry] = {
    5: {
        "price": 1800, "range": (1500, 2200),
        "scaling": "fixed", "type": "hardware", "confidence": "medium",
        "source": "Sungrow SH5.0RS / Fronius Symo 5.0 / SMA Sunny Boy 5.0 mid-tier residential.",
    },
    10: {
        "price": 2500, "range": (2100, 3200),
        "scaling": "fixed", "type": "hardware", "confidence": "medium",
        "source": "Sungrow SH10RT / Fronius Symo GEN24 10 / Goodwe ET 10kW.",
    },
    20: {
        "price": 3800, "range": (3200, 4800),
        "scaling": "fixed", "type": "hardware", "confidence": "medium",
        "source": "Fronius Tauro 20 / Sungrow SG20 / SMA Tripower 20.",
    },
}


def size_inverter_kw(pv_kwp: float) -> int:
    """Tier picker — must match frontend bom.ts:sizeInverter for parity."""
    if pv_kwp <= 8.8:
        return 5
    if pv_kwp <= 15.0:
        return 10
    return 20


# ---------------------------------------------------------------------------
# Main catalog. Keys MUST match BoM `part_name` strings VERBATIM.
# Any SKU the LLM might pick MUST appear here.
# ---------------------------------------------------------------------------
PRICE_CATALOG: dict[str, PriceEntry] = {
    # ─── Batteries ───
    "Battery 5kWh": {"price": 2800, "range": (2300, 3500), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "BNEF 2025 stationary LFP $70/kWh + EU 56% premium + BMS/casing/installer margin → ~€560/kWh."},
    "Battery LFP 5kWh": {"price": 2800, "range": (2300, 3500), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Same BNEF basis as Battery 5kWh; LFP tag implied since 2024."},
    "Battery 7kWh": {"price": 3800, "range": (3200, 4700), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Mid-tier 7kWh (BYD HVS, sonnenBatterie 7) sell €3.5-4.5k bare."},
    "Battery 10kWh": {"price": 5200, "range": (4500, 6500), "scaling": "fixed", "type": "hardware", "confidence": "high", "source": "Mid-tier 10kWh stack; matches LFP variant within ±5%."},
    "Battery LFP 10kWh": {"price": 5200, "range": (4500, 6500), "scaling": "fixed", "type": "hardware", "confidence": "high", "source": "PVB Energy 2025 Germany guide: 10kWh/5kW retails €8-10k incl. inverter; bare battery share ≈ €5-6k."},
    "Battery LFP 15kWh": {"price": 7500, "range": (6500, 9500), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Linear scaling from 10kWh tier minus ~10% volume discount; matches BYD HVS 15.4 listings."},
    "AC Coupling Module (Free)": {"price": 0, "range": (0, 0), "scaling": "fixed", "type": "hardware", "confidence": "high", "source": "Bundled at no extra cost in Reonic AC-coupling packages."},

    # ─── Wallbox ───
    "Wallbox": {"price": 900, "range": (500, 1500), "scaling": "fixed", "type": "hardware", "confidence": "high", "source": "ADAC 2025: 11kW wallbox €200-2000; mainstream (go-e, Easee, Heidelberg) cluster €700-1100."},
    "Wallbox 22kW v2": {"price": 1500, "range": (1100, 2000), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "22kW tier (KEBA P30, ABL eMH3) ~50% premium over 11kW; eMobility-Magazin 2025."},
    "Install Wallbox": {"price": 900, "range": (500, 2500), "scaling": "fixed", "type": "labor", "confidence": "high", "source": "emobility-magazin 2025: EFH wallbox install €500-3000, typical €800-1200."},
    "EV Charging Cable 4m": {"price": 180, "range": (120, 280), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Type-2 4m cable, mainstream brands €130-250."},
    "Travel & Logistics Flat Rate - Wallbox": {"price": 200, "range": (150, 350), "scaling": "fixed", "type": "service_fee", "confidence": "low", "source": "Reonic-specific add-on, lower than full PV travel flat."},
    "All-Inclusive Package - Wallbox": {"price": 400, "range": (250, 700), "scaling": "fixed", "type": "service_fee", "confidence": "low", "source": "Reonic-specific add-on; warranty + light after-care for wallbox."},

    # ─── Heat Pumps (Vaillant aroTHERM plus, outdoor unit only — accessories priced separately) ───
    "Heat Pump 5.5kW 230V": {"price": 6500, "range": (5500, 7800), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Vaillant VWL 55/8.1 outdoor unit. Package with 200L tank ab €11,089 → bare unit ≈ €6.5k."},
    "Heat Pump 7.5kW 230V": {"price": 7200, "range": (6200, 8500), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Vaillant VWL 75/8.1, 230V single-phase. heizungsdiscount24/unidomo bare-unit listings."},
    "Heat Pump 8kW": {"price": 7600, "range": (6500, 9000), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "8kW tier interpolated between Vaillant VWL 75 and 105; matches Stiebel Eltron WPL-A 7."},
    "Heat Pump 10.5kW 400V": {"price": 8500, "range": (7500, 10000), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Vaillant VWL 105/8.1 400V. Paket mit 250L Speicher ab €12,298 → bare unit ≈ €8.5k."},
    "Heat Pump 12.5kW 400V": {"price": 9800, "range": (8500, 11500), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Vaillant VWL 125/8.1 400V. billiger.de standalone listing ab €11,689; bare unit ≈ €9.8k."},
    "Heat Pump All-In-One 250L": {"price": 11000, "range": (9500, 13500), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Integrated HP + 250L tank package (Vaillant aroSTOR / Stiebel WPL classic)."},

    # ─── Heat Pump Accessories ───
    "Heat Pump Hydraulic Station": {"price": 1400, "range": (1200, 1700), "scaling": "fixed", "type": "hardware", "confidence": "high", "source": "Vaillant Hydraulikstation VWZ MEH 97/6 ab €1,337.80 (testbericht.de)."},
    "Smart Heating Controller": {"price": 600, "range": (450, 850), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Vaillant sensoCOMFORT VRC 720/3 weather-compensated controller."},
    "Hot Water Storage 250L": {"price": 1100, "range": (900, 1500), "scaling": "fixed", "type": "hardware", "confidence": "high", "source": "Vaillant uniSTOR plus VIH RW 250/3 BR ab €1,050."},
    "Hot Water Storage 300L": {"price": 1300, "range": (1000, 1700), "scaling": "fixed", "type": "hardware", "confidence": "high", "source": "Vaillant uniSTOR plus VIH RW 300/3 BR ab €1,216.90."},
    "Hot Water Storage": {"price": 1100, "range": (900, 1500), "scaling": "fixed", "type": "hardware", "confidence": "low", "source": "Generic listing; default to 250L mid-tier pricing."},
    "Buffer Storage 40L": {"price": 350, "range": (280, 500), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Small inline buffer; Reflex / Cosmo entry tier."},
    "Buffer Storage 100L": {"price": 600, "range": (450, 800), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Cosmo / Reflex 100L buffer, mainstream listing."},
    "Buffer Storage 200L": {"price": 900, "range": (700, 1200), "scaling": "fixed", "type": "hardware", "confidence": "high", "source": "Vaillant uniSTOR VPS 200 ab €890.50."},
    "Buffer Storage": {"price": 800, "range": (600, 1100), "scaling": "fixed", "type": "hardware", "confidence": "low", "source": "Generic listing; default to 200L mid-tier pricing."},

    # ─── HP install / outdoor work ───
    "Heat Pump Installation Compact B": {"price": 2500, "range": (1800, 3500), "scaling": "fixed", "type": "labor", "confidence": "medium", "source": "Outdoor unit placement, hydraulic connection, commissioning. Standard-complexity install."},
    "Heat Pump Installation Compact": {"price": 2200, "range": (1600, 3200), "scaling": "fixed", "type": "labor", "confidence": "medium", "source": "As Compact B, slightly less prep work."},
    "Heat Pump Installation Standard": {"price": 2800, "range": (2000, 3800), "scaling": "fixed", "type": "labor", "confidence": "medium", "source": "Standard install with full hydraulic rework."},
    "Garden Work Small B": {"price": 600, "range": (400, 1000), "scaling": "fixed", "type": "labor", "confidence": "low", "source": "Foundation pad / paver base for outdoor unit + minor groundskeeping."},
    "Garden Work Small": {"price": 500, "range": (350, 900), "scaling": "fixed", "type": "labor", "confidence": "low", "source": "Lighter version of Small B — paver base only."},
    "Garden Work Medium": {"price": 1100, "range": (800, 1700), "scaling": "fixed", "type": "labor", "confidence": "low", "source": "Concrete pad + cable trench + minor landscaping."},
    "Oil Tank Disposal Plastic": {"price": 1100, "range": (800, 1500), "scaling": "fixed", "type": "labor", "confidence": "medium", "source": "Plastic oil tank decommission + disposal; SHK trade rate."},
    "Radiator Replacement Compact": {"price": 350, "range": (250, 500), "scaling": "fixed", "type": "labor", "confidence": "medium", "source": "Single radiator swap incl. labor; mid-size compact unit."},

    # ─── Substructure (PER PANEL — multiply by quantity) ───
    "Substructure Concrete Tile Roof": {"price": 55, "range": (40, 80), "scaling": "per_panel", "type": "hardware", "confidence": "medium", "source": "K2/Schletter/Renusol hook-and-rail kit. Photovoltaikforum: €70-180/kWp → ~€55/panel for tile."},
    "Substructure Clay Tile Roof": {"price": 55, "range": (40, 80), "scaling": "per_panel", "type": "hardware", "confidence": "medium", "source": "Identical hardware to concrete tile (same hook-and-rail system)."},
    "Substructure Concrete Roof": {"price": 60, "range": (45, 85), "scaling": "per_panel", "type": "hardware", "confidence": "low", "source": "Slightly higher than tile due to penetration sealing on non-tile concrete."},
    "Substructure Clay Roof": {"price": 60, "range": (45, 85), "scaling": "per_panel", "type": "hardware", "confidence": "low", "source": "Non-tile clay; uses similar penetration sealing as concrete roof."},
    "Substructure Flat Roof": {"price": 80, "range": (60, 110), "scaling": "per_panel", "type": "hardware", "confidence": "medium", "source": "Aufständerung systems (K2 D-Dome, Renusol Console+) need ballast or anchor."},
    "Substructure Metal Roof": {"price": 50, "range": (35, 70), "scaling": "per_panel", "type": "hardware", "confidence": "medium", "source": "Stehfalz/Trapezblech clamp systems are the cheapest substructure category."},

    # ─── DC install (PER PANEL) ───
    "DC Install Concrete Tile Roof": {"price": 35, "range": (25, 55), "scaling": "per_panel", "type": "labor", "confidence": "low", "source": "DC labor + cabling + MC4 + string protection per panel; Yello/Fraunhofer €400-600/kWp split."},
    "DC Install Clay Tile Roof": {"price": 35, "range": (25, 55), "scaling": "per_panel", "type": "labor", "confidence": "low", "source": "Same labor profile as concrete tile."},
    "DC Install Concrete Roof": {"price": 40, "range": (30, 60), "scaling": "per_panel", "type": "labor", "confidence": "low", "source": "Slightly higher; non-tile roofs often need longer cable runs."},
    "DC Install Clay Roof": {"price": 40, "range": (30, 60), "scaling": "per_panel", "type": "labor", "confidence": "low", "source": "Same labor profile as concrete (non-tile) roof."},
    "DC Install Flat Roof": {"price": 40, "range": (30, 60), "scaling": "per_panel", "type": "labor", "confidence": "low", "source": "More cable per panel due to row spacing; no tile-flexing labor."},
    "DC Install Metal Roof": {"price": 35, "range": (25, 55), "scaling": "per_panel", "type": "labor", "confidence": "low", "source": "Metal-roof DC is fast (clamp + string); cheapest DC category."},

    # ─── Scaffolding (PER PANEL) ───
    "Scaffolding Setup & Removal": {"price": 60, "range": (40, 100), "scaling": "per_panel", "type": "labor", "confidence": "medium", "source": "photovoltaik.info: typical EFH scaffold €800-1500 incl. transport + 4-week stand."},

    # ─── Misc PV hardware ───
    "Power Optimizer 600W": {"price": 65, "range": (50, 90), "scaling": "fixed", "type": "hardware", "confidence": "high", "source": "SolarEdge / Tigo per-module optimizer typical residential listing."},
    "Replacement Tiles": {"price": 60, "range": (30, 120), "scaling": "fixed", "type": "hardware", "confidence": "low", "source": "Replacement clay/concrete tiles incl. logistics."},
    "Removal of Old System (per Panel)": {"price": 25, "range": (15, 40), "scaling": "per_panel", "type": "labor", "confidence": "medium", "source": "Decommissioning labor per panel for retrofit."},

    # ─── Inverter / energy management ───
    "Install Inverter": {"price": 400, "range": (250, 700), "scaling": "fixed", "type": "labor", "confidence": "medium", "source": "Mounting, AC connection, commissioning, monitoring setup. ~3-5 hrs of electrician labor."},
    "Energy Manager B": {"price": 900, "range": (700, 1300), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Reonic mid-tier energy manager incl. EVU coupling."},
    "Energy Management System": {"price": 1100, "range": (800, 1600), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Premium EMS bundle (e.g. SMA Home Manager 2.0, Solar-Log)."},
    "Smart Meter B": {"price": 350, "range": (250, 500), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "MID-certified smart meter for self-consumption tracking."},
    "Home Energy Monitor": {"price": 250, "range": (180, 400), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Shelly EM / Tibber Pulse class home consumption monitor."},
    "Inline Energy Meter": {"price": 200, "range": (130, 320), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "DIN-rail inline meter for sub-circuit metering."},
    "Sub-Meter": {"price": 150, "range": (100, 250), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Standard MID sub-meter, hutschiene mount."},

    # ─── Distribution / safety ───
    "Install Battery Storage": {"price": 800, "range": (500, 1200), "scaling": "fixed", "type": "labor", "confidence": "medium", "source": "PCS commissioning, BMS configuration, Heimanlage anmelden. ~6-8 hrs × €85-120/hr."},
    "AC Surge Protection": {"price": 250, "range": (180, 400), "scaling": "fixed", "type": "hardware", "confidence": "high", "source": "Type 1+2 AC surge protection (Dehn, Phoenix Contact, Citel) + DIN-rail."},
    "Selective Circuit Breaker (SLS)": {"price": 180, "range": (120, 280), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Selektiver Hauptleitungsschutzschalter 3-phase ~63A (Hager/ABB/Siemens)."},
    "Equipotential Bonding": {"price": 180, "range": (120, 280), "scaling": "fixed", "type": "labor", "confidence": "medium", "source": "Schutzpotentialausgleich nach DIN VDE 0100; labor + busbar + clamps."},
    "Sub-Distribution Board": {"price": 600, "range": (400, 900), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Hager / ABB sub-distribution Verteiler, populated."},
    "Smart Guard 63A": {"price": 280, "range": (200, 400), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Smart Guard 63A 3-phase grid protection relay."},
    "System Controller 3-Phase": {"price": 450, "range": (320, 650), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "3-phase system controller for HV/LV-coupled storage."},
    "Relay 1-Phase": {"price": 120, "range": (80, 200), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Single-phase contactor / coupling relay."},
    "APZ Field": {"price": 350, "range": (220, 550), "scaling": "fixed", "type": "labor", "confidence": "medium", "source": "Anschluss-Pflicht-Zähler field; meter operator preparation labor."},

    # ─── Meter cabinet ───
    "Meter Cabinet Repair": {"price": 600, "range": (300, 1200), "scaling": "fixed", "type": "labor", "confidence": "low", "source": "Zählerschrank conditional upgrade; range from light fix-up (€300) to full replacement (€1500+)."},
    "Meter Cabinet Replacement V2 Retain Old": {"price": 1400, "range": (1000, 2000), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "V2 replacement Zählerschrank with retention of old fields."},
    "Meter Cabinet Replacement V3 Retain Old": {"price": 1600, "range": (1200, 2300), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "V3 replacement, additional measurement section."},
    "Meter Cabinet Replacement Multi-Family": {"price": 2200, "range": (1600, 3200), "scaling": "fixed", "type": "hardware", "confidence": "medium", "source": "Multi-family Zählerschrank replacement."},
    "Install Meter Cabinet Replacement Retain Old": {"price": 800, "range": (550, 1200), "scaling": "fixed", "type": "labor", "confidence": "medium", "source": "Labor for V2/V3 retain-old swap; electrician + meter operator coordination."},

    # ─── Service Fees / Other (Reonic-internal — confidence:low) ───
    "Planning & Consulting": {"price": 500, "range": (300, 900), "scaling": "fixed", "type": "service_fee", "confidence": "medium", "source": "Site visit, dimensioning, EEG-Anmeldung and Marktstammdatenregister registration."},
    "Planning & Consulting B": {"price": 600, "range": (400, 1000), "scaling": "fixed", "type": "service_fee", "confidence": "medium", "source": "Higher-touch tier (HP + PV joint planning)."},
    "Travel & Logistics Flat Rate": {"price": 350, "range": (200, 600), "scaling": "fixed", "type": "service_fee", "confidence": "low", "source": "Reonic-specific bundle; estimated from typical handwerker travel-flat-rate structure."},
    "All-Inclusive Package B": {"price": 1200, "range": (800, 2000), "scaling": "fixed", "type": "service_fee", "confidence": "low", "source": "Reonic mid-tier add-on; bundles extended warranty + monitoring + after-care visits."},
    "All-Inclusive Package": {"price": 1000, "range": (700, 1700), "scaling": "fixed", "type": "service_fee", "confidence": "low", "source": "Reonic baseline add-on, lighter than B tier."},
    "Optional Solar Credit": {"price": -500, "range": (-1000, -200), "scaling": "fixed", "type": "credit", "confidence": "low", "source": "Customer-side credit/discount (referral, loyalty, or campaign)."},
    "Optional PV Insurance": {"price": 250, "range": (150, 400), "scaling": "fixed", "type": "service_fee", "confidence": "low", "source": "Annual PV insurance premium; 1st-year bundle."},
}


# Per-category fallback when an exact SKU isn't priced.
# This should rarely fire — every BoM line ought to land in PRICE_CATALOG.
PRICE_BY_CATEGORY: dict[str, PriceEntry] = {
    "ModuleFrameConstruction": {"price": 55, "range": (40, 80), "scaling": "per_panel", "type": "hardware", "confidence": "low", "source": "Category fallback — substructure tile-grade default."},
    "AccessoryToModule": {"price": 35, "range": (25, 55), "scaling": "per_panel", "type": "labor", "confidence": "low", "source": "Category fallback — DC install tile-grade default."},
    "InstallationFee": {"price": 600, "range": (400, 1000), "scaling": "fixed", "type": "labor", "confidence": "low", "source": "Category fallback — generic installation fee."},
    "ServiceFee": {"price": 600, "range": (400, 1000), "scaling": "fixed", "type": "service_fee", "confidence": "low", "source": "Category fallback — generic service fee."},
    "BatteryStorage": {"price": 5000, "range": (3500, 7500), "scaling": "fixed", "type": "hardware", "confidence": "low", "source": "Category fallback — mid-tier 10kWh battery default."},
    "AccessoryToBatteryStorage": {"price": 200, "range": (100, 400), "scaling": "fixed", "type": "hardware", "confidence": "low", "source": "Category fallback — generic battery accessory."},
    "AccessoryToInverter": {"price": 350, "range": (200, 600), "scaling": "fixed", "type": "hardware", "confidence": "low", "source": "Category fallback — generic inverter accessory."},
    "Heatpump": {"price": 8500, "range": (6500, 11000), "scaling": "fixed", "type": "hardware", "confidence": "low", "source": "Category fallback — mid-tier 10kW HP default."},
    "AccessoryToHeatpump": {"price": 800, "range": (500, 1300), "scaling": "fixed", "type": "hardware", "confidence": "low", "source": "Category fallback — generic HP accessory."},
    "WarmwaterStorage": {"price": 1100, "range": (800, 1500), "scaling": "fixed", "type": "hardware", "confidence": "low", "source": "Category fallback — 250L hot water tank default."},
    "HeatingStorage": {"price": 800, "range": (500, 1200), "scaling": "fixed", "type": "hardware", "confidence": "low", "source": "Category fallback — 200L buffer default."},
    "Wallbox": {"price": 900, "range": (500, 1500), "scaling": "fixed", "type": "hardware", "confidence": "low", "source": "Category fallback — 11kW wallbox default."},
    "Other": {"price": 200, "range": (100, 400), "scaling": "fixed", "type": "hardware", "confidence": "low", "source": "Category fallback — unknown line."},
}

# Final safety net — never used in practice if PRICE_CATALOG is complete.
PRICE_FALLBACK: PriceEntry = {
    "price": 200, "range": (100, 400),
    "scaling": "fixed", "type": "hardware", "confidence": "low",
    "source": "Final fallback — SKU absent from catalog and category map.",
}


def lookup(part_name: str, category: str | None = None) -> PriceEntry:
    """Resolve a BoM line to a PriceEntry. Order: exact SKU → category → fallback."""
    if part_name in PRICE_CATALOG:
        return PRICE_CATALOG[part_name]
    if category and category in PRICE_BY_CATEGORY:
        return PRICE_BY_CATEGORY[category]
    return PRICE_FALLBACK


# Version stamp — bumped whenever any price changes. Used by the frontend
# codegen script to detect drift between componentPrices.ts and this file.
CATALOG_VERSION = "2026-04-26"
