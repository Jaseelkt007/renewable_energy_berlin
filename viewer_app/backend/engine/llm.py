"""
llm.py — Gemini 3.1 Flash Lite designer.

Composes the final BoM by passing customer profile + retrieved similar projects
+ rule-based baseline + SKU catalog to Gemini, with response_schema enforcement.

Three "modes" share the same engine; only the OBJECTIVE overlay differs:
- budget   : minimize upfront cost
- balanced : standard installer recommendation
- premium  : maximum self-sufficiency

Falls back to bom_generator.py rules on any failure.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from .catalog import all_sku_names, catalog_for_prompt, category_for, is_valid_sku, technology_for

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

log = logging.getLogger("llm")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# Lazy genai import + client (so module can be imported without API key for tests)
_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set in environment")
        _client = genai.Client(api_key=api_key)
    return _client


MODEL_DESIGN = "gemini-3.1-flash-lite-preview"
MODEL_REFINE = "gemini-3.1-flash-lite-preview"


# ---------------------------------------------------------------------------
# Pydantic schema — kept FLAT for Gemini compatibility (no Union, no $ref nesting)
# ---------------------------------------------------------------------------

class BomLine(BaseModel):
    part_name: str = Field(description="Must be a part_name from the SKU catalog")
    quantity: float
    category: str
    technology: str = Field(description="One of: solar, ses, wallbox, heatpump")
    rationale: str = Field(description="One sentence (≤120 chars) for the homeowner")


class SystemSummary(BaseModel):
    pv_kwp: float = 0.0
    panels: int = 0
    battery_kwh: float = 0.0
    hp_kw: float = 0.0
    wallbox_count: int = 0


class BomResponse(BaseModel):
    bom: list[BomLine]
    system_summary: SystemSummary
    notes: str = Field(default="", description="Short context, e.g. 'PV not recommended due to property constraints'")


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are a senior solar/energy installer in Germany.
Generate a Bill of Materials (BoM) for the customer below.

{objective}{no_pv_overlay}{overrides}STRICT RULES:
1. Only use part_name values from the SKU catalog (exact spelling).
2. Include PV + battery + HP + wallbox AS APPROPRIATE.
3. If max_panels == 0: do NOT include any PV/inverter/scaffolding/DC/substructure items.
4. For HP: ALWAYS bundle hydraulic station, controller, buffer storage, hot water storage,
   install fee, and garden work.
5. Wallbox: include only if customer has_ev = true.
6. Each line needs a one-sentence rationale aimed at the homeowner (≤120 chars).
7. Compute system_summary: pv_kwp = panels * 0.45 kWp, battery_kwh from the battery line,
   hp_kw from the heat pump line, wallbox_count from wallbox lines.
8. If you exclude PV (e.g. heritage building), set notes explaining why.

CUSTOMER PROFILE:
{profile_json}

ROOF CAPACITY (max panels available): {max_panels}

SIMILAR PAST PROJECTS (use as evidence for what installers historically chose):
{neighbors_summary}

RULE-BASED BASELINE (deterministic safety net — you may deviate with reason):
{rule_bom_summary}

VALID SKU CATALOG (only choose from these names):
{catalog}

Return the final BoM as JSON matching the provided schema.
"""

OBJECTIVE_BUDGET = (
    "OBJECTIVE: Minimize upfront cost. Skip optional items (Optional Solar Credit, "
    "AC Surge Protection, Selective Circuit Breaker) unless payback < 5 years.\n"
    "Prefer smaller battery (5kWh or 7kWh). Skip wallbox if customer doesn't have EV.\n\n"
)

OBJECTIVE_BALANCED = (
    "OBJECTIVE: Standard installer recommendation, balanced ROI. "
    "Include common items (Install Battery Storage, Travel & Logistics, Planning & Consulting, "
    "AC Surge Protection, Selective Circuit Breaker) when relevant.\n\n"
)

OBJECTIVE_PREMIUM = (
    "OBJECTIVE: Maximize self-sufficiency and future-proofing. "
    "Prefer largest battery (LFP 15kWh), full HP bundle, surge protection, smart controllers, "
    "and Energy Manager B if applicable.\n\n"
)

