"""Bulk test-case import from a CSV file — the golden-dataset upload path.

Separate from eval/service.py so this is unit-testable without a DB
fixture: parsing/validation is pure (bytes in, TestCase list or a raised
CsvParseError out), and the route handler (api/prompt_eval_routes.py) is the
only place this touches Mongo, via the existing eval_service.update_suite_cases().
"""

from __future__ import annotations

import csv
import io
import os
import uuid

from eval.schema import TestCase

# Same env-override convention as api/skill_service.py's LOMA_SKILL_MAX_*_BYTES.
MAX_CSV_ROWS = int(os.environ.get("LOMA_EVAL_MAX_CSV_ROWS", "500"))
MAX_CSV_BYTES = int(os.environ.get("LOMA_EVAL_MAX_CSV_BYTES", str(2 * 1024 * 1024)))

REQUIRED_COLUMN = "input"
LIST_COLUMNS = ("expected_contains", "expected_not_contains")
OPTIONAL_COLUMNS = ("rubric",)


class CsvParseError(ValueError):
    """Raised for any invalid upload — size, encoding, missing column, or
    one or more bad rows. Always carries every problem found, not just the
    first, so a caller can show the whole picture in one pass rather than
    a fix-one-resubmit-find-the-next loop."""


def _split_list_cell(value: str) -> list[str]:
    # Same comma-separated convention as the dashboard's TagListInput for
    # the must-contain/must-not-contain fields — kept consistent so a value
    # typed by hand and a value uploaded via CSV behave identically.
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_cases_csv(data: bytes) -> list[TestCase]:
    """Parse a CSV file into TestCases. Columns (header row required):
    `input` (required), `expected_contains`/`expected_not_contains`
    (optional, comma-separated within one cell), `rubric` (optional).
    Column names are matched case-insensitively and independent of order.

    All-or-nothing: if ANY row is invalid, the whole upload is rejected with
    every row's problem listed — never a silent partial import. A bulk
    import path deserves the same scrutiny as a single hand-typed case, not
    less, precisely because a mistake here is harder to spot at a glance.
    """
    if len(data) > MAX_CSV_BYTES:
        raise CsvParseError(f"File is {len(data)} bytes, which exceeds the {MAX_CSV_BYTES}-byte limit")

    try:
        text = data.decode("utf-8-sig")  # -sig strips a BOM if Excel added one
    except UnicodeDecodeError as exc:
        raise CsvParseError("File is not valid UTF-8 — re-export as CSV UTF-8") from exc

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise CsvParseError("File has no header row")

    normalized_fields = {(f or "").strip().lower(): f for f in reader.fieldnames}
    if REQUIRED_COLUMN not in normalized_fields:
        raise CsvParseError(f"Missing required column: {REQUIRED_COLUMN!r}")

    rows = list(reader)
    if len(rows) > MAX_CSV_ROWS:
        raise CsvParseError(f"File has {len(rows)} rows, which exceeds the {MAX_CSV_ROWS}-row limit")
    if not rows:
        raise CsvParseError("File has a header row but no data rows")

    cases: list[TestCase] = []
    errors: list[str] = []
    for i, raw_row in enumerate(rows, start=2):  # start=2: header is row 1
        row = {(k or "").strip().lower(): (v or "") for k, v in raw_row.items()}
        input_text = row.get(REQUIRED_COLUMN, "").strip()
        if not input_text:
            errors.append(f"row {i}: {REQUIRED_COLUMN!r} is required")
            continue
        cases.append(TestCase(
            case_id=uuid.uuid4().hex[:12],
            input=input_text,
            expected_contains=_split_list_cell(row.get("expected_contains", "")),
            expected_not_contains=_split_list_cell(row.get("expected_not_contains", "")),
            rubric=row.get("rubric", "").strip(),
        ))

    if errors:
        raise CsvParseError("; ".join(errors))

    return cases
