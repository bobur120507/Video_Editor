"""A safe interpreter for the subset of After Effects expression JavaScript that templates use.

Covers what layout rigs and control layers are made of (AE expression reference,
ae-expressions.docsforadobe.dev): numbers, strings, arrays and vector maths, variables, if/else and
?:, comparisons and logic, `value`, `time`, `thisComp` / `comp("name")` with `.width/.height/.duration/
.frameDuration`, `.layer("name"|index).effect("name"|index)(n|"match name")` for expression controls,
`Math.*`, `linear/ease/easeIn/easeOut`, `clamp`, `wiggle`, `loopOut`, and Bodymovin's `$bm_rt =`.

It parses into its own tree and walks it: nothing is ever handed to Python's eval, and anything outside
the subset raises Unsupported so the caller keeps the keyframed value.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable

import numpy as np

from . import curves


class Unsupported(Exception):
    pass


TOKEN = re.compile(r"""\s*(?:(?P<num>\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+)|(?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
                    |(?P<id>[A-Za-z_$][\w$]*)|(?P<op>===|!==|==|!=|>=|<=|&&|\|\||\+=|-=|\*=|/=|[-+*/%<>!=?:(),\[\]{};.]))""", re.X)
COMMENTS = re.compile(r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|//[^\n\r]*|/\*.*?\*/""", re.S)


def _tokens(src: str) -> list[tuple[str, str]]:
    src = COMMENTS.sub(lambda m: m.group(1) or " ", src)
    out, pos = [], 0
    while pos < len(src):
        if src[pos:].strip() == "":
            break
        m = TOKEN.match(src, pos)
        if not m or m.end() == pos:
            raise Unsupported(f"cannot read {src[pos:pos + 20]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


# ---------------------------------------------------------------- parser (tuples as tree nodes)


class _Parser:
    def __init__(self, src: str):
        self.t = _tokens(src)
        self.i = 0

    def peek(self, k: int = 0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def take(self, value: str | None = None):
        tok = self.peek()
        if tok[0] is None or (value is not None and tok[1] != value):
            raise Unsupported(f"expected {value!r}, found {tok[1]!r}")
        self.i += 1
        return tok

    def accept(self, value: str) -> bool:
        if self.peek()[1] == value and self.peek()[0] in ("op", "id"):
            self.i += 1
            return True
        return False

    def program(self):
        body = []
        while self.peek()[0] is not None:
            if self.accept(";"):
                continue
            body.append(self.statement())
        return ("block", body)

    def block(self):
        if self.accept("{"):
            body = []
            while not self.accept("}"):
                if self.peek()[0] is None:
                    raise Unsupported("unclosed {")
                if self.accept(";"):
                    continue
                body.append(self.statement())
            return ("block", body)
        return self.statement()

    def statement(self):
        kind, val = self.peek()
        if kind == "id" and val in ("var", "let", "const"):
            self.i += 1
            node = ("assign", self.take()[1], "=", self.expr())
            while self.accept(","):
                node = ("block", [node, ("assign", self.take()[1], "=", self.expr())])
        elif kind == "id" and val == "if":
            self.i += 1
            self.take("(")
            cond = self.expr()
            self.take(")")
            then = self.block()
            other = self.block() if self.accept("else") else None
            node = ("if", cond, then, other)
        elif kind == "id" and self.peek(1)[1] in ("=", "+=", "-=", "*=", "/="):
            name = self.take()[1]
            op = self.take()[1]
            node = ("assign", name, op, self.expr())
        elif kind == "id" and val in ("for", "while", "function", "return", "switch", "do", "try", "new"):
            raise Unsupported(f"'{val}' is not supported")
        else:
            node = self.expr()
        self.accept(";")
        return node

    def expr(self):
        cond = self.binary(0)
        if self.accept("?"):
            a = self.expr()
            self.take(":")
            return ("cond", cond, a, self.expr())
        return cond

    LEVELS = [("||",), ("&&",), ("==", "!=", "===", "!=="), ("<", ">", "<=", ">="), ("+", "-"), ("*", "/", "%")]

    def binary(self, level: int):
        if level == len(self.LEVELS):
            return self.unary()
        left = self.binary(level + 1)
        while self.peek()[0] == "op" and self.peek()[1] in self.LEVELS[level]:
            op = self.take()[1]
            left = ("bin", op, left, self.binary(level + 1))
        return left

    def unary(self):
        if self.peek()[0] == "op" and self.peek()[1] in ("-", "+", "!"):
            return ("un", self.take()[1], self.unary())
        return self.postfix(self.primary())

    def postfix(self, node):
        while True:
            if self.accept("."):
                node = ("get", node, self.take()[1])
            elif self.accept("("):
                args = []
                if not self.accept(")"):
                    args.append(self.expr())
                    while self.accept(","):
                        args.append(self.expr())
                    self.take(")")
                node = ("call", node, args)
            elif self.accept("["):
                idx = self.expr()
                self.take("]")
                node = ("index", node, idx)
            else:
                return node

    def primary(self):
        kind, val = self.take()
        if kind == "num":
            return ("lit", float(val))
        if kind == "str":
            return ("lit", re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), val[1:-1]))
        if kind == "id":
            if val in ("true", "false"):
                return ("lit", val == "true")
            return ("name", val)
        if val == "(":
            node = self.expr()
            self.take(")")
            return node
        if val == "[":
            items = []
            if not self.accept("]"):
                items.append(self.expr())
                while self.accept(","):
                    items.append(self.expr())
                self.take("]")
            return ("array", items)
        raise Unsupported(f"unexpected {val!r}")


