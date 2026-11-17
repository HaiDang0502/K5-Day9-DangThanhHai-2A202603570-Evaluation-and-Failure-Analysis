"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    graded = _graded_metrics(spec, pr)

    # 1. Metric values must be real numbers. NaN compares False with everything, so `nan < min`
    #    is False and a naive gate passes it; inf hides nothing honest either.
    for k, v in (pr.get("metrics") or {}).items():
        if isinstance(v, float) and not math.isfinite(v):
            problems.append(f"metric {k} is {v!r} (NaN/inf): a crashed grader is not a score")
    for m in spec.get("metrics") or []:
        if m["id"] in graded and (pr.get("metrics") or {}).get(m["id"]) is None and _row_metric(pr, m["id"]):
            problems.append(f"metric {m['id']} missing from metrics although rows carry it")

    # 2. Grader crashes: evaluator_failures, or a None where a graded metric should be a bool.
    fails = pr.get("evaluator_failures")
    if fails is None or not isinstance(fails, list):
        problems.append("evaluator_failures missing: cannot prove every grader ran")
    elif fails:
        problems.append(f"{len(fails)} evaluator failure(s), first: {str(fails[0])[:80]}")
    rows = pr.get("rows") or []
    none_rows = [(r.get("item_id"), r.get("seed"), m) for r in rows for m in graded
                 if r.get(m) is None or (isinstance(r.get(m), float) and not math.isfinite(r[m]))]
    if none_rows:
        i, sd, m = none_rows[0]
        problems.append(f"{len(none_rows)} graded value(s) are None/NaN, first {i}@{sd}:{m}")
    odd = [(r.get("item_id"), r.get("seed"), m, r.get(m)) for r in rows for m in graded
           if r.get(m) is not None and not isinstance(r.get(m), bool)]
    if odd:
        i, sd, m, v = odd[0]
        problems.append(f"{len(odd)} graded value(s) are not booleans, first {i}@{sd}:{m}={v!r}")
    for k, v in (pr.get("metrics") or {}).items():
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
            problems.append(f"metric {k} has non-numeric value {v!r}")

    # 3. Row count and shape: items x seeds, no duplicates, manifest consistent with the baseline.
    man, bman = pr.get("manifest") or {}, base.get("manifest") or {}
    seeds, items = list(man.get("seeds") or []), man.get("items")
    if not isinstance(items, int) or not seeds:
        problems.append("manifest lacks items/seeds")
    elif len(rows) != items * len(seeds):
        problems.append(f"{len(rows)} rows, manifest says {items} items x {len(seeds)} seeds = {items * len(seeds)}")
    keys = [(r.get("item_id"), r.get("seed")) for r in rows]
    dup = len(keys) - len(set(keys))
    if dup:
        problems.append(f"{dup} duplicated (item_id, seed) row(s)")
    if sorted(seeds) != sorted(bman.get("seeds") or []):
        problems.append(f"seeds {sorted(seeds)} differ from baseline {sorted(bman.get('seeds') or [])}")
    if items != bman.get("items"):
        problems.append(f"manifest items {items} differ from baseline {bman.get('items')}")
    for k in ("eval_set", "metric_spec_version"):
        if k in bman and man.get(k) != bman.get(k):
            problems.append(f"manifest {k} {man.get(k)!r} differs from baseline {bman.get(k)!r}")
    if set(man.get("seeds") or []) != {r.get("seed") for r in rows}:
        problems.append("row seeds do not match manifest seeds")

    # 4. The (item, seed) set must be the baseline's: no cherry-picked seeds or items.
    bkeys = {(r.get("item_id"), r.get("seed")) for r in base.get("rows") or []}
    if set(keys) != bkeys:
        miss, extra = len(bkeys - set(keys)), len(set(keys) - bkeys)
        problems.append(f"(item, seed) set differs from baseline: {miss} missing, {extra} extra")

    # 5. Slice tags come from labels, not from the PR: relabelling an item hides it from a slice gate.
    bslices = {r.get("item_id"): sorted(r.get("slices") or []) for r in base.get("rows") or []}
    moved = sorted({r.get("item_id") for r in rows
                    if r.get("item_id") in bslices and sorted(r.get("slices") or []) != bslices[r.get("item_id")]})
    if moved:
        problems.append(f"slice tags differ from baseline for {len(moved)} item(s), first {moved[0]}")

    # 6. Reported metrics must be what the rows say (a hand-edited or stale aggregate).
    problems += _aggregate_mismatch(pr, spec)

    # 7. Ratchet: same eval-set version => thresholds only get stricter.
    if base_spec is not None:
        problems += ratchet(spec, base_spec)
    return problems


def _graded_metrics(spec: dict, rep: dict) -> list[str]:
    """Spec metrics that are per-row grader outputs (in GRADERS), i.e. must be a bool on every row."""
    from lab9.graders import GRADERS
    return [m["id"] for m in spec.get("metrics") or [] if m["id"] in GRADERS]


def _row_metric(rep: dict, mid: str) -> bool:
    return any(mid in r for r in rep.get("rows") or [])