NO_PV_OVERLAY = (
    "SPECIAL CASE — NO PV: This property cannot install solar (heritage building, "
    "shading, or no roof access). Design a renewable system using ONLY:\n"
    "- Heat pump (replaces fossil heating)\n"
    "- Battery storage (charges from grid off-peak for arbitrage)\n"
    "- Smart heating controller for dynamic-tariff optimization\n"
    "- Wallbox if customer has EV\n"
    "DO NOT include: PV, inverter, scaffolding, DC install, substructure, roof items.\n\n"
)

OBJECTIVES = {
    "budget": OBJECTIVE_BUDGET,
    "balanced": OBJECTIVE_BALANCED,
    "premium": OBJECTIVE_PREMIUM,
}


def _summarize_neighbor(n: dict) -> str:
    """Compact one-line summary of a neighbor's BoM for the prompt."""
    parts = [p["component_name"] for p in n["bom"] if p.get("component_name")]
    return f"  - project={n['project_id'][:10]} (dist={n['distance']:.2f}, {n['line_count']} items): {', '.join(parts[:8])}{'...' if len(parts) > 8 else ''}"


def _summarize_rule_bom(rule_bom: list[dict]) -> str:
    return "\n".join(f"  - {row['part_name']} (qty={row['quantity']})" for row in rule_bom)


def _build_overrides_clause(overrides: Optional[dict]) -> str:
    if not overrides:
        return ""
    clauses = []
    if "battery_kwh" in overrides:
        if overrides["battery_kwh"] in (0, None):
            clauses.append("- DO NOT include any battery storage")
        else:
            clauses.append(f"- Battery MUST be exactly {overrides['battery_kwh']} kWh (use 'Battery {overrides['battery_kwh']}kWh' or closest LFP variant)")
    if overrides.get("include_hp") is False:
        clauses.append("- DO NOT include heat pump or any heat-pump bundle items (no Hydraulic Station, no Buffer Storage, no Hot Water Storage, no Heat Pump Installation Compact)")
    if overrides.get("include_wallbox") is False:
        clauses.append("- DO NOT include wallbox or wallbox install fee")
    if overrides.get("include_surge") is False:
        clauses.append("- DO NOT include AC Surge Protection")
    if not clauses:
        return ""
    return "USER OVERRIDES (must honor strictly):\n" + "\n".join(clauses) + "\n\n"


def build_prompt(
    profile: dict,
    max_panels: int,
    mode: str,
    neighbors: list[dict],
    rule_bom: list[dict],
    overrides: Optional[dict] = None,
) -> str:
    objective = OBJECTIVES.get(mode, OBJECTIVE_BALANCED)
    no_pv_overlay = NO_PV_OVERLAY if max_panels == 0 else ""
    overrides_clause = _build_overrides_clause(overrides)
    neighbors_summary = (
        "\n".join(_summarize_neighbor(n) for n in neighbors)
        if neighbors else "  (no similar projects found — rely on baseline + rules)"
    )
    rule_bom_summary = _summarize_rule_bom(rule_bom) if rule_bom else "  (rule engine produced empty output)"

    return PROMPT_TEMPLATE.format(
        objective=objective,
        no_pv_overlay=no_pv_overlay,
        overrides=overrides_clause,
        profile_json=json.dumps(profile, indent=2, default=str),
        max_panels=max_panels,
        neighbors_summary=neighbors_summary,
        rule_bom_summary=rule_bom_summary,
        catalog=catalog_for_prompt(),
    )


# ---------------------------------------------------------------------------
# Rule-based fallback converter
# ---------------------------------------------------------------------------

