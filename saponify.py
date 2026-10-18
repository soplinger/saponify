#!/usr/bin/env python3
"""Saponify: an offline cold-process soap lye and batch calculator.

Reads a plain-text recipe, then works out sodium or potassium hydroxide,
water, fragrance load, fatty-acid quality scores and a cure timeline.
Standard library only; nothing leaves your machine.
"""

import argparse
import datetime as dt
import difflib
import json
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"
DEFAULT_WATER_PCT = 38.0
DEFAULT_SUPERFAT_PCT = 5.0
CURE_DAYS = 28
LYE_NAMES = {"naoh": "NaOH", "koh": "KOH"}
UNIT_TO_GRAMS = {
    "g": 1.0,
    "kg": 1000.0,
    "oz": 28.349523125,
    "lb": 453.59237,
}

# Saponification value (SAP) in grams of NaOH per gram of oil. Reference values;
# check them against your supplier's data before mixing. KOH values are derived
# from these with the molar-mass ratio, so every SAP in a recipe means NaOH.
KOH_PER_NAOH = 56.106 / 39.997
SAP = {
    "olive": 0.1337,
    "coconut": 0.1783,
    "palm": 0.1415,
    "palm kernel": 0.1560,
    "sunflower": 0.1354,
    "castor": 0.1286,
    "shea": 0.1283,
    "avocado": 0.1330,
    "sweet almond": 0.1357,
    "tallow": 0.1406,
    "cocoa butter": 0.1372,
    "canola": 0.1241,
}

# Approximate fatty-acid profiles, percent by weight of the oil.
PROFILES = {
    "olive": {"palmitic": 13, "stearic": 3, "oleic": 73, "linoleic": 10, "linolenic": 1},
    "coconut": {"caprylic": 8, "capric": 7, "lauric": 48, "myristic": 18,
                "palmitic": 9, "stearic": 2, "oleic": 6, "linoleic": 2},
    "palm": {"myristic": 1, "palmitic": 44, "stearic": 5, "oleic": 40, "linoleic": 10},
    "palm kernel": {"caprylic": 3, "capric": 4, "lauric": 48, "myristic": 16,
                    "palmitic": 8, "stearic": 2, "oleic": 17, "linoleic": 2},
    "sunflower": {"palmitic": 6, "stearic": 4, "oleic": 25, "linoleic": 65},
    "castor": {"palmitic": 1, "stearic": 1, "oleic": 4, "linoleic": 4, "ricinoleic": 90},
    "shea": {"palmitic": 5, "stearic": 40, "oleic": 47, "linoleic": 4},
    "avocado": {"palmitic": 17, "stearic": 1, "oleic": 60, "linoleic": 13},
    "sweet almond": {"palmitic": 6, "stearic": 2, "oleic": 70, "linoleic": 20},
    "tallow": {"myristic": 3, "palmitic": 26, "stearic": 14, "oleic": 44, "linoleic": 3},
    "cocoa butter": {"palmitic": 25, "stearic": 35, "oleic": 35, "linoleic": 3},
    "canola": {"palmitic": 4, "stearic": 2, "oleic": 62, "linoleic": 22, "linolenic": 9},
}

# Quality metrics: fatty acids summed, and the commonly cited target range.
QUALITY = {
    "hardness": (("lauric", "myristic", "palmitic", "stearic"), 29, 54),
    "cleansing": (("lauric", "myristic"), 12, 22),
    "bubbly": (("lauric", "myristic", "ricinoleic"), 14, 46),
    "creamy": (("palmitic", "stearic", "ricinoleic"), 16, 48),
    "conditioning": (("oleic", "linoleic", "linolenic", "ricinoleic"), 44, 69),
}

SETTINGS = ("title", "lye", "purity", "superfat", "water", "fragrance")
OIL_RE = re.compile(
    rf"(?P<name>.+?)\s+(?P<amount>{NUMBER}\s*[A-Za-z]+)(?:\s+sap=(?P<sap>{NUMBER}))?",
    re.IGNORECASE,
)


@dataclass
class Oil:
    name: str
    grams: float
    line: int
    sap: float | None = None


@dataclass
class Recipe:
    title: str = "Untitled batch"
    lye: str = "naoh"
    purity_pct: float = 100.0
    superfat_pct: float = DEFAULT_SUPERFAT_PCT
    water_pct: float = DEFAULT_WATER_PCT
    water_g: float | None = None
    fragrance_pct: float = 0.0
    oils: list[Oil] = field(default_factory=list)
    lines: dict[str, int] = field(default_factory=dict)


@dataclass
class Issue:
    severity: str
    line: int | None
    message: str


# ---------- parsing ----------

