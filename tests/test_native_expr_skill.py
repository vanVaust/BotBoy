from __future__ import annotations

import asyncio
import math
import time
import unittest

from botboy.skills.manager import safe_calculate
from botboy.skills.native.base import NativeSkillContext
from botboy.skills.native.expr import ExprError, ExprLimits, SafeEvaluator, evaluate_expression, format_value, run


def evaluate(source: str, limits: ExprLimits = ExprLimits()):
    return SafeEvaluator(limits).evaluate(source)[0]


class ExprEvaluationTests(unittest.TestCase):
    def test_arithmetic_math_and_constants(self) -> None:
        self.assertEqual(evaluate("2**10 + sqrt(16)"), 1028.0)
        self.assertAlmostEqual(evaluate("sin(pi / 2) + log(e)"), 2.0)
        self.assertEqual(evaluate("17 // 5, 17 % 5, -7 // 2"), (3, 2, -4))
        self.assertEqual(evaluate("0b1010 & 0b0110 | 1 ^ 3"), (0b1010 & 0b0110) | (1 ^ 3))
        self.assertEqual(evaluate("pow(3, 10**18, 7)"), pow(3, 10**18, 7))
        self.assertEqual(evaluate("(1+2j) * 2"), 2 + 4j)

    def test_statements_variables_and_augmented_assignment(self) -> None:
        value, variables = SafeEvaluator().evaluate("r = 0.05; n = 10\nx = 1000 * (1 + r) ** n\nx += 1; round(x, 2)")
        self.assertEqual(value, round(1000 * 1.05 ** 10 + 1, 2))
        self.assertEqual(variables["n"], 10)
        self.assertEqual(evaluate("a, b = divmod(17, 5); a * 10 + b"), 32)

    def test_sequences_comprehensions_conditionals(self) -> None:
        self.assertEqual(evaluate("[x * x for x in range(10) if x % 2 == 0]"), [0, 4, 16, 36, 64])
        self.assertEqual(evaluate("[(a, b) for a in [1, 2] for b in [3, 4] if a + b > 4]"), [(1, 4), (2, 3), (2, 4)])
        self.assertEqual(evaluate("xs = [5, 3, 9]; sorted(xs, reverse=True)[0], xs[-1], xs[1:]"), (9, 9, [3, 9]))
        self.assertEqual(evaluate("10 if 1 < 2 < 3 else 20"), 10)
        self.assertEqual(evaluate("3 in [1, 2, 3] and not 4 in [1]"), True)

    def test_statistics_functions(self) -> None:
        data = "[2, 4, 4, 4, 5, 5, 7, 9]"
        self.assertEqual(evaluate(f"mean({data})"), 5)
        self.assertEqual(evaluate(f"pstdev({data})"), 2.0)
        self.assertEqual(evaluate(f"median({data})"), 4.5)
        self.assertEqual(evaluate(f"quantiles({data}, n=4)"), [4.0, 4.5, 6.5])
        slope, intercept = evaluate("linear_regression([1, 2, 3], [2, 4, 6])")
        self.assertAlmostEqual(slope, 2.0)
        self.assertAlmostEqual(intercept, 0.0)

    def test_escape_primitives_are_rejected_before_evaluation(self) -> None:
        for source in ("().__class__", "[].__class__.__mro__", "(lambda: 1)()", "__import__('os')",
                       "import os", "'abc'", "f'{1}'", "open('x')", "def f(): pass", "for i in [1]: pass",
                       "x = y = __builtins__", "getattr(1, 'real')", "{1: 2}", "{1, 2}", "(x := 3)",
                       "_private = 1", "pi = 3", "sqrt = 1", "print(1)", "eval('1')", "1 is 1"):
            with self.subTest(source=source), self.assertRaises(ExprError):
                evaluate(source)

    def test_resource_bombs_fail_fast(self) -> None:
        bombs = ("9**9**9", "10**5000", "2 << 10**6", "(10**2000) * (10**2000)", "factorial(10**6)",
                 "comb(10**7, 5)", "range(10**9)", "[0] * 10**6", "[[0] * 10000] * 2 + [[0]] * 9999 + [[0]]",
                 "sum([[1] * 5000] * 3, [])", "prod([10**1000] * 5)", "quantiles([1, 2], n=10**8)",
                 "lcm(2**4000, 3**2500)",
                 "[x for x in range(10000) for y in range(10000)]", "exp(10**6)", "float(10**400)")
        for source in bombs:
            with self.subTest(source=source):
                started = time.perf_counter()
                with self.assertRaises(ExprError):
                    evaluate(source)
                self.assertLess(time.perf_counter() - started, 1.5)

    def test_shared_reference_nesting_cannot_explode(self) -> None:
        # Review finding: each level is short, but comparison/str walk 10**12.
        programs = (
            "a=[0]*10000; b=[0]*10000; c=[a]*10000; d=[b]*10000; [c]*10000 == [d]*10000",
            "a=[0]*1000; b=[a]*1000; [b]*1000",
            "[[[0]*10000]*10000]*10000",
        )
        for source in programs:
            with self.subTest(source=source[:40]):
                started = time.perf_counter()
                with self.assertRaisesRegex(ExprError, "expands to more than|longer than"):
                    evaluate(source)
                self.assertLess(time.perf_counter() - started, 1.0)
        self.assertTrue(evaluate("[[1, 2]] * 3 == [[1, 2], [1, 2], [1, 2]]"))

    def test_step_and_time_budgets(self) -> None:
        with self.assertRaisesRegex(ExprError, "Step budget"):
            evaluate("[x for x in range(5000) for y in range(50)]", ExprLimits(max_steps=10_000))
        with self.assertRaisesRegex(ExprError, "Time budget"):
            evaluate("[x for x in range(5000)]", ExprLimits(max_seconds=0.0))

    def test_source_size_limits(self) -> None:
        with self.assertRaisesRegex(ExprError, "longer than"):
            evaluate("1+" * 1500 + "1")
        with self.assertRaisesRegex(ExprError, "too large"):
            evaluate("+".join(["1"] * 500))

    def test_errors_are_wrapped(self) -> None:
        for source, message in (("1/0", "Division by zero"), ("sqrt(-1)", "sqrt()"), ("undefined + 1", "Undefined"),
                                ("[1][5]", "out of range"), ("sqrt", "function"), ("mean([])", "mean()")):
            with self.subTest(source=source), self.assertRaisesRegex(ExprError, message):
                evaluate(source)

    def test_rendering_is_bounded_for_shared_references(self) -> None:
        # The evaluator now refuses to build this (expanded-size cap); the
        # renderer must still stay bounded for any value handed to it.
        with self.assertRaisesRegex(ExprError, "expands to more than"):
            evaluate("[[0] * 10000] * 10000")
        value = [[0] * 10000] * 10000
        started = time.perf_counter()
        rendered = format_value(value, limit=500)
        self.assertLess(time.perf_counter() - started, 0.5)
        self.assertLessEqual(len(rendered), 520)
        self.assertTrue(rendered.endswith("[truncated]"))