def _auto_rationale(part_name: str, profile: dict) -> str:
    """Generate a generic rationale for fallback rule-based BoM lines."""
    lookups = {
        "Substructure": "Mounting hardware sized to your roof type and panel count.",
        "DC Install": "DC wiring and cable management for the panel array.",
        "Scaffolding": "Required for safe installation at roof height.",
        "Install Battery Storage": "Professional installation of the battery system.",
        "Install Inverter": "Inverter setup and grid connection.",
        "Battery": "Sized to maximize evening self-consumption.",
        "Heat Pump 5": "Replaces fossil heating, sized for small to medium homes.",
        "Heat Pump 7": "Replaces fossil heating, sized for medium homes.",
        "Heat Pump 10": "Replaces fossil heating, sized for medium-large German homes.",
        "Heat Pump 12": "Replaces fossil heating, sized for large homes with high heat demand.",
        "Hydraulic Station": "Connects the heat pump to your existing heating circuits.",
        "Smart Heating Controller": "Optimizes heat pump runtime against electricity prices.",
        "Hot Water Storage": "Stores hot water generated by the heat pump.",
        "Buffer Storage": "Decouples heating from grid demand for tariff arbitrage.",
        "Heat Pump Installation": "Standard installation package for the heat pump.",
        "Garden Work": "Outdoor preparation work for the heat pump unit.",
        "Wallbox": "EV charger sized for overnight charging.",
        "Install Wallbox": "Professional installation of the wallbox.",
        "Planning & Consulting": "System design, permits, and grid registration.",
        "Travel & Logistics": "Mobilization to your address.",
        "Meter Cabinet": "Required upgrades to your electrical meter cabinet.",
        "AC Surge Protection": "Protects equipment from grid surges and lightning.",
        "All-Inclusive Package": "Bundled service package covering common extras.",
        "Optional Solar Credit": "Discount applied to your solar package.",
        "Selective Circuit Breaker": "Selective tripping prevents whole-house blackouts.",
    }
    for key, text in lookups.items():
        if key in part_name:
            return text
    return "Standard component recommended for this system."


def _system_summary_from_bom(bom: list[BomLine], panels_used: int) -> SystemSummary:
    """Derive system summary numbers from the BoM lines."""
    pv_kwp = round(panels_used * 0.45, 2)

    battery_kwh = 0.0
    hp_kw = 0.0
    wallbox_count = 0

    for line in bom:
        name = line.part_name
        if "Battery" in name and "kWh" in name and "Install" not in name:
            # extract number from name like "Battery LFP 10kWh" or "Battery 7kWh"
            for token in name.split():
                if token.endswith("kWh"):
                    try:
                        battery_kwh = float(token.replace("kWh", ""))
                    except ValueError:
                        pass
        if name.startswith("Heat Pump ") and "kW" in name and "Installation" not in name and "Hydraulic" not in name and "All-In-One" not in name:
            for token in name.split():
                if token.endswith("kW"):
                    try:
                        hp_kw = max(hp_kw, float(token.replace("kW", "")))
                    except ValueError:
                        pass
        if name == "Wallbox" or name.startswith("Wallbox "):
            wallbox_count += int(line.quantity)

    return SystemSummary(
        pv_kwp=pv_kwp, panels=panels_used,
        battery_kwh=battery_kwh, hp_kw=hp_kw, wallbox_count=wallbox_count,
    )


