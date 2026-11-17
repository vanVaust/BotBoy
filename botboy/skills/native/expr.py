"""``expr`` — sandboxed expression and mini-program evaluator.

No ``eval``/``exec`` is involved: source is parsed with ``ast`` and walked by
an interpreter that only knows an explicit allowlist of node types, operators
and functions. Attribute access, lambdas, imports, f-strings, strings and
every statement except assignment are rejected before evaluation starts.

Resource budgets make the evaluator safe to run inside the event loop:

* AST node count and source length are bounded before evaluation;
* every evaluated node consumes a step and the wall-clock deadline is checked;
* integer results are capped in bit length and ``**``/``*``/``<<`` are
  pre-checked so the CPU is never spent building a number that would then be
  rejected (``9**9**9`` fails instantly instead of hanging the process);
* sequences (``range``, list repetition, comprehensions) are length-capped;
* ``factorial``/``comb``/``perm`` arguments are bounded.
"""
from __future__ import annotations

import ast
import math
import operator
import statistics
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple

from botboy.skills.native.base import NativeSkillContext, failure, raw_argument_text, result


class ExprError(ValueError):
    """Rejected or failed evaluation."""


@dataclass(frozen=True)
class ExprLimits:
    max_source_chars: int = 2000
    max_nodes: int = 800
    max_steps: int = 200_000
    max_seconds: float = 1.0
    max_int_bits: int = 4096
    max_sequence: int = 10_000
    # Elements reachable through nesting, counting shared references each
    # time they occur: ``[[a]*10000]*10000`` is short at every level but a
    # comparison, str() or sort walks 10**8+ elements in one C call.
    max_total_elements: int = 100_000
    max_variables: int = 64
    max_factorial_arg: int = 1000
    max_comb_arg: int = 10_000


DEFAULT_LIMITS = ExprLimits()


def expanded_size(value: Any, cap: int) -> int:
    """Elements reachable from ``value`` with shared references counted per use.

    Memoised by object id, so the cost is O(distinct objects) even when the
    expanded size is astronomically large; stops counting once ``cap`` is passed.
    """
    memo: Dict[int, int] = {}

    def size(item: Any) -> int:
        if not isinstance(item, (list, tuple)):
            return 0
        key = id(item)
        if key in memo:
            return memo[key]
        memo[key] = cap + 1  # cycle guard; lists cannot be cyclic here, but be safe
        total = len(item)
        for child in item:
            total += size(child)
            if total > cap:
                break
        memo[key] = min(total, cap + 1)
        return memo[key]

    return size(value)

_MATH_NAMES = (
    "acos", "acosh", "asin", "asinh", "atan", "atan2", "atanh", "cbrt", "ceil", "copysign",
    "cos", "cosh", "degrees", "dist", "erf", "erfc", "exp", "exp2", "expm1", "fabs", "floor",
    "fmod", "frexp", "fsum", "gamma", "gcd", "hypot", "isclose", "isfinite", "isinf", "isnan",
    "isqrt", "ldexp", "lgamma", "log", "log10", "log1p", "log2", "modf", "nextafter", "pow",
    "radians", "remainder", "sin", "sinh", "sqrt", "tan", "tanh", "trunc", "ulp",
)
_STATISTICS_NAMES = (
    "mean", "fmean", "median", "median_low", "median_high", "mode", "multimode", "stdev",
    "pstdev", "variance", "pvariance", "geometric_mean", "harmonic_mean", "quantiles",
    "correlation", "covariance",
)
CONSTANTS: Dict[str, Any] = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf, "nan": math.nan}

_COMPARE_OPS: Dict[type, Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
}
_UNARY_OPS: Dict[type, Callable[[Any], Any]] = {
    ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_, ast.Invert: operator.invert,
}
_SIMPLE_BINOPS: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_, ast.BitXor: operator.xor, ast.RShift: operator.rshift,
}
_ALLOWED_NODES = (
    ast.Module, ast.Expression, ast.Expr, ast.Assign, ast.AugAssign, ast.Constant, ast.Name,
    ast.Load, ast.Store, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Call,
    ast.keyword, ast.List, ast.Tuple, ast.Subscript, ast.Slice, ast.ListComp, ast.GeneratorExp,
    ast.comprehension, ast.And, ast.Or, *_COMPARE_OPS.keys(), *_UNARY_OPS.keys(),
    *_SIMPLE_BINOPS.keys(), ast.Mult, ast.Pow, ast.LShift,
)