class ExprSkillTests(unittest.TestCase):
    def call(self, command: str) -> dict:
        return asyncio.run(run(command, NativeSkillContext()))

    def test_success_output_contract(self) -> None:
        result = self.call("expr xs = [1, 2, 3]; sum(xs) / len(xs)")
        self.assertTrue(result["success"])
        self.assertEqual(result["type"], "expr")
        self.assertEqual(result["output"].splitlines()[0], "= 2")
        self.assertEqual(result["data"]["result"], 2.0)
        self.assertEqual(result["data"]["variables"], {"xs": "[1, 2, 3]"})

    def test_large_results_are_not_embedded_as_json(self) -> None:
        result = self.call("expr 2**4000")
        self.assertTrue(result["success"])
        self.assertIsInstance(result["data"]["result"], str)

    def test_failure_and_usage(self) -> None:
        self.assertFalse(self.call("expr").get("success"))
        failed = self.call("expr ().__class__")
        self.assertFalse(failed["success"])
        self.assertIn("Unsupported syntax: Attribute", failed["output"])
        self.assertTrue(self.call("expr help")["success"])


class CalculatorHardeningTests(unittest.TestCase):
    def test_safe_calculate_keeps_previous_surface(self) -> None:
        self.assertEqual(safe_calculate("2 + 2"), 4)
        self.assertAlmostEqual(safe_calculate("sqrt(2) * cos(0)"), math.sqrt(2))
        self.assertEqual(safe_calculate("abs(-3) + max(1, 2) + int(2.9)"), 7)
        self.assertEqual(evaluate_expression("tau / 2"), math.pi)

    def test_safe_calculate_rejects_bombs_and_statements(self) -> None:
        for source in ("9**9**9", "x = 1", "().__class__"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                safe_calculate(source)


if __name__ == "__main__":
    unittest.main()


class CalculatorScalarResultTests(unittest.TestCase):
    def test_calculator_refuses_sequence_results(self) -> None:
        # Review finding: str() of a shared-reference list blocked the loop.
        for source in ("[[[0]*10000]*10000]*10000", "[1, 2, 3]", "(1, 2)"):
            with self.subTest(source=source):
                started = time.perf_counter()
                with self.assertRaises(ValueError):
                    safe_calculate(source)
                self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual(safe_calculate("2**10"), 1024)
        self.assertTrue(safe_calculate("1 < 2"))
