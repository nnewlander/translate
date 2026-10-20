import json
from hashlib import sha256
import pytest
from booktrans.harness.batch_manifest import (
    append_sealed_checkpoint, commit_prepared_checkpoint, manifest_sha256, publish_manifest_successor,
    resolve_authoritative_manifest, resolve_checkpoint_seal_artifacts,
    sealed_checkpoint_manifest_projection, update_manifest,
)

def test_atomic_manifest_update_preserves_checkpoint_raw(tmp_path):
    raw=tmp_path/'raw.jsonl';raw.write_text('immutable\n',encoding='utf-8'); digest=sha256(raw.read_bytes()).hexdigest(); p=tmp_path/'batch_manifest.json'
    one={'completed_count':10,'checkpoints':[{'id':'checkpoint_01','raw_sha256':digest}]};update_manifest(p,one)
    two={'completed_count':20,'checkpoints':[*one['checkpoints'],{'id':'checkpoint_02','raw_sha256':'b'}]};update_manifest(p,two)
    assert json.loads(p.read_text(encoding='utf-8'))==two;assert sha256(raw.read_bytes()).hexdigest()==digest

def test_manifest_rejects_regression_and_drift(tmp_path):
    p=tmp_path/'batch_manifest.json';one={'completed_count':10,'checkpoints':[{'id':'checkpoint_01','raw_sha256':'a'}]};update_manifest(p,one)
    with pytest.raises(ValueError,match='REGRESSION'):update_manifest(p,{'completed_count':9,'checkpoints':one['checkpoints']})
    with pytest.raises(ValueError,match='DRIFT'):update_manifest(p,{'completed_count':20,'checkpoints':[{'id':'checkpoint_01','raw_sha256':'b'}]})


def test_append_sealed_checkpoint_derives_from_latest_manifest(tmp_path):
    manifest = tmp_path / 'batch_manifest.json'
    latest = {
        'chapter': 'chapter_11', 'batch': 'batch_004', 'target_count': 50,
        'completed_count': 30, 'checkpoint_count': 3, 'remaining_in_batch': 20,
        'status': 'OPEN_PROGRESS', 'next_checkpoint': 'checkpoint_04',
        'checkpoints': [{'id': f'checkpoint_{i:02d}'} for i in range(1, 4)],
    }
    update_manifest(manifest, latest)
    checkpoint = tmp_path / 'checkpoint_04'; checkpoint.mkdir()
    rows = '{"unit_uid":"one"}\n{"unit_uid":"two"}\n'
    for name in ('translation_request.jsonl', 'translation_raw_response.jsonl', 'translation_normalized_response.jsonl', 'translation_repaired_response.jsonl'):
        (checkpoint / name).write_text(rows, encoding='utf-8')
    qa = {'pass_count': 2, 'fail_count': 0, 'records': [{'passed': True}, {'passed': True}]}
    for name in ('translation_raw_qa_report.json', 'translation_normalized_qa_report.json', 'translation_final_qa_report.json'):
        (checkpoint / name).write_text(json.dumps(qa), encoding='utf-8')
    digest = sha256((checkpoint / 'translation_raw_response.jsonl').read_bytes()).hexdigest()
    normalization = {'raw_artifact': {'sha256_before': digest, 'sha256_after': digest}, 'input_unit_count': 2, 'normalized_unit_count': 0}
    (checkpoint / 'translation_normalization_report.json').write_text(json.dumps(normalization), encoding='utf-8')
    (checkpoint / 'semantic_review_report.json').write_text(json.dumps({'accept_count': 2}), encoding='utf-8')

    updated = append_sealed_checkpoint(manifest, checkpoint)

    assert updated['completed_count'] == 32
    assert updated['remaining_in_batch'] == 18
    assert updated['next_checkpoint'] == 'checkpoint_05'
    assert updated['checkpoints'][-1] == {'id': 'checkpoint_04', 'actual_count': 2, 'raw_sha256': digest, 'raw_pass': 2, 'final_pass': 2, 'normalization_changed': 0}


