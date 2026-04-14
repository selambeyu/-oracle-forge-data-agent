# KB Changelog

Tracks all additions, modifications, and removals of Knowledge Base documents.
Every new document must be listed here with its injection test result.

---

## 2026-04-13

### Added
- `kb/domain/dab_schemas.md` — PostgreSQL, MongoDB, SQLite, DuckDB schema reference for DAB datasets
- `kb/domain/join_key_glossary.md` — Cross-database join keys with type-cast rules
- `kb/domain/business_terms.md` — Business entity and metric terminology
- `kb/domain/unstructured_fields.md` — Fields requiring sandbox execution
- `kb/evaluation/dab_format.md` — DAB wire format reference (input/output)
- `kb/evaluation/scoring_method.md` — Pass@1 scoring definition and score log format
- `kb/evaluation/failure_categories.md` — Taxonomy of known DAB failure modes
- `kb/corrections/corrections_log.md` — Initialised (empty, append-only)
- `kb/architecture/` — Architecture reference docs (not loaded at agent runtime)

### Injection Test Status
| Document | Test run | Injected content detected | Outcome |
|---|---|---|---|
| dab_schemas.md | pending | — | — |
| join_key_glossary.md | pending | — | — |
| business_terms.md | pending | — | — |
| unstructured_fields.md | pending | — | — |
| dab_format.md | pending | — | — |
| scoring_method.md | pending | — | — |
| failure_categories.md | pending | — | — |

---

## Instructions for Adding a Document

1. Create the `.md` file in the appropriate `kb/` subdirectory.
2. Run injection test: `python probes/injection_tester.py --doc path/to/doc.md`
3. Record the result in the table above.
4. Add an entry to this CHANGELOG before merging to main.
