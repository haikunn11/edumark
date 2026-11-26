"""v0.7.1 — Điểm câu luôn = Σ tiêu chí và không vượt điểm tối đa (planning/04_2026-11-27_grader-score-clamp/plan.md).

Dữ liệu mẫu tái hiện đúng hình dạng lỗi thật trong SQLite (2026-11-27):
- class_a50039a2_hs24 Câu 1d: tiêu chí 0.8/1.0 nhưng câu ghi 1.0 ⇒ tổng 4.00 thay vì 3.80.
- class_a50039a2_hs22 Câu 1c: chấm lại theo thang 10 (tiêu chí 5/5 + 5/5, suggested 10) rồi bị gán max 1.0 ⇒ 10/1.
- class_a50039a2_hs26 Câu 2b: 7.5/3.0 (tiêu chí 5/5 + 2.5/5).
"""
import inspect

import pytest

from backend.app.models.schemas import CriterionEvaluation, QuestionGradingResult, QuestionRubric, RubricCriterion
from backend.app.services import agent2_grader
from backend.app.services.agent2_grader import reasoning_agent

EPS = 0.011


def _crit_sum(res: QuestionGradingResult) -> float:
    return round(sum(c.earned_points for c in res.criteria_evaluations), 2)


def _assert_consistent(res: QuestionGradingResult) -> None:
    assert res.suggested_score == res.earned_points
    assert 0.0 <= res.suggested_score <= res.max_score + EPS
    assert res.max_score == res.total_points
    if res.criteria_evaluations:
        assert abs(_crit_sum(res) - res.suggested_score) <= EPS
        for c in res.criteria_evaluations:
            assert 0.0 <= c.earned_points <= c.max_points + EPS


def test_v71_01_auto_parse_uses_criteria_sum_not_llm_suggested():
    """hs24 Câu 1d: LLM nói suggested 1.0 nhưng tiêu chí chỉ 0.8 ⇒ câu phải là 0.8."""
    data = {
        "total_points": 1.0,
        "suggested_score": 1.0,
        "criteria_evaluations": [
            {"criterion_id": "C1d_1", "name": "Tính toán trung gian", "score": 0.8, "max_score": 1.0, "status": "passed",
             "feedback": "Thiếu kết quả cuối cùng"},
        ],
    }
    _, res = reasoning_agent._parse_auto_rubric_response(data, "Câu 1d")
    assert res.suggested_score == 0.8
    _assert_consistent(res)


def test_v71_02_rubric_parse_uses_criteria_sum_not_llm_suggested():
    rubric = QuestionRubric(
        question_id="Câu 1d", max_score=1.0, total_points=1.0,
        criteria=[RubricCriterion(criterion_id="C1d_1", name="Tính toán", max_score=1.0)],
    )
    data = {"suggested_score": 1.0, "criteria_evaluations": [{"criterion_id": "C1d_1", "score": 0.8, "max_score": 1.0}]}
    res = reasoning_agent._parse_rubric_response(data, rubric)
    assert res.suggested_score == 0.8
    _assert_consistent(res)


def test_v71_03_auto_parse_rescales_criteria_above_question_max():
    """hs26 Câu 2b: câu tối đa 3.0 nhưng LLM đặt tiêu chí 5+5 và cho 7.5 ⇒ quy đổi tỉ lệ về thang 3.0."""
    data = {
        "total_points": 3.0,
        "suggested_score": 7.5,
        "criteria_evaluations": [
            {"criterion_id": "C1", "score": 5.0, "max_score": 5.0},
            {"criterion_id": "C2", "score": 2.5, "max_score": 5.0},
        ],
    }
    _, res = reasoning_agent._parse_auto_rubric_response(data, "Câu 2b")
    assert res.max_score == 3.0
    assert res.suggested_score == pytest.approx(2.25, abs=EPS)
    assert [c.max_points for c in res.criteria_evaluations] == [1.5, 1.5]
    _assert_consistent(res)


