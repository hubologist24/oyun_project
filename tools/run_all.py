# [PROJECT: tools] [ENTRY POINT]
# [SYMBOLS] classes: Gate, CombinedReport | functions: _c, _green, _red, _yellow, _blue, _bold, _reset_difficulty, _run_map, _run_combat, _run_adventure ...
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

ALL_WEAPON_SUBTYPES = ["sword", "greatsword", "bow",
                       "short_wand", "long_wand", "gun"]


def _c(code: str, text: str) -> str:
    if not _COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def _green(t):  return _c("92", t)
def _red(t):    return _c("91", t)
def _yellow(t): return _c("93", t)
def _blue(t):   return _c("96", t)
def _bold(t):   return _c("1", t)


def _reset_difficulty() -> None:
    import core.config as config
    from core import difficulty as diff_mod
    D = diff_mod.D
    preset = getattr(config, "DIFFICULTY_PRESET", None)
    if preset and preset in diff_mod.PRESETS:
        D.percent = diff_mod.PRESETS[preset]
    else:
        D.percent = getattr(config, "DIFFICULTY_PERCENT", 0)
    D.player_level = 1


@dataclass
class Gate:
    bot: str
    metric: str
    op: str
    threshold: float
    actual: float
    passed: bool

    def to_dict(self):
        return {"bot": self.bot, "metric": self.metric, "op": self.op,
                "threshold": self.threshold, "actual": self.actual,
                "passed": self.passed}


@dataclass
class CombinedReport:
    started_at: float = field(default_factory=time.time)
    elapsed_s: float = 0.0
    skipped: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    gates: list = field(default_factory=list)
    map_summary: Optional[dict] = None
    combat_summary: Optional[dict] = None
    adventure_summary: Optional[dict] = None

    def verdict(self) -> str:
        if self.errors:
            return "FAIL"
        if any(not g["passed"] for g in self.gates):
            return "FAIL"
        if self.skipped == ["map", "combat", "adventure"]:
            return "SKIP"
        return "PASS"

    def to_dict(self):
        return {
            "started_at": self.started_at,
            "elapsed_s": round(self.elapsed_s, 2),
            "overall_verdict": self.verdict(),
            "skipped": self.skipped,
            "errors": self.errors,
            "gates": self.gates,
            "bots": {
                "map":       self.map_summary,
                "combat":    self.combat_summary,
                "adventure": self.adventure_summary,
            },
        }


def _run_map(args) -> tuple[dict, float]:
    import tools.map_bot as mb

    cfg = mb.BotConfig()
    overrides = {
        "area_level": args.map_level,
        "biome":      args.map_biome,
        "archetype":  args.map_archetype,
        "size_tier":  args.map_size_tier,
    }
    results, elapsed = mb.run_batch(
        count=args.map_count,
        base_seed=args.map_seed,
        cfg=cfg,
        overrides=overrides,
        workers=args.map_workers,
        no_guardian_rate=args.map_no_guardian_rate,
    )
    summary = mb.summarize(results, elapsed)
    mb.print_summary(summary)
    return summary, elapsed


def _run_combat(args) -> tuple[dict, float]:
    import tools.combat_bot as cb

    combat_cfg = cb.CombatBotConfig()
    scenario = {
        "setup":       args.combat_scenario,
        "area_level":  args.combat_level,
        "count":       args.combat_enemies,
        "kind":        args.combat_boss_kind,
        "max_seconds": args.combat_max_seconds,
        "start_level": args.combat_start_level,
    }
    results, elapsed = cb.run_batch(
        count=args.combat_count,
        base_seed=args.combat_seed,
        cfg=combat_cfg,
        scenario=scenario,
        verbose=args.verbose,
    )
    summary = cb.summarize(results, elapsed)
    cb.print_summary(summary)
    return summary, elapsed