class _Budget:
    def __init__(self, limits: ExprLimits) -> None:
        self.limits = limits
        self.steps = 0
        # perf_counter, not monotonic: on Windows monotonic() advances in
        # ~15.6 ms ticks, so a short budget could expire unnoticed.
        self.deadline = time.perf_counter() + limits.max_seconds

    def tick(self) -> None:
        self.steps += 1
        if self.steps > self.limits.max_steps:
            raise ExprError(f"Step budget exceeded ({self.limits.max_steps} evaluation steps)")
        if self.steps % 256 == 0 and time.perf_counter() >= self.deadline:
            raise ExprError(f"Time budget exceeded ({self.limits.max_seconds:.1f}s)")


class SafeEvaluator:
    def __init__(self, limits: ExprLimits = DEFAULT_LIMITS, *, allow_statements: bool = True) -> None:
        self.limits = limits
        self.allow_statements = allow_statements
        self.functions = self._build_functions()

    # ── function table ────────────────────────────────────────────────────
    def _build_functions(self) -> Dict[str, Callable[..., Any]]:
        funcs: Dict[str, Callable[..., Any]] = {}
        for name in _MATH_NAMES:
            if hasattr(math, name):
                funcs[name] = getattr(math, name)
        for name in _STATISTICS_NAMES:
            if hasattr(statistics, name):
                funcs[name] = getattr(statistics, name)
        if hasattr(statistics, "linear_regression"):
            funcs["linear_regression"] = lambda xs, ys: tuple(statistics.linear_regression(xs, ys))
        limits = self.limits

        def factorial(n: Any) -> int:
            if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= limits.max_factorial_arg:
                raise ExprError(f"factorial() argument must be an integer in [0, {limits.max_factorial_arg}]")
            return math.factorial(n)

        def bounded_comb(fn: Callable[..., int]) -> Callable[..., int]:
            def wrapper(n: Any, k: Any = None) -> int:
                if not isinstance(n, int) or n > limits.max_comb_arg:
                    raise ExprError(f"{fn.__name__}() n must be an integer <= {limits.max_comb_arg}")
                return fn(n) if k is None else fn(n, k)
            return wrapper

        def prod(values: Any, start: Any = 1) -> Any:
            total = start
            for value in self._as_sequence(values, "prod"):
                total = self._mul(total, value)
            return total

        def lcm(*values: Any) -> int:
            total = 1
            for value in values:
                total = self._check_value(math.lcm(total, value))
            return total

        def bounded_range(*args: Any) -> List[int]:
            if not all(isinstance(arg, int) and not isinstance(arg, bool) for arg in args):
                raise ExprError("range() arguments must be integers")
            values = range(*args)
            if len(values) > limits.max_sequence:
                raise ExprError(f"range() longer than {limits.max_sequence} elements")
            return list(values)

        def safe_sum(values: Any, start: Any = 0) -> Any:
            # Numbers only: summing lists concatenates a growing list per item
            # (quadratic) inside one call that never ticks the budget. Lists
            # are joined with + in the program instead.
            items = self._as_sequence(values, "sum")
            for value in (start, *items):
                if isinstance(value, bool) or not isinstance(value, (int, float, complex)):
                    raise ExprError("sum() adds numbers only; join lists with +")
            total = start
            for value in items:
                total = self._binop(ast.Add(), total, value)
            return total

        def quantiles(values: Any, n: int = 4, method: str = "exclusive") -> List[float]:
            if not isinstance(n, int) or not 1 <= n <= limits.max_sequence:
                raise ExprError(f"quantiles() n must be an integer in [1, {limits.max_sequence}]")
            return statistics.quantiles(self._as_sequence(values, "quantiles"), n=n)

        def safe_pow(base: Any, exponent: Any, modulus: Any = None) -> Any:
            if modulus is not None:
                return pow(base, exponent, modulus)
            return self._pow(base, exponent)

        funcs.update({
            "factorial": factorial,
            "comb": bounded_comb(math.comb),
            "perm": bounded_comb(math.perm),
            "prod": prod,
            "lcm": lcm,
            "range": bounded_range,
            "pow": safe_pow,
            "sum": safe_sum,
            "quantiles": quantiles,
            "abs": abs, "round": round, "min": min, "max": max, "len": len,
            "int": int, "float": float, "bool": bool, "complex": complex, "divmod": divmod,
            "all": all, "any": any,
            "sorted": lambda values, reverse=False: sorted(self._as_sequence(values, "sorted"), reverse=reverse),
            "reversed": lambda values: list(reversed(self._as_sequence(values, "reversed"))),
            "list": lambda values=(): list(self._as_sequence(values, "list")),
            "tuple": lambda values=(): tuple(self._as_sequence(values, "tuple")),
        })
        return funcs

    # ── guards ─────────────────────────────────────────────────────────────
    def _as_sequence(self, values: Any, name: str) -> list:
        if not isinstance(values, (list, tuple)):
            raise ExprError(f"{name}() expects a list or tuple")
        return list(values)

    def _check_value(self, value: Any) -> Any:
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, int):
            if value.bit_length() > self.limits.max_int_bits:
                raise ExprError(f"Integer result exceeds {self.limits.max_int_bits} bits")
            return value
        if isinstance(value, (float, complex)):
            return value
        if isinstance(value, (list, tuple)):
            if len(value) > self.limits.max_sequence:
                raise ExprError(f"Sequence longer than {self.limits.max_sequence} elements")
            self._check_expanded(value)
            return value
        raise ExprError(f"Unsupported value type: {type(value).__name__}")

    def _check_expanded(self, value: Any) -> None:
        if expanded_size(value, self.limits.max_total_elements) > self.limits.max_total_elements:
            raise ExprError(
                f"Nested sequence expands to more than {self.limits.max_total_elements} elements"
            )

    def _mul(self, left: Any, right: Any) -> Any:
        if isinstance(left, (list, tuple)) or isinstance(right, (list, tuple)):
            seq, count = (left, right) if isinstance(left, (list, tuple)) else (right, left)
            if not isinstance(count, int) or isinstance(count, bool):
                raise ExprError("Sequences can only be repeated by an integer")
            if len(seq) * max(count, 0) > self.limits.max_sequence:
                raise ExprError(f"Sequence longer than {self.limits.max_sequence} elements")
        elif isinstance(left, int) and isinstance(right, int):
            if left.bit_length() + right.bit_length() > self.limits.max_int_bits + 1:
                raise ExprError(f"Integer result exceeds {self.limits.max_int_bits} bits")
        return self._check_value(left * right)

    def _pow(self, base: Any, exponent: Any) -> Any:
        if isinstance(base, int) and isinstance(exponent, int) and exponent > 0 and abs(base) > 1:
            if exponent * math.log2(abs(base)) > self.limits.max_int_bits:
                raise ExprError(f"Integer result exceeds {self.limits.max_int_bits} bits")
        try:
            return self._check_value(base ** exponent)
        except OverflowError as exc:
            raise ExprError(f"Numeric overflow: {exc}") from exc

    def _lshift(self, value: Any, shift: Any) -> Any:
        if isinstance(value, int) and isinstance(shift, int) and shift > 0:
            if value.bit_length() + shift > self.limits.max_int_bits:
                raise ExprError(f"Integer result exceeds {self.limits.max_int_bits} bits")
        return self._check_value(value << shift)

    # ── parsing ────────────────────────────────────────────────────────────
    def parse(self, source: str) -> ast.AST:
        text = source.strip()
        if not text:
            raise ExprError("Empty expression")
        if len(text) > self.limits.max_source_chars:
            raise ExprError(f"Source longer than {self.limits.max_source_chars} characters")
        mode = "exec" if self.allow_statements else "eval"
        try:
            tree = ast.parse(text, mode=mode)
        except SyntaxError as exc:
            raise ExprError(f"Syntax error: {exc.msg}") from exc
        count = 0
        for node in ast.walk(tree):
            count += 1
            if not isinstance(node, _ALLOWED_NODES):
                raise ExprError(f"Unsupported syntax: {type(node).__name__}")
            if isinstance(node, ast.Constant) and not (
                node.value is None or isinstance(node.value, (bool, int, float, complex))
            ):
                raise ExprError(f"Unsupported constant type: {type(node.value).__name__}")
        if count > self.limits.max_nodes:
            raise ExprError(f"Expression too large ({count} > {self.limits.max_nodes} AST nodes)")
        if isinstance(tree, ast.Module):
            if not tree.body:
                raise ExprError("Empty program")
            for stmt in tree.body:
                if not isinstance(stmt, (ast.Expr, ast.Assign, ast.AugAssign)):
                    raise ExprError(f"Unsupported statement: {type(stmt).__name__}")
        return tree

    # ── evaluation ─────────────────────────────────────────────────────────
    def evaluate(self, source: str) -> Tuple[Any, Dict[str, Any]]:
        """Evaluate ``source``; return (value of last expression, variables)."""
        tree = self.parse(source)
        budget = _Budget(self.limits)
        variables: Dict[str, Any] = {}
        if isinstance(tree, ast.Expression):
            return self._eval(tree.body, variables, budget), variables
        last: Any = None
        for stmt in tree.body:  # type: ignore[attr-defined]
            if isinstance(stmt, ast.Expr):
                last = self._eval(stmt.value, variables, budget)
            elif isinstance(stmt, ast.Assign):
                value = self._eval(stmt.value, variables, budget)
                for target in stmt.targets:
                    self._assign(target, value, variables)
                last = value
            else:
                assert isinstance(stmt, ast.AugAssign)
                if not isinstance(stmt.target, ast.Name):
                    raise ExprError("Augmented assignment target must be a name")
                current = self._lookup(stmt.target.id, variables)
                value = self._binop(stmt.op, current, self._eval(stmt.value, variables, budget))
                self._assign(stmt.target, value, variables)
                last = value
        return last, variables

    def _assign(self, target: ast.AST, value: Any, variables: Dict[str, Any]) -> None:
        if isinstance(target, ast.Name):
            name = target.id
            if name.startswith("_") or name in self.functions or name in CONSTANTS:
                raise ExprError(f"Cannot assign to reserved name {name!r}")
            if name not in variables and len(variables) >= self.limits.max_variables:
                raise ExprError(f"More than {self.limits.max_variables} variables")
            variables[name] = value
            return
        if isinstance(target, ast.Tuple):
            if not isinstance(value, (list, tuple)) or len(value) != len(target.elts):
                raise ExprError("Tuple assignment requires a sequence of matching length")
            for element, item in zip(target.elts, value):
                self._assign(element, item, variables)
            return
        raise ExprError("Assignment target must be a name or a tuple of names")

    def _lookup(self, name: str, variables: Dict[str, Any]) -> Any:
        if name in variables:
            return variables[name]
        if name in CONSTANTS:
            return CONSTANTS[name]
        if name in self.functions:
            raise ExprError(f"{name} is a function; call it with parentheses")
        raise ExprError(f"Undefined name: {name}")

    def _binop(self, op: ast.operator, left: Any, right: Any) -> Any:
        if isinstance(op, ast.Mult):
            return self._mul(left, right)
        if isinstance(op, ast.Pow):
            return self._pow(left, right)
        if isinstance(op, ast.LShift):
            return self._lshift(left, right)
        if isinstance(op, ast.Add) and isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
            if len(left) + len(right) > self.limits.max_sequence:
                raise ExprError(f"Sequence longer than {self.limits.max_sequence} elements")
        handler = _SIMPLE_BINOPS.get(type(op))
        if handler is None:
            raise ExprError(f"Unsupported operator: {type(op).__name__}")
        try:
            return self._check_value(handler(left, right))
        except ZeroDivisionError as exc:
            raise ExprError("Division by zero") from exc
        except OverflowError as exc:
            raise ExprError(f"Numeric overflow: {exc}") from exc

    def _eval(self, node: ast.AST, variables: Dict[str, Any], budget: _Budget) -> Any:
        budget.tick()
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self._lookup(node.id, variables)
        if isinstance(node, ast.BinOp):
            left = self._eval(node.left, variables, budget)
            right = self._eval(node.right, variables, budget)
            return self._binop(node.op, left, right)
        if isinstance(node, ast.UnaryOp):
            operand = self._eval(node.operand, variables, budget)
            return self._check_value(_UNARY_OPS[type(node.op)](operand))
        if isinstance(node, ast.BoolOp):
            value: Any = None
            for item in node.values:
                value = self._eval(item, variables, budget)
                if isinstance(node.op, ast.And) and not value:
                    return value
                if isinstance(node.op, ast.Or) and value:
                    return value
            return value
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, variables, budget)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._eval(comparator, variables, budget)
                # A comparison recurses inside one C call without ticking the
                # budget; bound the work it can do before starting it.
                for operand in (left, right):
                    if isinstance(operand, (list, tuple)):
                        self._check_expanded(operand)
                try:
                    outcome = _COMPARE_OPS[type(op)](left, right)
                except TypeError as exc:
                    raise ExprError(f"Cannot compare: {exc}") from exc
                if not outcome:
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            branch = node.body if self._eval(node.test, variables, budget) else node.orelse
            return self._eval(branch, variables, budget)
        if isinstance(node, (ast.List, ast.Tuple)):
            items = [self._eval(item, variables, budget) for item in node.elts]
            return self._check_value(items if isinstance(node, ast.List) else tuple(items))
        if isinstance(node, ast.Subscript):
            container = self._eval(node.value, variables, budget)
            if not isinstance(container, (list, tuple)):
                raise ExprError("Only lists and tuples can be indexed")
            if isinstance(node.slice, ast.Slice):
                parts = [
                    None if part is None else self._eval(part, variables, budget)
                    for part in (node.slice.lower, node.slice.upper, node.slice.step)
                ]
                return container[slice(*parts)]
            index = self._eval(node.slice, variables, budget)
            if not isinstance(index, int):
                raise ExprError("Index must be an integer")
            try:
                return container[index]
            except IndexError as exc:
                raise ExprError("Index out of range") from exc
        if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
            return self._comprehension(node, variables, budget)
        if isinstance(node, ast.Call):
            return self._call(node, variables, budget)
        raise ExprError(f"Unsupported syntax: {type(node).__name__}")

    def _comprehension(self, node: Any, variables: Dict[str, Any], budget: _Budget) -> List[Any]:
        output: List[Any] = []

        def run(generators: List[ast.comprehension], scope: Dict[str, Any]) -> None:
            if not generators:
                output.append(self._eval(node.elt, scope, budget))
                if len(output) > self.limits.max_sequence:
                    raise ExprError(f"Sequence longer than {self.limits.max_sequence} elements")
                return
            generator, rest = generators[0], generators[1:]
            if generator.is_async:
                raise ExprError("Async comprehensions are not supported")
            iterable = self._eval(generator.iter, scope, budget)
            if not isinstance(iterable, (list, tuple)):
                raise ExprError("Comprehensions iterate over lists, tuples or range()")
            for item in iterable:
                budget.tick()
                inner = dict(scope)
                self._assign(generator.target, item, inner)
                if all(self._eval(cond, inner, budget) for cond in generator.ifs):
                    run(rest, inner)

        run(list(node.generators), dict(variables))
        # Comprehensions build sequences too; without this check they slipped
        # nested shared-reference structures past max_total_elements.
        return self._check_value(output)

    def _call(self, node: ast.Call, variables: Dict[str, Any], budget: _Budget) -> Any:
        if not isinstance(node.func, ast.Name):
            raise ExprError("Only direct calls of allowed functions are permitted")
        name = node.func.id
        function = self.functions.get(name)
        if function is None:
            raise ExprError(f"Function not allowed: {name}")
        args = [self._eval(arg, variables, budget) for arg in node.args]
        kwargs: Dict[str, Any] = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                raise ExprError("**kwargs is not supported")
            kwargs[keyword.arg] = self._eval(keyword.value, variables, budget)
        # Builtins such as min()/sorted() compare their arguments in C without
        # ticking the budget: bound the work before calling them.
        for value in [*args, *kwargs.values()]:
            if isinstance(value, (list, tuple)):
                self._check_expanded(value)
        try:
            value = function(*args, **kwargs)
        except ExprError:
            raise
        except statistics.StatisticsError as exc:
            raise ExprError(f"{name}(): {exc}") from exc
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise ExprError(f"{name}(): {exc}") from exc
        if isinstance(value, tuple) and hasattr(value, "_fields"):
            value = tuple(value)
        return self._check_value(value)