def rule_bom_to_response(rule_bom: list[dict], profile: dict, max_panels: int) -> BomResponse:
    """Convert bom_generator.py output into a BomResponse for the fallback path."""
    panels_used = max_panels
    bom_lines: list[BomLine] = []
    for row in rule_bom:
        name = row["part_name"]
        if not is_valid_sku(name):
            # rule generator may produce names not in the strict catalog (e.g. variants);
            # skip silently — we want a valid response
            continue
        bom_lines.append(BomLine(
            part_name=name,
            quantity=float(row["quantity"]),
            category=row.get("category") or category_for(name),
            technology=row.get("technology") or technology_for(name),
            rationale=_auto_rationale(name, profile),
        ))
    summary = _system_summary_from_bom(bom_lines, panels_used)
    notes = "Rule-based fallback used (LLM unavailable or invalid response)."
    return BomResponse(bom=bom_lines, system_summary=summary, notes=notes)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def call_llm(
    profile: dict,
    max_panels: int,
    mode: str,
    neighbors: list[dict],
    rule_bom: list[dict],
    overrides: Optional[dict] = None,
    model: str = MODEL_DESIGN,
) -> BomResponse:
    """
    Generate a BoM via Gemini, with rule fallback on any failure.

    Args:
        profile:    customer profile dict
        max_panels: roof capacity (0 means no PV)
        mode:       budget | balanced | premium
        neighbors:  result of knn.get_similar_projects
        rule_bom:   result of bom_generator.generate_bill_of_materials
        overrides:  optional refinement constraints
        model:      Gemini model id (use MODEL_REFINE for cheap edits)

    Returns:
        BomResponse with validated SKUs, system_summary, and notes.
    """
    prompt = build_prompt(profile, max_panels, mode, neighbors, rule_bom, overrides)

    try:
        from google.genai import types
        client = _get_client()
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=BomResponse,
                temperature=0.3,
                max_output_tokens=8000,
            ),
        )

        # Try .parsed first (auto-validated), fall back to manual parse
        parsed: Optional[BomResponse] = response.parsed
        if parsed is None:
            text = response.text or ""
            if not text.strip():
                raise ValueError(f"Empty response from Gemini (finish={response.candidates[0].finish_reason if response.candidates else 'unknown'})")
            parsed = BomResponse.model_validate_json(text)

        # Validate SKUs against catalog — drop invalid lines (don't fail whole response)
        invalid = [b.part_name for b in parsed.bom if not is_valid_sku(b.part_name)]
        if invalid:
            log.warning("Dropping %d invalid SKUs from LLM output: %s", len(invalid), invalid[:5])
            parsed.bom = [b for b in parsed.bom if is_valid_sku(b.part_name)]

        if not parsed.bom:
            raise ValueError("All LLM-generated SKUs were invalid")

        # Recompute system_summary from the validated BoM (in case LLM math was off)
        recomputed = _system_summary_from_bom(parsed.bom, max_panels if max_panels > 0 else 0)
        # Trust LLM's wallbox_count if non-zero (it sees has_ev intent)
        if parsed.system_summary.wallbox_count and not recomputed.wallbox_count:
            recomputed.wallbox_count = parsed.system_summary.wallbox_count
        parsed.system_summary = recomputed

        return parsed

    except Exception as e:
        log.warning("LLM call failed (%s) — using rule fallback", e)
        return rule_bom_to_response(rule_bom, profile, max_panels)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Import here to avoid heavy deps at module load
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from bom_generator import generate_bill_of_materials
    from viewer_app.backend.engine.knn import get_similar_projects

    test_profile = {
        "energy_demand_wh": 6_500_000,
        "has_ev": True,
        "heating_existing_type": "Gas",
        "heating_existing_heating_demand_wh": 18_000_000,
        "house_size_sqm": 160,
    }
    max_panels = 25

    for mode in ["budget", "balanced", "premium"]:
        print(f"\n{'#' * 90}\n# MODE: {mode.upper()}\n{'#' * 90}")
        neighbors = get_similar_projects(test_profile, k=5, filter_mode=mode)
        rule_bom = generate_bill_of_materials(test_profile, max_panels)
        result = call_llm(test_profile, max_panels, mode, neighbors, rule_bom)
        print(f"Notes: {result.notes}")
        print(f"System: PV={result.system_summary.pv_kwp}kWp ({result.system_summary.panels}p), "
              f"Battery={result.system_summary.battery_kwh}kWh, HP={result.system_summary.hp_kw}kW, "
              f"WB={result.system_summary.wallbox_count}")
        print(f"BoM ({len(result.bom)} lines):")
        for i, line in enumerate(result.bom, 1):
            print(f"  {i:>2}. [{line.technology:<8}] {line.part_name:<45} qty={line.quantity}  ↳ {line.rationale}")

    # No-PV test
    print(f"\n{'#' * 90}\n# NO-PV (heritage building)\n{'#' * 90}")
    no_pv_profile = {
        "energy_demand_wh": 5_000_000,
        "has_ev": False,
        "heating_existing_type": "Oil",
        "heating_existing_heating_demand_wh": 22_000_000,
    }
    neighbors = get_similar_projects(no_pv_profile, k=3, filter_mode="balanced")
    rule_bom = generate_bill_of_materials(no_pv_profile, 0)
    result = call_llm(no_pv_profile, max_panels=0, mode="balanced", neighbors=neighbors, rule_bom=rule_bom)
    print(f"Notes: {result.notes}")
    print(f"System: PV={result.system_summary.pv_kwp}kWp, Battery={result.system_summary.battery_kwh}kWh, HP={result.system_summary.hp_kw}kW")
    print(f"BoM ({len(result.bom)} lines):")
    for i, line in enumerate(result.bom, 1):
        print(f"  {i:>2}. [{line.technology:<8}] {line.part_name:<45} qty={line.quantity}  ↳ {line.rationale}")
