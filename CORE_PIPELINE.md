# booktrans core pipeline

The reusable core accepts a source DOCX, produces immutable extraction and approved-authority evidence under a new run directory, and reconstructs a target-language DOCX without changing the source package.

`source DOCX → extract → translate → prepared authority and QA → reconstruct → target DOCX`

## Next-book intake

Use a new run and provide these fields before any model or production worker is launched:

| Field | Required | Meaning |
| --- | --- | --- |
| `book_id` | yes | Stable, filesystem-safe identifier for the new book. |
| `source_docx` | yes | Immutable source Word package. |
| `source_pdf` | no | Read-only corroborating visual/reading-order evidence. |
| `output_root` | yes | Parent directory for the new book's run artifacts. |
| `source_language` | yes | BCP 47-style source language tag. |
| `target_language` | yes | BCP 47-style target language tag. |

`config/next_book.example.json` is the intake template. The existing core CLI receives source and artifact location explicitly (`booktrans ... --input SOURCE_DOCX --project-root OUTPUT_ROOT`); a new book must not require a source-code edit or a current-book path substitution.

Run `python scripts/validate_next_book_intake.py --config NEW_BOOK.json` before launch. It is read-only and verifies required fields, a filesystem-safe book ID, readable source files, a valid DOCX package, available output root, and that the proposed run path does not already exist.

## OOXML and degradation policy

Core handling retains usable paragraphs, runs, styles, lists, tables, images, relationships, sections, hyperlinks, and native notes when present. PDF Assist is optional, read-only evidence for cases where DOCX alone is ambiguous: assets, captions, tables, notes, reading order, or page furniture. It never becomes a mutation authority.

| Level | Outcome |
| --- | --- |
| 1 | Preserve and project the approved structure directly. |
| 2 | Retain the local structure and use a safe, localized representation. |
| 3 | Preserve source evidence and emit an explicit degraded/reviewable result. |
| 4 | Stop automatic processing and route the unresolved case to `HUMAN_REQUIRED`. |

`HUMAN_REQUIRED` is a valid final endpoint: it records the evidence and does not silently guess, delete, or broaden a repair.

Automatic Reader Edition publishing is retired. It is neither a core stage nor a fallback for a new book.

## Offline core smoke test

`tests/fixtures/generic_pipeline_smoke.docx` is an independent synthetic source fixture. It contains headings, body paragraphs, a list, a table, an image, a hyperlink, package relationships, a normal section boundary, and a native Word footnote, with no content from any project book. `tests/unit/test_generic_pipeline_smoke.py` runs source inspection and extraction through prepared tagged authority, reconstruction, package parsing, and source-immutability checks. It makes no network or translation-model call.

The canonical core is freeze-ready after this smoke gate. Historical completed-run replay code is intentionally outside the next-book entry path.
