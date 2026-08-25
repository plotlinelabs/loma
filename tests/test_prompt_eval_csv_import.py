"""Unit tests for eval/csv_import.py — bulk test-case CSV upload.

Pure logic, no DB. Run: `.venv/bin/python tests/test_prompt_eval_csv_import.py`
(self-running; also works under pytest if available).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eval.csv_import as csv_import
from eval.csv_import import CsvParseError, parse_cases_csv


def _csv(text: str) -> bytes:
    return text.encode("utf-8")


def test_minimal_valid_csv():
    data = _csv("input\nWhat's your refund policy?\nHow do I reset my password?\n")
    cases = parse_cases_csv(data)
    assert len(cases) == 2
    assert cases[0].input == "What's your refund policy?"
    assert cases[0].expected_contains == []
    assert cases[0].rubric == ""


def test_full_columns():
    data = _csv(
        "input,expected_contains,expected_not_contains,rubric\n"
        '"What is 2+2?","4, four","I don''t know","Is the answer correct?"\n'
    )
    cases = parse_cases_csv(data)
    assert len(cases) == 1
    c = cases[0]
    assert c.input == "What is 2+2?"
    assert c.expected_contains == ["4", "four"]
    assert c.rubric == "Is the answer correct?"


def test_columns_matched_case_insensitively_and_any_order():
    data = _csv("Rubric,INPUT\nIs it polite?,Hello there\n")
    cases = parse_cases_csv(data)
    assert cases[0].input == "Hello there"
    assert cases[0].rubric == "Is it polite?"


def test_each_case_gets_a_unique_case_id():
    data = _csv("input\nfirst\nsecond\n")
    cases = parse_cases_csv(data)
    assert cases[0].case_id != cases[1].case_id
    assert cases[0].case_id and cases[1].case_id


def test_missing_input_column_rejects_whole_file():
    data = _csv("expected_contains\nfoo\n")
    try:
        parse_cases_csv(data)
        assert False, "expected CsvParseError"
    except CsvParseError as e:
        assert "input" in str(e)


def test_empty_input_cell_is_reported_and_rejects_whole_file():
    # A genuinely blank line is skipped entirely by the csv module (correct,
    # standard behavior) — to exercise "input present but empty" the row
    # needs another populated column so it isn't dropped before validation
    # ever sees it.
    data = _csv("input,rubric\ngood row,ok\n,Is it polite?\nanother good row,ok\n")
    try:
        parse_cases_csv(data)
        assert False, "expected CsvParseError"
    except CsvParseError as e:
        assert "row 3" in str(e)


def test_multiple_bad_rows_all_listed_not_just_the_first():
    data = _csv("input,rubric\n,a\nvalid,b\n,c\n")
    try:
        parse_cases_csv(data)
        assert False, "expected CsvParseError"
    except CsvParseError as e:
        # rows 2 and 4 both have an empty input — both should be named,
        # proving this isn't a silent partial import or a
        # fail-on-first-error path.
        assert "row 2" in str(e)
        assert "row 4" in str(e)


def test_no_data_rows_is_rejected():
    data = _csv("input\n")
    try:
        parse_cases_csv(data)
        assert False, "expected CsvParseError"
    except CsvParseError as e:
        assert "no data rows" in str(e)


def test_non_utf8_bytes_rejected_with_clear_message():
    data = b"input\n\xff\xfe not valid utf-8\n"
    try:
        parse_cases_csv(data)
        assert False, "expected CsvParseError"
    except CsvParseError as e:
        assert "UTF-8" in str(e)


def test_bom_prefixed_excel_export_is_handled():
    # Excel's "CSV UTF-8" export prepends a byte-order mark — utf-8-sig
    # strips it rather than treating it as part of the first column name.
    data = b"\xef\xbb\xbfinput\nhello\n"
    cases = parse_cases_csv(data)
    assert len(cases) == 1
    assert cases[0].input == "hello"


def test_row_limit_boundary():
    original = csv_import.MAX_CSV_ROWS
    csv_import.MAX_CSV_ROWS = 2
    try:
        ok = _csv("input\na\nb\n")
        assert len(parse_cases_csv(ok)) == 2
        too_many = _csv("input\na\nb\nc\n")
        try:
            parse_cases_csv(too_many)
            assert False, "expected CsvParseError"
        except CsvParseError as e:
            assert "row" in str(e).lower()
    finally:
        csv_import.MAX_CSV_ROWS = original


def test_byte_limit_boundary():
    original = csv_import.MAX_CSV_BYTES
    csv_import.MAX_CSV_BYTES = 20
    try:
        data = _csv("input\n" + "x" * 30 + "\n")
        try:
            parse_cases_csv(data)
            assert False, "expected CsvParseError"
        except CsvParseError as e:
            assert "byte" in str(e).lower()
    finally:
        csv_import.MAX_CSV_BYTES = original


def test_whitespace_only_input_is_treated_as_empty():
    data = _csv("input\n   \nvalid\n")
    try:
        parse_cases_csv(data)
        assert False, "expected CsvParseError"
    except CsvParseError as e:
        assert "row 2" in str(e)


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    fns = [g for n, g in sorted(globals().items()) if n.startswith("test_") and callable(g)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {fn.__name__}: {e!r}")
    print(f"\n{passed}/{len(fns)} tests passed")
    sys.exit(0 if passed == len(fns) else 1)
