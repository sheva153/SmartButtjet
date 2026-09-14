# Case-Insensitive Canonical Tag Matching — Design

## Summary

Treat every canonical tag name as an implicit detection alias. A message that
contains `вакалюк`, `Вакалюк`, or `ВАКАЛЮК` must therefore resolve to the same
stored tag, `вакалюк`, even when the administrator did not repeat the tag name
in its explicit alias list.

This closes the gap exposed by the runtime tag `вакалюк`, whose explicit
aliases are `вовчинецька`, `оксана`, and `вовч`: today those aliases match
case-insensitively, but the canonical name itself does not match at all.

## Requirements

- Matching is Unicode-aware and case-insensitive via `str.casefold()`.
- A tag's canonical name participates in detection alongside its configured or
  runtime aliases.
- Detection continues to use word boundaries, so a tag is not found inside a
  longer word.
- The parser returns the normalized canonical tag once, even if both its name
  and one or more aliases occur in the message.
- New-record parsing and `/retag` use exactly the same matching behavior.
- Categories retain their current alias-only behavior; this change applies only
  to tags.
- Existing `tags.csv`, `records.csv`, and `config.yaml` formats do not change.
- `/tags` continues to display only administrator-supplied aliases; the implicit
  canonical alias is matching behavior, not persisted data.

## Considered Approaches

### 1. Match the canonical name in `detect_tags` (recommended)

Keep stored and displayed aliases unchanged. At the shared tag-detection
boundary, test `[canonical_tag, *aliases]` after case-folding each term. Route
new-record parsing through this public helper, as `/retag` already does.

This is the smallest durable change, keeps parsing and retagging aligned, and
does not leak an implementation detail into `/tags` output or CSV data.

### 2. Add the canonical name in `merge_extra_tags`

This would also make the name detectable, but the merged taxonomy is used by
`/tags` rendering. The command would show redundant output such as
`вакалюк: вакалюк, вовчинецька, ...`, and matching-only data would become mixed
with persisted aliases.

### 3. Add `вакалюк` manually to `tags.csv`

This fixes one tag but repeats the same failure for every future tag whose name
is not manually duplicated as an alias. It is operationally fragile and does
not enforce the requested invariant.

## Components and Data Flow

1. `parse_income_message` builds the combined configured/runtime tag taxonomy.
2. It calls `detect_tags(text, taxonomy)` instead of the generic category
   matcher directly.
3. `detect_tags` case-folds the message, canonical name, and explicit aliases,
   then applies the existing word-bounded matching rule.
4. The returned canonical names pass through the existing Pydantic tag
   normalization and deduplication before persistence.
5. `/retag` already calls `detect_tags`, so it receives the same behavior with
   no repository or storage migration.

## Error Handling and Compatibility

No new user-facing errors are introduced. Configuration and runtime tag models
continue rejecting blank labels and aliases as they do now. Defensive
case-folding inside `detect_tags` makes its public contract reliable even when a
caller supplies a raw taxonomy rather than one already normalized by Pydantic.

Existing records are not rewritten automatically. An administrator can run
`/retag` to add canonical-name matches to historical records; existing tags are
preserved because retagging remains additive.

## Testing

Add focused regression coverage for:

- `вакалюк`, `Вакалюк`, and `ВАКАЛЮК` all returning `["вакалюк"]` when the
  canonical name is absent from the explicit aliases;
- canonical name plus explicit alias in one message still returning the tag
  once;
- word-boundary protection for the implicit canonical name;
- `parse_income_message` using the same behavior for runtime tags;
- `/retag` adding the canonical tag from historical text without removing
  existing tags.

Run `just check` before updating the pull request.

## Non-Goals

- Fuzzy matching, typo correction, stemming, or transliteration.
- Renaming tags or changing their display capitalization.
- Automatically rewriting existing CSV files.
- Changing category detection semantics.
