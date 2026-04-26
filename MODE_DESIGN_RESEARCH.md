# Mode Design — Research-Backed Analysis

> Why Budget / Balanced / Premium currently fails the customer, what the research literature and Reonic's own data say a *good* tier system should look like, and three options for what to do about it (no implementation yet).

**Status:** analysis only. No code changes proposed. The point of this doc is to land on a defensible product story before we touch the algorithm.

---

## 0. The complaint

For a customer at 4,500 kWh/yr base demand · gas heating · 22,000 kWh heating demand · large roof (max 129 panels):

| | Budget | Balanced | Premium |
|---|---:|---:|---:|
| PV | **58 kWp** | **58 kWp** | **58 kWp** |
| Panels | 129 | 129 | 129 |
| Battery | 5 kWh | 7 kWh | 15 kWh |
| HP | 12.5 kW | 12.5 kW | 12.5 kW |
| **CapEx** | **€43,200** | **€43,600** | **€65,800** |
| Autarky | 57% | 58% | 62% |
| Payback | 15.8 yr | 15.8 yr | **22.9 yr** |
| Annual savings | €2,742 | €2,767 | €2,876 |

Three things are wrong on the face of it:

1. **All three modes pick the same 58 kWp** — mode differentiation isn't working for the most expensive line item. The Budget *rationale text* even says *"sized for 18 panels"* but the BoM emits 129. The LLM is contradicting itself.
2. **58 kWp is a commercial-scale system** for a household using 4,500 kWh/yr — that's **kWp / MWh-demand ratio = 12.9**, vs. an HTW Berlin recommended optimum near 1.5 (see §2 below).
3. **Premium has worse economics than Budget.** Going Budget → Premium adds **+€22,600 capex** for **+€134/yr savings** — a **169-year break-even on the upgrade**. The Premium tier doesn't earn its name; it loses money compared to Budget.

This is the opposite of what you want to put in a hackathon pitch. A Reonic engineer in the audience will spot it instantly.

---

## 1. What the research literature says

### 1a. PV sizing — the academic view

