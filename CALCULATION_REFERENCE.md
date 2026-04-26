# Calculation Reference — Reonic AI Renewable Designer

Quick reference for **how every number on the screen is produced**. Use this when judges (or you) ask "where does that figure come from?".

Source files:
- `viewer_app/backend/engine/economics.py` — money, autarky, payback, CO₂
- `viewer_app/backend/engine/llm.py` — Gemini designer + system_summary
- `viewer_app/backend/engine/knn.py` — neighbor retrieval + confidence
- `viewer_app/backend/engine/pipeline.py` — orchestration + cache

---

## 1. The 3-layer engine (how a BoM is produced)

For every `(profile, max_panels, mode, overrides)`:

1. **Layer A — Rules** (`bom_generator.generate_bill_of_materials`)
   Deterministic baseline. Always runs. Used as both a sanity reference inside the LLM prompt **and** the fallback if the LLM call fails.
2. **Layer B — kNN** (`knn.get_similar_projects`)
   Pulls 5 most-similar historical projects. Their BoMs become "evidence" inside the prompt.
3. **Layer C — LLM** (`llm.call_llm`, Gemini 3.1 Flash Lite, structured output)
   Composes the final BoM from the SKU catalog, grounded in (1) rules + (2) neighbors.

Cache key: `sha256(profile + max_panels + mode + overrides)`. Hits return in <50 ms.

---

## 2. kNN — how "similar past projects" is computed

| Step | Detail |
|---|---|
| Corpus | `projects_status_quo.csv` ∩ `project_options_parts.csv` (`option_number == 1` only). Rows where `energy_demand_wh` is NaN/0 are dropped. |
| Continuous features | `energy_demand_wh`, `heating_existing_heating_demand_wh` — `log1p` then `StandardScaler`. |
| Boolean features | `has_ev`, `has_solar`, `has_storage`, `has_wallbox` (cast to float). |
| Categorical | `heating_existing_type` — one-hot, unknowns mapped to `"Unknown"`. |
| Index | `sklearn.NearestNeighbors`, `metric="euclidean"`, `n_neighbors=min(50, N)`. Over-fetches so mode filtering still yields k results. |
| Mode filter | `budget` → bottom-30% by line count · `balanced` → all · `premium` → top-30% by line count. |
| No-PV path | `max_panels == 0` ⇒ skips kNN, returns 2 hardcoded `NO_PV_ARCHETYPES` (HP + battery only). |

**`confidence`** in the response is set in `pipeline.py` from the smallest neighbor distance. Cutoff thresholds live there.

---

## 3. system_summary — the 4 hero tiles

Computed by `_system_summary_from_bom()` in `llm.py` after the LLM returns:

| Field | Formula |
|---|---|
| `pv_kwp` | `panels_used * 0.45` (450 Wp default module) |
| `panels` | `min(LLM-reported panels, max_panels)` |
| `battery_kwh` | Parsed from the battery line (e.g. `"Battery LFP 10kWh"` → 10) |
| `hp_kw` | Parsed from the heat-pump line (e.g. `"Heat Pump 10.5kW 400V"` → 10.5) |
| `wallbox_count` | Sum of quantities on wallbox lines |

The LLM is told to compute these itself, then the backend recomputes from the BoM as a safety net.

---

## 4. CapEx (€) — `economics.estimate_capex`

```
CapEx = panels * PANEL_HARDWARE_EUR
      + Σ over BoM lines: unit_price(part_name) * quantity
      rounded to nearest €100
```

`unit_price` is resolved in this priority:

1. **PRICE_TABLE** — hand-curated dict of ~70 SKUs at German residential averages (e.g. `Battery LFP 10kWh: €6500`, `Heat Pump 10.5kW 400V: €12000`).
2. **PRICE_BY_CATEGORY** — fallback by category (e.g. `Heatpump: €11000`, `BatteryStorage: €5000`).
3. **PRICE_FALLBACK** — final safety net: `€250`.

**`PANEL_HARDWARE_EUR = €120`** is the implicit per-panel cost (panel + small parts not in catalog). Per-panel SKUs like `Substructure …` and `DC Install …` are billed via PRICE_TABLE × panel count.

---

## 5. Self-sufficiency (autarky %) — `estimate_autarky`

HTW Berlin empirical curve (Weniger 2014, single-family German homes, H0 load profile):

```
pv_per_mwh    = pv_kwp / (annual_kwh / 1000)
base          = clip(0.30 + 0.20 * (pv_per_mwh - 1.0), 0, 0.55)
bat_per_kwp   = battery_kwh / pv_kwp
battery_boost = clip(0.25 * bat_per_kwp, 0, 0.30)
autarky       = clip(base + battery_boost, 0, 0.85)
```

