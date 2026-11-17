#!/usr/bin/env python
"""Evaluate a running NoteToGrid service on the example cases with the real language model.

    python scripts/evaluate.py --base-url http://localhost:8000
    python scripts/evaluate.py --only EXAMPLE-06

For every case it sends the request to /optimize-energy and checks:
  * interpretation: each note's directive type, hours and numbers against the expected answer
  * plan validity:  the returned schedule replayed against the expected directives and every battery/grid rule
  * cost:           the returned cost against the known optimum
  * latency
Each case is one model call (about USD 0.004 with gpt-4.1).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.directives import build_constraints, directive_from_interpretation  # noqa: E402
from app.schemas import Scenario  # noqa: E402
from app.validator import validate_plan, validate_totals  # noqa: E402

TOL = 0.01


def post_json(url: str, payload: dict, timeout: float) -> tuple[int, dict | str, float]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, body = resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, f"connection error: {exc}", (time.perf_counter() - t0) * 1000
    ms = (time.perf_counter() - t0) * 1000
    try:
        return status, json.loads(body), ms
    except json.JSONDecodeError:
        return status, body, ms


def note_problems(got: dict, exp: dict) -> list[str]:
    i = exp["note_index"]
    if got.get("applies") != exp["applies"] or got.get("directive_type") != exp["directive_type"]:
        return [f"note {i}: got {got.get('directive_type')}, expected {exp['directive_type']}"]
    ga, ea = got.get("structured_adjustment"), exp["structured_adjustment"]
    if ea is None:
        return [] if ga is None else [f"note {i}: no_op must have no adjustment"]
    if not isinstance(ga, dict):
        return [f"note {i}: adjustment missing"]
    problems = [] if ga.get("hours") == ea.get("hours") else [f"note {i}: hours {ga.get('hours')} != {ea.get('hours')}"]
    for k, v in ea.items():
        if k == "hours":
            continue
        try:
            if abs(float(ga.get(k)) - float(v)) > TOL:
                problems.append(f"note {i}: {k} {ga.get(k)} != {v}")
        except (TypeError, ValueError):
            problems.append(f"note {i}: {k} missing")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--cases", default=str(ROOT / "data" / "examples.json"))
    ap.add_argument("--only", help="run one case, e.g. EXAMPLE-06")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    cases = json.loads(Path(args.cases).read_text("utf-8"))["cases"]
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
        if not cases:
            print(f"no case with id {args.only}")
            return 2

    notes_ok = notes_total = plans_ok = 0
    gaps, latencies = [], []
    for case in cases:
        exp = case["expected_output"]["directive_interpretation"]
        optimum = case["expected_output"]["total_cost_bdt"]
        status, body, ms = post_json(f"{base}/optimize-energy", case["input"], args.timeout)
        latencies.append(ms)
        notes_total += len(exp)
        if status != 200 or not isinstance(body, dict):
            print(f"{case['id']}  HTTP {status}  {str(body)[:100]}")
            continue

        got = body.get("directive_interpretation", [])
        problems = [p for g, e in zip(got, exp) for p in note_problems(g, e)]
        if len(got) != len(exp):
            problems.append(f"{len(got)} interpretations for {len(exp)} notes")
        notes_ok += sum(not note_problems(g, e) for g, e in zip(got, exp))

        cons = build_constraints(Scenario.model_validate(case["input"]), [directive_from_interpretation(e) for e in exp])
        violations = validate_plan(cons, body.get("hourly_plan", []), tol=TOL)
        try:
            violations += validate_totals(body["hourly_plan"], cons.tariff, float(body["total_grid_kwh"]),
                                          float(body["total_cost_bdt"]), float(body["peak_grid_kwh"]))
        except (KeyError, TypeError, ValueError):
            violations.append("totals missing")
        cost = float(body.get("total_cost_bdt") or 0)
        if not violations:
            plans_ok += 1
            gaps.append(100 * (cost - optimum) / optimum)
        print(f"{case['id']}  {ms:6.0f} ms  interpretation {'ok  ' if not problems else 'MISS'}  "
              f"plan {'valid  ' if not violations else 'INVALID'}  cost {cost:,.2f}  optimum {optimum:,.2f}")
        for p in problems + violations[:5]:
            print(f"      {p}")

    n = len(cases)
    latencies.sort()
    print("\nSUMMARY")
    print(f"  notes interpreted correctly  {notes_ok}/{notes_total}")
    print(f"  valid plans                  {plans_ok}/{n}")
    if gaps:
        print(f"  cost above optimum           {max(gaps):.2f}% worst, {sum(gaps) / len(gaps):.2f}% mean")
    print(f"  latency                      p50 {latencies[n // 2]:.0f} ms, max {latencies[-1]:.0f} ms")
    return 0 if notes_ok == notes_total and plans_ok == n else 1


if __name__ == "__main__":
    sys.exit(main())
