"""Deterministic tests for the extraction-robustness SCORING logic.

No network, no Gemini, no datasets required. We feed hand-built ExtractionResult
objects and ground-truth dicts into the pure scoring functions and assert the
metrics. The live runners (score_cord / handwriting_probe) are exercised only by
the CLI against real data; here we cover their no-data / empty-dir behaviour.
"""
import math


from app.models.schemas import (ExtractionResult, DocumentQuality, NumField,
                                LineItem)
from app.evalrunner.extraction_eval import (
    rel_error, total_matches, line_item_count_error, score_one_cord,
    aggregate_cord, normalize_word, word_matches, score_cord, handwriting_probe,
)
from scripts.download_eval_datasets import (
    parse_cord_total, cord_line_item_count, cord_ground_truth,
)


def _bill(total: float | None, conf: float = 0.9, n_items: int = 1) -> ExtractionResult:
    return ExtractionResult(
        file_id="X", doc_type="HOSPITAL_BILL",
        quality=DocumentQuality(readable=True),
        total_amount=NumField(value=total, confidence=conf),
        line_items=[LineItem(description=f"item{i}", amount=10.0) for i in range(n_items)])


# --------------------------------------------------------------------------- #
# rel_error / total_matches                                                    #
# --------------------------------------------------------------------------- #

def test_exact_total_match_zero_error():
    assert rel_error(1500, 1500) == 0.0
    assert total_matches(1500, 1500) is True


def test_close_total_rel_error_and_tolerance():
    # 1400 vs 1500 -> ~0.0667
    err = rel_error(1400, 1500)
    assert math.isclose(err, 0.06666666, rel_tol=1e-4)
    # default tolerance 2% -> not a match
    assert total_matches(1400, 1500) is False
    # configurable tolerance 10% -> match
    assert total_matches(1400, 1500, tolerance=0.10) is True


def test_rel_error_missing_extracted_is_none():
    assert rel_error(None, 1500) is None
    assert total_matches(None, 1500) is False


def test_rel_error_truth_zero():
    assert rel_error(0, 0) == 0.0
    assert rel_error(5, 0) == 1.0


def test_line_item_count_error():
    assert line_item_count_error(3, 3) == 0
    assert line_item_count_error(2, 5) == 3
    assert line_item_count_error(5, 2) == 3


# --------------------------------------------------------------------------- #
# score_one_cord                                                               #
# --------------------------------------------------------------------------- #

def test_score_one_cord_perfect():
    rec = score_one_cord(_bill(1500, conf=0.95, n_items=3),
                         {"total": 1500, "line_item_count": 3})
    assert rec["total_match"] is True
    assert rec["rel_error"] == 0.0
    assert rec["line_item_count_error"] == 0
    assert rec["total_confidence"] == 0.95


def test_score_one_cord_off_by_a_bit():
    rec = score_one_cord(_bill(1400, n_items=2),
                         {"total": 1500, "line_item_count": 3})
    assert math.isclose(rec["rel_error"], 0.0667, rel_tol=1e-2)
    assert rec["total_match"] is False
    assert rec["line_item_count_error"] == 1


def test_score_one_cord_missing_total():
    rec = score_one_cord(_bill(None), {"total": 1500, "line_item_count": 1})
    assert rec["rel_error"] is None
    assert rec["total_match"] is False


# --------------------------------------------------------------------------- #
# aggregate_cord                                                               #
# --------------------------------------------------------------------------- #

def test_aggregate_empty():
    assert aggregate_cord([]) == {"n": 0}


def test_aggregate_mixed():
    recs = [
        score_one_cord(_bill(1500, conf=0.9, n_items=3), {"total": 1500, "line_item_count": 3}),
        score_one_cord(_bill(1400, conf=0.7, n_items=2), {"total": 1500, "line_item_count": 3}),
        score_one_cord(_bill(None, conf=0.0, n_items=0), {"total": 2000, "line_item_count": 2}),
    ]
    agg = aggregate_cord(recs)
    assert agg["n"] == 3
    # 1 of 3 within 2% tolerance
    assert math.isclose(agg["total_match_rate"], 1 / 3, rel_tol=1e-6)
    # only 2 of 3 had a scoreable total (third was None)
    assert agg["scored_totals"] == 2
    # mean rel error over the 2 scoreable: (0 + 0.0667)/2
    assert math.isclose(agg["mean_rel_total_error"], 0.0333, rel_tol=1e-2)
    # exact line-item count only the first
    assert math.isclose(agg["exact_line_item_count_rate"], 1 / 3, rel_tol=1e-6)
    assert math.isclose(agg["mean_total_confidence"], (0.9 + 0.7 + 0.0) / 3, rel_tol=1e-6)


# --------------------------------------------------------------------------- #
# handwriting normalization                                                    #
# --------------------------------------------------------------------------- #

def test_normalize_word():
    assert normalize_word("  Napa! ") == "napa"
    assert normalize_word("Para-cetamol") == "paracetamol"
    assert normalize_word(None) == ""


def test_word_matches_case_insensitive():
    assert word_matches("NAPA", "napa") is True
    assert word_matches("napa ", " Napa") is True
    assert word_matches("napa", "calpol") is False
    assert word_matches("", "napa") is False
    assert word_matches(None, "napa") is False


# --------------------------------------------------------------------------- #
# CORD ground-truth parsing (from the download script)                        #
# --------------------------------------------------------------------------- #

def test_parse_cord_total_idr_separators():
    # In CORD (IDR) both '.' and ',' are thousands separators.
    assert parse_cord_total("60.000") == 60000.0
    assert parse_cord_total("28,000") == 28000.0
    assert parse_cord_total("174,600") == 174600.0
    assert parse_cord_total("91000") == 91000.0


def test_parse_cord_total_garbage():
    assert parse_cord_total(None) is None
    assert parse_cord_total("") is None
    assert parse_cord_total("Rp") is None


def test_cord_line_item_count_dict_vs_list():
    assert cord_line_item_count({"nm": "x"}) == 1
    assert cord_line_item_count([{"a": 1}, {"b": 2}]) == 2
    assert cord_line_item_count(None) == 0


def test_cord_ground_truth_parse():
    raw = '{"gt_parse": {"menu": [{"nm":"a"},{"nm":"b"}], "total": {"total_price": "28,000"}}}'
    gt = cord_ground_truth(raw)
    assert gt["total"] == 28000.0
    assert gt["line_item_count"] == 2


def test_cord_ground_truth_no_total():
    raw = '{"gt_parse": {"menu": {"nm":"a"}, "total": {}}}'
    assert cord_ground_truth(raw) is None


# --------------------------------------------------------------------------- #
# Live runners: graceful no-data behaviour (no network)                       #
# --------------------------------------------------------------------------- #

def test_score_cord_empty_dir(tmp_path):
    out = score_cord(tmp_path)
    assert out["n"] == 0
    # records present but empty; no extraction attempted
    assert out.get("records", []) == []


def test_score_cord_missing_dir(tmp_path):
    out = score_cord(tmp_path / "does_not_exist")
    assert out["n"] == 0
    assert out.get("skipped") is True


def test_handwriting_probe_no_labels(tmp_path):
    out = handwriting_probe(tmp_path)
    assert out["skipped"] is True
