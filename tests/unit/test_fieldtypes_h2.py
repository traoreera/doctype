"""Les types de champ de la phase H, seconde série : entier, pourcentage, heure, durée, code, JSON, texte enrichi, mot de passe, et la sélection multiple.

Chaque type : validation STRICTE (aucune coercion), filtre et tri sur les deux bases, agrégats, et ce que le type refuse. Un secret ne sort JAMAIS de l'API.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import make_ctx
from xcore_doctype.context import RequestContext
from xcore_doctype.document import DocumentService, SystemReaders
from xcore_doctype.errors import DoctypeError, InvalidDefinition, PermissionDenied, ValidationFailed
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sealing import MASK, PREFIX, Sealer

KEY = "une-cle-de-test-assez-longue-pour-chiffrer"
GRANTS = ["lab:sample:read", "lab:sample:write", "lab:sample:delete"]
CTX = make_ctx("acme", "alice", permissions=GRANTS)
SAMPLE = {
    "schema_version": 1, "name": "Sample", "module": "lab", "track_changes": True,
    "fields": [
        {"fieldname": "title", "fieldtype": "Text", "in_list_view": True},
        {"fieldname": "n", "fieldtype": "Int"},
        {"fieldname": "pct", "fieldtype": "Percent"},
        {"fieldname": "at", "fieldtype": "Time"},
        {"fieldname": "dur", "fieldtype": "Duration"},
        {"fieldname": "src", "fieldtype": "Code", "options": "python"},
        {"fieldname": "extra", "fieldtype": "JSON"},
        {"fieldname": "body", "fieldtype": "TextEditor"},
        {"fieldname": "token", "fieldtype": "Password"},
    ],
}


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict]] = []

    def info(self, msg: str, **k) -> None:
        self.lines.append((msg, k))

    def error(self, msg: str, **k) -> None:
        self.lines.append((msg, k))


def build(db, tables, *, key: str | None = KEY, definitions=(SAMPLE,)):
    reg = DocTypeRegistry()
    for raw in definitions:
        reg.register(parse_definition(raw))
    log = _Log()
    svc = DocumentService(reg, db, tables, log, sealer=Sealer(key))
    svc.log = log  # type: ignore[attr-defined]
    return svc


@pytest.fixture
def svc(db, tables):
    return build(db, tables)


async def make(svc, **data):
    return await svc.create(CTX, "Sample", data)


class TestStrictValues:
    @pytest.mark.parametrize(
        "field,good,bad",
        [
            ("n", [0, -5, 2**53 - 1], [True, 1.5, 3.0, "12", 2**53]),
            ("pct", [0, 12.5, 100], [-1, 100.5, True, "5", float("nan")]),
            ("at", ["00:00:00", "23:59:59", "09:05:00"], ["24:00:00", "9:05:00", "12:60:00", "12:00", "12:00:00Z", 5]),
            ("dur", [0, 3600, 100 * 366 * 24 * 3600], [-1, 1.5, True, "1h", 100 * 366 * 24 * 3600 + 1]),
            ("src", ["print('x')\n", ""], [5, "x" * 100_001]),
            ("extra", [{"a": [1, 2, {"b": None}]}, [1, "x", True, 2.5]], ["texte", 5, {"a": float("nan")}, {"a": float("inf")}, {1: 2}]),
        ],
    )
    async def test_each_type_takes_its_form_and_nothing_else(self, svc, field, good, bad):
        for value in good:
            made = await make(svc, **{field: value})
            assert made["data"][field] == value
        for value in bad:
            with pytest.raises(ValidationFailed):
                await make(svc, **{field: value})

    async def test_json_has_a_depth_and_a_size_limit(self, svc):
        deep: object = "x"
        for _ in range(20):
            deep = {"k": deep}
        with pytest.raises(ValidationFailed):
            await make(svc, extra=deep)
        with pytest.raises(ValidationFailed):
            await make(svc, extra={"big": "x" * 100_001})

    @pytest.mark.parametrize(
        "html,ok",
        [
            ("<p>Bonjour <strong>toi</strong><br><a href=\"https://x.fr/a\">lien</a></p><ul><li><p>a</p></li></ul>", True),
            ("<p>1 &lt; 2 &amp; 3</p>", True),
            ("<h2>Titre</h2><blockquote><p>cité</p></blockquote><pre><code>x</code></pre><hr>", True),
            ("<script>alert(1)</script>", False),
            ("<p onclick=\"x\">a</p>", False),
            ("<a href=\"javascript:alert(1)\">x</a>", False),
            ("<a href=\"https://x.fr\" target=\"_blank\">x</a>", False),
            ("<img src=x onerror=alert(1)>", False),
            ("<p>a</p><img src=x onerror=alert(1)", False),
            ("<p style=\"color:red\">a</p>", False),
            ("<p>a", False),
            ("</p>", False),
            ("<!-- c -->", False),
            ("<iframe src=\"https://x\"></iframe>", False),
            ("<p>1 < 2</p>", False),
        ],
    )
    async def test_rich_text_is_a_closed_html_subset_refused_never_cleaned(self, svc, html, ok):
        if ok:
            assert (await make(svc, body=html))["data"]["body"] == html  # stocké tel quel : ce qui est envoyé est ce qui est affiché
        else:
            with pytest.raises(ValidationFailed):
                await make(svc, body=html)

    async def test_the_code_language_and_the_options_of_the_other_types_are_checked_at_load(self):
        bad = [
            {"fieldname": "c", "fieldtype": "Code", "options": "cobol"},
            {"fieldname": "n", "fieldtype": "Int", "options": "x"},
            {"fieldname": "j", "fieldtype": "JSON", "default": {}},
            {"fieldname": "p", "fieldtype": "Password", "default": "x"},
            {"fieldname": "b", "fieldtype": "TextEditor", "default": "<p>x</p>"},
            {"fieldname": "d", "fieldtype": "Duration", "default": -3},
            {"fieldname": "t", "fieldtype": "Time", "default": "25:00:00"},
            {"fieldname": "n", "fieldtype": "Int", "multiselect": True},
        ]
        for field in bad:
            with pytest.raises(InvalidDefinition):
                parse_definition({**SAMPLE, "fields": [field]})
        ok = parse_definition({**SAMPLE, "fields": [{"fieldname": "d", "fieldtype": "Duration", "default": 90}, {"fieldname": "t", "fieldtype": "Time", "default": "08:30:00"}, {"fieldname": "n", "fieldtype": "Int", "default": 0}]})
        assert [f.default for f in ok.fields] == [90, "08:30:00", 0]


class TestQueries:
    @pytest.fixture
    async def rows(self, svc):
        for i, (n, pct, at, dur) in enumerate([(5, 10.5, "08:00:00", 3600), (12, 50, "13:30:00", 5400), (-3, 100, "23:59:59", 60), (None, None, None, None)]):
            data = {"title": f"s{i}", **{k: v for k, v in {"n": n, "pct": pct, "at": at, "dur": dur}.items() if v is not None}}
            await make(svc, **data)

    async def titles(self, svc, **kw):
        return [i["data"]["title"] for i in (await svc.list(CTX, "Sample", **kw))["items"]]

    @pytest.mark.parametrize(
        "filters,expected",
        [
            ({"n": 5}, ["s0"]), ({"n": [">", 4]}, ["s0", "s1"]), ({"n": ["in", [12, -3]]}, ["s1", "s2"]), ({"n": ["!=", 5]}, ["s1", "s2", "s3"]),
            ({"pct": [">=", 50]}, ["s1", "s2"]), ({"pct": 10.5}, ["s0"]),
            ({"at": [">=", "13:00:00"]}, ["s1", "s2"]), ({"at": "08:00:00"}, ["s0"]), ({"at": ["<", "13:30:00"]}, ["s0"]),
            ({"dur": [">", 3600]}, ["s1"]), ({"dur": ["<=", 3600]}, ["s0", "s2"]),
        ],
    )
    async def test_filters(self, svc, rows, filters, expected):
        assert await self.titles(svc, filters=filters, order_by="title", descending=False) == expected

    @pytest.mark.parametrize("field,expected", [("n", ["s2", "s0", "s1", "s3"]), ("pct", ["s0", "s1", "s2", "s3"]), ("at", ["s0", "s1", "s2", "s3"]), ("dur", ["s2", "s0", "s1", "s3"])])
    async def test_sorting_puts_empty_values_last(self, svc, rows, field, expected):
        assert await self.titles(svc, order_by=field, descending=False) == expected
        assert (await self.titles(svc, order_by=field, descending=True))[-1] == "s3"

    async def test_aggregates_sum_a_duration_and_average_a_percentage(self, svc, rows):
        out = await svc.aggregate(CTX, "Sample", metrics=["sum:dur", "avg:pct", "max:n", "min:n", "sum:n"])
        values = out["rows"][0]["values"]
        assert values == [9060, pytest.approx((10.5 + 50 + 100) / 3), 12, -3, 14]

    @pytest.mark.parametrize("field", ["token", "extra", "body"])
    async def test_opaque_types_cannot_be_filtered_sorted_or_summed(self, svc, rows, field):
        with pytest.raises(ValidationFailed):
            await svc.list(CTX, "Sample", filters={field: "x"})
        with pytest.raises(ValidationFailed):
            await svc.aggregate(CTX, "Sample", metrics=[f"sum:{field}"])

    async def test_a_code_field_is_searched_with_like(self, svc):
        await make(svc, title="a", src="def hello():\n    return 1\n")
        await make(svc, title="b", src="print('x')")
        assert await self.titles(svc, filters={"src": ["like", "HELLO"]}) == ["a"]


class TestPasswords:
    SECRET = "s3cr3t-d'un-serveur-SMTP"

    def everything(self, svc_outputs) -> str:
        return json.dumps(svc_outputs, default=str, ensure_ascii=False)

    async def outputs(self, svc, name):
        return [
            await svc.get(CTX, "Sample", name),
            await svc.list(CTX, "Sample"),
            await svc.history(CTX, "Sample", name),
            await svc.search(CTX, "Sample", "tok"),
            await svc.global_search(CTX, "tok"),
            await svc.aggregate(CTX, "Sample"),
        ]

    async def test_a_secret_is_encrypted_at_rest_and_never_leaves_the_api(self, db, tables, svc):
        made = await make(svc, title="tok", token=self.SECRET)
        assert made["data"]["token"] == MASK
        stored = await _raw(db, tables, "Sample", made["name"])
        assert stored["token"].startswith(PREFIX) and self.SECRET not in stored["token"]
        updated = await svc.update(CTX, "Sample", made["name"], {"title": "tok", "token": "autre-secret-2"}, made["version"])
        assert updated["data"]["token"] == MASK
        for out in await self.outputs(svc, made["name"]):
            dump = self.everything(out)
            assert self.SECRET not in dump and "autre-secret-2" not in dump and PREFIX not in dump, dump

    async def test_the_mask_means_unchanged_and_a_new_value_replaces_it(self, db, tables, svc):
        made = await make(svc, title="tok", token=self.SECRET)
        before = (await _raw(db, tables, "Sample", made["name"]))["token"]
        kept = await svc.update(CTX, "Sample", made["name"], {"title": "renommé", "token": MASK}, made["version"])
        assert (await _raw(db, tables, "Sample", made["name"]))["token"] == before  # le même jeton : rien n'a été rechiffré
        cleared = await svc.update(CTX, "Sample", made["name"], {"title": "renommé"}, kept["version"])
        assert "token" not in cleared["data"] and "token" not in await _raw(db, tables, "Sample", made["name"])  # absent : effacé

    async def test_the_mask_without_a_stored_secret_is_refused(self, svc):
        with pytest.raises(ValidationFailed) as e:
            await make(svc, token=MASK)
        assert e.value.details[0]["type"] == "secret_placeholder"

    async def test_history_says_that_a_secret_changed_never_its_value(self, svc):
        made = await make(svc, title="tok", token=self.SECRET)
        await svc.update(CTX, "Sample", made["name"], {"title": "tok", "token": "deuxieme-secret"}, made["version"])
        entries = (await svc.history(CTX, "Sample", made["name"]))["items"]
        changes = [e["changes"]["token"] for e in entries if "token" in e["changes"]]
        assert changes == [{"old": MASK, "new": MASK}, {"old": None, "new": MASK}]

    async def test_without_a_secret_key_a_write_carrying_a_secret_is_refused_and_nothing_is_stored_in_clear(self, db, tables):
        keyless = build(db, tables, key=None)
        with pytest.raises(DoctypeError) as e:
            await keyless.create(CTX, "Sample", {"title": "x", "token": self.SECRET})
        assert (e.value.status, e.value.code) == (503, "secret_key_missing")
        assert (await keyless.list(CTX, "Sample"))["total"] == 0
        assert (await keyless.create(CTX, "Sample", {"title": "sans secret"}))["name"]  # le reste du DocType marche

    async def test_only_the_system_context_reads_the_secret_and_each_read_is_logged(self, db, tables, svc):
        made = await make(svc, title="tok", token=self.SECRET)
        system = SystemReaders(svc).system("acme", "envoi des e-mails")
        assert await system.secret("Sample", made["name"], "token") == self.SECRET
        assert any(m == "secret revealed" and k["reason"] == "envoi des e-mails" for m, k in svc.log.lines)
        assert SystemReaders(svc).system("acme", "x") is not None
        with pytest.raises(PermissionDenied):
            svc.reveal_secret(CTX, svc._doctype("Sample"), {"token": "x"}, "token")  # un utilisateur ordinaire n'a aucune porte
        with pytest.raises(ValidationFailed):
            await system.secret("Sample", made["name"], "title")  # pas un secret

    async def test_a_changed_key_makes_old_secrets_unreadable_instead_of_returning_garbage(self, db, tables, svc):
        made = await make(svc, title="tok", token=self.SECRET)
        other = build(db, tables, key="une-toute-autre-cle-de-chiffrement-32")
        with pytest.raises(DoctypeError) as e:
            await SystemReaders(other).system("acme", "relecture").secret("Sample", made["name"], "token")
        assert e.value.code == "secret_unreadable"

    async def test_a_secret_is_not_copied_when_a_submitted_document_is_amended(self, db, tables):
        sub = {**SAMPLE, "name": "Voucher", "submittable": True, "permissions": {"read": ["lab:sample:read"], "write": ["lab:sample:write"], "create": ["lab:sample:write"], "submit": ["lab:sample:write"], "cancel": ["lab:sample:write"]}}
        svc = build(db, tables, definitions=(sub,))
        made = await svc.create(CTX, "Voucher", {"title": "v", "token": self.SECRET})
        done = await svc.cancel(CTX, "Voucher", made["name"], (await svc.submit(CTX, "Voucher", made["name"], made["version"]))["version"])
        amended = await svc.amend(CTX, "Voucher", made["name"])
        assert "token" not in amended["data"] and amended["data"]["title"] == "v" and done["docstatus"] == 2

    async def test_a_secret_cannot_serve_in_a_condition_or_a_virtual_doctype(self):
        with pytest.raises(InvalidDefinition):
            parse_definition({**SAMPLE, "fields": [{"fieldname": "token", "fieldtype": "Password"}, {"fieldname": "note", "fieldtype": "Text", "depends_on": "token != ''"}]})
        with pytest.raises(InvalidDefinition):
            parse_definition({**SAMPLE, "source": "x", "track_changes": False, "fields": [{"fieldname": "token", "fieldtype": "Password"}]})

    def test_the_key_is_checked(self):
        with pytest.raises(ValueError):
            Sealer("trop-court")
        assert Sealer(None).available is False and Sealer(KEY).available is True
        sealed = Sealer(KEY).seal("x")
        assert sealed.startswith(PREFIX) and Sealer(KEY).unseal(sealed) == "x" and Sealer(KEY).seal("x") != sealed  # le jeton change à chaque fois


class TestMultiselect:
    def test_the_child_doctype_must_have_exactly_one_link_field(self):
        tag = {"schema_version": 1, "name": "Tag Link", "istable": True, "fields": [{"fieldname": "tag", "fieldtype": "Link", "options": "Tag"}]}
        tag_two = {"schema_version": 1, "name": "Tag Two", "istable": True, "fields": [{"fieldname": "tag", "fieldtype": "Link", "options": "Tag"}, {"fieldname": "note", "fieldtype": "Text"}]}
        tag_target = {"schema_version": 1, "name": "Tag", "module": "lab", "fields": [{"fieldname": "label", "fieldtype": "Text"}]}
        parent = lambda child: {"schema_version": 1, "name": "Item", "module": "lab", "fields": [{"fieldname": "tags", "fieldtype": "Table", "options": child, "multiselect": True}]}  # noqa: E731
        reg = DocTypeRegistry()
        reg.register(parse_definition(tag_target))
        reg.register(parse_definition(tag))
        reg.register(parse_definition(tag_two))
        reg.register(parse_definition(parent("Tag Link")))
        with pytest.raises(InvalidDefinition, match="UN seul champ"):
            reg2 = DocTypeRegistry()
            reg2.register(parse_definition(tag_target))
            reg2.register(parse_definition(tag_two))
            reg2.register(parse_definition(parent("Tag Two")))


async def _raw(db, tables, doctype: str, name: str) -> dict:
    from xcore_doctype.permissions import Scope
    from xcore_doctype.query import Query

    async with db.session() as session:
        doc = await Query(session, RequestContext.system("acme", "test"), tables).get(doctype, name, scope=Scope.everything("read"))
    return doc.data