def parse_amount(text):
    """Return grams for text such as '500g', '1.5 kg' or '2 lb'."""
    match = re.fullmatch(rf"({NUMBER})\s*([A-Za-z]+)", text.strip())
    if not match:
        raise ValueError(f"expected an amount like 500g, got {text!r}")
    unit = match.group(2).lower()
    if unit not in UNIT_TO_GRAMS:
        raise ValueError(f"unknown unit {match.group(2)!r} (use g, kg, oz or lb)")
    grams = float(match.group(1)) * UNIT_TO_GRAMS[unit]
    if grams <= 0:
        raise ValueError("amount must be greater than zero")
    return grams


def _percent(text, low, high, name):
    match = re.fullmatch(rf"({NUMBER})\s*%?", text.strip())
    if not match:
        raise ValueError(f"{name} must be a percentage like 5%, got {text!r}")
    number = float(match.group(1))
    if not low <= number <= high:
        raise ValueError(f"{name} must be between {low:g} and {high:g}%, got {number:g}")
    return number


def normalise_name(name):
    return " ".join(name.lower().replace("_", " ").split())


def _suggest(name):
    matches = difflib.get_close_matches(name, SAP.keys(), n=1)
    return f" (did you mean {matches[0]!r}?)" if matches else ""


def _parse_oil(value, number):
    match = OIL_RE.fullmatch(value)
    if not match:
        raise ValueError(f"expected 'oil NAME AMOUNT', e.g. 'oil olive 500g'; got {value!r}")
    name = normalise_name(match.group("name"))
    grams = parse_amount(match.group("amount"))
    sap = float(match.group("sap")) if match.group("sap") else None
    if sap is not None and not 0.05 <= sap <= 0.4:
        hint = " It looks like mg KOH/g: divide by 1000, then by 1.403." if sap > 1 else ""
        raise ValueError(f"sap must be grams of NaOH per gram of oil (about 0.05 to 0.4), "
                         f"got {match.group('sap')}.{hint}")
    if sap is None and name not in SAP:
        raise ValueError(f"unknown oil {name!r}{_suggest(name)}; add sap=VALUE to use a custom oil")
    return Oil(name=name, grams=grams, line=number, sap=sap)


def _apply_setting(recipe, keyword, value):
    if not value:
        raise ValueError(f"{keyword!r} needs a value")
    if keyword == "title":
        recipe.title = value
    elif keyword == "lye":
        if value.lower() not in LYE_NAMES:
            raise ValueError(f"lye must be naoh or koh, got {value!r}")
        recipe.lye = value.lower()
    elif keyword == "purity":
        recipe.purity_pct = _percent(value, 1, 100, "purity")
    elif keyword == "superfat":
        recipe.superfat_pct = _percent(value, 0, 50, "superfat")
    elif keyword == "fragrance":
        recipe.fragrance_pct = _percent(value, 0, 100, "fragrance")
    elif keyword == "water":
        if value.endswith("%"):
            recipe.water_pct = _percent(value, 1, 200, "water")
        else:
            recipe.water_g = parse_amount(value)


def parse_recipe(text):
    """Return (recipe, errors). errors is a list of (line_number, message)."""
    recipe = Recipe()
    errors = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        keyword = parts[0].lower()
        value = parts[1].strip() if len(parts) > 1 else ""
        try:
            if keyword == "oil":
                recipe.oils.append(_parse_oil(value, number))
            elif keyword in SETTINGS:
                if keyword in recipe.lines:
                    raise ValueError(f"{keyword!r} is already set on line {recipe.lines[keyword]}")
                _apply_setting(recipe, keyword, value)
                recipe.lines[keyword] = number
            else:
                raise ValueError(f"unknown keyword {parts[0]!r}")
        except ValueError as exc:
            errors.append((number, str(exc)))
    return recipe, errors


# ---------- calculations ----------

def sap_for_lye(naoh_sap, lye):
    return naoh_sap * KOH_PER_NAOH if lye == "koh" else naoh_sap


def sap_value(oil, lye):
    naoh_sap = oil.sap if oil.sap is not None else SAP[oil.name]
    return sap_for_lye(naoh_sap, lye)


def fatty_profile(oils):
    """Return (profile in percent, grams of oil that have a known profile)."""
    weights = {}
    tracked = 0.0
    for oil in oils:
        if oil.name not in PROFILES:
            continue
        tracked += oil.grams
        for acid, pct in PROFILES[oil.name].items():
            weights[acid] = weights.get(acid, 0.0) + oil.grams * pct
    if tracked == 0:
        return {}, 0.0
    return {acid: weight / tracked for acid, weight in weights.items()}, tracked