def format_value(value: Any, *, limit: int = 4000) -> str:
    """Render ``value`` without ever materialising more than ``limit`` chars.

    Lists may share references (``[[0]*n]*n``), so rendering must stop early
    instead of expanding the whole structure first.
    """
    pieces: List[str] = []
    size = 0

    def emit(text: str) -> bool:
        nonlocal size
        pieces.append(text)
        size += len(text)
        return size < limit

    def walk(item: Any) -> bool:
        if isinstance(item, (list, tuple)):
            is_list = isinstance(item, list)
            if not emit("[" if is_list else "("):
                return False
            for index, element in enumerate(item):
                if index and not emit(", "):
                    return False
                if not walk(element):
                    return False
            closing = "]" if is_list else ("," if len(item) == 1 else "") + ")"
            return emit(closing)
        if isinstance(item, float):
            return emit(f"{item:.12g}")
        if isinstance(item, complex):
            return emit(f"({item.real:.12g}{item.imag:+.12g}j)")
        return emit(str(item))

    complete = walk(value)
    text = "".join(pieces)
    if not complete or len(text) > limit:
        text = text[:limit] + " ... [truncated]"
    return text


def _json_compatible(value: Any, *, budget: int = 1000) -> bool:
    """True when ``value`` is small and plain enough to embed in result data."""
    remaining = [budget]

    def check(item: Any) -> bool:
        remaining[0] -= 1
        if remaining[0] < 0:
            return False
        if item is None or isinstance(item, (bool, int, float)):
            return not isinstance(item, int) or item.bit_length() <= 63
        if isinstance(item, (list, tuple)):
            return all(check(element) for element in item)
        return False

    return check(value)


