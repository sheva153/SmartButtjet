# Case-Insensitive Canonical Tag Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every canonical tag name an implicit, Unicode case-insensitive detection alias for both new messages and `/retag`.

**Architecture:** Keep persisted and displayed alias lists unchanged. Extend the shared parser-layer tag detector so it optionally considers taxonomy labels as match terms, then route new-message tag parsing through that same public `detect_tags` entry point already used by `/retag`.

**Tech Stack:** Python 3.12–3.13, Pydantic 2, pytest 9, Ruff, Pyright, uv, just

## Global Constraints

- Matching is Unicode-aware and case-insensitive via `str.casefold()`.
- Canonical names are implicit aliases only for tags; category behavior is unchanged.
- Existing `tags.csv`, `records.csv`, and `config.yaml` formats do not change.
- `/tags` continues to display only administrator-supplied aliases.
- Matching remains word-bounded and returns each canonical tag at most once.
- Existing records change only when an administrator explicitly runs `/retag`.
- Use `uv` for Python commands and `just` for the full developer gate.
- Work only on `feature/goals-forecast-tags-import`; never push directly to `main`.

---

### Task 1: Share canonical-name matching across parsing and retagging

**Files:**
- Modify: `src/income_stats/parsers/income_parser.py:389-405,487-488`
- Test: `tests/unit/test_income_parser.py:363-392`
- Test: `tests/integration/test_csv_repository.py:551-613`

**Interfaces:**
- Consumes: `merge_extra_tags(tags: dict[str, list[str]], extra_tags: dict[str, list[str]] | None) -> dict[str, list[str]]`
- Produces: `detect_tags(text: str, taxonomy: dict[str, list[str]]) -> list[str]`, with canonical tag names included as implicit match terms
- Preserves: `_detect_labels(text: str, aliases: dict[str, list[str]], *, include_labels: bool = False) -> list[str]`, so category callers remain alias-only

- [ ] **Step 1: Add focused failing parser and repository regression tests**

Add these tests after `test_extra_tags_are_merged_into_detected_tags` in `tests/unit/test_income_parser.py`:

```python
@pytest.mark.parametrize("text", ["вакалюк", "Вакалюк", "ВАКАЛЮК"])
def test_detect_tags_matches_canonical_tag_name_case_insensitively(
    text: str,
) -> None:
    taxonomy = {"вакалюк": ["вовч"]}

    assert detect_tags(text, taxonomy) == ["вакалюк"]


def test_detect_tags_returns_canonical_tag_once_and_keeps_word_boundaries() -> None:
    taxonomy = {"вакалюк": ["вовч"]}

    assert detect_tags("Вакалюк і вовч", taxonomy) == ["вакалюк"]
    assert detect_tags("псевдовакалюк", taxonomy) == []


def test_runtime_tag_canonical_name_is_used_by_income_parser(
    income_config: IncomeConfig,
) -> None:
    parsed = parse_income_message(
        "ВАКАЛЮК 500",
        income_config,
        extra_tags={"вакалюк": ["вовч"]},
    )

    assert parsed[0].tags == ["вакалюк"]
```

Add this test after `test_retag_records_unions_detected_tags_without_dropping_existing` in `tests/integration/test_csv_repository.py`:

```python
def test_retag_records_detects_canonical_tag_name_case_insensitively(
    csv_repository: CsvRecordsRepository,
) -> None:
    csv_repository.create_record_sync(
        make_record(
            id="canonical-tag",
            telegram_message_id=3,
            original_text="ВАКАЛЮК 500",
            tags=["cash"],
        )
    )

    result = csv_repository.retag_records_sync(
        {"вакалюк": ["вовч"]}, detect_tags
    )

    assert result.changed == 1
    assert result.deltas[0][1] == ["вакалюк"]
    record = csv_repository.get_record_sync("canonical-tag")
    assert record is not None
    assert record.tags == ["cash", "вакалюк"]
```

- [ ] **Step 2: Run the focused tests and verify the missing behavior**

Run:

```bash
uv run pytest \
  tests/unit/test_income_parser.py::test_detect_tags_matches_canonical_tag_name_case_insensitively \
  tests/unit/test_income_parser.py::test_detect_tags_returns_canonical_tag_once_and_keeps_word_boundaries \
  tests/unit/test_income_parser.py::test_runtime_tag_canonical_name_is_used_by_income_parser \
  tests/integration/test_csv_repository.py::test_retag_records_detects_canonical_tag_name_case_insensitively \
  -v
```

Expected: the three parametrized canonical-name cases, runtime parser test, and repository retag test fail because only explicit aliases are currently searched. The combined alias/deduplication and word-boundary assertions remain green.

- [ ] **Step 3: Implement canonical-name matching at the shared tag boundary**

Replace `_detect_labels` and `detect_tags` in `src/income_stats/parsers/income_parser.py` with:

```python
def _detect_labels(
    text: str,
    aliases: dict[str, list[str]],
    *,
    include_labels: bool = False,
) -> list[str]:
    lowered = text.casefold()
    detected: list[str] = []
    for label, terms in aliases.items():
        match_terms = [label, *terms] if include_labels else terms
        if any(
            re.search(
                rf"(?<!\w){re.escape(term.casefold())}(?!\w)",
                lowered,
            )
            for term in match_terms
        ):
            detected.append(label)
    return detected


def detect_tags(text: str, taxonomy: dict[str, list[str]]) -> list[str]:
    """Detect tag labels whose canonical name or aliases appear in `text`.

    Public entry point for both new-message parsing and retroactive retagging.
    Matching is Unicode case-insensitive and word-bounded.
    """
    return _detect_labels(text, taxonomy, include_labels=True)
```

Then change the tag assignment inside `parse_income_message` to call the shared public helper while leaving category detection unchanged:

```python
categories = _detect_labels(text, config.categories) or ["other"]
tags = detect_tags(text, merge_extra_tags(config.tags, extra_tags))
```

- [ ] **Step 4: Run focused regression tests and the surrounding parser/repository suites**

Run:

```bash
uv run pytest tests/unit/test_income_parser.py tests/integration/test_csv_repository.py -v
```

Expected: all parser and CSV repository tests pass, including every new canonical-name case.

- [ ] **Step 5: Run the complete repository gate**

Run:

```bash
just check
```

Expected: Ruff formatting and lint pass, Pyright reports 0 errors, and the full pytest suite passes.

- [ ] **Step 6: Commit the tested implementation**

```bash
git add \
  src/income_stats/parsers/income_parser.py \
  tests/unit/test_income_parser.py \
  tests/integration/test_csv_repository.py
git commit -m "fix: match canonical tag names case-insensitively"
```

After the commit, verify `git status --short --branch` shows no uncommitted changes and that the feature branch is ahead of its remote only by the new design, plan, and implementation commits pending the PR update.
