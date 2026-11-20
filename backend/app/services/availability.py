"""可用观测计算：把采集证据折算成有凭据支撑的营业分钟，再导出单位时间指标。

口径约束（智能告警与指标入口共用）：

1. 先由采集源的营业日历把窗口展开成营业时段；营业时段之外的分钟不进任何分母。
2. 观测批次只按落在营业时段内的部分计入，批次条数按覆盖比例分摊；窗口外不计数。
3. 停采回执分两类记账：计划停采与数据源意外失败。两类回执的分钟先求并集，
   重叠的分钟只扣一次，不按回执条数相加。
4. 有观测凭据的分钟即使落在回执覆盖范围内，也保留在分母里，并标记回执与观测冲突。
5. 营业时段整段没有任何观测批次时，该时段是零销量还是缺报无法区分，只能用空态表达；
   而整段收到 count=0 的批次是可核验的零销量，读数为 0.0 笔/分钟，两者不能混写。
6. 单位时间读数只用实采营业分钟作分母；缺报、停采分钟一律不进分母，也不做外推。
7. 只有覆盖率 100% 且缺报为 0 时，当期速率才与完整基线同口径可比；否则说明不可比原因。
8. 取数失败的批次可以重试；重试成功后按同一口径重算，不改变已发布的窗口口径。
"""
from __future__ import annotations

from datetime import date as date_cls
from typing import Any, Iterable

from app.store import store

MODULE = "availability"

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
PLANNED = "计划停采"
UNPLANNED = "意外停采"


def _minutes(start: int, end: int) -> set[int]:
    return set(range(start, end))


def _span(ranges: Iterable[tuple[int, int]]) -> set[int]:
    result: set[int] = set()
    for start, end in ranges:
        result |= _minutes(int(start), int(end))
    return result


