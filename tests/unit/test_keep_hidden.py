"""`overrides.keep_hidden` : enregistrer sa disposition n'efface pas les blocs que l'on ne voit pas."""

from __future__ import annotations

from xcore_doctype.overrides import keep_hidden


def sc(name, **extra):
    return {"type": "shortcut", "doctype": name, **extra}


def names(blocks):
    return [b.get("doctype") or b.get("text") for b in blocks]


BASE = [{"type": "header", "text": "H"}, sc("A"), sc("Secret"), sc("B"), sc("Secret2")]


def test_a_hidden_block_stays_after_the_visible_block_that_preceded_it():
    # A, B visibles ; l'utilisateur met B avant A : Secret suit A, Secret2 suit B.
    out = keep_hidden(BASE, {0, 1, 3}, [sc("B"), sc("A")])
    assert names(out) == ["B", "Secret2", "A", "Secret"]


def test_without_its_predecessor_it_goes_before_the_visible_block_that_followed():
    out = keep_hidden(BASE, {0, 1, 3}, [sc("B")])  # A retiré : Secret (qui suivait A) se range avant B, son voisin d'après
    assert names(out) == ["Secret", "B", "Secret2"]


def test_with_no_neighbour_left_it_goes_last_and_nothing_is_ever_dropped_or_changed():
    out = keep_hidden(BASE, {0, 1, 3}, [sc("New")])
    assert names(out)[:1] == ["New"] and set(names(out)) == {"New", "Secret", "Secret2"}
    assert [b for b in out if b.get("doctype") == "Secret"] == [sc("Secret")]


def test_the_visible_blocks_are_the_ones_submitted_with_their_changes():
    out = keep_hidden(BASE, {0, 1, 3}, [sc("A", label="Mon A"), sc("B", new=True)])
    assert [b for b in out if b.get("doctype") == "A"] == [sc("A", label="Mon A")]


def test_a_resized_or_relabelled_neighbour_is_still_found():
    base = [{"type": "number_card", "label": "N", "doctype": "A", "cols": 3}, sc("Secret")]
    out = keep_hidden(base, {0}, [{"type": "number_card", "label": "N", "doctype": "A", "cols": 12, "color": "#aabbcc"}])
    assert names(out) == ["A", "Secret"]


def test_nothing_hidden_means_the_submitted_list_as_is_and_the_input_is_not_mutated():
    submitted = [sc("A"), sc("B")]
    assert keep_hidden([sc("A"), sc("B")], {0, 1}, submitted) == submitted
    base = list(BASE)
    keep_hidden(base, {0, 1, 3}, [sc("A")])
    assert base == BASE
