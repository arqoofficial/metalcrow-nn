"""Tests for the non-model parts of synonym clustering."""

from term_dict.synonym_cluster import edit_distance_ratio, transliterate


def test_transliterate_loanword():
    # электролизёр -> elektrolizer, close to english "electrolizer".
    assert transliterate("электролизёр").startswith("elektroliz")


def test_transliterate_strips_nonalnum():
    assert transliterate("СХ/ЭВ") == "skhev"


def test_edit_distance_identical():
    assert edit_distance_ratio("nickel", "nickel") == 1.0


def test_edit_distance_close_loanword():
    r = edit_distance_ratio("elektrolizer", "electrolyzer")
    assert 0.7 < r < 1.0


def test_edit_distance_unrelated():
    assert edit_distance_ratio("nickel", "flotation") < 0.5
