"""Deterministic Qwen production worker.

This package is the production hot path.  It never calls Codex, never mutates
a frozen selection, never overwrites RAW/QA, and never reseals a checkpoint.
"""