def _run_adventure(args) -> tuple[dict, float]:
    import tools.adventure_bot as ab

    adv_cfg = ab.AdventureConfig(
        extend_free      = not args.adventure_spend_stones,
        auto_equip       = not args.adventure_no_auto_equip,
        max_extensions   = args.adventure_max_extensions,
        max_time_seconds = args.adventure_max_time,
        start_level      = args.adventure_start_level,

        god_stones        = args.adventure_god_stones,
        bypass_seals      = args.adventure_bypass_seals,
        bypass_level_req  = args.adventure_bypass_level_req,
        boss_hp_scale     = args.adventure_boss_hp_scale,
        boss_damage_scale = args.adventure_boss_damage_scale,
        tutorial_boss_hp  = args.adventure_tutorial_boss_hp,
    )
    results, elapsed = ab.run_batch(
        count=args.adventure_count,
        base_seed=args.adventure_seed,
        cfg=adv_cfg,
        verbose=args.verbose,
    )
    summary = ab.summarize(results, elapsed)
    ab.print_summary(summary)
    return summary, elapsed


def _gate(bot: str, metric: str, op: str,
          actual: float, threshold: float) -> Gate:
    if op == "<=":
        passed = actual <= threshold
    elif op == ">=":
        passed = actual >= threshold
    else:
        raise ValueError(f"bad op {op!r}")
    return Gate(bot=bot, metric=metric, op=op,
                threshold=threshold, actual=round(actual, 4), passed=passed)


def _print_gate(g: Gate) -> None:
    status = _green("PASS") if g.passed else _red("FAIL")
    print(f"   gate [{g.metric}]: {g.actual} {g.op} {g.threshold}   {status}")


def _banner(title: str, color=_blue) -> None:
    line = "=" * 66
    print()
    print(color(line))
    print(color(f"  {title}"))
    print(color(line))


def _run_adventure_matrix(args) -> tuple[dict, float]:
    """Run the adventure bot across every (level, weapon) combination."""
    import tools.adventure_bot as ab

    levels  = args.levels if args.levels else [args.adventure_start_level or 10]
    weapons = args.weapon_subtypes if args.weapon_subtypes else [None]
    if weapons == ["all"]:
        weapons = ALL_WEAPON_SUBTYPES

    matrix: dict = {}
    t0 = time.time()
    total = len(levels) * len(weapons)
    idx = 0

    for level in levels:
        for weapon in weapons:
            idx += 1
            weapon_label = weapon or "random"
            print()
            _banner(f"[{idx}/{total}] Level {level} / Weapon {weapon_label}", _blue)

            cfg = ab.AdventureConfig(
                extend_free          = True,
                auto_equip           = not args.adventure_no_auto_equip,
                max_extensions       = args.adventure_max_extensions,
                max_time_seconds     = args.adventure_max_time,
                start_level          = level,
                force_weapon_subtype = weapon,
                god_stones           = args.adventure_god_stones,
                bypass_seals         = args.adventure_bypass_seals,
                bypass_level_req     = args.adventure_bypass_level_req,
                boss_hp_scale        = args.adventure_boss_hp_scale,
                boss_damage_scale    = args.adventure_boss_damage_scale,
                tutorial_boss_hp     = args.adventure_tutorial_boss_hp,
            )

            key = f"{level}|{weapon_label}"
            try:
                results, elapsed = ab.run_batch(
                    count     = args.adventure_count,
                    base_seed = args.adventure_seed,
                    cfg       = cfg,
                    verbose   = args.verbose,
                )
                summary = ab.summarize(results, elapsed)
                ab.print_summary(summary)
                matrix[key] = {"level": level, "weapon": weapon_label,
                               "summary": summary}
            except Exception as e:
                print(_red(f"[matrix] crashed on {key}: {type(e).__name__}: {e}"))
                if args.verbose:
                    print(traceback.format_exc())
                matrix[key] = {"level": level, "weapon": weapon_label,
                               "error": f"{type(e).__name__}: {e}"}

    elapsed = time.time() - t0
    _print_level_weapon_matrix(matrix, levels, weapons)
    return {"levels": levels,
            "weapons": [w or "random" for w in weapons],
            "results": matrix}, elapsed


