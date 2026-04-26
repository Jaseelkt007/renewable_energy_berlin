# Project Motivation — Reonic AI Renewable Designer

> A north-star document for the Big Hack Berlin / Reonic Track submission.
> This explains **what we're building, why, what we're solving, and how**.
> A separate `PHASE_PLAN.md` will translate this into concrete tasks.

---

## 1. Executive Summary

We are building an **AI-powered renewable-energy system designer** that turns a customer profile and a roof model into a complete, refinable, and explainable Bill of Materials (BoM) — combining PV, battery storage, heat pumps, and wallbox **as appropriate**.

Our submission is differentiated along three axes:

1. **It is a system designer, not a solar configurator.** It works gracefully even when PV does not apply (heritage building, shaded roof, no roof access).
2. **It gives the homeowner three honest options** (Budget / Balanced / Premium) instead of a single "trust this" black-box quote.
3. **It explains every line item** with one-sentence rationales, grounded in 1062 historical Reonic projects.

The engine is a three-layer pipeline — **deterministic rules + kNN retrieval over historical projects + Claude LLM with constrained JSON output** — and it produces results in seconds, refinable live in the UI.

---

## 2. The Problem

### What Reonic asked for

> *"Build an AI-powered solution that generates renewable energy system designs for residential customers. Given basic project inputs from an installer or customer, the system should propose a complete setup that combines PV, battery storage, and heat pumps as appropriate. The user should be allowed to refine the design afterwards."*

Three signals in this brief matter most:

1. **"As appropriate"** — the system must *decide* which technologies fit, not always quote all three.
2. **"Refine afterwards"** — interactive, not one-shot.
3. **"AI-powered"** — graders are looking for genuine reasoning, not a spreadsheet wrapped in HTML.

### The pain Reonic actually faces

Designing a residential renewable-energy system today takes installers **2–6 hours per quote**:

- Read the customer profile, infer demand patterns
- Estimate roof capacity (often via site visit or rough sketch)
- Pick PV size, battery size, inverter, heat pump (if applicable)
- Hand-assemble the BoM from a catalog of hundreds of SKUs
- Apply implicit "if HP then also hydraulic station + buffer + controller" bundles
- Tweak for budget, customer preferences, regulatory edge cases
- Justify each line to the customer at the kitchen table

That time is the single biggest non-installation cost in Reonic's business. Cutting quote time from hours to minutes — *without losing installer trust* — is the prize.

---

## 3. Why This Matters — The Gap in the Market

We researched the landscape (see `RESEARCH_NOTES.md` for sources). The market splits into **three non-overlapping layers**, none of which solves Reonic's actual problem:

| Layer | Who owns it | What they do |
|---|---|---|
| **Roof layout / design** | Aurora Solar, OpenSolar, EasySolar | 3D roof + panel placement via computer vision |
| **Techno-economic optimization** | NREL HOMER, NREL SAM, PVsyst | Hourly simulation → least-cost system |
| **Post-install operational dispatch** | 1Komma5° (Heartbeat), Enpal, gridX | Virtual power plants over installed systems |

**Nobody owns the "configure-from-customer-profile-to-installer-grade-BoM" layer.**

- Aurora gives a layout but no coherent multi-tech BoM.
- HOMER/SAM gives optimization but no quote-able output.
- 1Komma5° optimizes operation, not selection.
- Zolar literally pivoted from installer to "software for installers" to chase exactly this gap — which validates the opportunity.

