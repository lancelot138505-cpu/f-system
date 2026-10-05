"""Verifier-only oracles for the frozen V9.4 search-space baseline task family."""
from __future__ import annotations


def longest_increasing_run(nums):
    if not nums:
        return 0
    best = run = 1
    for index in range(1, len(nums)):
        run = run + 1 if nums[index] > nums[index - 1] else 1
        best = max(best, run)
    return best


def longest_decreasing_run(nums):
    if not nums:
        return 0
    best = run = 1
    for index in range(1, len(nums)):
        run = run + 1 if nums[index] < nums[index - 1] else 1
        best = max(best, run)
    return best


def count_adjacent_increases(nums):
    return sum(nums[index] > nums[index - 1] for index in range(1, len(nums)))


def count_adjacent_equals(nums):
    return sum(nums[index] == nums[index - 1] for index in range(1, len(nums)))


FOUNDATIONS = (
    {"label": "B01", "semantics": "longest strictly increasing contiguous run", "oracle": longest_increasing_run},
    {"label": "B02", "semantics": "longest strictly decreasing contiguous run", "oracle": longest_decreasing_run},
    {"label": "B04", "semantics": "number of adjacent increases", "oracle": count_adjacent_increases},
    {"label": "B06", "semantics": "number of adjacent equal pairs", "oracle": count_adjacent_equals},
)


def _combine(left, right, operator):
    if operator == "+":
        return lambda nums: left(nums) + right(nums)
    if operator == "-":
        return lambda nums: left(nums) - right(nums)
    raise ValueError("unsupported frozen task-family operator: " + operator)


def chain_tasks():
    """Return a 12-goal arithmetic chain over capabilities in the clean seed.

    The task graph is verifier-side only. Composer receives each Goal's public
    contract and input/output examples, never these predecessor declarations.
    """
    base = {row["label"]: row["oracle"] for row in FOUNDATIONS}
    definitions = (
        {"task_id": "D01", "left": "B01", "operator": "+", "right": "B02", "semantics": "sum of increasing and decreasing run lengths"},
        {"task_id": "D02", "left": "D01", "operator": "+", "right": "B04", "semantics": "prior aggregate plus adjacent-increase count"},
        {"task_id": "D03", "left": "D02", "operator": "-", "right": "B06", "semantics": "prior aggregate less adjacent-equality count"},
        {"task_id": "D04", "left": "D03", "operator": "+", "right": "B01", "semantics": "prior aggregate plus increasing-run length"},
        {"task_id": "D05", "left": "D04", "operator": "+", "right": "B02", "semantics": "prior aggregate plus decreasing-run length"},
        {"task_id": "D06", "left": "D05", "operator": "-", "right": "B04", "semantics": "prior aggregate less adjacent-increase count"},
        {"task_id": "D07", "left": "D06", "operator": "+", "right": "B06", "semantics": "prior aggregate plus adjacent-equality count"},
        {"task_id": "D08", "left": "D07", "operator": "-", "right": "B01", "semantics": "prior aggregate less increasing-run length"},
        {"task_id": "D09", "left": "D08", "operator": "+", "right": "B04", "semantics": "prior aggregate plus adjacent-increase count"},
        {"task_id": "D10", "left": "D09", "operator": "+", "right": "B02", "semantics": "prior aggregate plus decreasing-run length"},
        {"task_id": "D11", "left": "D10", "operator": "-", "right": "B06", "semantics": "prior aggregate less adjacent-equality count"},
        {"task_id": "D12", "left": "D11", "operator": "+", "right": "B01", "semantics": "prior aggregate plus increasing-run length"},
    )
    oracles = dict(base)
    for row in definitions:
        oracles[row["task_id"]] = _combine(oracles[row["left"]], oracles[row["right"]], row["operator"])
    return definitions, oracles
