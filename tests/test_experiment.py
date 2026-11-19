from __future__ import annotations

import statistics
import unittest

from askdata.db import QueryResult
from askdata.experiment import Experiment
from askdata.models import Question
from askdata.runner import EvalResult, QuestionResult
from askdata.scoring import CORRECT, Outcome
from askdata.stats import paired_bootstrap


def question(qid: str) -> Question:
    return Question(id=qid, difficulty="easy", tags=(), question="有多少订单？", gold_sql="SELECT 1")


def make_run(statuses: dict[str, str]) -> EvalResult:
    result = EvalResult(strategy_name="fake", dataset_version="v", started_at="2026-01-01 00:00:00")
    result.results = [
        QuestionResult(
            question=question(qid),
            generated_sql="SELECT 1",
            outcome=Outcome(status),
            gold=QueryResult(["c"], [(1,)]),
            pred=None,
        )
        for qid, status in statuses.items()
    ]
    return result


class ExperimentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.experiment = Experiment(
            strategy_name="fake",
            dataset_version="v",
            runs=[
                make_run({"a": CORRECT, "b": "value_mismatch", "c": CORRECT}),
                make_run({"a": CORRECT, "b": "value_mismatch", "c": "value_mismatch"}),
                make_run({"a": CORRECT, "b": "value_mismatch", "c": "value_mismatch"}),
            ],
        )

    def test_mean_and_std(self) -> None:
        self.assertAlmostEqual(self.experiment.per_run_ex[0], 2 / 3)
        self.assertAlmostEqual(self.experiment.mean_ex, statistics.fmean([2 / 3, 1 / 3, 1 / 3]))
        self.assertAlmostEqual(self.experiment.std_ex, statistics.stdev([2 / 3, 1 / 3, 1 / 3]))

    def test_pass_counts(self) -> None:
        self.assertEqual(self.experiment.passes(), {"a": 3, "b": 0, "c": 1})

    def test_unstable_and_stable_failures(self) -> None:
        self.assertEqual(self.experiment.unstable(), ["c"])
        self.assertEqual(self.experiment.stable_failures(), ["b"])

    def test_histogram(self) -> None:
        histogram = self.experiment.stability_histogram()
        self.assertEqual(histogram[3], ["a"])
        self.assertEqual(histogram[1], ["c"])
        self.assertEqual(histogram[0], ["b"])

    def test_single_run_has_zero_std(self) -> None:
        experiment = Experiment("fake", "v", runs=[make_run({"a": CORRECT})])
        self.assertEqual(experiment.std_ex, 0.0)
        self.assertEqual(experiment.unstable(), [])


class BootstrapTest(unittest.TestCase):
    def test_identical_arms_have_zero_interval(self) -> None:
        result = paired_bootstrap([0.5, 1.0, 0.0], [0.5, 1.0, 0.0], n_boot=200)
        self.assertEqual(result["mean_diff"], 0.0)
        self.assertEqual(result["ci_low"], 0.0)
        self.assertEqual(result["ci_high"], 0.0)

    def test_dominated_arm_excludes_zero(self) -> None:
        result = paired_bootstrap([1.0, 1.0, 1.0], [0.0, 0.0, 0.0], n_boot=200)
        self.assertAlmostEqual(result["mean_diff"], 1.0)
        self.assertGreater(result["ci_low"], 0.0)
        self.assertEqual(result["p_le_zero"], 0.0)

    def test_length_mismatch_raises(self) -> None:
        with self.assertRaises(ValueError):
            paired_bootstrap([1.0], [1.0, 0.0])

class RenderMarkdownTest(unittest.TestCase):
    """报告渲染：均值、极值、耗时都要出现在 md 里。

    原文件测的是统计口径，这里补"渲染出来对不对"——
    那条 90.0% ± 2.0 最终是长在 markdown 里的。
    """

    def test_contains_mean_and_table(self) -> None:
        from askdata.experiment import render_markdown

        exp = Experiment(
            strategy_name="llm",
            dataset_version="v1",
            runs=[make_run({"q1": CORRECT}), make_run({"q1": "wrong_value"})],
        )
        text = render_markdown(exp)
        self.assertIn("llm", text)
        self.assertIn("|", text)

    def test_empty_experiment_does_not_crash(self) -> None:
        from askdata.experiment import render_markdown

        exp = Experiment(strategy_name="llm", dataset_version="v1")
        self.assertIsInstance(render_markdown(exp), str)

    def test_extra_fields_rendered(self) -> None:
        from askdata.experiment import render_markdown

        run = make_run({"q1": CORRECT})
        run.extra = {"prompt_tokens": 1000, "latency_ms": 2500}
        text = render_markdown(
            Experiment(strategy_name="llm", dataset_version="v1", runs=[run])
        )
        self.assertIn("2.5s", text)


if __name__ == "__main__":
    unittest.main()
