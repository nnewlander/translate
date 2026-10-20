# Start a new book

## Step 1

Copy `config/next_book.example.json` to a new JSON file outside the historical runs.

## Step 2

Set `book_id`, `source_docx`, optional `source_pdf`, `output_root`, `source_language`, and `target_language`.

## Step 3

Run:

```powershell
python scripts/validate_next_book_intake.py --config NEW_BOOK.json
```

Proceed only when it returns `"status": "PASS"`.

## Step 4

Use the configured output root and book ID for a new run, then invoke the canonical CLI with the source DOCX and project root. Do not edit Python source or substitute a historical source path.

## Step 5

Run the canonical sequence:

`extract → translate → authority/QA → reconstruct`

## Step 6

Apply the degradation policy: Level 1 preserve; Level 2 local safe downgrade; Level 3 degraded, reviewable evidence; Level 4 `HUMAN_REQUIRED`.

## Freeze policy

`CORE_ARCHITECTURE_FROZEN = YES`. Change core only for a verified generic defect or a repeated, cross-book problem supported by stable evidence. A single-book exception stays local or routes to `HUMAN_REQUIRED`; it is not automatically a generic core feature.

Historical replay and completed-run scripts are `HISTORICAL_ONLY` and are not next-book runtime inputs.