def _aggregate_mismatch(pr: dict, spec: dict) -> list[str]:
    from lab9.eval import aggregate
    rows = pr.get("rows") or []
    graded = [m for m in _graded_metrics(spec, pr) if all(m in r for r in rows)]
    if not rows or not graded or any(not isinstance(r.get(m), bool) for r in rows for m in graded) \
            or not all(isinstance(r.get("cost"), (int, float)) for r in rows):
        return []          # malformed rows are reported by the checks above
    try:
        want = aggregate(rows, graded, k_values=spec.get("pass_k", []))
    except Exception as exc:   # noqa: BLE001 -- an unreadable report is untrustworthy, not a crash
        return [f"cannot recompute metrics from rows: {exc!r}"[:120]]
    out = []
    for k, v in want.items():
        got = (pr.get("metrics") or {}).get(k, "absent")
        if got == "absent" and k not in {m["id"] for m in spec.get("metrics") or []}:
            continue
        if isinstance(v, (int, float)) and isinstance(got, (int, float)) and math.isfinite(got):
            if abs(v - got) > 1e-3:
                out.append(f"metric {k}={got} but rows give {v}")
        elif v != got and not (isinstance(got, float) and not math.isfinite(got)):
            out.append(f"metric {k}={got!r} but rows give {v!r}")
    return out


def _looser(new: dict, old: dict) -> list[str]:
    """Threshold dict `new` is looser than `old` on some key."""
    why = []
    for k in ("min",):
        if k in old and (k not in new or new[k] < old[k]):
            why.append(f"{k} {old[k]} -> {new.get(k)}")
    for k in ("max", "max_increase"):
        if k in old and (k not in new or new[k] > old[k]):
            why.append(f"{k} {old[k]} -> {new.get(k)}")
    if old.get("no_significant_drop") and not new.get("no_significant_drop"):
        why.append("no_significant_drop dropped")
    return why


def ratchet(spec: dict, base_spec: dict) -> list[str]:
    """Same eval-set version: a metric/slice may not disappear, loosen, or stop blocking."""
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        return []          # a new eval-set version starts its own thresholds (and a new baseline)
    out = []
    for k in ("eval_set", "labels", "seeds", "adversarial_pack", "pass_k"):
        if k in base_spec and spec.get(k) != base_spec.get(k):
            out.append(f"ratchet: {k} changed on the same eval-set version")
    bs_new, bs_old = spec.get("bootstrap") or {}, base_spec.get("bootstrap") or {}
    if bs_new.get("confidence", 0.95) > bs_old.get("confidence", 0.95) \
            or bs_new.get("n_resamples", 1000) < bs_old.get("n_resamples", 1000):
        out.append("ratchet: bootstrap made laxer")
    new_m = {m["id"]: m for m in spec.get("metrics") or []}
    for m in base_spec.get("metrics") or []:
        n = new_m.get(m["id"])
        if n is None:
            out.append(f"ratchet: metric {m['id']} removed")
            continue
        if m.get("blocking") and not n.get("blocking"):
            out.append(f"ratchet: metric {m['id']} no longer blocking")
        out += [f"ratchet: metric {m['id']} {w}" for w in _looser(n.get("threshold") or {}, m.get("threshold") or {})]
    new_s = {(x.get("name"), x.get("metric")): x for x in spec.get("slices") or []}
    for x in base_spec.get("slices") or []:
        sid = f"{x.get('name')}/{x.get('metric')}"
        n = new_s.get((x.get("name"), x.get("metric")))
        if n is None:
            out.append(f"ratchet: slice {sid} removed")
            continue
        if x.get("blocking") and not n.get("blocking"):
            out.append(f"ratchet: slice {sid} no longer blocking")
        out += [f"ratchet: slice {sid} {w}" for w in _looser(n, x)]
    return out


_PASS_K = re.compile(r"pass_(at|hat)_(\d+)$")


def slice_items(rep: dict, metric: str, slice_name: str) -> dict[str, float]:
    """Per-item value of `metric` on one slice. Row metrics: mean over seeds (eval.per_item).
    pass_at_k / pass_hat_k: the unbiased per-item estimate from that item's pass_rate rows, so a
    slice can demand reliability (all k seeds pass), not just a good average."""
    m = _PASS_K.match(metric)
    if not m:
        return per_item(rep, metric, slice_name=slice_name)
    fn = stats.pass_hat_k if m.group(1) == "hat" else stats.pass_at_k
    k = int(m.group(2))
    runs: dict[str, list[bool]] = {}
    for r in rep.get("rows") or []:
        if slice_name in (r.get("slices") or []) and r.get("pass_rate") is not None:
            runs.setdefault(r["item_id"], []).append(bool(r["pass_rate"]))
    return {i: fn(len(v), sum(v), k) for i, v in runs.items() if len(v) >= k}


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, mid = sl["name"], sl["metric"]
        sid = f"slice:{name}/{mid}"
        a, b = slice_items(pr, mid, name), slice_items(base, mid, name)
        verdict, note, val = "ok", "", None
        if not a:
            verdict = "missing"                     # a slice with no rows cannot vouch for anything
        else:
            val = round(sum(a.values()) / len(a), 4)
            if "min" in sl and val < sl["min"]:
                verdict = "fail"
            if sl.get("no_significant_drop"):
                keys = sorted(set(a) & set(b))
                if keys:
                    d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                       n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                                                       confidence=bs.get("confidence", 0.95))
                    note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                    if hi < 0:
                        verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if sl.get("blocking") else "warn")
        th = {k: sl[k] for k in ("min", "no_significant_drop") if k in sl}
        lines.append(f"  {sid:<34} {val!s:<8} n={len(a):<3} {json.dumps(th):<36} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    try:
        problems = integrity_check(pr, base, spec, base_spec)
    except Exception as exc:   # noqa: BLE001 -- a report the checker cannot even read is blocked
        problems = [f"report unreadable by integrity_check: {exc!r}"[:160]]
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
