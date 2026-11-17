#!/usr/bin/env python
"""Build data/examples.json: synthetic campus days, operator notes and the directives each note should become.

    python scripts/make_examples.py

Demand, solar and tariffs are generated from a fixed seed. Each case's optimal cost is computed by the
service's own LP under the expected directives, so the file doubles as a regression set for the tests.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.directives import build_constraints, directive_from_interpretation  # noqa: E402
from app.optimizer import build_plan, compute_totals, solve_lp  # noqa: E402
from app.schemas import Scenario  # noqa: E402

DEMAND = [90, 85, 80, 80, 85, 95, 110, 130, 150, 165, 175, 180, 180, 175, 170, 170, 175, 190, 200, 195, 180, 150, 120, 100]
SOLAR = [0, 0, 0, 0, 0, 0, 5, 25, 60, 95, 125, 140, 145, 135, 110, 75, 35, 8, 0, 0, 0, 0, 0, 0]
TARIFF = [5, 5, 5, 5, 5, 5, 6, 7, 8, 8, 8, 8, 8, 8, 8, 9, 10, 12, 13, 13, 12, 10, 7, 6]


def day(rng: random.Random) -> list[dict]:
    scale, sun = rng.uniform(0.9, 1.1), rng.uniform(0.8, 1.15)
    return [{"hour": h,
             "demand_kwh": 5 * round(DEMAND[h] * scale * rng.uniform(0.95, 1.05) / 5),
             "solar_kwh": 5 * round(SOLAR[h] * sun / 5),
             "tariff_bdt_per_kwh": TARIFF[h]} for h in range(24)]


def battery(capacity, initial, minimum, rate):
    return {"capacity_kwh": capacity, "initial_energy_kwh": initial, "minimum_energy_kwh": minimum,
            "max_charge_kwh_per_hour": rate, "max_discharge_kwh_per_hour": rate}


def d(kind, hours=None, why="", **values):
    if kind == "no_op":
        return {"directive_type": "no_op", "structured_adjustment": None, "explanation": why}
    return {"directive_type": kind, "structured_adjustment": {"hours": hours, **values}, "explanation": why}


CASES = [
    ("Panel washing with an unrelated note", battery(220, 110, 40, 50), [
        ("Panel washing is scheduled from 10 AM to noon; plan on roughly 40% of the usual solar output.",
         d("solar_reduction", [10, 11], "Washing leaves 40% of solar from 10 AM to noon.", factor=0.4)),
        ("The cafeteria menu changes on Friday.", d("no_op", why="A menu change does not affect today's energy plan.")),
    ]),
    ("Charger offline overnight", battery(200, 100, 30, 50), [
        ("The battery charger is offline from midnight until 6 AM for a firmware update.",
         d("no_charge_window", [0, 1, 2, 3, 4, 5], "The charger is offline from midnight to 6 AM.")),
    ]),
    ("Evening reserve given as a share of capacity", battery(250, 160, 40, 60), [
        ("Keep at least 60% of the battery capacity in reserve between 5 PM and 8 PM.",
         d("minimum_battery_reserve", [17, 18, 19], "60% of 250 kWh must stay stored from 5 PM to 8 PM.",
           minimum_energy_kwh=150)),
    ]),
    ("No discharge during relay testing", battery(220, 110, 40, 50), [
        ("Relay testing tonight: the battery must not discharge from 7 PM to 9 PM.",
         d("no_discharge_window", [19, 20], "No discharge during relay testing from 7 PM to 9 PM.")),
    ]),
    ("Feeder limit on grid import", battery(260, 180, 40, 60), [
        ("The feeder is limited to 160 kWh of grid import per hour from 6 PM until 10 PM.",
         d("max_grid_window", [18, 19, 20, 21], "Grid import is capped at 160 kWh from 6 PM to 10 PM.",
           max_grid_kwh=160)),
    ]),
    ("Haze and a discharge block", battery(220, 110, 40, 50), [
        ("Haze will cut solar output by 30% from 9 AM to 1 PM.",
         d("solar_reduction", [9, 10, 11, 12], "A 30% cut leaves 70% of solar from 9 AM to 1 PM.", factor=0.7)),
        ("Battery discharge is blocked from 5 PM until 7 PM while protection relays are checked.",
         d("no_discharge_window", [17, 18], "No discharge from 5 PM to 7 PM during relay checks.")),
    ]),
    ("Window that crosses midnight", battery(220, 110, 40, 50), [
        ("Do not charge the battery from 10 PM to 2 AM while the switchgear is serviced.",
         d("no_charge_window", [0, 1, 22, 23], "No charging from 10 PM through 2 AM.")),
    ]),
    ("All-day reserve", battery(240, 140, 40, 50), [
        ("Hold at least 100 kWh in the battery all day for the medical centre.",
         d("minimum_battery_reserve", list(range(24)), "At least 100 kWh must stay stored all day.",
           minimum_energy_kwh=100)),
    ]),
    ("Solar fully offline with a distractor", battery(220, 110, 40, 50), [
        ("Inverter maintenance: no usable solar from 11 AM until 1 PM.",
         d("solar_reduction", [11, 12], "No solar is usable from 11 AM to 1 PM.", factor=0.0)),
        ("Exam schedules will be posted next week.", d("no_op", why="Exam schedules do not affect today's energy plan.")),
    ]),
    ("Transformer cap and evening reserve", battery(250, 150, 40, 60), [
        ("Grid draw must stay at or below 165 kWh between 5 PM and 10 PM because of a transformer limit.",
         d("max_grid_window", [17, 18, 19, 20, 21], "Grid import is capped at 165 kWh from 5 PM to 10 PM.",
           max_grid_kwh=165)),
        ("Keep 80 kWh in the battery from 6 PM to 10 PM.",
         d("minimum_battery_reserve", [18, 19, 20, 21], "At least 80 kWh must stay stored from 6 PM to 10 PM.",
           minimum_energy_kwh=80)),
    ]),
    ("Only unrelated notes", battery(220, 110, 40, 50), [
        ("The parking lot lights are being replaced next month.",
         d("no_op", why="Work planned for next month does not affect today's schedule.")),
    ]),
    ("Three notes: roof work, discharge block, lecture", battery(220, 110, 40, 50), [
        ("Solar will be about one-fifth of the forecast from 1 PM to 3 PM during roof work.",
         d("solar_reduction", [13, 14], "Roof work leaves one-fifth of solar from 1 PM to 3 PM.", factor=0.2)),
        ("No battery discharge from 6 PM to 7 PM.", d("no_discharge_window", [18], "No discharge from 6 PM to 7 PM.")),
        ("A guest lecture moved to Thursday.", d("no_op", why="A rescheduled lecture does not affect today's energy plan.")),
    ]),
]


def main() -> None:
    rng = random.Random(2026)
    out = []
    for i, (label, bat, notes) in enumerate(CASES, 1):
        sid = f"EXAMPLE-{i:02d}"
        request = {"scenario_id": sid, "operator_notes": [n for n, _ in notes], "hours": day(rng), "battery": bat}
        interp = [{"note_index": k, "applies": e["directive_type"] != "no_op", **e} for k, (_, e) in enumerate(notes)]
        interp = [{key: e[key] for key in ("note_index", "applies", "directive_type", "structured_adjustment",
                                           "explanation")} for e in interp]
        cons = build_constraints(Scenario.model_validate(request), [directive_from_interpretation(e) for e in interp])
        sol = solve_lp(cons)
        if sol is None:
            raise SystemExit(f"{sid} is infeasible under its expected directives")
        _, cost, _ = compute_totals(build_plan(cons, sol), cons.tariff)
        out.append({"id": sid, "label": label, "input": request,
                    "expected_output": {"directive_interpretation": interp, "total_cost_bdt": cost}})
        print(f"{sid}  {label:<48} optimal cost {cost:,.2f} BDT")
    (ROOT / "data" / "examples.json").write_text(json.dumps({"cases": out}, indent=1) + "\n", encoding="utf-8")
    (ROOT / "data" / "sample_request.json").write_text(json.dumps(out[0]["input"], indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