def _run_combat_matrix(args) -> tuple[dict, float]:
    import tools.combat_bot as cb

    levels  = args.levels if args.levels else [args.combat_level]
    weapons = args.weapon_subtypes if args.weapon_subtypes else [None]
    if weapons == ["all"]:
        weapons = ALL_WEAPON_SUBTYPES

    matrix: dict = {}
    t0 = time.time()
    total = len(levels) * len(weapons)
    idx = 0

    for level in levels:
        for weapon in weapons:
            idx += 1
            weapon_label = weapon or "random"
            print()
            _banner(f"[{idx}/{total}] Level {level} / Weapon {weapon_label}", _blue)

            combat_cfg = cb.CombatBotConfig(max_seconds=args.combat_max_seconds)
            scenario = {
                "setup":                args.combat_scenario,
                "area_level":           level,
                "count":                args.combat_enemies,
                "kind":                 args.combat_boss_kind,
                "max_seconds":          args.combat_max_seconds,
                "start_level":          level,
                "force_weapon_subtype": weapon,
            }

            key = f"{level}|{weapon_label}"
            try:
                results, elapsed = cb.run_batch(
                    count     = args.combat_count,
                    base_seed = args.combat_seed,
                    cfg       = combat_cfg,
                    scenario  = scenario,
                    verbose   = args.verbose,
                )
                summary = cb.summarize(results, elapsed)
                cb.print_summary(summary)
                matrix[key] = {"level": level, "weapon": weapon_label,
                               "summary": summary}
            except Exception as e:
                print(_red(f"[matrix] crashed on {key}: {type(e).__name__}: {e}"))
                if args.verbose:
                    print(traceback.format_exc())
                matrix[key] = {"level": level, "weapon": weapon_label,
                               "error": f"{type(e).__name__}: {e}"}

    elapsed = time.time() - t0
    _print_level_weapon_matrix(matrix, levels, weapons, mode="combat")
    return {"levels": levels,
            "weapons": [w or "random" for w in weapons],
            "results": matrix}, elapsed