def test_versioned_manifest_and_seal_generation_are_single_authority(tmp_path):
    root = tmp_path / 'batch_manifest.json'
    legacy = {'target_count': 2, 'completed_count': 0, 'checkpoint_count': 0, 'next_checkpoint': 'checkpoint_01', 'checkpoints': []}
    update_manifest(root, legacy)
    checkpoint = tmp_path / 'checkpoint_01'; checkpoint.mkdir()
    generation = checkpoint / 'seal_generations' / 'generation_01'; generation.mkdir(parents=True)
    evidence = generation / 'checkpoint_evidence.json'; final = generation / 'translation_final_qa_report.json'
    evidence.write_text('{"logical_work_orders":["body.p.1"]}', encoding='utf-8')
    final.write_text('{"pass_count":1,"fail_count":0,"records":[{"passed":true}]}', encoding='utf-8')
    successor = {
        'target_count': 2, 'completed_count': 1, 'checkpoint_count': 1, 'next_checkpoint': 'checkpoint_02',
        'checkpoints': [{'id': 'checkpoint_01', 'actual_count': 1, 'raw_sha256': 'x', 'final_pass': 1,
            'seal_generation': {'generation': 1, 'path': 'seal_generations/generation_01',
                'checkpoint_evidence_sha256': manifest_sha256(evidence), 'final_qa_sha256': manifest_sha256(final)}}],
    }
    path = publish_manifest_successor(tmp_path, successor)
    resolved_path, resolved = resolve_authoritative_manifest(tmp_path)
    assert resolved_path == path and resolved['checkpoints'][0]['seal_generation']['generation'] == 1
    resolved_cp, resolved_evidence, resolved_final, generation_id = resolve_checkpoint_seal_artifacts(tmp_path, resolved['checkpoints'][0])
    assert (resolved_cp, resolved_evidence, resolved_final, generation_id) == (checkpoint, evidence, final, 1)


def test_compensating_successor_is_explicit_and_legacy_paths_remain_compatible(tmp_path):
    root = tmp_path / 'batch_manifest.json'
    historical = {'target_count': 2, 'completed_count': 2, 'checkpoint_count': 1, 'next_checkpoint': None, 'checkpoints': [{'id': 'checkpoint_01'}]}
    update_manifest(root, historical)
    rollback = {'target_count': 2, 'completed_count': 0, 'checkpoint_count': 0, 'next_checkpoint': 'checkpoint_01', 'checkpoints': [], 'authority_action': 'COMPENSATING_SUPERSESSION'}
    path = publish_manifest_successor(tmp_path, rollback, allow_compensating_rollback=True)
    assert resolve_authoritative_manifest(tmp_path)[0] == path
    checkpoint = tmp_path / 'checkpoint_01'; checkpoint.mkdir()
    assert resolve_checkpoint_seal_artifacts(tmp_path, {'id': 'checkpoint_01'})[1:] == (checkpoint / 'checkpoint_evidence.json', checkpoint / 'translation_final_qa_report.json', 0)


def test_corrected_generation_publishes_a_new_pointer_after_safe_compensation(tmp_path):
    root = tmp_path / 'batch_manifest.json'
    update_manifest(root, {'target_count': 1, 'completed_count': 1, 'checkpoint_count': 1, 'next_checkpoint': None, 'checkpoints': [{'id': 'checkpoint_01'}]})
    safe = {'target_count': 1, 'completed_count': 0, 'checkpoint_count': 0, 'next_checkpoint': 'checkpoint_01', 'checkpoints': [], 'authority_action': 'COMPENSATING_SUPERSESSION'}
    rollback = publish_manifest_successor(tmp_path, safe, allow_compensating_rollback=True)
    corrected = {'target_count': 1, 'completed_count': 1, 'checkpoint_count': 1, 'next_checkpoint': None, 'checkpoints': [{'id': 'checkpoint_01', 'seal_generation': {'generation': 1, 'path': 'seal_generations/generation_01', 'checkpoint_evidence_sha256': 'e', 'final_qa_sha256': 'f'}}]}
    published = publish_manifest_successor(tmp_path, corrected)
    assert rollback.name == 'batch_manifest_v1.json'
    assert published.name == 'batch_manifest_v2.json'
    assert resolve_authoritative_manifest(tmp_path)[0] == published


