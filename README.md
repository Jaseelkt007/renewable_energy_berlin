# Reonic AI Renewable Designer

> **Not a solar configurator. An energy-system designer.**
>
> Address-in, full system-out. PV + battery + heat pump + wallbox **as appropriate**, three honest options, refinable live, with one-sentence rationale per line item — and graceful degradation when solar doesn't apply.

**Submission for** Big Hack Berlin 2026 · Reonic Track
**Team** Powerly

---

## Live demo

- **Frontend:** https://lovable.dev/projects/a6628333-eca9-4ea3-83cf-e26f6e95c663?magic_link=mc_0e2e2808-216c-48c4-a4b5-9b74be133d70
- **Backend:** https://github.com/Jaseelkt007/renewable_energy_berlin.git
- **Backup video:** https://youtu.be/Tj6L6ervH2k

---

## The 90-second demo

1. **Address in, design out** — type a Berlin address → 3D roof + auto-placed panels.
2. **It's not just panels** — full BoM with PV + battery + HP + wallbox; rationale per line; per-line € sourced from the canonical catalog.
3. **Three honest options** — Compare: Budget / Balanced / Premium with cost, autarky %, payback. Each card shows **exactly which SKUs the higher tier adds** ("Premium adds: Power Optimizer, Sub-Distribution Board, Smart Guard 63A, EMS, Equipotential Bonding, Optional PV Insurance — +€5,055"). No black-box price jump.
4. **The closer: works without sun** — heritage toggle → PV drops out → HP + battery + smart controller.

---

## Architecture — the 3-layer engine

```
   Customer profile + max_panels (max_panels can be 0)
            │
   ┌────────┼────────┐
   ▼        ▼        ▼
 Budget  Balanced  Premium       ← three modes, one engine
   │        │        │
   └────────┼────────┘
            ▼
 ┌─────────────────────────────────────┐
 │  Layer A — Rules                    │  always-on safety net
 │  Layer B — kNN over 1062 projects   │  retrieval / evidence
 │  Layer C — Gemini 3.1 Flash Lite    │  composition + rationale (JSON)
 └─────────────────────────────────────┘
            │
            ▼
   Three meaningfully-different BoMs · live refinement
```

Defense-in-depth: LLM fails → fall back to rules · kNN far → flag low confidence in the UI · rules always run.

---

## The three options — Budget / Balanced / Premium

Three meaningfully-different system designs, **not three sizes of the same system**. The differentiation comes from two axes — *sizing* (PV + battery) and *integration* (protection + monitoring SKUs) — both anchored in published research and validated against Reonic's own historical data.

```
                                        Budget        Balanced       Premium
─────────────────────────────────────────────────────────────────────────────
Story for the customer       Fastest payback    Best 25-yr NPV    Max self-sufficiency
PV size (× annual demand)         1.0×              1.5×              2.0×
PV minimum (kWp floor)            7.0               7.0               7.0
Battery (× kWp PV)                0.6              0.9               1.2
                                  ↓                ↓                  ↓
                              snapped to catalog tiers: 5 / 7 / 10 / 15 kWh
─────────────────────────────────────────────────────────────────────────────
Adds (vs. previous tier):
  AC Surge Protection                              ✓                  ✓
  Selective Circuit Breaker (SLS)                  ✓                  ✓
  All-Inclusive Package B                          ✓                  ✓
  Smart Heating Controller (with HP)               ✓                  ✓
  Sub-Distribution Board                                              ✓
  Smart Guard 63A grid protection                                     ✓
  Energy Management System (VPP-ready)                                ✓
  Power Optimizer 600W (per-panel)                                    ✓
  Equipotential Bonding (DIN VDE 0100)                                ✓
  Optional PV Insurance                                               ✓
─────────────────────────────────────────────────────────────────────────────
```

**Premium is *quality + integration*, not raw oversize.** A Premium customer gets per-panel monitoring, full protection, an Energy Management System ready for VPP and dynamic-tariff revenue, and the most self-sufficiency the system can deliver — all with a payback that stays competitive with Budget (typically within 2–3 years).

### Why these specific rules

Every threshold in that table is grounded in either peer-reviewed research, published market data, or Reonic's own 580 historical projects. The critical anchors:

| Choice | Source | Cross-check |
|---|---|---|
| PV multiplier 1.0 / 1.5 / 2.0 × demand | [HTW Berlin Weniger — Sizing of Residential PV-Battery Systems](https://solar.htw-berlin.de/publikationen/sizing-residential-pv-battery-systems/) recommends the 1.0–2.0 kWp/MWh band as the economic optimum once feed-in is below buy price. | Reonic's median historical project: **1.86 kWp/MWh**. |
| 7 kWp floor regardless of demand | [HTW Berlin — Dimensionierung von PV-Anlagen für Prosumer](https://solar.htw-berlin.de/studien/dimensionierung-von-pv-anlagen/): smaller systems do not amortize fixed install costs (scaffolding, planning, inverter). | Reonic's bottom-quintile customers landed at **7.2 kWp median** even for 2.7 MWh demand. |
| Battery 0.6 / 0.9 / 1.2 kWh per kWp PV | [HTW Berlin Stromspeicher-Inspektion 2024](https://solar.htw-berlin.de/studien/stromspeicher-inspektion-2024/): optimum 1.0–1.5 kWh/kWp; autarky-vs-cost knee at ~65%. | Reonic's median kWh/kWp ratio: **0.93** (n=548 projects with battery). |
| Premium SKU set (SLS, AC Surge, EMS, Power Optimizer, Sub-Distribution Board, Equipotential Bonding, Smart Guard 63A) | These are exactly the SKUs that distinguish Reonic's top-third historical projects from bottom-third. Selective Circuit Breaker appears in **74%** of top-tier vs **17%** of bottom-tier projects; AC Surge in **72%** vs **17%**. | Cross-checked with [pv-magazine: 2025 EUPD residential solar market report](https://www.pv-magazine.com/2025/08/19/residential-solar-shifts-from-surge-to-strategy-eupd-report-spotlights-market-leaders/) — leading installers differentiate on integration depth, not raw kWh. |
| Premium = quality, not bigger | [pv-magazine: Germany's Solarspitzengesetz (Feb 2025)](https://www.pv-tech.org/germany-passes-law-to-curb-pv-generation-surpluses-and-negative-pricing/) — feed-in subsidies are now suspended during negative-price intervals at €0.0794/kWh. **Oversizing PV is strictly worse than it was 12 months ago.** | [Fraunhofer ISE 2024](https://www.ise.fraunhofer.de/de/veroeffentlichungen/studien/photovoltaik-und-batteriespeicherzubau-in-deutschland.html): self-consumption is now the only economic lever. |
| HP sized by full-load-hours (`heat_demand / 2000`) snapped to catalog tier | [Vaillant aroTHERM range guidance 2025](https://www.vaillant-group.com/news-stories/new-arotherm-plus-heat-pump-more-efficient-quieter-and-flexible-installation-options.html): 8–10 kW is the German single-family standard tier. | Vaillant's standalone listings + BAFA 2025 incentive distribution |

The full research analysis with quantitative back-checks against Reonic's data lives in [`MODE_DESIGN_RESEARCH.md`](./MODE_DESIGN_RESEARCH.md).

### What changes per tier — in numbers

For the canonical demo profile (4,500 kWh/yr · Gas heating · 22 MWh heat demand · 33-panel roof):

| | Budget | Balanced | Premium |
|---|---:|---:|---:|
| Panels | 25 | 33 | 33 |
| PV (kWp) | 11.2 | 14.8 | 14.8 |
| Battery | 7 kWh | 15 kWh | 15 kWh |
| Heat pump | 12.5 kW | 12.5 kW + Smart Controller | 12.5 kW + Smart Controller + EMS |
| **CapEx** | **€31,700** | **€39,100** | **€44,200** |
| Autarky | 46% | 62% | 62% |
| Payback | 13.3 yrs | 13.5 yrs | 15.3 yrs |
| BoM line items | 17 | 22 | 27 |

**Premium → Balanced delta of +€5,055** maps line-by-line to **6 named protection-and-integration SKUs** (Power Optimizer, Sub-Distribution Board, Smart Guard 63A, Equipotential Bonding, EMS, Optional PV Insurance) — visible in the Compare modal under each card's *"Premium adds"* block (returned by the backend as `tier_diffs.premium_vs_balanced`).

---

## Quickstart

### Backend

```bash
# from repo root
cd viewer_app/backend
.venv/bin/uvicorn viewer_app.backend.main:app --port 8000
```

Required env vars (in `.env` at repo root):

```bash
GEMINI_API_KEY=...
GOOGLE_API_KEY=...   # for Solar API / geocoding
```

Verify the design engine:

```bash
curl -s http://localhost:8000/api/design/info | jq
curl -s -X POST http://localhost:8000/api/design \
  -H "Content-Type: application/json" \
  -d '{
    "profile": {
      "energy_demand_wh": 6500000,
      "has_ev": true,
      "heating_existing_type": "Gas",
      "heating_existing_heating_demand_wh": 18000000,
      "house_size_sqm": 160
    },
    "max_panels": 25,
    "mode": "balanced"
  }' | jq
```

### Frontend

The UI lives in a separate repo (`Powerly` / Lovable). Point it at the backend via `src/config/designApiConfig.ts` (`DESIGN_API_BASE_URL`).

---

## Folder structure

```
Berlin_hackathon/
│
├── README.md                              ← you are here
│
├── PROJECT_MOTIVATION.md                  ← the WHY: problem, market gap, hypothesis, roadmap
├── PHASE_PLAN.md                          ← the HOW: sequenced build plan, milestones M1–M7
├── MODE_DESIGN_RESEARCH.md                ← Budget/Balanced/Premium research backing (HTW · Fraunhofer · Reonic data)
├── CALCULATION_REFERENCE.md               ← how every UI number is computed (CapEx · autarky · payback · CO₂)
├── PRICING_FIX_PLAN.md                    ← single-source-of-truth pricing architecture
├── PRICING_DISCREPANCY_REPORT.md          ← diagnosis of the original two-pricer drift bug
├── COMPARE_CARD_LOVABLE_SPEC.md           ← Lovable spec: "Premium adds…" block in the Compare modal
├── PHASE_9_FRONTEND_SPEC.md               ← frontend demo-polish spec (presets + API status dot)
├── PITCH_DECK.md                          ← 6-slide deck content with speaker notes
├── ONE_PAGER.md                           ← one-page submission write-up
├── DEMO_VIDEO_SCRIPT.md                   ← shot list for the 90-second backup video
├── SUBMISSION_CHECKLIST.md                ← final sign-off list
│
├── bom_generator.py                       ← Layer A: deterministic rules baseline (always-on safety net)
├── requirements.txt
│
├── projects_status_quo.csv                ← 1062 historical Reonic customer profiles
├── project_options_parts.csv              ← 1062 × N option-line BoMs (n=580 with full PV + demand match)
│
├── 3D_Modell Brandenburg.glb              ← demo roof models (consumed by roof3d)
├── 3D_Modell Hamburg.glb
├── 3D_Modell North Germany.glb
├── 3D_Modell Ruhr.glb
│
├── roof3d/                                ← 3D roof analysis pipeline (GLB → planes → panel layout)
│   ├── loader.py                          ← GLB load + mesh wrangling
│   ├── planes.py                          ← roof-plane clustering
│   ├── candidates.py                      ← panel-placement candidate generation
│   ├── placement.py                       ← module specs + arrangement
│   ├── usable.py                          ← obstruction filtering
│   ├── seeded.py                          ← user-seeded plane / ROI flow
│   ├── edit.py                            ← panel-placement validation
│   ├── quality.py                         ← gate parameters
│   ├── contract.py                        ← shared types (RoofDesign, BBox, …)
│   ├── assemble.py                        ← planes → frontend contract
│   ├── manual_config.py
│   ├── glb_metadata.json
│   └── project_glb_map.json
│
├── scripts/                               ← dev/build helpers (not used at runtime)
│   ├── build_all.py                       ← regenerate every project_map.json
│   ├── emit_auto.py · emit_manual.py · emit_mock.py
│   ├── audit_glb.py · inspect_glb.py
│   └── visualize_*.py · preview_overlay.py
│
├── out/                                   ← cached roof analysis outputs (.roof.json + previews)
│
└── viewer_app/
    │
    ├── backend/                           ★ THE DEMO BACKEND — deploy this
    │   ├── main.py                        ← FastAPI surface
    │   │                                     POST /api/design          · /api/design/all-modes · /api/design/refine
    │   │                                     GET  /api/design/info     · /api/catalog
    │   ├── project_map.json               ← canonical roof-model registry
    │   ├── requirements.txt
    │   └── engine/                        ★ THE 3-LAYER DESIGN ENGINE
    │       ├── pipeline.py                ← orchestration: rules → kNN → LLM, with cache
    │       ├── sizing.py                  ← demand-anchored PV/battery/HP target sizing per mode
    │       ├── knn.py                     ← Layer B: kNN retrieval over 1062 past projects + no-PV archetypes
    │       ├── llm.py                     ← Layer C: Gemini 3.1 Flash Lite prompt + structured-output schema
    │       ├── economics.py               ← CapEx · autarky · payback · CO₂ closed-form math
    │       ├── price_catalog.py           ← canonical SKU price catalog (single source of truth)
    │       ├── catalog.py                 ← SKU validation + categorization helpers
    │       └── tier_diff.py               ← Compare-modal "what each tier adds" computation
    │
    └── frontend/                          ⚠ DEV-ONLY: internal Next.js roof-viewer used during
                                             development to debug plane clustering & panel placement.
                                             NOT the production demo UI — that lives in a separate
                                             repo (Powerly / Lovable) and points here via
                                             DESIGN_API_BASE_URL.
```

> **Where the production demo UI lives:** the customer-facing demo (Powerly) is a **separate repository**, not under `viewer_app/frontend/`. It points at this backend via `DESIGN_API_BASE_URL` in its config (`src/config/designApiConfig.ts`). Treat `viewer_app/frontend/` as a developer tool only — judges don't see it.

---

## API surface

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/design/info` | Health + cache stats + catalog version |
| `GET`  | `/api/catalog` | Canonical price catalog (used by frontend codegen for the Installation Estimator) |
| `POST` | `/api/design` | Single-mode BoM (`profile`, `max_panels`, `mode`, `overrides?`) |
| `POST` | `/api/design/all-modes` | Three BoMs + `tier_diffs` (Compare modal: what each tier adds over the lower one) |
| `POST` | `/api/design/refine` | BoM with hard user overrides (battery_kwh, include_hp, include_wallbox, include_surge) |

Every BoM line carries `unit_price_eur`, `line_total_eur`, `cost_type` (hardware / labor / service_fee / credit), `price_source`, `price_confidence`, and `is_implicit` — the frontend renders these verbatim, no client-side pricing math. Full request/response shapes in [`CALCULATION_REFERENCE.md`](./CALCULATION_REFERENCE.md) §12 and [`PRICING_FIX_PLAN.md`](./PRICING_FIX_PLAN.md) §3.1.

---

## Stack

**Backend:** Python · FastAPI · pandas · scikit-learn (`NearestNeighbors`) · Pydantic · Google GenAI SDK
**LLM:** Gemini 3.1 Flash Lite Preview (`gemini-3.1-flash-lite-preview`) for both design and live refinement — `response_schema` for structured output
**Frontend:** TanStack Router · React 19 · Vite · Tailwind 4 · Radix · framer-motion · Zustand · react-query
**3D roof:** Cesium · Google Solar API photogrammetry

---

## Honest limitations

- Pricing uses 2025 German residential averages with sourcing per SKU (pvXchange, BNEF, Vaillant via testbericht.de, Photovoltaikforum, ADAC), not Reonic's real installer-cost data. Tooltips show the source per line.
- Autarky / payback are HTW Berlin closed-form curves (Weniger 2014), not pvlib hourly simulation.
- Tier sizing rules (PV × demand multipliers, battery × kWp ratios) are validated against Reonic's 580 historical projects but the multipliers themselves are simplifications of a continuous Pareto frontier. A real multi-objective optimization is on the roadmap.
- kNN over 1062 projects has gaps for rare profile combinations — surfaced as low confidence in the UI.
- The LLM's structured output is the safety net for SKU integrity; SKU aliases catch the most common LLM near-misses (e.g. "Battery 15kWh" → "Battery LFP 15kWh"); rules are the final fallback for total LLM failure.

These are footnoted in the UI and called out on the limitations slide.

---

## Roadmap

A four-stage flywheel that turns the hackathon win into a defensible product — see [`PROJECT_MOTIVATION.md`](./PROJECT_MOTIVATION.md) §12:

1. Surrogate-grade optimization (MLP trained on MILP)
2. Pareto-honest multi-objective tradeoffs
3. Counterfactual explanations ("skip HP: −€18k, +1.4 tCO₂/yr")
4. Active learning from installer overrides → data moat

---

## License

Hackathon submission. All rights reserved during the judging period.

---

*Built in 48 hours · Berlin · 2026.*
