from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from booktrans.domain.ir import CoverageLedger, DocumentIR
from booktrans.domain.models import InspectionReport


class InspectionRepository:
    """Small SQL boundary for M1; no domain service builds SQL itself."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_or_create_document(self, report: InspectionReport) -> int:
        source = report.source
        row = self.connection.execute(
            "SELECT id FROM documents WHERE source_sha256 = ?", (source.sha256,)
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cursor = self.connection.execute(
            "INSERT INTO documents(source_sha256, canonical_source_path, size_bytes, first_seen_at_utc) "
            "VALUES (?, ?, ?, ?)",
            (source.sha256, source.path, source.size_bytes, datetime.now(UTC).isoformat()),
        )
        return int(cursor.lastrowid)

    def record_inspection(self, report: InspectionReport, report_path: Path, part_sizes: dict[str, int]) -> None:
        document_id = self.get_or_create_document(report)
        cursor = self.connection.execute(
            "INSERT INTO inspection_runs(run_id, document_id, inspected_at_utc, report_path, source_mutated) "
            "VALUES (?, ?, ?, ?, ?)",
            (report.run_id, document_id, report.inspected_at_utc.isoformat(), str(report_path), int(report.source.source_mutated)),
        )
        inspection_run_id = int(cursor.lastrowid)
        self.connection.executemany(
            "INSERT INTO parts(inspection_run_id, part_name, content_type, size_bytes) VALUES (?, ?, ?, ?)",
            [(inspection_run_id, name, None, size) for name, size in part_sizes.items()],
        )


class ExtractionRepository:
    """Persists M2 audit records without creating translation-era tables."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def _get_or_create_document(self, document: DocumentIR) -> int:
        source = document.source
        row = self.connection.execute("SELECT id FROM documents WHERE source_sha256 = ?", (source.sha256,)).fetchone()
        if row is not None:
            return int(row["id"])
        cursor = self.connection.execute(
            "INSERT INTO documents(source_sha256, canonical_source_path, size_bytes, first_seen_at_utc) VALUES (?, ?, ?, ?)",
            (source.sha256, source.path, source.size_bytes, datetime.now(UTC).isoformat()),
        )
        return int(cursor.lastrowid)

    def record_extraction(
        self,
        run_id: str,
        document: DocumentIR,
        ledger: CoverageLedger,
        document_ir_path: Path,
        coverage_path: Path,
    ) -> None:
        document_id = self._get_or_create_document(document)
        self.connection.execute(
            "INSERT INTO extraction_runs(run_id, document_id, extracted_at_utc, document_ir_path, coverage_path, source_mutated, coverage_complete) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, document_id, document.extracted_at_utc.isoformat(), str(document_ir_path), str(coverage_path), 0, int(ledger.coverage_complete)),
        )
        for block in document.blocks:
            self.connection.execute(
                "INSERT OR IGNORE INTO units(document_id, unit_uid, display_id, part_name, story, block_type, content_role, source_hash, structure_fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (document_id, block.unit_uid, block.display_id, block.part_name, block.story, block.block_type, block.content_role, block.source_hash, block.structure_fingerprint),
            )