def _print_level_weapon_matrix(matrix, levels, weapons, mode="adventure"):
    _banner(f"COMPARISON MATRIX ({mode})", _bold)

    header_fmt = "  {:<6} {:<14} {:<7} {:<6} {:<8} {:<8} {:<11} {:<11} {:<8} {:<8}"
    row_fmt    = "  {:<6} {:<14} {:<7} {:<6} {:<8} {:<8} {:<11} {:<11} {:<8} {:<8}"

    if mode == "combat":
        headers = ["Level", "Weapon", "Cleared", "Deaths",
                   "DPS", "DmgTaken", "Kills", "Attacks",
                   "Time(s)", "HP%"]
    else:
        headers = ["Level", "Weapon", "Ext", "Lvl", "Deaths",
                   "Kills", "DmgDealt", "DmgTaken",
                   "DPS~", "Time(s)"]

    print(header_fmt.format(*headers))
    print("  " + "-" * 106)

    for level in levels:
        for weapon in weapons:
            key = f"{level}|{weapon or 'random'}"
            entry = matrix.get(key, {})
            wname = weapon or "random"

            if "error" in entry:
                print(row_fmt.format(level, wname, "ERR", "-", "-", "-",
                                     "-", "-", "-", "-") + f"  {_red('ERROR')}")
                continue

            s = entry.get("summary", {}) or {}
            #tm = (s.get("time_seconds") or {}).get("mean", 0) or 0
            #dd = (s.get("damage_dealt") or {}).get("mean", 0) or 0
            #dps = round(dd / tm, 1) if tm > 0 else 0.0

            tm = (s.get("time_seconds") or {}).get("mean", 0) or 0
            if mode == "combat":
                dps = (s.get("dps") or {}).get("mean", 0) or 0
            else:
                dd = (s.get("damage_dealt") or {}).get("mean", 0) or 0
                dps = round(dd / tm, 1) if tm > 0 else 0.0

            if mode == "combat":
                deaths  = (s.get("deaths") or {}).get("mean", 0) \
                          if "deaths" in s else s.get("died", 0)
                killed  = (s.get("kills") or {}).get("mean", 0) \
                          if "kills" in s else "-"
                cleared = s.get("clear_rate", 0)
                hp_pct  = (s.get("hp_frac") or 0) * 100 if "hp_frac" in s \
                          else round(100 * (1 - s.get("death_rate", 0)), 1)
                print(row_fmt.format(
                    level, wname,
                    f"{cleared:.0%}" if isinstance(cleared, float) else cleared,
                    deaths, dps,
                    (s.get("damage_taken") or {}).get("mean", 0),
                    killed, s.get("valid_scenarios", "-"),
                    tm, f"{hp_pct:.0f}%",
                ))
            else:
                ext = (s.get("extensions") or {}).get("mean", 0)
                lvl = (s.get("level") or {}).get("mean", 0)
                dth = (s.get("deaths") or {}).get("mean", 0)
                kl  = (s.get("kills") or {}).get("mean", 0)
                dt  = (s.get("damage_taken") or {}).get("mean", 0)
                print(row_fmt.format(level, wname, ext, lvl, dth, kl,
                                     dd, dt, dps, tm))
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Run all three bots (map / combat / adventure) and combine reports.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    ap.add_argument("--seed-all", type=int, default=None,
                    help="Set map/combat/adventure seeds all to this value.")
    ap.add_argument("--out", help="Write combined JSON report here.")
    ap.add_argument("--ci", action="store_true",
                    help="Exit non-zero if any gate fails.")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--no-color", action="store_true")

    ap.add_argument("--skip-map",       action="store_true")
    ap.add_argument("--skip-combat",    action="store_true")
    ap.add_argument("--skip-adventure", action="store_true")

    ap.add_argument("--map-count", type=int, default=500)
    ap.add_argument("--map-seed",  type=int, default=1)
    ap.add_argument("--map-workers", type=int, default=1)
    ap.add_argument("--map-level",  type=int)
    ap.add_argument("--map-biome")
    ap.add_argument("--map-archetype",
                    choices=["branch_merge", "hub_spokes", "loop", "gauntlet", "labyrinth"])
    ap.add_argument("--map-size-tier", type=int, choices=[-1, 0, 1, 2])
    ap.add_argument("--map-no-guardian-rate", type=float, default=0.0)
    ap.add_argument("--map-max-reject", type=float, default=0.15,
                    help="CI gate: reject_rate must be <= this.")

    ap.add_argument("--combat-count", type=int, default=50)
    ap.add_argument("--combat-seed",  type=int, default=1)
    ap.add_argument("--combat-level", type=int, default=5)
    ap.add_argument("--combat-enemies", type=int, default=4)
    ap.add_argument("--combat-boss-kind", default="hollow_warden",
                    help="hollow_warden|dash_predator|ranged_zoner|summoner|reflector|berserker")
    ap.add_argument("--combat-start-level", type=int, default=None)
    ap.add_argument("--combat-max-seconds", type=float, default=90.0)
    ap.add_argument("--combat-max-death", type=float, default=0.40,
                    help="CI gate: death_rate must be <= this.")

    ap.add_argument("--adventure-count", type=int, default=3)
    ap.add_argument("--adventure-seed",  type=int, default=1)
    ap.add_argument("--adventure-max-extensions", type=int, default=3)
    ap.add_argument("--adventure-max-time", type=float, default=300.0)
    ap.add_argument("--adventure-start-level", type=int, default=None)
    ap.add_argument("--adventure-spend-stones", action="store_true",
                    help="Spend Extension Stones to extend (default: debug/free).")
    ap.add_argument("--adventure-no-auto-equip", action="store_true")
    

    ap.add_argument("--combat-scenario", choices=["wave", "elite", "boss", "mixed"],
                default="mixed")

    ap.add_argument("--adventure-god-stones", action="store_true")
    ap.add_argument("--adventure-bypass-seals", action="store_true")
    ap.add_argument("--adventure-bypass-level-req", action="store_true")
    ap.add_argument("--adventure-boss-hp-scale", type=float, default=1.0)
    ap.add_argument("--adventure-boss-damage-scale", type=float, default=1.0)
    ap.add_argument("--adventure-tutorial-boss-hp", type=int, default=None)  

    ap.add_argument("--adventure-min-extensions", type=float, default=2.0,
                    help="CI gate: mean extensions must be >= this.") 


    ap.add_argument("--levels", type=int, nargs="+", default=None,
                    help="Matrix mode: run the same bot at each of these levels "
                         "(e.g. --levels 5 10 20 50 75 100 300 500).")
    ap.add_argument("--weapon-subtypes", type=str, nargs="+", default=None,
                    help="Matrix mode: restrict the starting weapon to these subtypes "
                         "(e.g. --weapon-subtypes gun bow greatsword short_wand) "
                         "or 'all' to sweep every subtype.")
    ap.add_argument("--matrix-bot", choices=["adventure", "combat"],
                    default="adventure",
                    help="Which bot to sweep in matrix mode (default: adventure).") 

     # --- NEW: matrix-mode aliases for the adventure flags ---
    ap.add_argument("--god-stones", action="store_true",
                    help="Matrix alias for --adventure-god-stones.")
    ap.add_argument("--bypass-seals", action="store_true",
                    help="Matrix alias for --adventure-bypass-seals.")
    ap.add_argument("--bypass-level-req", action="store_true",
                    help="Matrix alias for --adventure-bypass-level-req.")
    
    
    args = ap.parse_args(argv)

    # Fold matrix-mode aliases into the canonical adventure flags
    if args.god_stones:
        args.adventure_god_stones = True
    if args.bypass_seals:
        args.adventure_bypass_seals = True
    if args.bypass_level_req:
        args.adventure_bypass_level_req = True

    global _COLOR
    if args.no_color:
        _COLOR = False

    if args.seed_all is not None:
        args.map_seed = args.combat_seed = args.adventure_seed = args.seed_all

     # -------- matrix mode short-circuit --------
    matrix_mode = (args.levels is not None) or (args.weapon_subtypes is not None)

    if matrix_mode:
        _reset_difficulty()
        report = CombinedReport()
        matrix_summary = None
        try:
            if args.matrix_bot == "combat":
                matrix_summary, _ = _run_combat_matrix(args)
                report.combat_summary = matrix_summary
            else:
                matrix_summary, _ = _run_adventure_matrix(args)
                report.adventure_summary = matrix_summary
        except Exception as e:
            report.errors.append({"bot": args.matrix_bot,
                                  "error": f"{type(e).__name__}: {e}",
                                  "traceback": traceback.format_exc()})
            print(_red(f"[run_all] matrix bot crashed: {e}"))
            if args.verbose:
                print(traceback.format_exc())

        report.elapsed_s = time.time() - report.started_at
        verdict = report.verdict()
        _banner("OVERALL (matrix mode)", _bold)
        print(f"  Bot:          {args.matrix_bot}")
        print(f"  Errors:       {len(report.errors)}")
        print(f"  Total time:   {report.elapsed_s:.1f}s")
        print(f"  Verdict:      " +
              (_green("PASS") if not report.errors
               else _red("FAIL")))

        if args.out:
            payload = report.to_dict()
            payload["matrix"] = matrix_summary
            out_dir = os.path.dirname(os.path.abspath(args.out))
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=str)
            print(f"\n  Matrix report -> {args.out}")

        if args.ci and report.errors:
            return 1
        return 0
    # -------- end matrix mode --------

    report = CombinedReport()

    if args.skip_map:       report.skipped.append("map")
    if args.skip_combat:    report.skipped.append("combat")
    if args.skip_adventure: report.skipped.append("adventure")

    if not args.skip_map:
        _banner("MAP BOT", _blue)
        _reset_difficulty()
        try:
            summary, _ = _run_map(args)
            report.map_summary = summary
            g = _gate("map", "reject_rate", "<=",
                      summary.get("reject_rate", 1.0),
                      args.map_max_reject)
            report.gates.append(g.to_dict())
            _print_gate(g)
        except Exception as e:
            err = {"bot": "map", "error": f"{type(e).__name__}: {e}",
                   "traceback": traceback.format_exc()}
            report.errors.append(err)
            print(_red(f"[run_all] map bot crashed: {e}"))
            if args.verbose:
                print(err["traceback"])

    if not args.skip_combat:
        _banner("COMBAT BOT", _blue)
        _reset_difficulty()
        try:
            summary, _ = _run_combat(args)
            report.combat_summary = summary
            g = _gate("combat", "death_rate", "<=",
                      summary.get("death_rate", 1.0),
                      args.combat_max_death)
            report.gates.append(g.to_dict())
            _print_gate(g)

            # Death rate alone is insufficient: an invalid/empty scenario can
            # otherwise report zero deaths and falsely pass the combat gate.
            valid_scenarios = summary.get("valid_scenarios", 0)
            total_scenarios = summary.get("scenarios", args.combat_count)
            valid_rate = valid_scenarios / max(1, total_scenarios)
            g = _gate("combat", "valid_scenario_rate", ">=",
                      valid_rate, 1.0)
            report.gates.append(g.to_dict())
            _print_gate(g)
        except Exception as e:
            err = {"bot": "combat", "error": f"{type(e).__name__}: {e}",
                   "traceback": traceback.format_exc()}
            report.errors.append(err)
            print(_red(f"[run_all] combat bot crashed: {e}"))
            if args.verbose:
                print(err["traceback"])

    if not args.skip_adventure:
        _banner("ADVENTURE BOT", _blue)
        _reset_difficulty()
        try:
            summary, _ = _run_adventure(args)
            report.adventure_summary = summary

            ext_mean = (summary.get("extensions") or {}).get("mean", 0.0)
            bypass_mode = (args.adventure_bypass_seals
                           and args.adventure_bypass_level_req)
            if bypass_mode:
                gate_target = float(args.adventure_max_extensions) * 0.8
            else:
                gate_target = args.adventure_min_extensions

            g = _gate("adventure", "extensions.mean", ">=",
                      ext_mean, gate_target)
            report.gates.append(g.to_dict())
            _print_gate(g)
        except Exception as e:
            err = {"bot": "adventure", "error": f"{type(e).__name__}: {e}",
                   "traceback": traceback.format_exc()}
            report.errors.append(err)
            print(_red(f"[run_all] adventure bot crashed: {e}"))
            if args.verbose:
                print(err["traceback"])

    report.elapsed_s = time.time() - report.started_at
    verdict = report.verdict()
    passed_gates = sum(1 for g in report.gates if g["passed"])
    total_gates  = len(report.gates)

    _banner("OVERALL", _bold)
    print(f"  Bots run:     {3 - len(report.skipped)}/3"
          + (f"   (skipped: {', '.join(report.skipped)})" if report.skipped else ""))
    print(f"  Gates passed: {passed_gates}/{total_gates}")
    print(f"  Bot crashes:  {len(report.errors)}")
    print(f"  Total time:   {report.elapsed_s:.1f}s")
    if verdict == "PASS":
        print(f"  Verdict:      {_green('PASS')}")
    elif verdict == "SKIP":
        print(f"  Verdict:      {_yellow('SKIPPED (no bots ran)')}")
    else:
        print(f"  Verdict:      {_red('FAIL')}")
        for g in report.gates:
            if not g["passed"]:
                print(f"    - {g['bot']}: {g['metric']} = {g['actual']} "
                      f"{g['op']} {g['threshold']}  " + _red("[FAIL]"))
        for err in report.errors:
            print(f"    - crash: {err['bot']}: {err['error']}")

    if args.out:
        try:
            out_dir = os.path.dirname(os.path.abspath(args.out))
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
                
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, indent=2, default=str)
            print(f"\n  Combined report -> {args.out}")
        except OSError as e:
            print(_red(f"\n  [run_all] Could not write {args.out}: {e}"))
            if args.ci:
                return 1

    if args.ci:
        if report.errors:
            return 1
        if any(not g["passed"] for g in report.gates):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