_CACHE: dict[str, Any] = {}


def parse(src: str):
    if src not in _CACHE:
        _CACHE[src] = _Parser(src).program()
    return _CACHE[src]


def supported(src: str) -> bool:
    try:
        parse(src)
        return True
    except Unsupported:
        return False


# ---------------------------------------------------------------- evaluation


def _vec(v):
    return np.asarray(v, dtype=np.float64) if isinstance(v, (list, tuple, np.ndarray)) else v


def _arith(op: str, a, b):
    if isinstance(a, str) or isinstance(b, str):
        if op == "+":
            return f"{a}{b}"
        raise Unsupported("text maths")
    a, b = _vec(a), _vec(b)
    if isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and op in ("*", "/", "%"):
        raise Unsupported("array * array")
    if isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and a.shape != b.shape:  # AE pads the shorter with 0
        n = max(a.size, b.size)
        a, b = np.pad(a, (0, n - a.size)), np.pad(b, (0, n - b.size))
    r = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "/": lambda: a / b, "%": lambda: np.fmod(a, b)}[op]()
    return r.tolist() if isinstance(r, np.ndarray) else float(r)


def _truth(v) -> bool:
    if isinstance(v, (list, np.ndarray)):
        return True
    return bool(v)


def _interp(fn: Callable, args: list):
    if len(args) == 3:  # linear(t, value1, value2): t from 0 to 1
        t, v1, v2 = args
        return _out(fn(float(t), 0.0, 1.0, v1, v2))
    if len(args) == 5:
        t, a, b, v1, v2 = args
        return _out(fn(float(t), float(a), float(b), v1, v2))
    raise Unsupported("interpolation arguments")


def _out(v):
    v = np.asarray(v, dtype=np.float64)
    return v.tolist() if v.ndim else float(v)


MATH = {"min": min, "max": max, "abs": abs, "floor": math.floor, "ceil": math.ceil, "round": round, "sqrt": math.sqrt,
        "pow": math.pow, "sin": math.sin, "cos": math.cos, "tan": math.tan, "atan": math.atan, "atan2": math.atan2,
        "asin": math.asin, "acos": math.acos, "exp": math.exp, "log": math.log, "PI": math.pi, "E": math.e}


class Context:
    """What an expression can see. `comp(name)` / `this_comp` return objects with width, height, duration,
    frameDuration and layer(name|index) -> object with effect(name|index) -> callable(n|match name) -> value."""

    def __init__(self, value, time: float, this_comp=None, comp: Callable[[str], Any] | None = None,
                 loop_out: Callable[[str, int], Any] | None = None, seed: int = 0):
        self.value, self.time, self.this_comp, self.comp_fn, self.loop_out_fn, self.seed = value, time, this_comp, comp, loop_out, seed


