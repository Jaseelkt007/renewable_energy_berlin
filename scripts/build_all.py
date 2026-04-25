"""M9 — batch-build a canonical roof JSON for every project in the map.

Reads `roof3d/project_glb_map.json` and, for each project, produces
`out/<project_id>.roof.json` by:

  1. Running the auto pipeline (M4 candidates -> M5 cluster -> M6 usable -> M7 panels).
  2. If the auto pipeline returns zero panels or raises, falling back to the M3
     manual config (frozen hand-tuned planes) for that GLB.

The single canonical file per project is the contract for Person 1's
recommendation engine: open it, read `summary.system_kwp`, treat it as the
roof-imposed cap on the proposed PV size.

Usage:
    python scripts/build_all.py                       # all mapped projects
    python scripts/build_all.py 297be54c5e7e4aad      # one project_id
    python scripts/build_all.py --no-fallback         # skip M3 fallback
    python scripts/build_all.py --max-planes 8        # forwarded to auto
"""
from __future__ import annotations

import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roof3d.contract import RoofDesign  # round-trip validation
from roof3d.manual_config import MANUAL_CONFIGS

# Re-use the orchestrators from the M3 / M7 scripts.
import scripts.emit_auto as emit_auto_mod
import scripts.emit_manual as emit_manual_mod

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "roof3d" / "project_glb_map.json"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)


@dataclass
class BuildOutcome:
    project_id: str
    glb_file: str
    method: str           # "auto" | "manual_fallback" | "manual_only" | "failed"
    planes: int
    panels: int
    kwp: float
    error: str | None
    out_path: Path | None


def _make_glbconfig(entry: dict):
    """Adapt a project_glb_map entry to the GLBConfig dataclass that emit_*
    expects. We synthesize one from MANUAL_CONFIGS if a manual config exists,
    otherwise build a bare config with no manual planes (auto only)."""
    glb_file = entry["glb_file"]
    project_id = entry["project_id"]
    cfg = MANUAL_CONFIGS.get(glb_file)
    if cfg is not None:
        # Replace the project_id with the canonical one from the map (in case
        # the manual config's project_id is stale).
        from roof3d.manual_config import GLBConfig
        return GLBConfig(glb_file=glb_file, project_id=project_id, planes=cfg.planes)
    from roof3d.manual_config import GLBConfig
    return GLBConfig(glb_file=glb_file, project_id=project_id, planes=())


def _move_canonical(temp_path: Path, project_id: str) -> Path:
    """`emit_auto` writes to `out/<project_id>.auto.roof.json`; `emit_manual`
    writes to `out/<project_id>.roof.json`. M9 wants the canonical name
    `out/<project_id>.roof.json`, so for the auto path we copy the JSON
    contents over after writing."""
    canonical = OUT / f"{project_id}.roof.json"
    canonical.write_text(temp_path.read_text())
    return canonical


def build_one(entry: dict, *, allow_fallback: bool, auto_opts: dict) -> BuildOutcome:
    cfg = _make_glbconfig(entry)
    project_id = cfg.project_id
    glb_file = cfg.glb_file
    canonical = OUT / f"{project_id}.roof.json"

    # 1. Try auto first.
    auto_err: str | None = None
    auto_path: Path | None = None
    auto_panel_count = 0
    try:
        auto_path = emit_auto_mod.emit(cfg, **auto_opts)
        # Round-trip + panel count check
        d = RoofDesign.from_json(auto_path.read_text())
        auto_panel_count = d.summary.panel_count
        if auto_panel_count > 0:
            canonical_path = _move_canonical(auto_path, project_id)
            return BuildOutcome(
                project_id=project_id, glb_file=glb_file, method="auto",
                planes=len(d.roof_planes), panels=d.summary.panel_count,
                kwp=d.summary.system_kwp, error=None, out_path=canonical_path,
            )
    except Exception as e:
        auto_err = f"{type(e).__name__}: {e}"
        traceback.print_exc()

    # 2. Auto produced nothing useful → fall back to M3 manual config (if available).
    if not allow_fallback:
        return BuildOutcome(
            project_id=project_id, glb_file=glb_file,
            method="failed" if auto_err else "auto",
            planes=0, panels=0, kwp=0.0, error=auto_err or "auto produced 0 panels",
            out_path=auto_path,
        )

    if not cfg.planes:
        return BuildOutcome(
            project_id=project_id, glb_file=glb_file, method="failed",
            planes=0, panels=0, kwp=0.0,
            error=(auto_err or "auto produced 0 panels") + " and no manual config available",
            out_path=auto_path,
        )

    try:
        manual_path = emit_manual_mod.emit(cfg)
        d = RoofDesign.from_json(manual_path.read_text())
        # emit_manual already writes to <project_id>.roof.json, which is canonical.
        return BuildOutcome(
            project_id=project_id, glb_file=glb_file, method="manual_fallback",
            planes=len(d.roof_planes), panels=d.summary.panel_count,
            kwp=d.summary.system_kwp, error=auto_err, out_path=canonical,
        )
    except Exception as e:
        return BuildOutcome(
            project_id=project_id, glb_file=glb_file, method="failed",
            planes=0, panels=0, kwp=0.0,
            error=f"auto: {auto_err} | manual: {type(e).__name__}: {e}",
            out_path=None,
        )


def parse_args(argv: list[str]) -> tuple[list[str], bool, dict]:
    keys: list[str] = []
    allow_fallback = True
    auto_opts: dict = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--no-fallback":
            allow_fallback = False; i += 1
        elif a == "--max-planes":
            auto_opts["max_planes"] = int(argv[i + 1]); i += 2
        elif a == "--min-usable":
            auto_opts["min_usable_area_m2"] = float(argv[i + 1]); i += 2
        elif a == "--no-bumps":
            auto_opts["detect_bumps"] = False; i += 1
        else:
            keys.append(a); i += 1
    return keys, allow_fallback, auto_opts


def main() -> None:
    project_filter, allow_fallback, auto_opts = parse_args(sys.argv[1:])
    if not MAP_PATH.is_file():
        print(f"missing {MAP_PATH}", file=sys.stderr)
        sys.exit(2)
    entries = json.loads(MAP_PATH.read_text())["projects"]
    if project_filter:
        entries = [e for e in entries if e["project_id"] in project_filter]
        if not entries:
            print(f"no entries match: {project_filter}", file=sys.stderr); sys.exit(2)

    print(f"building {len(entries)} project(s) (fallback={'on' if allow_fallback else 'off'})")
    outcomes: list[BuildOutcome] = []
    for e in entries:
        outcomes.append(build_one(e, allow_fallback=allow_fallback, auto_opts=auto_opts))

    print(f"\n{'project_id':<22}{'glb':<26}{'method':<18}{'planes':>7}{'panels':>7}{'kWp':>9}")
    print("-" * 89)
    for o in outcomes:
        flag = "" if o.error is None else " ⚠"
        print(f"{o.project_id:<22}{o.glb_file:<26}{o.method:<18}"
              f"{o.planes:>7}{o.panels:>7}{o.kwp:>9.2f}{flag}")
    print()
    fails = [o for o in outcomes if o.method == "failed"]
    if fails:
        print(f"{len(fails)} project(s) FAILED — see traceback(s) above", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