def test_v71_04_fit_regraded_10pt_result_into_original_max():
    """hs22 Câu 1c: chấm lại theo thang 10 (10/10) rồi đưa về câu tối đa 1.0 ⇒ 1.0/1.0, tiêu chí 0.5 + 0.5."""
    evals = [
        CriterionEvaluation(criterion_id="C1", score=5.0, max_score=5.0),
        CriterionEvaluation(criterion_id="C2", score=5.0, max_score=5.0),
    ]
    res = QuestionGradingResult(question_id="Câu 1c", suggested_score=10.0, max_score=10.0, criteria_evaluation=evals)
    fitted = agent2_grader.fit_result_to_max(res, 1.0)
    assert fitted.max_score == 1.0 and fitted.suggested_score == 1.0
    assert [c.earned_points for c in fitted.criteria_evaluations] == [0.5, 0.5]
    _assert_consistent(fitted)


def test_v71_05_fit_partial_score_keeps_proportion():
    """hs27 Câu 1a dạng 4/4 + 4/4 + 1/2 trên thang 10 ⇒ 0.9/1.0."""
    evals = [
        CriterionEvaluation(criterion_id="C1", score=4.0, max_score=4.0),
        CriterionEvaluation(criterion_id="C2", score=4.0, max_score=4.0),
        CriterionEvaluation(criterion_id="C3", score=1.0, max_score=2.0),
    ]
    res = QuestionGradingResult(question_id="Câu 1a", suggested_score=9.0, max_score=10.0, criteria_evaluation=evals)
    fitted = agent2_grader.fit_result_to_max(res, 1.0)
    assert fitted.suggested_score == pytest.approx(0.9, abs=EPS)
    _assert_consistent(fitted)


def test_v71_06_fit_without_criteria_scales_and_clamps():
    res = QuestionGradingResult(question_id="Câu 3", suggested_score=8.0, max_score=10.0)
    fitted = agent2_grader.fit_result_to_max(res, 2.0)
    assert fitted.suggested_score == pytest.approx(1.6, abs=EPS) and fitted.max_score == 2.0
    over = QuestionGradingResult(question_id="Câu 4", suggested_score=5.0, max_score=2.0)
    assert agent2_grader.fit_result_to_max(over).suggested_score == 2.0


def test_v71_08_fit_on_record_loaded_from_json_counts_each_criterion_once():
    """Bản ghi đọc lại từ SQLite có 2 list tiêu chí RIÊNG (criteria_evaluation & criteria_evaluations, cùng nội dung).
    hs27 Câu 1d: 0.5/0.5 + 0.25/0.5 phải ra 0.75 — không được đếm trùng thành thang 2.0 rồi quy đổi còn 0.74."""
    crit = [
        {"criterion_id": "C1", "score": 0.5, "earned_points": 0.5, "max_score": 0.5, "max_points": 0.5},
        {"criterion_id": "C2", "score": 0.25, "earned_points": 0.25, "max_score": 0.5, "max_points": 0.5},
    ]
    payload = {"question_id": "Câu 1d", "suggested_score": 0.75, "max_score": 1.0, "total_points": 1.0,
               "criteria_evaluation": [dict(c) for c in crit], "criteria_evaluations": [dict(c) for c in crit]}
    res = QuestionGradingResult.model_validate(payload)
    assert res.criteria_evaluation is not res.criteria_evaluations  # đúng hình dạng bản ghi thật
    fitted = agent2_grader.fit_result_to_max(res, 1.0)
    assert fitted.suggested_score == 0.75
    assert [c.max_points for c in fitted.criteria_evaluations] == [0.5, 0.5]
    assert fitted.criteria_evaluation is fitted.criteria_evaluations
    _assert_consistent(fitted)


def test_v71_07_regrade_paths_use_fit_helper():
    """2 đường chấm lại (2-pass OCR ở student_clusterer, chấm lại 1 câu ở routes) phải quy đổi qua fit_result_to_max,
    KHÔNG được chỉ gán đè max_score (nguyên nhân gốc của 10/1, 7.5/3)."""
    from backend.app.api import routes
    from backend.app.services import student_clusterer

    for mod in (student_clusterer, routes):
        src = inspect.getsource(mod)
        assert "fit_result_to_max(" in src, f"{mod.__name__} chưa dùng fit_result_to_max"
        assert "new_res.max_score = q_res.max_score" not in src
        assert "new_qr.max_score = old_max" not in src
