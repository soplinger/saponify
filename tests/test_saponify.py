import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import saponify  # noqa: E402

EXAMPLE = os.path.join(ROOT, "examples", "lavender.soap")


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = saponify.main(argv)
    return code, out.getvalue(), err.getvalue()


def recipe_from(text):
    recipe, errors = saponify.parse_recipe(text)
    if errors:
        raise AssertionError(f"unexpected parse errors: {errors}")
    return recipe


class ParseAmountTests(unittest.TestCase):
    def test_units_attached_spaced_and_case_insensitive(self):
        self.assertAlmostEqual(saponify.parse_amount("500g"), 500.0)
        self.assertAlmostEqual(saponify.parse_amount("500 g"), 500.0)
        self.assertAlmostEqual(saponify.parse_amount("1.5 kg"), 1500.0)
        self.assertAlmostEqual(saponify.parse_amount("2LB"), 907.18474, places=3)
        self.assertAlmostEqual(saponify.parse_amount("3.5 oz"), 99.2233, places=3)

    def test_rejects_missing_unit_unknown_unit_and_zero(self):
        for bad in ("500", "5 cups", "0g", "-3g", "g"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                saponify.parse_amount(bad)


class ParseRecipeTests(unittest.TestCase):
    def test_comments_and_blank_lines_are_skipped(self):
        recipe = recipe_from("# header\n\n  \noil olive 100g  # trailing note\n")
        self.assertEqual(len(recipe.oils), 1)
        self.assertEqual(recipe.oils[0].grams, 100.0)

    def test_errors_carry_line_numbers_and_do_not_stop_parsing(self):
        text = "oil olive 100g\nlye lithium\noil olive\noil coconut 50g\n"
        _, errors = saponify.parse_recipe(text)
        self.assertEqual([number for number, _ in errors], [2, 3])

    def test_unknown_oil_suggests_closest_name(self):
        _, errors = saponify.parse_recipe("oil olvie 500g\n")
        self.assertEqual(errors[0][0], 1)
        self.assertIn("did you mean 'olive'", errors[0][1])

    def test_custom_oil_needs_sap_and_is_accepted_with_it(self):
        _, errors = saponify.parse_recipe("oil mango butter 200g\n")
        self.assertTrue(errors)
        recipe = recipe_from("oil mango butter 200g sap=0.137\n")
        self.assertEqual(recipe.oils[0].name, "mango butter")
        self.assertEqual(recipe.oils[0].sap, 0.137)

    def test_repeated_setting_is_an_error(self):
        _, errors = saponify.parse_recipe("superfat 5%\nsuperfat 6%\n")
        self.assertEqual(errors[0][0], 2)
        self.assertIn("already set on line 1", errors[0][1])

    def test_out_of_range_percentages_are_errors(self):
        _, errors = saponify.parse_recipe("superfat 80%\npurity 0%\n")
        self.assertEqual([number for number, _ in errors], [1, 2])

    def test_water_accepts_percent_or_grams(self):
        self.assertEqual(recipe_from("water 40%\n").water_pct, 40.0)
        recipe = recipe_from("water 250g\n")
        self.assertEqual(recipe.water_g, 250.0)

    def test_lye_must_be_naoh_or_koh(self):
        _, errors = saponify.parse_recipe("lye LYE\n")
        self.assertEqual(errors[0][0], 1)


class CalculationTests(unittest.TestCase):
    def test_naoh_for_pure_olive_with_no_superfat(self):
        # 1000 g olive x 0.1337 g NaOH/g oil = 133.7 g
        recipe = recipe_from("superfat 0%\noil olive 1000g\n")
        result = saponify.calculate(recipe)
        self.assertAlmostEqual(result["lye_g"], 133.7, places=2)

    def test_superfat_reduces_lye_by_its_percentage(self):
        # 133.7 x 0.95 = 127.015
        recipe = recipe_from("superfat 5%\noil olive 1000g\n")
        self.assertAlmostEqual(saponify.calculate(recipe)["lye_g"], 127.015, places=3)

    def test_purity_correction_divides_by_purity(self):
        # 127.015 / 0.98 = 129.607
        recipe = recipe_from("purity 98%\nsuperfat 5%\noil olive 1000g\n")
        self.assertAlmostEqual(saponify.calculate(recipe)["lye_g"], 129.607, places=3)

    def test_koh_uses_potassium_sap_value(self):
        # 1000 g olive x 0.1337 g NaOH/g x 1.40274 = 187.55 g KOH
        recipe = recipe_from("lye koh\nsuperfat 0%\noil olive 1000g\n")
        self.assertAlmostEqual(saponify.calculate(recipe)["lye_g"], 187.55, delta=0.01)

    def test_custom_sap_is_naoh_and_converted_for_koh(self):
        recipe = recipe_from("lye koh\nsuperfat 0%\noil mango 1000g sap=0.1337\n")
        self.assertAlmostEqual(saponify.calculate(recipe)["lye_g"], 187.55, delta=0.01)

    def test_koh_ratio_matches_molar_masses(self):
        self.assertAlmostEqual(saponify.KOH_PER_NAOH, 56.106 / 39.997, places=6)

    def test_sap_outside_naoh_range_is_an_error(self):
        _, errors = saponify.parse_recipe("oil mango 200g sap=190\n")
        self.assertEqual(errors[0][0], 1)
        self.assertIn("mg KOH/g", errors[0][1])

    def test_water_in_grams_scales_with_oil_total(self):
        result = saponify.calculate(
            recipe_from("water 200g\noil olive 1000g\n"), oil_total=2000.0
        )
        self.assertAlmostEqual(result["water_g"], 400.0)

    def test_water_defaults_to_38_percent_of_oils(self):
        result = saponify.calculate(recipe_from("oil olive 1000g\n"))
        self.assertAlmostEqual(result["water_g"], 380.0)
        self.assertEqual(result["water_pct"], 38.0)

    def test_water_in_grams_overrides_percentage(self):
        result = saponify.calculate(recipe_from("water 200g\noil olive 1000g\n"))
        self.assertAlmostEqual(result["water_g"], 200.0)
        self.assertIsNone(result["water_pct"])

    def test_water_ratio_and_lye_concentration(self):
        result = saponify.calculate(recipe_from("superfat 0%\noil olive 1000g\n"))
        self.assertAlmostEqual(result["water_ratio"], 380.0 / 133.7, places=4)
        self.assertAlmostEqual(result["lye_concentration_pct"], 133.7 / 513.7 * 100, places=4)

    def test_shares_sum_to_100_and_merge_repeated_oils(self):
        recipe = recipe_from("oil olive 300g\noil olive 100g\noil coconut 600g\n")
        result = saponify.calculate(recipe)
        shares = {oil["name"]: oil["percent"] for oil in result["oils"]}
        self.assertAlmostEqual(shares["olive"], 40.0)
        self.assertAlmostEqual(sum(shares.values()), 100.0)

    def test_fragrance_load_in_grams(self):
        result = saponify.calculate(recipe_from("fragrance 3%\noil olive 1000g\n"))
        self.assertAlmostEqual(result["fragrance_g"], 30.0)

    def test_oil_total_scales_oils_lye_and_water(self):
        base = saponify.calculate(recipe_from("oil olive 500g\noil coconut 500g\n"))
        doubled = saponify.calculate(
            recipe_from("oil olive 500g\noil coconut 500g\n"), oil_total=2000.0
        )
        self.assertAlmostEqual(doubled["oil_total_g"], 2000.0)
        self.assertAlmostEqual(doubled["lye_g"], base["lye_g"] * 2)
        self.assertAlmostEqual(doubled["water_g"], base["water_g"] * 2)

    def test_quality_scores_use_fatty_acid_groups(self):
        profile = {"palmitic": 25.0, "stearic": 10.0, "oleic": 45.0, "linoleic": 20.0}
        scores = saponify.quality_scores(profile)
        self.assertEqual(scores["hardness"]["status"], "ok")
        self.assertAlmostEqual(scores["hardness"]["value"], 35.0)
        self.assertEqual(scores["cleansing"]["status"], "low")
        self.assertEqual(scores["conditioning"]["status"], "ok")

    def test_castor_is_flagged_high_for_bubbly_and_conditioning(self):
        result = saponify.calculate(recipe_from("oil castor 1000g\n"))
        self.assertEqual(result["quality"]["bubbly"]["status"], "high")
        self.assertEqual(result["quality"]["conditioning"]["status"], "high")

    def test_all_profiles_are_close_to_100_percent(self):
        for name, profile in saponify.PROFILES.items():
            with self.subTest(oil=name):
                self.assertTrue(88 <= sum(profile.values()) <= 101)

    def test_cure_timeline_dates(self):
        timeline = saponify.cure_timeline(dt.date(2026, 10, 17))
        self.assertEqual(timeline["cut_from"], "2026-10-18")
        self.assertEqual(timeline["cut_to"], "2026-10-19")
        self.assertEqual(timeline["ready"], "2026-11-14")


class LintTests(unittest.TestCase):
    def messages(self, text):
        recipe, errors = saponify.parse_recipe(text)
        return saponify.lint(recipe, errors)

    def test_warns_on_low_superfat_high_fragrance_and_custom_oil(self):
        issues = self.messages(
            "superfat 1%\nfragrance 8%\noil olive 500g\noil mango butter 500g sap=0.137\n"
        )
        text = " | ".join(issue.message for issue in issues)
        self.assertIn("superfat 1% is low", text)
        self.assertIn("fragrance 8% is above 6%", text)
        self.assertIn("no fatty-acid profile for 'mango butter'", text)

    def test_warns_on_duplicate_oil_lines(self):
        issues = self.messages("oil olive 100g\noil olive 50g\n")
        self.assertTrue(any(issue.line == 2 and "also on line 1" in issue.message for issue in issues))

    def test_error_for_recipe_with_no_oils(self):
        issues = self.messages("title Empty\n")
        self.assertTrue(any(issue.severity == "error" for issue in issues))

    def test_example_recipe_has_no_errors(self):
        with open(EXAMPLE, encoding="utf-8") as handle:
            issues = self.messages(handle.read())
        self.assertFalse([issue for issue in issues if issue.severity == "error"])


class CliTests(unittest.TestCase):
    def test_calc_on_example_exits_zero_and_reports_lye(self):
        code, out, _ = run_cli(["calc", EXAMPLE, "--start", "2026-10-17"])
        self.assertEqual(code, 0)
        self.assertIn("Lye (NaOH)", out)
        self.assertIn("Cure timeline", out)
        self.assertIn("Ready: 2026-11-14", out)

    def test_calc_json_is_valid_and_has_quality(self):
        code, out, _ = run_cli(["calc", EXAMPLE, "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("hardness", data["quality"])
        self.assertIsNone(data["timeline"])

    def test_calc_with_bad_recipe_exits_one_with_file_and_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.soap")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("oil olive 100g\noil nonsense 10g\n")
            code, out, err = run_cli(["calc", path])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn(f"{path}:2: error: unknown oil 'nonsense'", err)

    def test_check_ok_on_example(self):
        code, out, _ = run_cli(["check", EXAMPLE])
        self.assertEqual(code, 0)
        self.assertIn("OK", out)

    def test_check_strict_fails_on_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "warn.soap")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("superfat 1%\noil olive 100g\n")
            self.assertEqual(run_cli(["check", path])[0], 0)
            self.assertEqual(run_cli(["check", path, "--strict"])[0], 1)

    def test_oils_lists_olive_with_koh_value(self):
        code, out, _ = run_cli(["oils", "--lye", "koh"])
        self.assertEqual(code, 0)
        self.assertIn("olive", out)
        self.assertIn("0.1875", out)

    def test_bad_oil_total_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            saponify.main(["calc", EXAMPLE, "--oil-total", "lots"])
        self.assertEqual(ctx.exception.code, 2)

    def test_missing_file_returns_two(self):
        code, _, err = run_cli(["calc", os.path.join(ROOT, "does-not-exist.soap")])
        self.assertEqual(code, 2)
        self.assertIn("saponify:", err)


if __name__ == "__main__":
    unittest.main()