def evaluate(src: str, ctx: Context):
    """The expression's result (number, list or string), as AE gives it: the last statement's value."""
    env: dict[str, Any] = {}
    result = [None]

    def name(n: str):
        if n in env:
            return env[n]
        if n == "value":
            return ctx.value
        if n in ("time",):
            return ctx.time
        if n == "thisComp" and ctx.this_comp is not None:
            return ctx.this_comp
        if n == "Math":
            return MATH
        if n == "comp" and ctx.comp_fn is not None:
            return ctx.comp_fn
        if n in ("linear", "ease", "easeIn", "easeOut"):
            fn = {"linear": curves.linear, "ease": curves.ease, "easeIn": curves.ease_in, "easeOut": curves.ease_out}[n]
            return lambda *a: _interp(fn, list(a))
        if n == "clamp":
            return lambda v, a, b: _out(np.clip(np.asarray(v, float), a, b))
        if n == "degreesToRadians":
            return lambda d: math.radians(d)
        if n == "radiansToDegrees":
            return lambda r: math.degrees(r)
        if n == "wiggle":
            def wig(freq, amp, octaves=1, amp_mult=0.5, t=None):
                t = ctx.time if t is None else t
                v = np.atleast_1d(np.asarray(ctx.value, float))
                off = [float(curves.wiggle(t, freq, amp, int(octaves), amp_mult, seed=ctx.seed + 7 * d)) for d in range(v.size)]
                return _out(v + off) if np.ndim(ctx.value) else float(ctx.value) + off[0]
            return wig
        if n == "loopOut" and ctx.loop_out_fn is not None:
            return lambda kind="cycle", num=0: ctx.loop_out_fn(str(kind), int(num))
        raise Unsupported(f"unknown name {n!r}")

    def ev(node):
        op = node[0]
        if op == "lit":
            return node[1]
        if op == "name":
            return name(node[1])
        if op == "array":
            return [ev(x) for x in node[1]]
        if op == "bin":
            o, a = node[1], ev(node[2])
            if o == "&&":
                return ev(node[3]) if _truth(a) else a
            if o == "||":
                return a if _truth(a) else ev(node[3])
            b = ev(node[3])
            if o in ("==", "==="):
                return a == b
            if o in ("!=", "!=="):
                return a != b
            if o in ("<", ">", "<=", ">="):
                if isinstance(a, (list, np.ndarray)) or isinstance(b, (list, np.ndarray)):
                    raise Unsupported("array comparison")
                return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[o]
            return _arith(o, a, b)
        if op == "un":
            v = ev(node[2])
            if node[1] == "!":
                return not _truth(v)
            return _arith("*", v, -1.0) if node[1] == "-" else v
        if op == "cond":
            return ev(node[2]) if _truth(ev(node[1])) else ev(node[3])
        if op == "get":
            obj, attr = ev(node[1]), node[2]
            if isinstance(obj, dict):
                if attr not in obj:
                    raise Unsupported(f"Math.{attr}")
                return obj[attr]
            if isinstance(obj, (list, str)) and attr == "length":
                return float(len(obj))
            if attr.startswith("_") or not hasattr(obj, attr):
                raise Unsupported(f".{attr}")
            return getattr(obj, attr)
        if op == "call":
            fn = ev(node[1])
            if not callable(fn):
                raise Unsupported("call of a non-function")
            args = [ev(a) for a in node[2]]
            return fn(*args)
        if op == "index":
            obj, i = ev(node[1]), ev(node[2])
            try:
                return obj[int(i)]
            except (TypeError, IndexError, ValueError) as e:
                raise Unsupported("index") from e
        if op == "assign":
            n, o, v = node[1], node[2], ev(node[3])
            if o != "=":
                v = _arith(o[0], env[n] if n in env else name(n), v)
            env[n] = v
            if n == "$bm_rt":
                result[0] = v
            return v
        if op == "if":
            if _truth(ev(node[1])):
                return ev(node[2])
            return ev(node[3]) if node[3] is not None else None
        if op == "block":
            last = None
            for s in node[1]:
                v = ev(s)
                if v is not None:
                    last = v
            return last
        raise Unsupported(op)

    try:
        last = ev(parse(src))
    except Unsupported:
        raise
    except (ZeroDivisionError, TypeError, ValueError, KeyError, OverflowError) as e:
        raise Unsupported(f"{type(e).__name__}: {e}") from e
    out = result[0] if result[0] is not None else last
    if out is None:
        raise Unsupported("no value")
    return out