- Hard caps: 55% PV-only, +30% from battery, 85% absolute ceiling.
- `autarky = 0` whenever `pv_kwp == 0` (heritage case).
- `annual_kwh` = base electricity demand **plus** HP electricity (`heat_demand / COP`) when an HP is present, so autarky scales correctly with the larger load.

---

## 6. Annual savings (€/yr)

Sum of four streams (any not applicable = 0):

| Stream | Formula |
|---|---|
| PV self-consumption | `autarky * annual_kwh * 0.32 €/kWh` (avoided grid imports) |
| Feed-in earnings | `(1 - autarky) * 0.5 * annual_kwh * 0.08 €/kWh` (assumes 50% of surplus exported) |
| HP fuel switch | `heat_demand_kwh * (0.12 - 0.32 / 3.3)` — gas saved minus HP electricity. Floored at 0. |
| Battery arbitrage (no-PV only) | `battery_kwh * 365 * €0.15/kWh-day` peak-vs-off-peak spread |

---

## 7. Payback (years)

```
payback = round(CapEx / annual_savings, 1)
```

If `annual_savings <= 0` ⇒ display `99.9` (front-end can show as `—`).

---

## 8. CO₂ saved (t/yr) — `estimate_co2`

```
pv_avoided   = autarky * annual_kwh * 0.40 kg/kWh           (when has_pv)
gas_emit     = heat_demand_kwh * 0.20 kg/kWh                (gas combustion)
hp_emit      = (heat_demand_kwh / 3.3) * 0.40 kg/kWh        (HP grid electricity)
hp_avoided   = max(0, gas_emit - hp_emit)
total_t      = (pv_avoided + hp_avoided) / 1000
```

---

## 9. Tariff & physics constants (single source of truth)

All in `economics.py` near the top:

| Constant | Value | Note |
|---|---|---|
| `ELECTRICITY_BUY` | €0.32/kWh | German residential 2025 |
| `ELECTRICITY_SELL` | €0.08/kWh | EEG feed-in for systems <10 kWp |
| `GAS_PRICE` | €0.12/kWh | |
| `HP_COP` | 3.3 | Modern air-source seasonal average |
| `GRID_CO2_KG_PER_KWH` | 0.40 | German mix 2024 |
| `PANEL_WP` | 450 | Module wattage assumption |
| `PANEL_HARDWARE_EUR` | 120 | Implicit per-panel cost |
| `BATTERY_ARBITRAGE_EUR_PER_KWH_DAY` | 0.15 | No-PV case only |

These flow back to the UI in `economics.assumptions` so the source line under the metrics is honest:
> *Source — HTW Berlin self-consumption curves; 2025 German residential tariffs*

---

## 10. Mode differences (Budget / Balanced / Premium)

The mode does **two** things:

1. **kNN filter** — biases the retrieved neighbors (line-count quantile).
2. **LLM objective overlay** — prepended to the prompt:

| Mode | Objective text effect |
|---|---|
| Budget | Skip optional items unless payback < 5 yr · prefer 5–7 kWh battery · skip wallbox if no EV |
| Balanced | Standard installer recommendation · include common service/install fees |
| Premium | Largest battery (LFP 15 kWh) · full HP bundle · surge protection · Energy Manager B |

Same engine, three prompt overlays. That's why all three return the same response shape and you can flip between them via `queryClient.setQueryData`.

---

## 11. User overrides (Refine drawer)

`overrides` are injected as a hard-rules clause at the very top of the prompt (before the objective). Each is rendered as `DO NOT …` / `MUST …`:

- `battery_kwh: 0` → no battery
- `battery_kwh: 5/7/10/15` → that exact size
- `include_hp: false` → no HP **and no bundle items** (hydraulic station, buffer, hot water, install)
- `include_wallbox: false` → no wallbox or install fee
- `include_surge: false` → no AC Surge Protection

Overrides are part of the cache key, so identical requests are deduplicated.

---

## 12. Where the frontend reads each number

| UI element | Source field |
|---|---|
| 4 system tiles | `response.system_summary.{pv_kwp, panels, battery_kwh, hp_kw, wallbox_count}` |
| BoM line | `response.bom[i].{part_name, quantity, category, technology, rationale}` |
| CapEx hero | `response.economics.capex_eur` |
| Self-sufficiency / Payback / CO₂ / Annual savings | `response.economics.{autarky_pct, payback_years, co2_saved_t_per_year, annual_savings_eur}` |
| "Source" footnote | `response.economics.assumptions.source` |
| Confidence dot + label | `response.confidence` + `response.neighbors_used` |
| Notes line | `response.notes` (LLM-populated, e.g. heritage rationale) |
| `cached` badge | `response.cached` |

---

*All figures are hackathon-grade estimates, intentionally conservative, and footnoted in the UI as such. The point is to be plausible and consistent across modes, not to replace a full HOMER / PVSOL simulation.*
