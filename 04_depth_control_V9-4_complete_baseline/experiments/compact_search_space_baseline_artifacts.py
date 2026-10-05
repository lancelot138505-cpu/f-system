"""Reduce completed V9.4 baseline outputs to review-critical metrics and receipts."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.v94_search_space_baseline import (
    compact_acquisition_trace,
    compact_equivalence_trace,
    compact_fallback_trace,
    compact_library_growth,
    compact_lifecycle_trace,
    compact_mode_result,
    compact_paired_result,
    compact_shortcut_trace as compact_source_shortcut_trace,
    compact_task_family_manifest,
    summarize_admission_receipts,
    summarize_formation_audits,
)

OUT = ROOT / "artifacts" / "search_space_baseline"
ARCHIVE = OUT / "full_search_trace_records.tar.gz"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def compact_admission_summary(value):
    if isinstance(value, list):
        return summarize_admission_receipts(value)
    if isinstance(value, dict) and value.get("format") == "admission-receipt-summary":
        for row in value.get("unique_structures", []):
            row.pop("tasks", None)
        return value
    raise SystemExit("unrecognized admission receipt format; leaving artifacts unchanged")


def compact_formation_summary(value):
    if isinstance(value, list):
        return summarize_formation_audits(value)
    if isinstance(value, dict) and value.get("format") == "formation-audit-summary":
        return value
    raise SystemExit("unrecognized formation audit format; leaving artifacts unchanged")


def compact_shortcut_trace(rows):
    return compact_source_shortcut_trace(rows)


def main():
    out_root = OUT.resolve()
    admission_path = OUT / "admission_results.json"
    formation_path = OUT / "formation_audit.json"
    admissions = read(admission_path)
    formations = read(formation_path)

    # Keep the per-identity/source counts and public checks; drop repeated receipt payloads.
    admission_summary = compact_admission_summary(admissions)
    formation_summary = compact_formation_summary(formations)
    write(admission_path, admission_summary)
    write(formation_path, formation_summary)

    task_family_path = OUT / "task_family.json"
    task_family = read(task_family_path)
    if "depth_tasks" in task_family:
        write(task_family_path, compact_task_family_manifest(task_family))

    trace_dir = OUT / "traces"
    for path in sorted(trace_dir.glob("*.json")):
        record = read(path)
        if path.name.endswith("_acquisition.json"):
            compacted = compact_acquisition_trace(record)
        else:
            compacted = compact_mode_result(record)
        write(path, compacted)

    write(OUT / "library_growth.json", compact_library_growth(read(OUT / "library_growth.json")))
    write(OUT / "lifecycle_trace.json", compact_lifecycle_trace(read(OUT / "lifecycle_trace.json")))
    write(OUT / "equivalence_trace.json", compact_equivalence_trace(read(OUT / "equivalence_trace.json")))
    write(OUT / "fallback_trace.json", compact_fallback_trace(read(OUT / "fallback_trace.json")))

    pair_path = OUT / "paired_2x2_results.json"
    pair_data = read(pair_path)
    old_pairs = pair_data.get("per_goal", []) if isinstance(pair_data, dict) else pair_data
    compact_pairs = []
    for pair in old_pairs:
        compacted = compact_paired_result(pair)
        compact_pairs.append(compacted)
    write(pair_path, compact_pairs)

    summary_path = OUT / "summary.json"
    summary = read(summary_path)
    summary.update({
        "admission_receipts_total": admission_summary["receipt_count"],
        "admission_receipts_by_source": admission_summary["receipt_counts_by_source"],
        "admission_unique_structure_count": admission_summary["unique_structure_count"],
        "main_chain_new_persistent_F_count": admission_summary["main_chain_new_persistent_F_count"],
        "branch_local_admission_receipts": admission_summary["branch_local_receipt_count"],
        "all_admission_receipts_exact_parent_pass": admission_summary["all_exact_parent_pass"],
    })
    summary.pop("admission_rows", None)
    summary.pop("all_admission_rows_exact_parent_pass", None)
    write(summary_path, summary)

    shortcut_path = OUT / "shortcut_trace.json"
    write(shortcut_path, compact_shortcut_trace(read(shortcut_path)))

    report_path = OUT / "report.md"
    report = report_path.read_text(encoding="utf-8")
    report = report.replace(
        "四种视图的目标结果均与 frozen oracle 对应：`PASS`。主链新 F strict admission `168` 条；其中 exact-parent PASS `168`，失败 `0`。全部 Python/JavaScript/oracle/candidate-cold/post-promotion-cold/public-reload 检查都记录在 admission receipts 和 trace。",
        "四种视图的目标结果均与 frozen oracle 对应：`PASS`。本轮共 168 条 strict admission receipt，涉及 12 个唯一 Structure ID：主链首次持久新增 12 个 F，FULL+EXPANDED 与 ACTIVE+EXPANDED 的反事实路线各产生 78 条 branch-local receipt。后续目标会在各自临时 snapshot 中再次形成相同 F identity；这些 receipt 不合并到主链 Library。168 条 receipt 的 exact-parent 检查全部 PASS。",
    )
    report = report.replace(
        "17. 所有新入库 F exact-parent：`168/168 PASS`。",
        "17. strict admission exact-parent：`168/168 receipt PASS`，覆盖 12 个唯一 Structure ID；主链持久新增 12 个 F。",
    )
    report = report.replace(
        "18. 所有新入库 F Python = JavaScript = oracle：`PASS`。",
        "18. 12 个唯一 F 的 Python = JavaScript = oracle（重复 branch receipt 复核也通过）：`PASS`。",
    )
    report = report.replace(
        "19. 所有新入库 F cold reload：`PASS`。",
        "19. 12 个唯一 F 的 cold reload（重复 branch receipt 复核也通过）：`PASS`。",
    )
    report = report.replace(
        "后续目标会在各自临时 snapshot 中再次形成相同 F identity；这些 receipt 不合并到主链 Library。",
        "每个目标 Dk 都在独立临时 snapshot 中依次重组 D01…Dk，因此每条 EXPANDED 路线有 `1+2+…+12=78` 条 receipt；这些临时 F 不合并到主链 Library。总数是 `12+78+78=168` 条记录，不是 168 个持久 F。",
    )
    report = report.replace(
        "`summary.json`、`baseline_state.json`、`task_family.json`、`library_growth.json`、`lifecycle_trace.json`、`equivalence_trace.json`、`depth_metrics.json`、`breadth_metrics.json`、`combined_metrics.json`、`paired_2x2_results.json`、`admission_results.json`、`formation_audit.json`、`shortcut_trace.json`、`fallback_trace.json` 和 `traces/Dxx_<view>.json` 保存完整机器记录。",
        "机器产物仅保留汇总计数、冻结 Goal/seed/digest manifest、各模式指标、按 Structure ID/source 去重的准入与 exact-parent 结果、shortcut/fallback 摘要，以及每 Goal 的关键 trace。原始候选事件、重复 promotion receipt 正文和中间搜索对象不持久化。",
    )
    report = report.replace(
        "本次完整原始事件明细归档在 `full_search_trace_records.tar.gz`。",
        "原始候选事件、重复 promotion receipt 正文和中间搜索对象不持久化。",
    )
    report = "\n".join(
        "机器产物只保留汇总计数、seed/digest manifest、每模式指标、按 F ID/source 聚合的 admission/exact-parent 检查，以及 shortcut/fallback 和 FIRST_CLASS public-slot 关键 trace；候选级事件与重复临时对象不落盘。"
        if "full_search_trace_records.tar.gz" in line else line
        for line in report.splitlines()) + "\n"
    report_path.write_text(report, encoding="utf-8")

    if ARCHIVE.exists():
        if ARCHIVE.resolve().parent != out_root:
            raise SystemExit("archive path escaped the named output directory; refusing deletion")
        ARCHIVE.unlink()
    print(json.dumps({
        "receipts": admission_summary["receipt_count"],
        "unique_structure_ids": admission_summary["unique_structure_count"],
        "main_chain_persistent_new_F": admission_summary["main_chain_new_persistent_F_count"],
        "branch_local_receipts": admission_summary["branch_local_receipt_count"],
        "archive_removed": not ARCHIVE.exists(),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