def _merge(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    ordered = sorted((int(a), int(b)) for a, b in intervals if int(b) > int(a))
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _round(value: float, digits: int = 4) -> float:
    return round(value, digits)


def _coverage_label(observed: int, effective: int) -> str:
    if effective <= 0:
        # 没有有效营业分钟就没有分母，覆盖率无从谈起，不能写成 0%。
        return "—"
    return f"{_round(observed / effective * 100, 2):g}%"


def _rate(volume: float, denominator: int, digits: int = 2) -> float | None:
    """单位时间读数：分母只能是实采营业分钟；没有实采分钟就不出读数。"""
    if denominator <= 0:
        return None
    return _round(volume / denominator, digits)


def _weekday(date_text: str) -> str:
    try:
        return WEEKDAYS[date_cls.fromisoformat(str(date_text)).weekday()]
    except (TypeError, ValueError):
        return ""


class AvailabilityService:
    """营业日历 + 观测批次 + 停采回执 -> 覆盖分解与单位时间指标。"""

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def list_sources(self) -> list[dict[str, Any]]:
        return store.rows("source_calendar")

    def list_windows(self, source_id: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows("collection_window")
        if source_id:
            rows = [row for row in rows if row.get("source_id") == source_id]
        return rows

    def list_observations(self, window_id: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows("observation_batch")
        if window_id:
            rows = [row for row in rows if row.get("window_id") == window_id]
        return rows

    def list_blackouts(self, window_id: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows("blackout_receipt")
        if window_id:
            rows = [row for row in rows if row.get("window_id") == window_id]
        return rows

    def list_baselines(self, source_id: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows("baseline_profile")
        if source_id:
            rows = [row for row in rows if row.get("source_id") == source_id]
        return rows

    # ------------------------------------------------------------------
    # 单窗口
    # ------------------------------------------------------------------

    def _source(self, source_id: Any) -> dict[str, Any] | None:
        for row in self.list_sources():
            if row.get("source_id") == source_id:
                return row
        return None

    def _baseline(self, source_id: Any) -> dict[str, Any] | None:
        profiles = self.list_baselines(str(source_id))
        return profiles[0] if profiles else None

    def _business_segments(
        self, source: dict[str, Any], window: dict[str, Any]
    ) -> tuple[list[tuple[int, int]], list[dict[str, Any]]]:
        """营业日历展开；返回营业时段与非营业分钟（含全天停业）。"""
        window_start = int(window["start_minute"])
        window_end = int(window["end_minute"])
        window_date = str(window.get("date") or "")
        if window_date in {str(item) for item in source.get("closures", [])}:
            return [], [{"start": window_start, "end": window_end, "reason": "全天停业"}]
        weekday = _weekday(window_date)
        sessions = [row for row in source.get("calendar", []) if row.get("weekday") == weekday]
        if not sessions:
            return [], [{"start": window_start, "end": window_end, "reason": "非营业日"}]
        segments = []
        for session in sessions:
            start = max(window_start, int(session["open"]))
            end = min(window_end, int(session["close"]))
            if end > start:
                segments.append((start, end))
        merged = _merge(segments)
        if not merged:
            return [], [{"start": window_start, "end": window_end, "reason": "窗口不在营业时段内"}]
        outside = []
        cursor = window_start
        for start, end in merged:
            if start > cursor:
                outside.append({"start": cursor, "end": start, "reason": "非营业时段"})
            cursor = max(cursor, end)
        if cursor < window_end:
            outside.append({"start": cursor, "end": window_end, "reason": "非营业时段"})
        return merged, outside

    def compute_window(self, window_id: str) -> dict[str, Any] | None:
        window = next((row for row in self.list_windows() if row.get("window_id") == window_id), None)
        if window is None:
            return None
        source = self._source(window.get("source_id"))
        if source is None:
            return self._missing_source(window)

        window_start = int(window["start_minute"])
        window_end = int(window["end_minute"])
        business, outside = self._business_segments(source, window)
        business_set = _span(business)

        # ---- 观测证据：只认营业时段与窗口内的分钟 ----
        observed_set: set[int] = set()
        minute_volume: dict[int, tuple[str, float]] = {}
        observation_rows: list[dict[str, Any]] = []
        failed_batches: list[dict[str, Any]] = []
        duplicate_minutes: set[int] = set()
        duplicate_rows: list[dict[str, Any]] = []
        for batch in self.list_observations(window_id):
            start = int(batch["start"])
            end = int(batch["end"])
            span_inside_window = _minutes(max(window_start, start), min(window_end, end))
            inside = span_inside_window & business_set
            if batch.get("fetch_status") == "取数失败":
                failed_batches.append(
                    {
                        "batch_id": batch.get("batch_id"),
                        "start": start,
                        "end": end,
                        "count": batch.get("count"),
                        "fetch_status": "取数失败",
                        "retryable": True,
                    }
                )
                continue
            # 条数按分钟内均摊：跨营业边界的批次只把落在营业时段内的部分算进来。
            per_minute = float(batch.get("count", 0) or 0) / len(span_inside_window) if span_inside_window else 0.0
            arrived = str(batch.get("arrived_at") or "")
            for minute in inside:
                if minute in minute_volume:
                    duplicate_minutes.add(minute)
                    duplicate_rows.append(
                        {
                            "batch_id": batch.get("batch_id"),
                            "minute": minute,
                            "reason": "同一分钟被多个观测批次覆盖，按到达时间保留最新一批",
                        }
                    )
                previous = minute_volume.get(minute)
                if previous is None or arrived >= previous[0]:
                    minute_volume[minute] = (arrived, per_minute)
            observed_set |= inside
            observation_rows.append(
                {
                    "batch_id": batch.get("batch_id"),
                    "kind": batch.get("kind", "常规采集"),
                    "start": start,
                    "end": end,
                    "count": batch.get("count", 0),
                    "attributed_count": _round(per_minute * len(inside), 2),
                    "observed_minutes": len(inside),
                    "outside_business_minutes": len(span_inside_window) - len(inside),
                    "arrived_at": batch.get("arrived_at"),
                }
            )
        attributed_volume = sum(value for _, value in minute_volume.values())

        # ---- 停采回执：分类记账 + 分钟并集去重 ----
        planned_ids: dict[str, set[int]] = {}
        unplanned_ids: dict[str, set[int]] = {}
        blackout_rows: list[dict[str, Any]] = []
        for receipt in self.list_blackouts(window_id):
            start = int(receipt["start"])
            end = int(receipt["end"])
            covered = _minutes(start, end) & business_set
            kind = str(receipt.get("kind") or UNPLANNED)
            if kind == PLANNED:
                planned_ids.setdefault(str(receipt.get("blackout_id")), set()).update(covered)
            else:
                kind = UNPLANNED
                unplanned_ids.setdefault(str(receipt.get("blackout_id")), set()).update(covered)
            blackout_rows.append(
                {
                    "blackout_id": receipt.get("blackout_id"),
                    "kind": kind,
                    "reason": receipt.get("reason"),
                    "start": start,
                    "end": end,
                    "business_minutes": len(covered),
                    "overlaps_observation": len(covered & observed_set) > 0,
                    "received_at": receipt.get("received_at"),
                }
            )

        planned_union = set().union(*planned_ids.values()) if planned_ids else set()
        unplanned_union = set().union(*unplanned_ids.values()) if unplanned_ids else set()
        all_blackout = planned_union | unplanned_union
        # 有观测凭据的分钟不因回执而退出分母；回执与观测冲突单独标记。
        observed_set &= business_set
        stoppage = all_blackout - observed_set
        effective_set = business_set - stoppage
        observed = len(observed_set)
        effective = len(effective_set)
        missing = effective - observed
        # 重叠回执按“计划优先”归属，保证两类分钟数相加等于总停采分钟。
        planned_minutes = len(planned_union & stoppage)
        unplanned_minutes = len(stoppage - planned_union)
        overlap_saved = (
            sum(len(v) for v in planned_ids.values())
            + sum(len(v) for v in unplanned_ids.values())
            - len(all_blackout)
        )

        # ---- 缺口归集 ----
        gaps: list[dict[str, Any]] = []
        if planned_minutes:
            gaps.append({"reason": PLANNED, "minutes": planned_minutes})
        if unplanned_minutes:
            gaps.append({"reason": UNPLANNED, "minutes": unplanned_minutes})
        if missing:
            gap: dict[str, Any] = {"reason": "缺报", "minutes": missing}
            busiest = window.get("busiest")
            if busiest:
                busiest_set = _minutes(int(busiest["start"]), int(busiest["end"])) & business_set
                in_busiest = len(busiest_set - observed_set - stoppage)
                if in_busiest:
                    gap["busiest_missing_minutes"] = in_busiest
                    gap["detail"] = (
                        f"缺报含最忙时段 {busiest['start']}–{busiest['end']} 共 {in_busiest} 分钟；"
                        "不能用已采片段速率外推这段"
                    )
            gaps.append(gap)

        # ---- 空态：整段无可核验观测 ----
        session_empties = self._session_empties(business, business_set, observed_set, stoppage)
        empty_states: list[dict[str, Any]] = []
        if not business_set:
            empty_states.append({"scope": "窗口", "reason": "窗口内没有营业分钟（全天停业或不在营业时段）"})
        elif effective <= 0:
            empty_states.append({"scope": "窗口", "reason": "营业分钟全部被停采覆盖，没有可发布的分母"})
        elif observed == 0:
            empty_states.append(
                {"scope": "窗口", "reason": "有效营业分钟内没有可核验观测，缺报与零销量无法区分"}
            )
        empty_states.extend(session_empties)

        coverage = _round(observed / effective, 4) if effective else None
        published_rate = _rate(attributed_volume, observed)
        baseline_block = self._baseline_block(
            self._baseline(source.get("source_id")),
            observed=observed,
            effective=effective,
            missing=missing,
            published_rate=published_rate,
        )
        status, message = self._status(
            source=source,
            business=business_set,
            effective=effective,
            observed=observed,
            missing=missing,
            failed=failed_batches,
        )

        return {
            "window_id": window_id,
            "source_id": window.get("source_id"),
            "source_name": source.get("name"),
            "date": window.get("date"),
            "weekday": _weekday(str(window.get("date") or "")),
            "start_minute": window_start,
            "end_minute": window_end,
            "scenario": window.get("scenario"),
            "status": status,
            "message": message,
            "business_minutes": len(business_set),
            "business_segments": [{"start": a, "end": b} for a, b in business],
            "outside_business": outside,
            "effective_minutes": effective,
            "observed_minutes": observed,
            "missing_minutes": missing,
            "blackout_minutes": len(stoppage),
            "planned_blackout_minutes": planned_minutes,
            "unplanned_blackout_minutes": unplanned_minutes,
            "overlapping_receipt_minutes_deduped": overlap_saved,
            "observation_count": _round(attributed_volume, 2),
            "coverage": coverage,
            "coverage_label": _coverage_label(observed, effective),
            "published_rate": published_rate,
            "rate_unit": "笔/分钟",
            "rate_denominator": "实采营业分钟",
            "rate_denominator_minutes": observed,
            "decomposition": [
                {"label": "窗口分钟", "minutes": window_end - window_start},
                {"label": "非营业分钟", "minutes": sum(int(i["end"]) - int(i["start"]) for i in outside)},
                {"label": "营业分钟", "minutes": len(business_set)},
                {"label": "计划停采", "minutes": planned_minutes},
                {"label": "意外停采", "minutes": unplanned_minutes},
                {"label": "实采营业分钟", "minutes": observed},
                {"label": "缺报分钟", "minutes": missing},
            ],
            "observations": observation_rows,
            "failed_batches": failed_batches,
            "duplicate_minutes": sorted(duplicate_minutes),
            "duplicate_batches": duplicate_rows,
            "blackouts": blackout_rows,
            "gaps": gaps,
            "baseline": baseline_block,
            "empty_states": empty_states,
        }

    def _session_empties(
        self,
        business: list[tuple[int, int]],
        business_set: set[int],
        observed_set: set[int],
        stoppage: set[int],
    ) -> list[dict[str, Any]]:
        """营业时段级的空态：整段有营业分钟但一个观测凭据都没有。"""
        states: list[dict[str, Any]] = []
        for start, end in business:
            session_set = _minutes(start, end) & business_set
            if not (session_set & observed_set) and (session_set - stoppage):
                states.append(
                    {
                        "scope": "营业时段",
                        "start": start,
                        "end": end,
                        "reason": "该时段整段有凭据但没有观测批次，不能按零销量处理",
                    }
                )
        return states

    def _missing_source(self, window: dict[str, Any]) -> dict[str, Any]:
        return {
            "window_id": window.get("window_id"),
            "source_id": window.get("source_id"),
            "status": "数据源缺失",
            "message": f"窗口引用的数据源 {window.get('source_id')} 不在采集源清单里，无法读取营业日历",
            "business_minutes": 0,
            "effective_minutes": 0,
            "observed_minutes": 0,
            "missing_minutes": 0,
            "coverage": None,
            "coverage_label": "—",
            "published_rate": None,
            "decomposition": [],
            "observations": [],
            "failed_batches": [],
            "blackouts": [],
            "gaps": [],
            "baseline": None,
            "empty_states": [{"scope": "窗口", "reason": "缺少营业日历，无法确定分母"}],
        }

    def _baseline_block(
        self,
        baseline: dict[str, Any] | None,
        *,
        observed: int,
        effective: int,
        missing: int,
        published_rate: float | None,
    ) -> dict[str, Any]:
        if baseline is None:
            return {
                "available": False,
                "comparable": False,
                "baseline_rate": None,
                "published_rate": published_rate,
                "delta": None,
                "reason": "该数据源尚未形成完整窗口基线；没有同口径的 100% 覆盖样本，无法给出可比基线",
            }
        baseline_rate = _round(float(baseline.get("rate", 0)), 2)
        complete = effective > 0 and missing == 0 and observed == effective
        block: dict[str, Any] = {
            "available": True,
            "baseline_rate": baseline_rate,
            "baseline_minutes": baseline.get("minutes"),
            "baseline_window": baseline.get("window_id"),
            "baseline_coverage": "100%",
            "published_rate": published_rate,
            "comparable": complete,
            "delta": _round(float(published_rate) - baseline_rate, 2)
            if complete and published_rate is not None
            else None,
        }
        if complete:
            block["reason"] = "当期窗口 100% 核验，与基线同为实采营业分钟口径，可以比较"
        else:
            block["reason"] = (
                f"当期只核验 {observed}/{effective} 分钟（覆盖 {_coverage_label(observed, effective)}，"
                f"缺 {missing} 分钟），片段速率与完整窗口基线不同口径，不能比较"
            )
        return block

    def _status(
        self,
        *,
        source: dict[str, Any],
        business: set[int],
        effective: int,
        observed: int,
        missing: int,
        failed: list[dict[str, Any]],
    ) -> tuple[str, str]:
        if not source.get("calendar"):
            return "无营业日历", "采集源没有配置营业日历，不能确定营业分钟"
        if not business:
            return "全天停业", "营业日历在当天标记停业，窗口内没有营业分钟，指标为空态"
        if effective <= 0:
            return "全部停采", "营业分钟全部被停采回执覆盖，没有可发布的分母"
        if observed <= 0:
            return "无有效观测", "有效营业分钟没有任何观测凭据，缺报与零销量无法区分"
        if missing > 0:
            note = f"覆盖率 {_coverage_label(observed, effective)}，只发布实采分钟口径的当期读数"
            if failed:
                note += f"；另有 {len(failed)} 个批次取数失败，可重试"
            return "部分覆盖", note
        return "完整覆盖", "有效营业分钟全部有观测凭据，可与完整基线同口径比较"

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------

    def retry_window(self, window_id: str) -> tuple[dict[str, Any] | None, str]:
        """重试取数失败的批次；只改批次状态，不改窗口口径。"""
        window = next((row for row in self.list_windows() if row.get("window_id") == window_id), None)
        if window is None:
            return None, f"采集窗口 {window_id} 不存在"
        failed = [row for row in self.list_observations(window_id) if row.get("fetch_status") == "取数失败"]
        if not failed:
            return self.compute_window(window_id), "该窗口没有取数失败的批次，无需重试"
        for batch in failed:
            batch["fetch_status"] = "已重试成功"
            batch["attempts"] = int(batch.get("attempts", 0)) + 1
        return self.compute_window(window_id), f"已重试 {len(failed)} 个批次，覆盖分钟已重新计入"

    # ------------------------------------------------------------------
    # 指标入口
    # ------------------------------------------------------------------

    def metric_entry(self) -> dict[str, Any]:
        """覆盖分解表 + 单位时间指标：所有读数只来自实采营业分钟。"""
        rows = [self.compute_window(str(row["window_id"])) for row in self.list_windows()]
        rows = [row for row in rows if row is not None]
        effective = sum(int(row.get("effective_minutes", 0)) for row in rows)
        observed = sum(int(row.get("observed_minutes", 0)) for row in rows)
        missing = sum(int(row.get("missing_minutes", 0)) for row in rows)
        volume = sum(float(row.get("observation_count", 0)) for row in rows)
        comparable = [row["window_id"] for row in rows if row.get("baseline", {}).get("comparable")]
        return {
            "metric_code": "SALES_PER_AVAILABLE_MINUTE",
            "metric_name": "营业分钟单位销量",
            "definition": "观测批次条数 / 有观测凭据支撑的营业分钟",
            "denominator_rule": "只使用实采营业分钟；缺报、计划停采、意外停采均不进分母，也不做外推",
            "rate_unit": "笔/分钟",
            "decomposition": [
                {"label": "营业分钟", "minutes": sum(int(row.get("business_minutes", 0)) for row in rows)},
                {"label": "计划停采", "minutes": sum(int(row.get("planned_blackout_minutes", 0)) for row in rows)},
                {"label": "意外停采", "minutes": sum(int(row.get("unplanned_blackout_minutes", 0)) for row in rows)},
                {"label": "实采营业分钟", "minutes": observed},
                {"label": "缺报分钟", "minutes": missing},
            ],
            "totals": {
                "windows": len(rows),
                "effective_minutes": effective,
                "observed_minutes": observed,
                "missing_minutes": missing,
                "observation_count": _round(volume, 2),
                "coverage": _round(observed / effective, 4) if effective else None,
                "coverage_label": _coverage_label(observed, effective),
                "published_rate": _rate(volume, observed),
                "comparable_windows": comparable,
                "empty_states": [
                    {"window_id": row["window_id"], "states": row.get("empty_states", [])}
                    for row in rows
                    if row.get("empty_states")
                ],
            },
            "windows": rows,
            "baselines": self.list_baselines(),
        }

    def verify(self) -> dict[str, Any]:
        """把示例场景的核对结果整理成一张对账单，便于逐项复核口径。"""
        items = []
        for window in self.list_windows():
            computed = self.compute_window(str(window["window_id"]))
            if computed is None:
                continue
            items.append(
                {
                    "window_id": computed["window_id"],
                    "scenario": computed.get("scenario"),
                    "status": computed["status"],
                    "effective_minutes": computed["effective_minutes"],
                    "observed_minutes": computed["observed_minutes"],
                    "missing_minutes": computed["missing_minutes"],
                    "coverage_label": computed["coverage_label"],
                    "published_rate": computed["published_rate"],
                    "baseline_comparable": bool(computed["baseline"].get("comparable")),
                    "baseline_reason": computed["baseline"].get("reason"),
                    "empty_states": computed.get("empty_states", []),
                }
            )
        return {"items": items}