Academic literature (Beck et al. on MILP for German residential heat-pump + PV + battery systems, HTW Berlin's Weniger papers on PV/battery sizing) is rigorous but **not deployable as a sales-floor configurator**. MILP runs take minutes per household; HTW rules are too coarse for individual quotes.

**The gap we are filling: the installer-grade configurator that sits between Aurora's pixels and HOMER's math.**

---

## 4. The Hackathon Context

We are competing against ~100 other teams. Honest read of what most will build:

- **~60%** will ship an LLM-wrapper: form → GPT call → BoM JSON. Generic, no roof, no rationale.
- **~20%** will do roof photogrammetry only. Pretty visuals but a weak BoM.
- **~10%** will go MILP-heavy. Technically deep but the demo will hang.
- **~5%** will think about UX. **This is our competitive segment.**
- **~5%** will be off-brief or chaotic.

**Strategic implication**: we cannot out-MILP the optimization teams or out-vision the photogrammetry teams. We win by **out-producting everyone** — multi-tech reasoning, three honest options, refinable UI, and graceful degradation when solar does not apply.

The pitch sentence we are designing the entire submission around:

> *"Most teams here built a solar configurator. We built an energy-system designer — it works with or without sun, gives the homeowner three real options instead of one black-box answer, and explains why every line item is in their quote."*

---

## 5. Our Hypothesis

The winning configurator is not the one that **computes the most**. It is the one that **learns and explains**. Specifically:

- **Grounded in real data** beats clean theory. 1062 past projects beat any axiom set.
- **Interpretable** beats optimal. Installers reject black-box AI; they accept AI that explains itself.
- **Refinable** beats finalized. The installer's edit is the highest-quality signal in the system.
- **Multi-option** beats single-recommendation. Homeowners want to feel they chose, not that they were chosen for.
- **Multi-tech** beats solar-first. PV is one branch, not the trunk.

These hypotheses are not provable in 48 hours. But they are testable, defensible, and they produce a demo that judges will remember.

---

## 6. What We Are Building

### A four-act demo (90 seconds end-to-end)

**Act 1 — "Address in, design out" (15s)**
Existing 3D roof viewer. Type address → roof appears → panels auto-placed.
*Visual hook. Already built (see `viewer_app/frontend/`).*

**Act 2 — "But it's not just panels" (30s)**
Profile inputs flow in alongside (heating type, EV, household size). System proposes a full BoM: PV + battery + HP + wallbox. Each line has a one-sentence rationale.
*Differentiator vs. the 60% LLM-wrapper teams.*

**Act 3 — "Three honest options, not one black box" (30s)**
Click "Compare options" → three cards: Budget / Balanced / Premium.
Each shows system summary (PV kWp / Battery kWh / HP yes-no), estimated price, autarky %, payback years.
*The "oh shit" moment for judges.*

**Act 4 — The closer: "And it works without sun" (15s)**
Toggle: *heritage building* / *no roof access*. PV drops out. System pivots to: heat pump + battery + dynamic-tariff arbitrage.
*The moment a judge writes a star next to our team name.*

---

## 7. Architecture Overview

### The engine (used for all three options)

```
                  ┌─────────────────────────────────┐
                  │  Customer profile + max_panels  │
                  │  (max_panels can be 0!)         │
                  └─────────────────────────────────┘
                                │
                ┌───────────────┼───────────────┐
                ▼               ▼               ▼
         "Budget" mode   "Balanced" mode  "Premium" mode
                │               │               │
                ▼               ▼               ▼
        ┌──────────────────────────────────────────┐
        │  SAME 3-LAYER ENGINE                     │
        │  ┌────────────────────────────────────┐  │
        │  │ Layer A: Rules (bom_generator.py)  │  │
        │  │   ↪ deterministic safety net       │  │
        │  ├────────────────────────────────────┤  │
        │  │ Layer B: kNN retrieval             │  │
        │  │   ↪ 5 most similar past projects   │  │
        │  ├────────────────────────────────────┤  │
        │  │ Layer C: LLM (Claude)              │  │
        │  │   ↪ constrained JSON BoM           │  │
        │  │   ↪ per-line rationale             │  │
        │  └────────────────────────────────────┘  │
        └──────────────────────────────────────────┘
                │               │               │
                ▼               ▼               ▼
            BoM #1          BoM #2          BoM #3
         (Budget)        (Balanced)       (Premium)
                │               │               │
                └───────┬───────┴───────┬───────┘
                        ▼               ▼
              ┌────────────────────────────┐
              │  UI: 1 default, 3 on click │
              │  + live refinement sliders │
              └────────────────────────────┘
```

### The three modes

| Mode | LLM objective | kNN reranking |
|---|---|---|
| **Budget** | "Minimize upfront cost while covering core needs. Skip optional items unless <5y payback." | Filter neighbors to bottom-30% cost projects |
| **Balanced** | "Standard installer recommendation, balanced ROI." | Use kNN as-is — what installers actually chose |
| **Premium** | "Maximize self-sufficiency and future-proofing. Prefer larger battery, full HP package, surge protection." | Filter neighbors to top-30% completeness |

The engine itself does not change. Only the **objective hint** to the LLM and the **kNN reranking criterion** vary across modes. This gives us three meaningfully-different BoMs without building real Pareto optimization.

### The three-layer engine — defense in depth

| Layer | Robust to | Failure mode without it |
|---|---|---|
| **Rules** (`bom_generator.py`) | Anything — always returns a valid BoM | Pipeline crashes on weird inputs |
| **kNN retrieval** | Common cases (lots of similar past projects) | LLM with no evidence hallucinates SKUs |
| **LLM (Claude)** | Edge cases, reasoning, explanation | Pure rules feel mechanical, not "AI" |

If the LLM call fails or times out → fall back to rules. If kNN returns far neighbors → flag low confidence in the UI ("unusual profile, conservative quote"). **Three layers of redundancy = a robust live demo.**

### Why this stack specifically

- **Rules**: we already have `bom_generator.py`. It is deterministic, auditable, and based on real frequency analysis. Free baseline.
- **kNN**: 1062 historical projects is the closest thing to ground truth Reonic has. Retrieval grounds the LLM in *real* decisions, not invented ones.
- **LLM (Claude)**: composes the final BoM, applies bundle constraints, generates per-line rationale. Constrained JSON output prevents SKU hallucination.

---

## 8. Critical Design Decisions (and why)

### Why kNN + LLM and not pure ML (e.g., LightGBM per SKU)?

A LightGBM multi-label classifier could predict per-SKU inclusion probabilities. But:

- It needs careful train/test discipline we don't have time for in 48h
- It cannot generate **rationale text** for the homeowner
- It cannot handle out-of-distribution profiles gracefully

The LLM gives us reasoning + explanation + edge-case handling for free, with kNN providing the evidence base. LightGBM is on the post-hackathon roadmap, not the critical path.

### Why no real Pareto optimization?

Real multi-objective optimization (NSGA-II, ε-constraint MILP) takes minutes per Pareto front. We do not have that time at quote-time *or* in the dev cycle. The three-prompt trick gives us the *visible* UX benefit (three meaningfully-different options) without the engineering cost.

### Why no pvlib hourly simulation?

Two reasons:
1. Judges will not see the simulation — it does not help the demo.
2. The brief explicitly says module sizing is "not very critical."

Closed-form estimates (HTW Berlin self-consumption curves, simple payback math) are good enough for displayed numbers, with an honest footnote about the assumption.

### Why no MILP solver?

MILP is rigorous but:
- Slow at quote time (minutes per household)
- Adds a heavy dependency (HiGHS, linopy) for one demo feature
- The judges are product people, not operations researchers

MILP belongs on the **roadmap slide** as future work — "the offline oracle that validates the fast pipeline" — not in the hackathon build.

### Why design for the no-solar case?

Three reasons:
1. The brief says **"as appropriate"** — quoting PV when it does not fit is a bug.
2. ~90% of competing teams will silently break on heritage / shaded / no-roof inputs. We do not.
3. Act 4 of the demo is the *single most memorable moment* a judge will see in our pitch.

### Why preset demo profiles + free input, not free input only?

A live demo must not break. Preset cards (Family of 4 / Heritage Building / EV Owner) make Acts 1–4 deterministic. Free-input mode is available as a "play with it" toggle, with input clamping and a "low confidence" banner for edge cases.

---

## 9. What We Are NOT Building (and why)

| Skipped | Why | Where it lives |
|---|---|---|
| pvlib / Prosumpy hourly simulation | Doesn't help demo; brief de-prioritizes sizing | Roadmap |
| MILP solver (HiGHS / linopy) | Slow, heavyweight, no demo benefit | Roadmap (offline oracle) |
| Real Pareto optimization | 3-prompt trick achieves the visible UX | Roadmap |
| Learned dispatch surrogate | Needs MILP training data we don't have | Roadmap |
| Active learning from installer overrides | Needs production traffic | Roadmap |
| Counterfactual explanations ("skip HP → −€18k") | Nice-to-have; build if time permits in buffer | Stretch goal |
| Multi-language support | Not asked | Roadmap |
| Real pricing engine | We use rough €/component approximations | Roadmap |

**All of these go on a single "What's next" roadmap slide in the pitch deck.** This signals depth without burning hackathon hours on things that do not help us win.

---

## 10. Honest Limitations We Will Own on Stage

A submission that admits its limits is more credible than one that overclaims. We will say out loud:

- **Pricing is approximate.** Rough per-component €-figures, not Reonic's real cost data.
- **Autarky / payback are estimates** based on HTW Berlin curves and 2025 German tariffs.
- **kNN over 1062 projects has gaps** for rare profile combinations — flagged in the UI.
- **The LLM occasionally needs guardrails.** Constrained JSON schema prevents SKU invention; rule fallback handles total LLM failure.
- **No real-time roof analysis** if the address isn't pre-loaded — we use the existing photogrammetry pipeline, not live satellite ingest.

Saying these openly turns weaknesses into credibility signals.

---

## 11. Differentiators (the slide judges remember)

| Most teams will... | We will... |
|---|---|
| Wrap GPT in a form | Compose rules + retrieval + LLM with three-layer fallback |
| Quote one number | Show three real options with cost / autarky / payback tradeoffs |
| Assume PV always applies | Pivot gracefully when solar doesn't fit (Act 4) |
| Show a static BoM | Live refinement: sliders update the BoM in real time |
| Generate a quote | Generate a quote *with one-sentence rationale per line* |
| Hide model assumptions | Footnote every estimate with its source (HTW Berlin, 2025 tariffs) |
| Hallucinate SKUs | Constrained JSON output against the actual catalog |

---

## 12. Roadmap Beyond the Hackathon

We position the hackathon submission as **the working slice of a larger product thesis**: the configurator flywheel.

```
   ┌─────────────────────────────────────────────────────────┐
   │  Surrogate-grade optimization (MLP trained on MILP)     │
   │     ↪ MILP-quality output at retrieval-pipeline speed   │
   └─────────────────────────────────────────────────────────┘
                              ↓
   ┌─────────────────────────────────────────────────────────┐
   │  Pareto-honest tradeoffs (real multi-objective)         │
   │     ↪ Show the actual cost-vs-autarky frontier          │
   └─────────────────────────────────────────────────────────┘
                              ↓
   ┌─────────────────────────────────────────────────────────┐
   │  Counterfactual explanations                            │
   │     ↪ "Skip HP: −€18k CapEx, +1.4 tCO₂/yr"              │
   └─────────────────────────────────────────────────────────┘
                              ↓
   ┌─────────────────────────────────────────────────────────┐
   │  Active learning from installer overrides               │
   │     ↪ Each edit is a labeled correction → moat          │
   └─────────────────────────────────────────────────────────┘
                              ↓
                  (loop: better data → better surrogate)
```

The hackathon delivers Layer 0 (the rules+kNN+LLM engine and the three-option UX). The rest is the 6-month roadmap that turns the hackathon win into a defensible product.

---

## 13. Success Criteria

### For the demo (the 90 seconds that matter)

- [ ] Roof viewer loads instantly for at least 3 preset addresses
- [ ] BoM generates in <5 seconds with visible per-line rationale
- [ ] "Compare options" shows three meaningfully different BoMs
- [ ] At least one slider in the refinement panel updates the BoM live
- [ ] "No solar" toggle produces a coherent HP+battery alternative

### For the pitch (the slides)

- [ ] One-sentence positioning that lands ("not a solar configurator, an energy-system designer")
- [ ] One slide showing the three-layer engine architecture
- [ ] One slide showing the four-piece roadmap (surrogate / Pareto / counterfactual / active learning)
- [ ] Honest limitations slide

### For credibility (the questions judges ask)

- [ ] Can we explain *why* the LLM picked each line? (Yes — rationales are baked in)
- [ ] How do we know the BoM is realistic? (Yes — grounded in 1062 past projects via kNN)
- [ ] What happens with weird inputs? (Rule fallback + low-confidence flag)
- [ ] How would this scale? (Roadmap slide answers this directly)

---

## 14. Why We Will Win

We will win because we matched the brief precisely instead of over- or under-shooting it:

- We built **what Reonic asked for** (a renewable-energy system designer, not a solar tool).
- We respected what the brief de-prioritized (we didn't burn time on sizing optimality).
- We addressed what the brief required but most teams will skip (refinement, multi-tech reasoning, graceful degradation).
- We told a story judges can repeat to each other after we leave the room.

Most teams will produce a demo. We are producing a **product thesis with a working slice attached**.

---

## 15. Reference Material

- `bom_generator.py` — the deterministic rule engine (Layer A of the three-layer pipeline)
- `viewer_app/frontend/` — existing 3D roof viewer + panel placement (Act 1 of the demo)
- `project_options_parts.csv` — 10.7k component rows across 1062 projects (kNN corpus)
- `projects_status_quo.csv` — customer profiles (kNN feature space)
- `Reonic Track - AI Renewable Designe.txt` — the original brief
- `RESEARCH_NOTES.md` — competitive landscape + academic prior art (to be created)
- `PHASE_PLAN.md` — task-level implementation plan (to be created next)

---

*Last updated: 2026-04-26 — pre-implementation kickoff.*
