"""Conditions `depends_on` : analyse, évaluation et vecteurs partagés avec le navigateur (doc/doctype.md D22).

`tests/vectors/expr_cases.json` est écrit À LA MAIN : le résultat attendu n'est jamais calculé par le code testé.
`ui/tests/expr.spec.ts` exécute les mêmes cas, sur les mêmes arbres, dans le navigateur (`expr_asts.json` est généré
par `scripts/gen_expr_asts.py`, et `test_asts_file_is_up_to_date` vérifie qu'il n'a pas dérivé).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xcore_doctype.expr import ExprError, compile_expr, evaluate, referenced_fields

VECTORS = Path(__file__).resolve().parents[1] / "vectors"
DOC = json.loads((VECTORS / "expr_cases.json").read_text())
FIELDS = DOC["fields"]


@pytest.mark.parametrize("case", DOC["cases"], ids=lambda c: c["expr"][:60].replace("\n", "\\n"))
def test_cases(case):
    ast = compile_expr(case["expr"], FIELDS)
    got = evaluate(ast, case["values"])
    assert got is case["expect"], f"{case['expr']!r} sur {case['values']} : attendu {case['expect']}, obtenu {got}"


@pytest.mark.parametrize("case", DOC["errors"], ids=lambda c: c["expr"][:50].replace("\n", "\\n"))
def test_errors(case):
    with pytest.raises(ExprError) as e:
        compile_expr(case["expr"], FIELDS)
    assert case["error"] in str(e.value), f"{case['expr']!r}: {e.value}"


def test_asts_file_is_up_to_date():
    """Le navigateur évalue ces arbres : un arbre périmé testerait autre chose que ce que le serveur envoie."""
    from scripts.gen_expr_asts import build

    assert json.loads((VECTORS / "expr_asts.json").read_text()) == build(), "régénérez : python scripts/gen_expr_asts.py"


class TestSafety:
    @pytest.mark.parametrize("src", ["__import__('os').system('x')", "a.__class__", "open('/etc/passwd')", "a if b else c", "[x for x in a]"])
    def test_nothing_executable_is_ever_accepted(self, src):
        with pytest.raises(ExprError):
            compile_expr(src, FIELDS)

    def test_evaluation_never_raises_on_any_json_value(self):
        ast = compile_expr("a == 1 or a > 1 or a in [1, 'x'] or not a", FIELDS)
        for v in (None, True, 0, 1.5, "s", "", [1], {"x": 1}, float("nan"), float("inf"), 10**30):
            assert evaluate(ast, {"a": v}) in (True, False)

    def test_the_ast_is_plain_json(self):
        ast = compile_expr("type in ['a', 'b'] and not vip or n >= -1", FIELDS)
        assert json.loads(json.dumps(ast)) == ast

    def test_referenced_fields(self):
        assert referenced_fields(compile_expr("a == 1 and (b or not c) and n in [1]", FIELDS)) == {"a", "b", "c", "n"}
        assert referenced_fields(compile_expr("true", FIELDS)) == set()

    def test_pathological_inputs_stay_fast(self):
        import time

        t = time.perf_counter()
        for src in ("a and " * 80 + "a", "(" * 15 + "a" * 1 + ")" * 15, "a == " + "'" + "x" * 400 + "'", "a in [" + ",".join(["'x'"] * 100) + "]"):
            evaluate(compile_expr(src, FIELDS), {"a": 1})
        assert time.perf_counter() - t < 0.5


class TestFieldNames:
    def test_names_are_case_sensitive_but_keywords_are_not(self):
        assert evaluate(compile_expr("AND" if False else "a AND b", FIELDS), {"a": 1, "b": 1}) is True
        with pytest.raises(ExprError, match="inconnu"):
            compile_expr("A == 1", FIELDS)  # le champ s'appelle « a »

    def test_a_keyword_cannot_be_a_field_reference(self):
        with pytest.raises(ExprError):
            compile_expr("in == 1", FIELDS + ["in"])