def _write_human_initial_checkpoint(checkpoint, *, include_human=True, duplicate_human=False):
    checkpoint.mkdir()
    request = [{"unit_uid": "provider"}, {"unit_uid": "human"}]
    raw = [{"unit_uid": "provider"}]
    repaired = request
    for name, rows in {
        "translation_request.jsonl": request,
        "translation_raw_response.jsonl": raw,
        "translation_normalized_response.jsonl": raw,
        "translation_repaired_response.jsonl": repaired,
    }.items():
        (checkpoint / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    raw_sha = sha256((checkpoint / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    (checkpoint / "translation_normalization_report.json").write_text(json.dumps({
        "raw_artifact": {"sha256_before": raw_sha, "sha256_after": raw_sha},
        "input_unit_count": 1, "normalized_unit_count": 0,
    }), encoding="utf-8")
    qa = {"pass_count": 2, "fail_count": 0, "records": [{"passed": True}, {"passed": True}]}
    for name in ("translation_raw_qa_report.json", "translation_normalized_qa_report.json", "translation_final_qa_report.json"):
        (checkpoint / name).write_text(json.dumps(qa), encoding="utf-8")
    (checkpoint / "semantic_review_report.json").write_text(json.dumps({"accept_count": 2}), encoding="utf-8")
    if include_human:
        human = {"artifact_type": "PRODUCTION_HUMAN_APPROVED_CANDIDATE", "schema_version": 3,
                 "origin": "HUMAN_INITIAL_CANDIDATE", "semantic_terminal": "HUMAN_APPROVED",
                 "unit_uid": "human", "working": {"unit_uid": "human"}, "qa": {"passed": True}}
        directory = checkpoint / "human_approved_candidates"; directory.mkdir()
        (directory / "human.json").write_text(json.dumps(human), encoding="utf-8")
        if duplicate_human:
            (directory / "human-duplicate.json").write_text(json.dumps(human), encoding="utf-8")


def test_human_initial_candidate_is_exact_frozen_membership_not_raw_count(tmp_path):
    manifest = tmp_path / "batch_manifest.json"
    update_manifest(manifest, {"target_count": 2, "completed_count": 0, "checkpoint_count": 0,
                               "next_checkpoint": "checkpoint_01", "checkpoints": []})
    checkpoint = tmp_path / "checkpoint_01"
    _write_human_initial_checkpoint(checkpoint)
    projection = sealed_checkpoint_manifest_projection(json.loads(manifest.read_text()), checkpoint)
    assert projection["completed_count"] == 2 and projection["checkpoints"][0]["actual_count"] == 2


def test_generation_seal_projection_reads_one_active_seal_dir_without_root_fallback(tmp_path):
    """A corrected prepared generation may coexist with stale root evidence."""
    manifest = tmp_path / "batch_manifest.json"
    latest = {"target_count": 2, "completed_count": 0, "checkpoint_count": 0,
              "next_checkpoint": "checkpoint_01", "checkpoints": []}
    update_manifest(manifest, latest)
    checkpoint = tmp_path / "checkpoint_01"
    _write_human_initial_checkpoint(checkpoint)
    root_prepared = (checkpoint / "translation_repaired_response.jsonl").read_bytes()
    for name in ("translation_normalized_qa_report.json", "translation_final_qa_report.json",
                 "semantic_review_report.json"):
        (checkpoint / name).unlink()
    generation = checkpoint / "seal_generations" / "prepared_current"; generation.mkdir(parents=True)
    request = [{"unit_uid": "provider"}, {"unit_uid": "human"}]
    (generation / "translation_repaired_response.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in request), encoding="utf-8")
    qa = {"pass_count": 2, "fail_count": 0, "records": [{"passed": True}, {"passed": True}]}
    for name in ("translation_normalized_qa_report.json", "translation_final_qa_report.json"):
        (generation / name).write_text(json.dumps(qa), encoding="utf-8")
    (generation / "semantic_review_report.json").write_text(json.dumps({"accept_count": 2}), encoding="utf-8")
    (generation / "checkpoint_evidence.json").write_text(json.dumps({"logical_work_orders": ["one", "two"]}), encoding="utf-8")
    seal = {"generation": 1, "path": "seal_generations/prepared_current", "prepared_state_id": "current"}

    projection = sealed_checkpoint_manifest_projection(latest, checkpoint, seal_generation=seal)
    published = publish_manifest_successor(tmp_path, projection)

    assert published.name == "batch_manifest_v1.json"
    assert projection["checkpoints"][0]["seal_generation"]["path"] == seal["path"]
    assert not (checkpoint / "translation_normalized_qa_report.json").exists()
    assert not (checkpoint / "translation_final_qa_report.json").exists()
    assert (checkpoint / "translation_repaired_response.jsonl").read_bytes() == root_prepared


@pytest.mark.parametrize("include_human, duplicate_human, error", [
    (False, False, "CANDIDATE_MEMBERSHIP_MISMATCH"),
    (True, True, "HUMAN_INITIAL_UIDS_INVALID"),
])
def test_human_initial_membership_rejects_incomplete_or_ambiguous_checkpoint(tmp_path, include_human, duplicate_human, error):
    manifest = tmp_path / "batch_manifest.json"
    update_manifest(manifest, {"target_count": 2, "completed_count": 0, "checkpoint_count": 0,
                               "next_checkpoint": "checkpoint_01", "checkpoints": []})
    checkpoint = tmp_path / "checkpoint_01"
    _write_human_initial_checkpoint(checkpoint, include_human=include_human, duplicate_human=duplicate_human)
    with pytest.raises(ValueError, match=error):
        sealed_checkpoint_manifest_projection(json.loads(manifest.read_text()), checkpoint)


def test_prepared_checkpoint_commit_is_idempotent_after_publication(tmp_path):
    manifest = tmp_path / "batch_manifest.json"
    update_manifest(manifest, {"target_count": 2, "completed_count": 0, "checkpoint_count": 0,
                               "next_checkpoint": "checkpoint_01", "checkpoints": []})
    checkpoint = tmp_path / "checkpoint_01"
    _write_human_initial_checkpoint(checkpoint)
    path, first, committed = commit_prepared_checkpoint(tmp_path, checkpoint)
    assert path == manifest and committed is True
    before = manifest.read_bytes()
    for _ in range(10):
        path, replay, committed = commit_prepared_checkpoint(tmp_path, checkpoint)
        assert path == manifest and replay == first and committed is False
        assert manifest.read_bytes() == before
