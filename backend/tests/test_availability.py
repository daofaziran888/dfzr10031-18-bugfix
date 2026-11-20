"""可用观测口径测试：覆盖分解、停采去重、空态与指标入口。

用标准库 unittest 跑：cd backend && .venv/bin/python -m unittest discover -s tests
每个用例都在独立的 Store 快照上改数据，不影响其他用例。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.store import store  # noqa: E402
from app.services.availability import AvailabilityService  # noqa: E402


def reset_tables() -> None:
    """把可变表恢复成种子数据，避免用例之间互相污染。"""
    from app.seed import SEED_ROWS

    for name in ("source_calendar", "collection_window", "observation_batch", "blackout_receipt", "baseline_profile"):
        store._tables[name] = [dict(row) for row in SEED_ROWS[name]]


class AvailabilityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        reset_tables()
        self.service = AvailabilityService()

    # ---- 示例：营业 60 分钟，补传 30 分钟 12 笔 ----

    def test_half_covered_window_uses_observed_minutes_as_denominator(self) -> None:
        row = self.service.compute_window("WIN-EXAMPLE")
        self.assertEqual(row["business_minutes"], 60)
        self.assertEqual(row["effective_minutes"], 60)
        self.assertEqual(row["observed_minutes"], 30)
        self.assertEqual(row["missing_minutes"], 30)
        self.assertEqual(row["observation_count"], 12)
        # 先发布带覆盖率的当期读数，分母只能是实采分钟：12 / 30 = 0.4。
        self.assertEqual(row["coverage"], 0.5)
        self.assertEqual(row["published_rate"], 0.4)
        self.assertEqual(row["rate_denominator_minutes"], 30)
        self.assertEqual(row["status"], "部分覆盖")

    def test_half_covered_window_is_not_comparable_with_full_baseline(self) -> None:
        row = self.service.compute_window("WIN-EXAMPLE")
        self.assertFalse(row["baseline"]["available"] is False)
        self.assertFalse(row["baseline"]["comparable"])
        self.assertIsNone(row["baseline"]["delta"])
        self.assertIn("不能比较", row["baseline"]["reason"])

    def test_missing_minutes_are_not_zero_sales(self) -> None:
        row = self.service.compute_window("WIN-EXAMPLE")
        gaps = {item["reason"]: item["minutes"] for item in row["gaps"]}
        self.assertEqual(gaps["缺报"], 30)
        # 缺报没有进分母，也没有算出 12/60 = 0.2 这种把缺报当零的读数。
        self.assertNotEqual(row["published_rate"], 0.2)

    # ---- 全天停业 ----

    def test_closed_source_has_no_denominator(self) -> None:
        row = self.service.compute_window("WIN-CLOSED")
        self.assertEqual(row["business_minutes"], 0)
        self.assertEqual(row["effective_minutes"], 0)
        self.assertIsNone(row["published_rate"])
        self.assertEqual(row["coverage"], None)
        self.assertEqual(row["coverage_label"], "—")
        self.assertEqual(row["status"], "全天停业")
        self.assertTrue(row["empty_states"])
        self.assertEqual(row["empty_states"][0]["scope"], "窗口")

    # ---- 整段零销量 ----

    def test_full_window_zero_sales_is_zero_rate_not_empty(self) -> None:
        row = self.service.compute_window("WIN-ZERO")
        self.assertEqual(row["effective_minutes"], 60)
        self.assertEqual(row["observed_minutes"], 60)
        self.assertEqual(row["published_rate"], 0.0)
        self.assertEqual(row["status"], "完整覆盖")
        self.assertEqual(row["empty_states"], [])
        self.assertTrue(row["baseline"]["comparable"])

    # ---- 停采回执 ----

    def test_planned_and_unplanned_blackouts_are_booked_separately(self) -> None:
        row = self.service.compute_window("WIN-DEDUP")
        self.assertEqual(row["planned_blackout_minutes"], 15)
        self.assertEqual(row["unplanned_blackout_minutes"], 5)
        gaps = {item["reason"]: item["minutes"] for item in row["gaps"]}
        self.assertEqual(gaps["计划停采"], 15)
        self.assertEqual(gaps["意外停采"], 5)

    def test_overlapping_blackouts_deduct_overlap_once(self) -> None:
        row = self.service.compute_window("WIN-DEDUP")
        # 两张回执各 15 分钟，重叠 10 分钟，总停采只能是 20 分钟。
        self.assertEqual(row["blackout_minutes"], 20)
        self.assertEqual(row["overlapping_receipt_minutes_deduped"], 10)
        self.assertEqual(row["effective_minutes"], 40)
        self.assertEqual(row["observed_minutes"], 30)
        self.assertEqual(row["missing_minutes"], 10)

    # ---- 补传一半 ----

    def test_interrupted_source_retry_changes_denominator(self) -> None:
        before = self.service.compute_window("WIN-INTERRUPT")
        self.assertEqual(before["observed_minutes"], 45)
        self.assertEqual(before["missing_minutes"], 15)
        self.assertEqual(before["published_rate"], 0.47)
        self.assertFalse(before["baseline"]["comparable"])
        entry, message = self.service.retry_window("WIN-INTERRUPT")
        self.assertIn("没有取数失败", message)
        self.assertEqual(entry["observed_minutes"], 45)

    def test_failed_batch_can_be_retried(self) -> None:
        before = self.service.compute_window("WIN-DEDUP")
        self.assertEqual(len(before["failed_batches"]), 1)
        self.assertEqual(before["observed_minutes"], 30)
        entry, message = self.service.retry_window("WIN-DEDUP")
        self.assertIn("已重试", message)
        self.assertEqual(entry["observed_minutes"], 40)
        self.assertEqual(entry["missing_minutes"], 0)
        self.assertEqual(entry["status"], "完整覆盖")
        self.assertEqual(entry["failed_batches"], [])

    def test_retry_is_idempotent(self) -> None:
        self.service.retry_window("WIN-DEDUP")
        entry, message = self.service.retry_window("WIN-DEDUP")
        self.assertIn("没有取数失败", message)
        self.assertEqual(entry["observed_minutes"], 40)

    # ---- 基线 ----

    def test_complete_window_without_baseline_explains_reason(self) -> None:
        row = self.service.compute_window("WIN-NOBASE")
        self.assertEqual(row["status"], "完整覆盖")
        self.assertEqual(row["published_rate"], 0.3)
        self.assertFalse(row["baseline"]["available"])
        self.assertFalse(row["baseline"]["comparable"])
        self.assertIn("基线", row["baseline"]["reason"])

    def test_complete_window_can_compare_with_baseline(self) -> None:
        row = self.service.compute_window("WIN-FULL")
        self.assertTrue(row["baseline"]["comparable"])
        self.assertEqual(row["baseline"]["delta"], 0.0)

    # ---- 指标入口 ----

    def test_metric_entry_decomposition_adds_up(self) -> None:
        entry = self.service.metric_entry()
        totals = entry["totals"]
        self.assertEqual(totals["effective_minutes"], totals["observed_minutes"] + totals["missing_minutes"])
        decomposition = {item["label"]: item["minutes"] for item in entry["decomposition"]}
        self.assertEqual(
            decomposition["营业分钟"],
            decomposition["实采营业分钟"]
            + decomposition["缺报分钟"]
            + decomposition["计划停采"]
            + decomposition["意外停采"],
        )
        self.assertEqual(totals["published_rate"], round(totals["observation_count"] / totals["observed_minutes"], 2))

    def test_metric_entry_marks_empty_windows(self) -> None:
        entry = self.service.metric_entry()
        empty_ids = [item["window_id"] for item in entry["totals"]["empty_states"]]
        self.assertIn("WIN-CLOSED", empty_ids)
        self.assertNotIn("WIN-ZERO", empty_ids)

    def test_metric_entry_rate_excludes_closed_window(self) -> None:
        entry = self.service.metric_entry()
        closed = next(row for row in entry["windows"] if row["window_id"] == "WIN-CLOSED")
        self.assertIsNone(closed["published_rate"])
        self.assertEqual(closed["coverage"], None)

    # ---- 边界 ----

    def test_unknown_window_returns_none(self) -> None:
        self.assertIsNone(self.service.compute_window("WIN-NOT-EXIST"))

    def test_batch_outside_business_hours_is_not_counted(self) -> None:
        store.rows("observation_batch").append(
            {
                "id": 900,
                "batch_id": "OBS-OUTSIDE",
                "window_id": "WIN-FULL",
                "kind": "常规采集",
                "start": 500,
                "end": 560,
                "count": 6,
                "arrived_at": "2026-11-20T10:05:00",
                "fetch_status": "已取数",
            }
        )
        row = self.service.compute_window("WIN-FULL")
        self.assertEqual(row["observed_minutes"], 60)
        # 只把营业内的 20 分钟并进分母；这 20 分钟与整段批次重叠，按到达时间择新后
        # 计数为 6 笔（新批次） + 21/60*40 ≈ 14 笔（旧批次未重叠部分）。
        self.assertEqual(len(row["duplicate_minutes"]), 20)
        self.assertEqual(row["observation_count"], 20)


if __name__ == "__main__":
    unittest.main()
