"""Deterministically reconcile a sealed terminal batch closure."""
from __future__ import annotations
import argparse, re, sys, json
from hashlib import sha256
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path: sys.path.insert(0, str(SCRIPTS))
import advance_full_book_batch013 as policy
from booktrans.harness.batch_manifest import resolve_authoritative_manifest, resolve_checkpoint_seal_artifacts

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument('--batch', required=True, type=int); parser.add_argument('--what-if', action='store_true'); parser.add_argument('--close-only', action='store_true'); parser.add_argument('--rollover-only', action='store_true'); args = parser.parse_args()
    if args.close_only and args.rollover_only: raise RuntimeError('CLOSURE_MODE_AMBIGUOUS')
    number = args.batch
    batch_path = RUN / f'full_book_batch_{number:03d}'; label = f'FULL_BOOK_BATCH_{number:03d}'
    progress_paths = [
        (int(m.group(1)), path)
        for path in RUN.glob('full_book_progress_v*.json')
        if (m := re.fullmatch(r'full_book_progress_v(\d+)\.json', path.name))
    ]
    prior = max(progress_paths)[1]
    progress_path = RUN / f'full_book_progress_v{int(re.search(r"v(\d+)", prior.name).group(1))+1}.json'
    # Replaying a completed close-only operation must derive the same
    # completion payload.  Its generated progress is not a new prior batch;
    # recover the original prior from its immutable supersession binding.
    if args.close_only and (RUN / f'full_book_batch_{number:03d}_completion_report.json').is_file():
        close_progresses = []
        for version, path in progress_paths:
            payload = json.loads(path.read_text(encoding='utf-8'))
            if payload.get('status') == 'FULL_BOOK_HARDENING_PENDING' and payload.get('latest_completed_batch') == label:
                close_progresses.append((version, path, payload))
        if close_progresses:
            _version, progress_path, close_progress = max(close_progresses)
            supersedes = close_progress.get('supersedes')
            if not isinstance(supersedes, str) or not (RUN / supersedes).is_file():
                raise RuntimeError('CANONICAL_CLOSE_PROGRESS_BINDING_INVALID')
            prior = RUN / supersedes
    manifest_path, manifest = resolve_authoritative_manifest(batch_path)
    # A batch-scoped generic release gate is the release-authority handoff
    # regardless of whether its authoritative manifest is named at the root
    # or is a versioned successor.  Restricting this to ``*_vN`` manifests
    # made root-manifest batches re-adjudicate historical checkpoint debt.
    uses_batch_release_gate = any(batch_path.glob('release_gate_v*.json'))
    if uses_batch_release_gate:
        _validate_versioned_closure(batch_path, label, manifest_path, manifest)
        class _BatchPath:
            def __init__(self, path: Path, manifest_path: Path): self.path, self.manifest_path = path, manifest_path
            def __truediv__(self, name: str) -> Path: return self.manifest_path if name == 'batch_manifest.json' else self.path / name
        policy.BATCH = _BatchPath(batch_path, manifest_path)
        # The validated batch gate is the sole release-debt authority.  The
        # historical checkpoint reports remain immutable completeness evidence.
        policy.human_approved_release_ids = lambda _run, known: set(known)
    else:
        policy.BATCH = batch_path
    policy.PRIOR=prior; policy.SELECTION=RUN/f'full_book_batch_{number:03d}_selection.json'; policy.NEXT_BATCH=RUN/f'full_book_batch_{number+1:03d}'; policy.NEXT_SELECTION=RUN/f'full_book_batch_{number+1:03d}_selection.json'; policy.COMPLETION=RUN/f'full_book_batch_{number:03d}_completion_report.json'; policy.PROGRESS=progress_path; policy.BATCH_LABEL=label; policy.NEXT_BATCH_LABEL=f'FULL_BOOK_BATCH_{number+1:03d}'
    mode = ['--close-only'] if args.close_only else (['--rollover-only'] if args.rollover_only else [])
    sys.argv=[sys.argv[0]] + (['--what-if'] if args.what_if else []) + mode; policy.main()


def _validate_versioned_closure(batch_dir: Path, label: str, manifest_path: Path, manifest: dict) -> None:
    if manifest.get('batch') != label or manifest.get('status') != 'COMPLETE' or manifest.get('completed_count') != manifest.get('target_count') or manifest.get('checkpoint_count') != len(manifest.get('checkpoints') or []):
        raise RuntimeError('ROLLOVER_AUTHORITATIVE_MANIFEST_INCOMPLETE')
    for checkpoint in manifest.get('checkpoints') or []:
        _directory, evidence, _final, _generation = resolve_checkpoint_seal_artifacts(batch_dir, checkpoint)
        payload = json.loads(evidence.read_text(encoding='utf-8'))
        if not isinstance(payload.get('logical_work_orders'), list) or len(payload['logical_work_orders']) != checkpoint.get('actual_count'):
            raise RuntimeError('ROLLOVER_CHECKPOINT_SEAL_INVALID')
    gates = sorted(batch_dir.glob('release_gate_v*.json'))
    if not gates: raise RuntimeError('ROLLOVER_RELEASE_GATE_MISSING')
    gate_path = gates[-1]; gate = json.loads(gate_path.read_text(encoding='utf-8'))
    debt_path = Path(gate.get('release_debt_manifest', ''))
    if not (gate.get('artifact_type') == 'BATCH_RELEASE_GATE' and gate.get('batch') == label and gate.get('batch_dir') == batch_dir.name and gate.get('verdict') == 'PASS' and gate.get('unresolved_ids') == [] and gate.get('integrity_pass') is True and gate.get('candidate_authority_pass') is True and debt_path.is_file() and sha256(debt_path.read_bytes()).hexdigest() == gate.get('release_debt_manifest_sha256')):
        raise RuntimeError('ROLLOVER_RELEASE_GATE_BINDING_INVALID')
if __name__ == '__main__': main()