| Source | Finding | Citation |
|---|---|---|
| **HTW Berlin (Weniger)** | "PV system sizes above **1.6 kWp/MWh** [annual demand] cannot compete against grid electricity costs without a battery." Optimum kWp/MWh ratio depends on tariff regime. | [HTW Berlin sizing study](https://solar.htw-berlin.de/studien/dimensionierung-von-pv-anlagen/) |
| **HTW Berlin (recent)** | Roof-filling oversize *can* still pay back if specific cost €/kWp drops with system size (scaffolding amortization, planning fee dilution) — but break-even time grows. | Same study |
| **Fraunhofer ISE 2024** | "Self-consumption is the most important lever for profitability." Feed-in tariff is approaching zero relevance. | [Fraunhofer ISE study](https://www.ise.fraunhofer.de/de/veroeffentlichungen/studien/photovoltaik-und-batteriespeicherzubau-in-deutschland.html) |
| **2025 EEG** | Residential feed-in tariff **€0.0794/kWh** (Aug 2025), down from €0.082. Negative-price 15-min intervals **suspended** under Solarspitzengesetz (Feb 2025) — oversized systems take direct revenue hits. | [pv-magazine](https://www.pv-magazine.com/2025/08/04/germany-reduces-feed-in-tariffs-for-solar-up-to-1-mw/) · [PV Tech](https://www.pv-tech.org/germany-passes-law-to-curb-pv-generation-surpluses-and-negative-pricing/) |

**Reading these together:** German residential PV in 2025 is firmly in a **self-consumption-first** regime. Oversizing PV beyond 2× annual demand earns marginal feed-in at €0.08/kWh while displacing grid imports at €0.32/kWh costs **4× as much** if those panels actually displace consumption. So every kWh you over-produce costs the customer ~€0.24 in opportunity, not gains them ~€0.08.

The mainstream recommendation has shifted in the last 24 months from "fill the roof" to **"size to demand × 1.2–1.5, then add battery"**.

### 1b. Battery sizing — the academic view

| Source | Finding |
|---|---|
| **HTW Berlin Stromspeicher-Inspektion 2024** | Optimum is **1.0–1.5 kWh storage per kWp PV**. Going from 0 → 10 kWh battery moves autarky 30% → 65% (+35 pp). Going 10 → 20 kWh moves it ~+10 pp. Beyond 85% autarky each marginal point is "disproportionately expensive." |
| **Fraunhofer ELEKTROPRAXIS 2025** | "Often-recommended 10 kWh batteries are typically not economical for a typical household. In a household with typical appliances and a heat pump, a 5 kWh battery amortizes only after eight years." |

**Reading these:** the autarky-vs-battery curve flattens hard. The first 5 kWh of storage is an obvious yes; 10 kWh is a "depends on your demand"; 15 kWh is rarely worth it without a heat pump *and* an EV. Past 15 kWh you're paying for grid services (VPP) more than self-consumption.

### 1c. Heat pump sizing

| Source | Finding |
|---|---|
| **Vaillant aroTHERM range (2025)** | 3–12 kW outputs. **8–10 kW is the standard tier for German single-family homes.** Systems below 10 kW are 50% of 2024 revenue. |
| **BAFA / BEG 2025** | 30–70% subsidy on heat pump install costs; payback dropped from double-digit to single-digit years. |

For a 22,000 kWh/yr heat demand house: **8–10 kW is the HP that fits.** Picking 12.5 kW for that profile is one tier oversized — at our €1,300/kW catalog price, that's ~€2,500 wasted across all three modes uniformly. (This isn't mode-specific bug but it adds to the cost story.)

---

## 2. What Reonic's own past projects say

This is the strongest piece of evidence and it lives in your own CSV. From `projects_status_quo.csv` × `project_options_parts.csv` (option 1 only, n=580 projects):

| Metric | Value |
|---|---:|
| Median annual demand | **4,500 kWh** |
| Median panel count | **20 panels** |
| Median PV size | **9.0 kWp** |
| Median kWp / MWh-demand ratio | **1.86** |
| 90th percentile panels | 30 |
| 90th percentile kWp/MWh | 3.24 |
| 99th percentile kWp/MWh | 5.5 |
| **Max panels in any past project** | **63** |

**Translation:** Reonic's own installers, looking at the same kind of customer, picked **20 panels (9 kWp)**. Our LLM picked **129 panels (58 kWp)** for the same customer.

That's **6× the 90th percentile** and **9× the median** of what Reonic actually does in production. Not even one project in the 580-row corpus went above 63 panels.

The kNN layer retrieves these reasonable past projects and surfaces them in the prompt. The LLM **ignores them** and snaps to the `max_panels` value, because the prompt explicitly says *"ROOF CAPACITY (max panels available): 129"* with no demand-anchored target alongside it.

So the failure mode is **the prompt overrules the data**. The kNN is doing its job. The LLM is not.

---

## 3. What real installer tiers actually mean

Searched real German installer offerings (Enpal, 1Komma5°, Senec, Zolar, EUPD report). None of them publicly publish a "Budget / Balanced / Premium" matrix as a single trio — but the *pattern* across them is consistent. Tier differentiation on residential PV+battery+HP packages tends to come from:

| Axis | Budget | Balanced | Premium |
|---|---|---|---|
| **PV size** | Cover demand × 1.0–1.2 | Demand × 1.3–1.5 | Demand × 1.5–1.8, capped by roof |
| **Battery** | 5 kWh or none | 10 kWh | 10–15 kWh |
| **Inverter brand** | Generic (Goodwe, Solis) | Mid-tier (Sungrow, Fronius Symo) | Tier-1 (Fronius GEN24, SMA) |
| **Module brand** | Tier-2 monocrystalline | Tier-1 mono PERC/TOPCon | Premium (back-contact, glass-glass, 25-yr product warranty) |
| **HP brand** | Generic | Vaillant aroTHERM | Vaillant + sensoCOMFORT controller |
| **Energy management** | Smart meter | Smart meter + monitoring | Full HEMS (Energy Manager, VPP-ready) |
| **Service tier** | Standard install | Mid + 5-yr warranty | Full premium service + 10-yr warranty + monitoring |
| **Financing** | Buy or basic loan | Buy or 0%-loan | Lease/PPA available |

**The differentiation axis Premium customers actually pay for is *quality* and *integration*, not *quantity*.** A Premium customer wants:
- Better hardware brands (warranty, efficiency, longevity)
- Tighter PV+battery+HP integration (one app, one controller)
- VPP / dynamic-tariff readiness (future revenue stream)
- Premium service experience (faster install, better warranty, dedicated support)

They do **not** want a 4× oversized PV system. That's a backwards Premium definition — overpaying for hardware that doesn't earn.

This matches the Sifted / Saur / EUPD coverage of the German market: the leading installers are pivoting *away* from raw kWh competition and *into* integration depth, dynamic tariffs, and VPP services. 1Komma5° literally launched a battery for **non-PV homes** in 2025, betting that grid services beat self-consumption as a margin driver.

---

## 4. Diagnosis — exactly what's broken in our pipeline

Three layers compounding:

### Layer 1 — prompt has no demand anchor

`viewer_app/backend/engine/llm.py:103`:

```
ROOF CAPACITY (max panels available): {max_panels}
```

This is the only sizing signal the LLM gets. There's no "target panels", no "annual demand × 1.3", no anchor to the neighbor projects' median. The LLM defaults to "use the whole roof" because that's what the prompt highlights and there's no countervailing instruction.

The `STRICT RULES` block says *"Include PV ... AS APPROPRIATE"* but never quantifies "appropriate."

### Layer 2 — mode prompts steer hardware bloat, not sizing

`OBJECTIVE_BUDGET`, `OBJECTIVE_BALANCED`, `OBJECTIVE_PREMIUM` only mention **batteries and accessories**:

- Budget: "Prefer smaller battery"
- Balanced: "Standard installer recommendation"
- Premium: "Largest battery, full HP bundle, surge protection, smart controllers, Energy Manager B"

None say anything about **PV size** or **HP size**. Result: PV is uniform at `max_panels` across modes; HP is uniform at whatever single tier the LLM picks; only battery + ad-hoc service items vary. Premium becomes "Balanced + extra labor lines" rather than a meaningfully different system.

### Layer 3 — kNN reranking by line-count amplifies bloat

`knn.py` `filter_mode`:
- Budget → bottom-30% projects by line count
- Premium → top-30% projects by line count

Top-30% line-count projects are the most expensive ones in the corpus. The LLM treats those as evidence and copies their patterns. This is a structural bias toward Premium = bloated past system, not Premium = better-quality system.

### Combined effect

- All three modes get the same prompt anchor (max_panels) → same PV.
- Mode prompts only steer accessories → minor delta in battery + service.
- kNN bias amplifies Premium accessory count → labor/service subtotal explodes.
- Per the live test: Budget labor €6,610 vs Premium labor €17,155 — a **+€10,545 labor jump** for ~5 extra service lines and a bigger battery.
- PV (the dominant cost driver) doesn't move at all. So Premium becomes "Balanced with €22k more in non-revenue-generating extras."

The customer sees: pay €22k more, get +5% autarky, lose 7 years on payback. That's the opposite of premium.

---

## 5. The four design questions that matter

We can't fix this without first deciding what we *want* the three modes to mean. Four open questions, ranked from most-load-bearing to least:

### Q1 — What is the PV-sizing rule?

**Option A — Demand-anchored (HTW Berlin orthodox, post-2024 EEG):**
- Budget: PV = annual demand × 1.0
- Balanced: PV = annual demand × 1.3
- Premium: PV = annual demand × 1.5
- Cap all at `max_panels`.

Pros: aligns with current research and 2025 EEG economics. Defensible to anyone who's read Weniger 2014. Most-similar to what Reonic's own median project does (1.86 kWp/MWh in your data).

Cons: throws away "fill-the-roof" for customers who genuinely want maximum lifetime CO₂ savings.

**Option B — Roof-filling (HTW Berlin contrarian post-2024 paper):**
- Budget: smallest PV that meets demand (~1.1× demand)
- Balanced: 1.5× demand
- Premium: max_panels (fill roof)

Pros: Premium has a real differentiator. "Maximum lifetime kWh" is a credible Premium pitch.

Cons: Premium payback gets long. Customer has to pay for all that exported kWh. Defensible but the salesperson has to lead with "lifetime CO₂" not "ROI".

**Option C — Hybrid (recommend B with A as default):**
- Budget: PV = demand × 1.0 (fastest payback)
- Balanced: PV = demand × 1.5 (best NPV over 25 years)
- Premium: PV = max(demand × 1.5, max_panels × 0.8) — pricier but still capped if roof is huge

Pros: gives the salesperson a story for each tier. Budget = fastest payback. Balanced = best NPV. Premium = max self-sufficiency *and* future-proof for EV/HP/VPP.

Cons: more code, more prompt engineering.

### Q2 — What is the battery-sizing rule?

Current: Budget 5, Balanced 7-10, Premium 15. This is reasonable but isolated from PV size.

**Recommendation:** tie to PV. HTW says 1.0–1.5 kWh battery per kWp PV.
- Budget: round_to_catalog(0.7 × kWp)
- Balanced: round_to_catalog(1.0 × kWp)
- Premium: round_to_catalog(1.5 × kWp), max 15 kWh.

For a 9 kWp system that gives Budget 5 / Balanced 10 / Premium 15. For a 13 kWp it gives Budget 7-10 / Balanced 15 / Premium 15. Coherent.

### Q3 — What does Premium *actually add*?

The research is unambiguous: customers willing to pay for Premium want **integration and quality**, not raw oversize. Recommendation:

| Premium adds | Why |
|---|---|
| **Energy Manager B + Smart Heating Controller** | Coordinates PV, battery, HP, dynamic tariffs. Real €100–200/yr saving. |
| **Selective Circuit Breaker, AC Surge Protection** | Equipment protection — a real warranty argument. |
| **Premium service tier** (All-Inclusive Package B) | Reonic-side margin item; aligns with how installers actually price up. |
| **Future-proofing language** in the rationale | "Ready for EV, HP retrofit, VPP enrollment" — the *story* is part of Premium. |

These together cost ~€2,500–3,500. Budget → Premium gap should be in the **€8,000–12,000 range**, not the current €22,600. The cost gap should mostly come from PV size + battery, not from extras.

### Q4 — Should HP size respond to mode?

Probably not. HP is sized to heat demand (Vaillant 8 kW for medium house, 10–12 kW for large). Pushing a customer to a bigger HP for "Premium" is just over-engineering.

But: Premium can swap to **Heat Pump All-In-One 250L** (€11,000) instead of 10.5 kW + separate hot-water tank (€8,500 + €1,300 = €9,800). The All-In-One is a Vaillant aroSTOR — that's the kind of integration Premium customers buy.

---

## 6. What "Premium" should feel like in the demo

For the same household profile (4,500 kWh / no EV / Gas / 33-panel roof):

| | Budget | Balanced | Premium |
|---|---:|---:|---:|
| PV | **9 kWp** (20 panels, 1.0× demand) | **13.5 kWp** (30 panels, 1.5× demand) | **14.85 kWp** (33 panels, fill roof) |
| Battery | 5 kWh | 10 kWh | 15 kWh |
| HP | 10.5 kW + std controller | 10.5 kW + Smart Heating Controller | 10.5 kW + Smart Controller + **Energy Manager B** + premium All-In-One option |
| Service | Travel + planning | + AC Surge Protection + SLS | + All-Inclusive Package B + extended warranty |
| **CapEx** | ~€20–22k | ~€26–29k | ~€32–36k |
| Autarky | ~50% | ~62% | ~72% |
| Payback | **9–10 yr** | 10–12 yr | 12–14 yr |
| Story | "Fastest payback. Smallest CapEx that still covers your needs." | "Best long-term return. Sized for tomorrow's electricity prices." | "Maximum self-sufficiency. Integrated quality. Ready for EV / VPP / dynamic tariffs." |

Each tier has a credible *story* for *who would pick it*:
- Budget = first-time buyer, cash-tight, "I want the math to work fast"
- Balanced = mainstream homeowner, ROI-conscious, "I'll keep this house 15+ years"
- Premium = early-adopter / future-proof / values integration over price

Premium's payback (12–14 yr) ends up *similar* to Budget's (9–10 yr). It's longer, but only by ~3 years — and the customer gets meaningful extras (better autarky, integrated controller, premium service, future-revenue readiness). That's defensible.

The current behaviour (Premium 22.9 yr vs Budget 15.8 yr) is **indefensible** — the customer is paying €22k extra for worse economics.

---

## 7. Three options for what to do about it

### Option α — Right-size by demand, keep Premium meaningful (recommended)

Implement Q1 Option C + Q2 + Q3 above. Estimated effort:
- Compute `target_panels_by_mode(profile, mode, max_panels)` in `pipeline.py`
- Pass it into the prompt as a **hard target** alongside `max_panels`
- Rewrite mode objectives to differentiate by quality+sizing, not just battery
- Add a post-LLM sanity check that rejects pv_kwp > 2.5× demand and re-prompts

Total: ~80 lines of backend Python. No frontend changes.

**Story for the pitch:** *"We size PV by demand, not by roof. HTW Berlin Weniger 2014 says oversizing residential PV beyond 1.6 kWp/MWh of demand stops paying back; the 2025 EEG and Solarspitzengesetz make it strictly worse. Our three modes give the customer a real choice between fastest payback, best NPV, and maximum self-sufficiency — not between three sizes of the same oversized system."*

**Demo impact:** Premium has a meaningful 14-year payback (down from 23). Customer can plausibly pick any tier. Compare modal becomes a real story, not a trap.

### Option β — Honest "fill the roof" Premium with explicit framing

Keep Premium = max_panels but reframe it. Add a banner/footer to Premium that says:
> *"Maximum lifetime kWh. Best for customers prioritizing CO₂ over ROI. Payback longer than Budget; total 25-year savings higher."*

Budget and Balanced get the right-sized treatment from Option α. Premium stays roof-filling but is honest about it.

**Pros:** keeps the dramatic CapEx delta in the demo (Premium is visibly more expensive).
**Cons:** Premium is still economically irrational for most customers. Saved by the framing only — judges may push back.

### Option γ — Don't fix; document the limitation

Add a disclaimer to the Compare modal: *"The current model favours roof-filling for all tiers. Demand-anchored sizing is on the post-hackathon roadmap."* Then push **Option α** as a roadmap slide.

**Pros:** zero code change, ships now.
**Cons:** the bug is visible during the live demo. A judge will ask. We have to talk our way out.

---

## 8. My recommendation

**Option α**, and put the research itself into the pitch.

The story becomes:

> *"Most teams will silently oversize PV because that's what their LLM does. We sized to HTW Berlin's 2014 recommendation, validated against Reonic's own 580 historical projects (median 1.86 kWp / MWh of demand), and produced three meaningfully-different tiers — Budget = 9-year payback, Balanced = best NPV, Premium = max integration with disciplined economics. Premium is **better**, not just **bigger**."*

That single paragraph is worth more than the rest of the pitch combined. It demonstrates:
- We read the literature.
- We looked at the real data (and we have 580 projects to back it up — the kNN was always meant to do this; we just need to make the LLM listen to it).
- We understand that the 2025 EEG and Solarspitzengesetz changed the economics.
- We can defend a Premium tier that isn't extractive.

The fix is small (~80 LOC backend, no frontend). The pitch impact is huge.

---

## 9. What I need from you to proceed

Three decisions:

1. **PV-sizing rule** — A (demand × {1.0, 1.3, 1.5}), B (fill roof for Premium), or C (hybrid)? My vote: **C**.
2. **Battery-sizing rule** — tie to kWp via HTW 1.0–1.5 kWh/kWp, or keep the current fixed tiers (5/10/15)? My vote: **tie to kWp**.
3. **Premium SKU set** — Energy Manager B + Smart Heating Controller + premium service + All-Inclusive Package B + future-proof rationale text? My vote: **all of those, none of the bloat**.

Once we agree on those three, the implementation plan writes itself (≈45 min of backend coding, all in `llm.py` prompts and `pipeline.py` sizing helpers).

---

## Sources

- [HTW Berlin — Sizing of Residential PV Battery Systems](https://solar.htw-berlin.de/publikationen/sizing-residential-pv-battery-systems/)
- [HTW Berlin — Sinnvolle Dimensionierung von PV-Anlagen für Prosumer](https://solar.htw-berlin.de/studien/dimensionierung-von-pv-anlagen/)
- [HTW Berlin — Stromspeicher-Inspektion 2024](https://solar.htw-berlin.de/studien/stromspeicher-inspektion-2024/)
- [Weniger et al. 2013 — Sizing and Grid Integration of Residential PV-Battery Systems (PDF)](https://solar.htw-berlin.de/wp-content/uploads/WENIGER-2013-Sizing-and-Grid-Integration-of-Residential-PV-Battery-Systems.pdf)
- [Fraunhofer ISE — Photovoltaik- und Batteriespeicherzubau in Deutschland 2024](https://www.ise.fraunhofer.de/de/veroeffentlichungen/studien/photovoltaik-und-batteriespeicherzubau-in-deutschland.html)
- [pv-magazine — Germany reduces feed-in tariffs for solar up to 1 MW (Aug 2025)](https://www.pv-magazine.com/2025/08/04/germany-reduces-feed-in-tariffs-for-solar-up-to-1-mw/)
- [PV Tech — Germany passes Solarspitzengesetz (Feb 2025)](https://www.pv-tech.org/germany-passes-law-to-curb-pv-generation-surpluses-and-negative-pricing/)
- [Sifted — Enpal, 1Komma5, Zolar adapt to harsh market realities (2025)](https://sifted.eu/articles/enpal-1komma5-zolar-solar-giants)
- [pv-magazine — 1Komma5° releases residential battery for non-PV homes (Apr 2025)](https://www.pv-magazine.com/2025/04/22/1komma5-releases-residential-battery-for-non-pv-homeowners/)
- [Vaillant — aroTHERM range and sizing guidance (2025)](https://www.vaillant-group.com/news-stories/new-arotherm-plus-heat-pump-more-efficient-quieter-and-flexible-installation-options.html)
- [Elektropraxis (DE) — Fraunhofer-Studie: Batteriespeicher oft erst nach Jahren wirtschaftlich (2025)](https://elektropraxis.at/energieversorgung/fraunhofer-studie-batteriespeicher-wirtschaftlichkeit/)
- [pv-magazine — Residential solar shifts from surge to strategy (EUPD report, Aug 2025)](https://www.pv-magazine.com/2025/08/19/residential-solar-shifts-from-surge-to-strategy-eupd-report-spotlights-market-leaders/)

Internal data: `projects_status_quo.csv` × `project_options_parts.csv` (n=580 projects, option 1 only). Analysis stored in `viewer_app/backend/engine/knn.py`.

---

*Diagnosis date: 2026-04-26. Companion to `PRICING_DISCREPANCY_REPORT.md` (the previous price-discrepancy diagnosis) and `CALCULATION_REFERENCE.md` (the formulas).*
