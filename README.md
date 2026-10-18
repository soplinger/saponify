# Saponify

**Offline cold-process soap calculator: lye, water, quality scores and cure dates from one plain-text recipe.**

Saponify reads a short recipe file and works out the exact amount of sodium or potassium hydroxide to add, how much water to use, how much fragrance to load, the fatty-acid quality profile of the bar, and when it can be cut and used. It is a single Python file with no dependencies, no network calls and no accounts.

> **Safety first.** Lye is caustic. Wear goggles and gloves, add lye to water (never water to lye), and work in a ventilated space. Saponify is a calculator, not a substitute for checking your supplier's SAP values and following a tested recipe.

## Features

- **Plain-text recipes**: a readable `.soap` file with `title`, `lye`, `purity`, `superfat`, `water`, `fragrance` and `oil` lines.
- **Lye for NaOH or KOH** from a built-in table of 12 common oils, using each oil's saponification value.
- **Superfat**: reduces lye by a chosen percentage, so the finished bar keeps some free oil.
- **Lye purity correction**: grosses up the lye weight for 98% flakes, 90% KOH and so on.
- **Water by percentage or grams**: defaults to 38% of oil weight, or set an exact gram amount.
- **Water:lye ratio and lye concentration** reported, so you can check the mix before you pour.
- **Oil shares**: each oil's grams and percentage of the batch, with repeated oils merged.
- **Fragrance load** calculated in grams from a percentage of oil weight.
- **Quality scores** (hardness, cleansing, bubbly, creamy, conditioning) from the fatty-acid profile, each flagged `low`, `ok` or `high` against commonly cited ranges.
- **Batch scaling**: `--oil-total 2lb` scales every oil, then recomputes lye and water.
- **Cure timeline**: `--start YYYY-MM-DD` gives the cut window (24 to 48 hours) and the date the bar is ready after a 28-day cure.
- **Units everywhere**: amounts take `g`, `kg`, `oz` or `lb` in any case, with or without a space (`500g`, `1.5 kg`, `3.5 oz`).
- **Custom oils** with `sap=VALUE` on the oil line, for anything not in the built-in table. The value is always the NaOH saponification value (g per g of oil); KOH is derived from it, so one number works for either lye.
- **Line-numbered lint** (`check`): errors and warnings as `file:line: severity: message`, with "did you mean" hints for misspelled oils and `--strict` for CI-style checks.
- **Machine-readable output**: `calc --json` and `oils --json` for scripts and spreadsheets.
- **Clear exit codes**: `0` success, `1` recipe errors (or warnings with `--strict`), `2` usage or file problems.

## Quick start

The repository includes `examples/lavender.soap`:

```text
# Lavender cold-process bar, about 1 kg of oils
title Lavender bar
lye naoh
purity 98%
superfat 5%
water 38%
fragrance 3%

oil olive 400g
oil coconut 300g
oil palm 200g
oil shea 3.5 oz   # 99.2 g, ounces are fine too
```

Check it, then calculate it with a pour date:

```bash
python3 saponify.py check examples/lavender.soap
python3 saponify.py calc examples/lavender.soap --start 2026-10-17
```

`calc` prints the oil table, lye, water, fragrance, quality scores and cure dates. To make a 2 lb batch from the same recipe:

```bash
python3 saponify.py calc examples/lavender.soap --oil-total 2lb
```

To see the built-in SAP values for KOH:

```bash
python3 saponify.py oils --lye koh
```

## How to run

Requires Python 3.10 or newer. No installation is needed.

| Command | What it does |
| --- | --- |
| `saponify.py calc RECIPE [--oil-total AMOUNT] [--start DATE] [--json]` | Work out lye, water, fragrance, quality and cure dates |
| `saponify.py check RECIPE [--strict]` | Lint the recipe and print line-numbered errors and warnings |
| `saponify.py oils [--lye naoh\|koh] [--json]` | List the built-in oils and their saponification values |

### Recipe format

| Line | Meaning |
| --- | --- |
| `title TEXT` | Batch name shown in the report |
| `lye naoh` or `lye koh` | Lye type (default `naoh`) |
| `purity PERCENT` | Lye purity, 1 to 100 (default 100%) |
| `superfat PERCENT` | Superfat, 0 to 50 (default 5%) |
| `water PERCENT%` or `water AMOUNT` | Water as percent of oils, or exact grams (default 38%) |
| `fragrance PERCENT` | Fragrance load as percent of oils |
| `oil NAME AMOUNT [sap=VALUE]` | An oil and its weight, for example `oil olive 500g`. `sap` is the NaOH value, 0.05 to 0.4 |

Anything after `#` is a comment, and blank lines are ignored. Each setting may appear once. Oil lines may repeat.

## How to test

The tests use the standard library `unittest` module, so they also run under pytest if you have it installed:

```bash
python3 -m unittest discover -s tests -v
# or, with pytest installed:
python3 -m pytest tests
```

The suite covers unit parsing, line-numbered parse errors, the lye and superfat maths against hand-worked values, purity and KOH, water rules, scaling, the quality ranges, cure dates, lint warnings, and the CLI exit codes on the example recipe.

## Reference data

- **SAP values** are reference figures for 12 oils. Your supplier's SAP data for your exact batch of oil is the authority, so check it before you mix.
- **Fatty-acid profiles** are approximate and vary between suppliers and cultivars.
- **Quality ranges** (hardness 29 to 54, cleansing 12 to 22, bubbly 14 to 46, creamy 16 to 48, conditioning 44 to 69) are commonly cited targets, not guarantees.

## Project layout

```text
saponify.py              the whole program (parser, calculations, CLI)
examples/lavender.soap   a sample recipe
tests/test_saponify.py   unit and CLI tests
```
