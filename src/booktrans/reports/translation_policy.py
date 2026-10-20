"""Translator-facing calibration policy; deliberately not a prompt framework."""

CALIBRATION_TRANSLATION_POLICY = """# Calibration Translation Policy

Translate each requested block accurately and completely into natural, modern, easy-to-understand Chinese.

- Do not omit, summarize, compress, or add explanations not present in the source.
- Preserve logical relations, negation, conditions, modality, tone strength, numbers, and cautious treatment of proper nouns.
- Preserve protected content exactly, including URLs, email addresses, DOI, ISBN, and protected references.
- Move each semantic anchor to the Chinese text carrying that semantic presentation. Keep every supplied anchor exactly once; do not invent an anchor id.
- Do not require Chinese word order, target length, target segment count, or target run count to match English. Source meaning takes priority over English word order.
- Return only the requested target segments for the current block. Continuation context is input-only; do not translate or repeat it.
- `translator_notes` is reserved for a real ambiguity, missing context, or another issue that requires human judgment. If the unit can be translated reliably, set it to `null`.
- Do not add a note merely because code is preserved verbatim, protected content is preserved, a normal proper-noun choice is made, or normal structural handling is used.
"""