def quality_scores(profile):
    if not profile:
        return {}
    scores = {}
    for metric, (acids, low, high) in QUALITY.items():
        value = sum(profile.get(acid, 0.0) for acid in acids)
        if value < low:
            status = "low"
        elif value > high:
            status = "high"
        else:
            status = "ok"
        scores[metric] = {"value": value, "low": low, "high": high, "status": status}
    return scores


def cure_timeline(start):
    """Dates are measured from the pour date. Cut after 24 to 48 hours, cure 4 weeks."""
    return {
        "cut_from": (start + dt.timedelta(days=1)).isoformat(),
        "cut_to": (start + dt.timedelta(days=2)).isoformat(),
        "ready": (start + dt.timedelta(days=CURE_DAYS)).isoformat(),
    }


def calculate(recipe, oil_total=None):
    if not recipe.oils:
        raise ValueError("recipe has no oil lines")
    oils = recipe.oils
    factor = 1.0
    if oil_total is not None:
        factor = oil_total / sum(oil.grams for oil in oils)
        oils = [replace(oil, grams=oil.grams * factor) for oil in oils]
    total = sum(oil.grams for oil in oils)

    theoretical = sum(oil.grams * sap_value(oil, recipe.lye) for oil in oils)
    lye_g = theoretical * (1 - recipe.superfat_pct / 100) / (recipe.purity_pct / 100)
    if recipe.water_g is not None:
        water_g = recipe.water_g * factor
    else:
        water_g = total * recipe.water_pct / 100

    shares = {}
    for oil in oils:
        shares[oil.name] = shares.get(oil.name, 0.0) + oil.grams
    profile, tracked = fatty_profile(oils)

    return {
        "title": recipe.title,
        "lye": recipe.lye,
        "purity_pct": recipe.purity_pct,
        "superfat_pct": recipe.superfat_pct,
        "oils": [
            {"name": name, "grams": grams, "percent": grams / total * 100}
            for name, grams in shares.items()
        ],
        "oil_total_g": total,
        "lye_g": lye_g,
        "water_g": water_g,
        "water_pct": recipe.water_pct if recipe.water_g is None else None,
        "water_ratio": water_g / lye_g,
        "lye_concentration_pct": lye_g / (lye_g + water_g) * 100,
        "fragrance_pct": recipe.fragrance_pct,
        "fragrance_g": total * recipe.fragrance_pct / 100,
        "coverage_pct": tracked / total * 100,
        "quality": quality_scores(profile),
        "timeline": None,
    }


# ---------- linting ----------

def lint(recipe, parse_errors):
    issues = [Issue("error", number, message) for number, message in parse_errors]
    if not recipe.oils:
        issues.append(Issue("error", None, "no oil lines; add at least one 'oil NAME AMOUNT' line"))
    if "superfat" in recipe.lines and recipe.superfat_pct < 2:
        issues.append(Issue("warning", recipe.lines["superfat"],
                            f"superfat {recipe.superfat_pct:g}% is low; the bar may feel harsh"))
    if "superfat" in recipe.lines and recipe.superfat_pct > 10:
        issues.append(Issue("warning", recipe.lines["superfat"],
                            f"superfat {recipe.superfat_pct:g}% is high; the bar may be soft"))
    if recipe.fragrance_pct > 6:
        issues.append(Issue("warning", recipe.lines.get("fragrance"),
                            f"fragrance {recipe.fragrance_pct:g}% is above 6% of oils; "
                            "check your supplier's usage limit"))

    seen = {}
    for oil in recipe.oils:
        if oil.name in seen:
            issues.append(Issue("warning", oil.line,
                                f"{oil.name!r} is also on line {seen[oil.name]}; "
                                "list each oil once so the shares read clearly"))
        else:
            seen[oil.name] = oil.line
        if oil.name not in PROFILES:
            issues.append(Issue("warning", oil.line,
                                f"no fatty-acid profile for {oil.name!r}; "
                                "quality scores leave it out"))

    if not parse_errors and recipe.oils:
        result = calculate(recipe)
        if not result["quality"]:
            issues.append(Issue("warning", None, "no fatty-acid profiles; quality scores unavailable"))
        for metric, score in result["quality"].items():
            if score["status"] != "ok":
                issues.append(Issue("warning", None,
                                    f"{metric} {score['value']:.1f} is {score['status']} "
                                    f"(target {score['low']}-{score['high']})"))
    return issues


# ---------- output ----------

