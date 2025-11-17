import pytest
from unittest.mock import patch, MagicMock
from app.services.semantic_matcher import (
    _normalize_text,
    compute_semantic_match,
    find_matching_keyword,
)


# =====================================================================================
# TEST _normalize_text
# =====================================================================================

def test_normalize_text_basic():
    assert _normalize_text("  Hello   World  ") == "hello world"


def test_normalize_text_empty():
    assert _normalize_text("") == ""
    assert _normalize_text(None) == ""


def test_normalize_text_unicode():
    assert _normalize_text("ÄÖ Ü") == "äö ü"


# =====================================================================================
# TEST compute_semantic_match  (WITH MODEL MOCKED)
# =====================================================================================

@pytest.mark.parametrize("threshold", [0.5, 0.8])
def test_compute_semantic_match_above_threshold(threshold, monkeypatch):
    # Mock threshold
    monkeypatch.setattr("app.services.semantic_matcher.settings.SIMILARITY_THRESHOLD", threshold)

    # Fake transformer model
    fake_model = MagicMock()
    fake_model.encode.side_effect = [
        # 1) keyword embeddings (2 keywords)
        [[0.0, 1.0], [1.0, 0.0]],
        # 2) message embedding
        [[0.0, 0.9]],
    ]

    # Patch MODEL = fake_model
    monkeypatch.setattr("app.services.semantic_matcher.MODEL", fake_model)

    kw = ["alpha", "beta"]

    # Force similarity results
    with patch("app.services.semantic_matcher.cosine_similarity") as mock_cos:
        mock_cos.return_value = [[0.4, 0.95]]   # best match: index 1

        best_kw, score = compute_semantic_match("something", kw)

        assert best_kw == "beta"
        assert score == 0.95


def test_compute_semantic_match_below_threshold(monkeypatch):
    monkeypatch.setattr("app.services.semantic_matcher.settings.SIMILARITY_THRESHOLD", 0.9)

    fake_model = MagicMock()
    fake_model.encode.side_effect = [
        [[0, 1], [1, 0]],
        [[0.5, 0.5]],
    ]
    monkeypatch.setattr("app.services.semantic_matcher.MODEL", fake_model)

    with patch("app.services.semantic_matcher.cosine_similarity") as mock_cos:
        mock_cos.return_value = [[0.3, 0.4]]   # below threshold

        best_kw, score = compute_semantic_match("msg", ["a", "b"])

        assert best_kw is None
        assert score == 0.4


def test_compute_semantic_match_no_model(monkeypatch):
    monkeypatch.setattr("app.services.semantic_matcher.MODEL", None)

    best_kw, score = compute_semantic_match("msg", ["a", "b"])

    assert best_kw is None
    assert score == 0.0


def test_compute_semantic_match_exception(monkeypatch):
    fake_model = MagicMock()
    fake_encode = MagicMock(side_effect=Exception("boom"))

    fake_model.encode = fake_encode

    # Patch MODEL with fake model
    monkeypatch.setattr("app.services.semantic_matcher.MODEL", fake_model)

    best_kw, score = compute_semantic_match("msg", ["alpha"])

    assert best_kw is None
    assert score == 0.0



# =====================================================================================
# TEST find_matching_keyword
# =====================================================================================

def test_find_keyword_substring_match():
    kw = ["error", "warning"]
    result_kw, score = find_matching_keyword("Critical Error happened", kw)
    assert result_kw == "error"
    assert score == 1.0


def test_find_keyword_substring_case_insensitive():
    kw = ["SeRvEr CrAsH"]
    result_kw, score = find_matching_keyword("server crash occurred", kw)
    assert result_kw == "SeRvEr CrAsH"
    assert score == 1.0


def test_find_keyword_empty_list():
    result_kw, score = find_matching_keyword("test", [])
    assert result_kw is None
    assert score == 0.0


def test_find_keyword_semantic_fallback(monkeypatch):
    """
    Force substring to fail, semantic fallback should be used.
    """
    # No substring match
    kw = ["alpha", "beta"]

    # Semantic fallback returns keyword
    with patch("app.services.semantic_matcher.compute_semantic_match") as mock_sem:
        mock_sem.return_value = ("beta", 0.88)
        result_kw, score = find_matching_keyword("msg", kw)

        assert result_kw == "beta"
        assert score == 0.88


def test_find_keyword_semantic_fallback_none(monkeypatch):
    """
    semantic fallback returns (None, score)
    """
    kw = ["alpha", "beta"]

    with patch("app.services.semantic_matcher.compute_semantic_match") as mock_sem:
        mock_sem.return_value = (None, 0.42)
        result_kw, score = find_matching_keyword("something", kw)

        assert result_kw is None
        assert score == 0.42