def evaluate_expression(source: str, limits: ExprLimits = DEFAULT_LIMITS) -> Any:
    """Evaluate a single expression (no statements); used by ``calculate``."""
    value, _ = SafeEvaluator(limits, allow_statements=False).evaluate(source)
    return value


_USAGE = (
    "Usage: expr <expression or program>\n"
    "  expr 2**10 + sqrt(16)\n"
    "  expr r = 0.05; n = 10; 1000 * (1 + r) ** n\n"
    "  expr xs = [2, 4, 4, 5, 7]; mean(xs), stdev(xs)\n"
    "Statements are separated by ';' or newlines. Supports math and statistics functions,\n"
    "lists, comprehensions, comparisons and conditional expressions."
)


async def run(command: str, ctx: NativeSkillContext) -> dict:
    source = raw_argument_text(command, 1)
    if not source.strip() or source.strip() in {"help", "--help"}:
        return failure("expr", _USAGE) if not source.strip() else result("expr", _USAGE, usage=True)
    started = time.perf_counter()
    try:
        value, variables = SafeEvaluator().evaluate(source)
    except ExprError as exc:
        return failure("expr", f"expr error: {exc}", source=source)
    elapsed_ms = (time.perf_counter() - started) * 1000
    rendered = format_value(value)
    lines = [f"= {rendered}"]
    if variables:
        lines.append("vars: " + ", ".join(f"{name}={format_value(val, limit=200)}" for name, val in variables.items()))
    json_safe = value if _json_compatible(value) else rendered
    return result(
        "expr",
        "\n".join(lines),
        source=source,
        result=json_safe,
        result_text=rendered,
        variables={name: format_value(val, limit=200) for name, val in variables.items()},
        elapsed_ms=round(elapsed_ms, 3),
    )