def format_report(result):
    lye = LYE_NAMES[result["lye"]]
    out = [result["title"], ""]
    out.append(f"Lye: {lye}, purity {result['purity_pct']:g}%, superfat {result['superfat_pct']:g}%")
    out.append("")
    out.append(f"{'Oil':<22}{'Grams':>10}{'Share':>9}")
    for oil in result["oils"]:
        out.append(f"{oil['name']:<22}{oil['grams']:>10.1f}{oil['percent']:>8.1f}%")
    out.append(f"{'Total oils':<22}{result['oil_total_g']:>10.1f}{100.0:>8.1f}%")
    out.append("")

    lye_label = f"Lye ({lye})"
    out.append(f"{lye_label:<22}{result['lye_g']:>8.1f} g")
    if result["water_pct"] is not None:
        water_note = f"{result['water_pct']:g}% of oils"
    else:
        water_note = "set in grams"
    out.append(f"{'Water':<22}{result['water_g']:>8.1f} g  ({water_note})")
    out.append(f"{'Water:lye ratio':<22}{result['water_ratio']:>8.2f}")
    out.append(f"{'Lye concentration':<22}{result['lye_concentration_pct']:>7.1f}%")
    if result["fragrance_pct"]:
        out.append(f"{'Fragrance':<22}{result['fragrance_g']:>8.1f} g  "
                   f"({result['fragrance_pct']:g}% of oils)")

    out.append("")
    if result["quality"]:
        out.append(f"Quality (commonly cited ranges, based on {result['coverage_pct']:.0f}% of oil weight)")
        for metric, score in result["quality"].items():
            out.append(f"  {metric:<14}{score['value']:>6.1f}   "
                       f"{score['low']}-{score['high']}  {score['status']}")
    else:
        out.append("Quality: no fatty-acid profiles for these oils")

    if result["timeline"]:
        timeline = result["timeline"]
        out.append("")
        out.append("Cure timeline (from pour date)")
        out.append(f"  Cut:   {timeline['cut_from']} to {timeline['cut_to']} (24 to 48 hours)")
        out.append(f"  Ready: {timeline['ready']} ({CURE_DAYS} days)")
    return "\n".join(out)


def print_issues(path, issues):
    for issue in issues:
        where = f"{path}:{issue.line}" if issue.line else str(path)
        print(f"{where}: {issue.severity}: {issue.message}", file=sys.stderr)


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def cmd_calc(args):
    recipe, parse_errors = parse_recipe(read_text(args.recipe))
    issues = lint(recipe, parse_errors)
    print_issues(args.recipe, issues)
    if any(issue.severity == "error" for issue in issues):
        return 1
    result = calculate(recipe, args.oil_total)
    if args.start:
        result["timeline"] = cure_timeline(args.start)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(format_report(result))
    return 0


def cmd_check(args):
    recipe, parse_errors = parse_recipe(read_text(args.recipe))
    issues = lint(recipe, parse_errors)
    print_issues(args.recipe, issues)
    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warning"]
    if errors or (args.strict and warnings):
        print(f"{args.recipe}: {len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)
        return 1
    print(f"{args.recipe}: OK, {len(recipe.oils)} oil line(s), {len(warnings)} warning(s)")
    return 0


def cmd_oils(args):
    rows = [{"name": name, "sap": sap_for_lye(naoh_sap, args.lye)} for name, naoh_sap in SAP.items()]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    label = f"SAP ({LYE_NAMES[args.lye]})"
    print(f"{'Oil':<16}{label:>14}")
    for row in rows:
        print(f"{row['name']:<16}{row['sap']:>14.4f}")
    return 0


def _amount_arg(text):
    try:
        return parse_amount(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _date_arg(text):
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a date like 2026-10-17, got {text!r}") from exc


def build_parser():
    parser = argparse.ArgumentParser(
        prog="saponify",
        description="Offline cold-process soap lye and batch calculator.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    calc = sub.add_parser("calc", help="work out lye, water, quality and cure dates")
    calc.add_argument("recipe", help="plain-text recipe file")
    calc.add_argument("--oil-total", type=_amount_arg, metavar="AMOUNT",
                      help="scale all oils to this total, e.g. 2lb")
    calc.add_argument("--start", type=_date_arg, metavar="YYYY-MM-DD",
                      help="pour date, used for the cure timeline")
    calc.add_argument("--json", action="store_true", help="print machine-readable JSON")
    calc.set_defaults(handler=cmd_calc)

    check = sub.add_parser("check", help="lint a recipe file with line-numbered messages")
    check.add_argument("recipe", help="plain-text recipe file")
    check.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    check.set_defaults(handler=cmd_check)

    oils = sub.add_parser("oils", help="list built-in oils and their SAP values")
    oils.add_argument("--lye", choices=sorted(LYE_NAMES), default="naoh")
    oils.add_argument("--json", action="store_true", help="print machine-readable JSON")
    oils.set_defaults(handler=cmd_oils)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except OSError as exc:
        print(f"saponify: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
