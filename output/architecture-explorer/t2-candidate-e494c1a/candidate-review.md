# Architecture Explorer T2：Supervisor → Worker 候选证据报告

> 本报告由确定性扫描器从机器候选记录生成。它是待人工审核的候选材料，不是架构批准。

## 扫描元数据

- 源码 Commit：`e494c1a25347d72ae9c279dc8ddc875ec87af144`
- Worktree 状态：`clean`
- 扫描器：`architecture-explorer-t2/1.1`
- 扫描时间：`2026-10-30T03:10:04Z`
- 候选报告摘要 SHA-256：`0f0f016de85759c20193b05b342da643fc59738ce376de0443ac4aa4f4debf5c`
- 扫描结果：`complete`

## 封闭扫描范围

- 生产源码：`scripts/production/production_campaign_supervisor.ps1`
- 生产源码：`scripts/production/run_qwen_deterministic_worker.py`
- 生产源码：`src/booktrans/production_worker/worker.py`
- 辅助测试：`tests/production/test_supervisor_launch_boundary.ps1`（只作辅助证据）
- 辅助测试：`tests/unit/test_production_entrypoint_bootstrap.py`（只作辅助证据）
- 辅助测试：`tests/unit/test_qwen_deterministic_worker.py`（只作辅助证据）

## 候选关系（未审核）

- 状态：`candidate` / `PENDING_HUMAN_REVIEW`
- 建议方向：`production-control -> translation-execution`
- 建议关系类型：`process-launch`
- 静态可达状态：`STATIC_CONDITIONAL`
- 真实运行状态：`NOT_COLLECTED`

### 静态扫描能够陈述的条件

- Supervisor argument vector selects --mode production.
- Supervisor argument vector supplies --authorize-production-checkpoint-write.
- Supervisor binds batch and checkpoint from the current rebuilt state.
- The wrapper imports the exact Worker main and calls it only under __name__ == '__main__'.
- Worker main parses args through build_parser().parse_args(argv).
- Worker main's exact canary guard exits before the production guard sequence.
- Worker main then requires worker-id, then paired batch/checkpoint, before a top-level run_production_checkpoint return.

### 保留未知事项

- Static scanning does not prove that the Supervisor operational loop reaches this launch on any particular run.
- Static scanning does not prove that runtime preconditions, credentials, or external services are available.
- Parameter business meaning beyond the observed names, literals, and direct bindings remains unreviewed.

## 必需证据判定

| 证据种类 | 扫描状态 | 证据 ID |
|---|---|---|
| `worker_script_binding` | `unique` | `evidence:sha256:05ea188dd9085a65c4642f0c75961b731a4a8477fd86ed4f02f1633e4ca77b13` |
| `production_argument_vector` | `unique` | `evidence:sha256:5f59c334ec000f69e01750b17e111d71662c2e183a54ce25bdd62a6cc0f362a3` |
| `process_boundary_definition` | `unique` | `evidence:sha256:208e42baa3b95b1ef9f4400e521f3ec5ecc558d4091371667614b793e425b711` |
| `process_boundary_invocation` | `unique` | `evidence:sha256:2b0a961f9feeeaff6d9325bc1ae50ae299723ecfd0fc58a8952d35a469aa250e` |
| `worker_wrapper_import` | `unique` | `evidence:sha256:c09b17cb3de85d77888286adef6daee1cfd6ca9ab4b4e1efcf6639addcfa1d6a` |
| `worker_wrapper_main_call` | `unique` | `evidence:sha256:179b1775ba7d00c1b9b4d371c4ec714f98259b47738f63b6073625f0b75bd1dc` |
| `worker_mode_contract` | `unique` | `evidence:sha256:2d7d1ecfb242d54ccf66abf727985ae2ee84ea3db028d95a261479e8ef3a3de2` |
| `worker_authorization_contract` | `unique` | `evidence:sha256:b1641bdedc290731af75efff179ba3aeb50f4d00e2f261021c5f69137c6157aa` |
| `worker_argument_parse` | `unique` | `evidence:sha256:abb2483c873946cca5de88a0e9f8405f69d5be723c190eb7ced3f0b5d4771fc2` |
| `worker_canary_exit_guard` | `unique` | `evidence:sha256:0f0af96ec0e1e8cefac855e55e6618e30355053e0d0f85e66c1f09cf342b010d` |
| `worker_id_guard` | `unique` | `evidence:sha256:ebb4142d4597073e24236794886dd8365f271e21672af5f25af8152f283ad8a5` |
| `worker_batch_checkpoint_guard` | `unique` | `evidence:sha256:d3006bdb27376812bd512a822f55db071eef6cf77bdbe700ecb69417bff21914` |
| `worker_production_branch` | `unique` | `evidence:sha256:8174f96a8fd30d19c2a08a3d1da987f018eba6d3ea294b391c7d8986f23e715d` |

## 源码证据

### `worker_script_binding` — 生产事实

- Evidence ID：`evidence:sha256:05ea188dd9085a65c4642f0c75961b731a4a8477fd86ed4f02f1633e4ca77b13`
- 位置：`scripts/production/production_campaign_supervisor.ps1:52`
- Symbol：`$deterministicWorkerScript`
- 结构锚点：`powershell:assignment:$deterministicWorkerScript`
- 语法位置：`module/assignment:$deterministicWorkerScript#1`
- 片段 SHA-256：`32249443cf0e783808c579a69bf04b14b75eb6b796d756cc562551f2a591ea0a`
- 文件 SHA-256：`63c6c8556f684d2b12d695869be456ac0d9614d7c778a998d1731df54811ed47`

```text
$deterministicWorkerScript = Join-Path $Root "scripts\production\run_qwen_deterministic_worker.py"

```

### `process_boundary_definition` — 生产事实

- Evidence ID：`evidence:sha256:208e42baa3b95b1ef9f4400e521f3ec5ecc558d4091371667614b793e425b711`
- 位置：`scripts/production/production_campaign_supervisor.ps1:224`
- Symbol：`Start-DeterministicWorkerProcess`
- 结构锚点：`powershell:function:Start-DeterministicWorkerProcess`
- 语法位置：`function:Start-DeterministicWorkerProcess#1`
- 片段 SHA-256：`113a6afaad9335832b31e72e6a446e45ec25f4b21dbd76431d8f70421e351dfa`
- 文件 SHA-256：`63c6c8556f684d2b12d695869be456ac0d9614d7c778a998d1731df54811ed47`

```text
function Start-DeterministicWorkerProcess([string]$FilePath, [string[]]$ArgumentVector, [string]$WorkingDirectory, [string]$StdoutPath, [string]$StderrPath, [ref]$LaunchDiagnostics) {
    $LaunchDiagnostics.Value.launch_stage = 'PROCESS_ARGUMENT_BUILD'
    $LaunchDiagnostics.Value.executable = $FilePath
    $LaunchDiagnostics.Value.working_directory = $WorkingDirectory
    $quotedArguments = @($ArgumentVector | ForEach-Object { ConvertTo-ChildCommandLineArgument ([string]$_) })
    # Windows PowerShell can expose ProcessStartInfo.EnvironmentVariables as
    # null for a valid instance.  Do not use that collection: the child gets
    # an inherited environment captured at Process.Start instead.
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new($FilePath, [string]::Join(' ', [string[]]$quotedArguments))
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $childEnvironment = Get-CanonicalChildEnvironment
    $environmentKeys = @($childEnvironment.Keys | ForEach-Object { [string]$_ })
    $duplicateGroups = @($environmentKeys | Group-Object { $_.ToUpperInvariant() } | Where-Object { $_.Count -gt 1 })
    $LaunchDiagnostics.Value.env_key_count = $environmentKeys.Count
    $LaunchDiagnostics.Value.case_insensitive_duplicate_count = $duplicateGroups.Count
    $LaunchDiagnostics.Value.launch_stage = 'ENVIRONMENT_BUILD'
    $environmentScope = [System.EnvironmentVariableTarget]::Process
    $normalizedKeys = @('Path', 'BOOKTRANS_PRODUCTION_WORKER_ID', 'BOOKTRANS_PRODUCTION_WORKER_HANDOFF_PATH', 'BOOKTRANS_PRODUCTION_BATCH', 'BOOKTRANS_PRODUCTION_CHECKPOINT')
    $parentEnvironment = [Environment]::GetEnvironmentVariables($environmentScope)
    $affectedKeys = @($parentEnvironment.Keys | ForEach-Object { [string]$_ } | Where-Object {
        $candidate = $_
        $null -ne ($normalizedKeys | Where-Object { $_ -ieq $candidate } | Select-Object -First 1)
    })
    foreach ($name in $normalizedKeys) {
        if ($null -eq ($affectedKeys | Where-Object { $_ -ieq $name } | Select-Object -First 1)) { $affectedKeys += $name }
    }
    $parentSnapshot = @()
    foreach ($key in $affectedKeys) {
        $parentSnapshot += [ordered]@{ key = $key; value = [Environment]::GetEnvironmentVariable($key, $environmentScope) }
    }
    try {
        # Only the normalized launch keys are changed.  Every other variable
        # is inherited untouched, and the process environment is restored
        # before this worker launch returns.
        foreach ($key in $affectedKeys) { [Environment]::SetEnvironmentVariable($key, $null, $environmentScope) }
        foreach ($key in $normalizedKeys) {
            if ($childEnvironment.ContainsKey($key)) {
                [Environment]::SetEnvironmentVariable($key, [string]$childEnvironment[$key], $environmentScope)
            }
        }
        $child = [System.Diagnostics.Process]::new()
        $child.StartInfo = $startInfo
        $LaunchDiagnostics.Value.launch_stage = 'START_PROCESS_ENTER'
        if (-not $child.Start()) { throw [System.InvalidOperationException]::new('WORKER_LAUNCH_FAILED:PROCESS_START_RETURNED_FALSE') }
        $LaunchDiagnostics.Value.child_created = $true
        $LaunchDiagnostics.Value.launch_stage = 'START_PROCESS_RETURN'
        $stdoutTask = $child.StandardOutput.ReadToEndAsync()
        $stderrTask = $child.StandardError.ReadToEndAsync()
        $LaunchDiagnostics.Value.launch_stage = 'CHILD_EXIT_WAIT'
        $child.WaitForExit()
        [System.IO.File]::WriteAllText($StdoutPath, $stdoutTask.GetAwaiter().GetResult(), $utf8NoBom)
        [System.IO.File]::WriteAllText($StderrPath, $stderrTask.GetAwaiter().GetResult(), $utf8NoBom)
        return $child
    } finally {
        $currentEnvironment = [Environment]::GetEnvironmentVariables($environmentScope)
        $restoreKeys = @($currentEnvironment.Keys | ForEach-Object { [string]$_ } | Where-Object {
            $candidate = $_
            $null -ne ($normalizedKeys | Where-Object { $_ -ieq $candidate } | Select-Object -First 1)
        })
        foreach ($key in $restoreKeys) { [Environment]::SetEnvironmentVariable($key, $null, $environmentScope) }
        foreach ($entry in $parentSnapshot) {
            if ($null -ne $entry.value) { [Environment]::SetEnvironmentVariable([string]$entry.key, [string]$entry.value, $environmentScope) }
        }
    }
}


```

### `production_argument_vector` — 生产事实

- Evidence ID：`evidence:sha256:5f59c334ec000f69e01750b17e111d71662c2e183a54ce25bdd62a6cc0f362a3`
- 位置：`scripts/production/production_campaign_supervisor.ps1:1279`
- Symbol：`$workerArgumentVector`
- 结构锚点：`powershell:assignment:$workerArgumentVector`
- 语法位置：`production-loop/assignment:$workerArgumentVector#1`
- 片段 SHA-256：`585b46b61f3bea89e2092abc66ee8a41de89f78840c18e33fc1314c05d0c1099`
- 文件 SHA-256：`63c6c8556f684d2b12d695869be456ac0d9614d7c778a998d1731df54811ed47`

```text
                $workerArgumentVector = @(
                    $deterministicWorkerScript, '--root', $Root, '--run-path', $run,
                    '--worker-id', $workerId, '--handoff-path', $workerHandoffPath,
                    '--batch-label', $before.active_batch, '--checkpoint', $before.next_checkpoint,
                    '--mode', 'production', '--authorize-production-checkpoint-write'
                ) + $workerArgs

```

### `process_boundary_invocation` — 生产事实

- Evidence ID：`evidence:sha256:2b0a961f9feeeaff6d9325bc1ae50ae299723ecfd0fc58a8952d35a469aa250e`
- 位置：`scripts/production/production_campaign_supervisor.ps1:1305`
- Symbol：`Start-DeterministicWorkerProcess`
- 结构锚点：`powershell:call:Start-DeterministicWorkerProcess`
- 语法位置：`production-loop/call:Start-DeterministicWorkerProcess#1`
- 片段 SHA-256：`e51bb0c99bb0a6cfb713477c495fec8f968ad7803a22df53c7d9c2911d042052`
- 文件 SHA-256：`63c6c8556f684d2b12d695869be456ac0d9614d7c778a998d1731df54811ed47`

```text
                    $child = Start-DeterministicWorkerProcess $python $workerArgumentVector $Root $workerStdoutPath $workerStderrPath ([ref]$launchDiagnostics)

```

### `worker_wrapper_import` — 生产事实

- Evidence ID：`evidence:sha256:c09b17cb3de85d77888286adef6daee1cfd6ca9ab4b4e1efcf6639addcfa1d6a`
- 位置：`scripts/production/run_qwen_deterministic_worker.py:11`
- Symbol：`module`
- 结构锚点：`python:import:booktrans.production_worker.worker.main`
- 语法位置：`module/import:worker.main#1`
- 片段 SHA-256：`91dae863a97e6b138ad691f2673994d16eddf25bc3c4a946c93d035080da5529`
- 文件 SHA-256：`8c2b76fcb1e1ac8499a431626c28b7751e6e2f8c1d3180634828629e3e63c0c9`

```text
from booktrans.production_worker.worker import main
```

### `worker_wrapper_main_call` — 生产事实

- Evidence ID：`evidence:sha256:179b1775ba7d00c1b9b4d371c4ec714f98259b47738f63b6073625f0b75bd1dc`
- 位置：`scripts/production/run_qwen_deterministic_worker.py:14`
- Symbol：`module`
- 结构锚点：`python:main-guard:call-main`
- 语法位置：`module/if:__name__==__main__/call:main#1`
- 片段 SHA-256：`6033fff73548e19f3c2a68f692078c2c58ea34357472d17e1f6d0f84c459f5f0`
- 文件 SHA-256：`8c2b76fcb1e1ac8499a431626c28b7751e6e2f8c1d3180634828629e3e63c0c9`

```text
if __name__ == "__main__":
    raise SystemExit(main())
```

### `worker_mode_contract` — 生产事实

- Evidence ID：`evidence:sha256:2d7d1ecfb242d54ccf66abf727985ae2ee84ea3db028d95a261479e8ef3a3de2`
- 位置：`src/booktrans/production_worker/worker.py:741`
- Symbol：`build_parser`
- 结构锚点：`python:function:build_parser/add_argument:--mode`
- 语法位置：`function:build_parser/call:add_argument:--mode#1`
- 片段 SHA-256：`316ef8a56ba98b43f158b023505a993cf3cbc17ef8830d2c2612351c435b27a3`
- 文件 SHA-256：`7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`

```text
parser.add_argument("--mode", choices=("canary", "production"), default="canary")
```

### `worker_authorization_contract` — 生产事实

- Evidence ID：`evidence:sha256:b1641bdedc290731af75efff179ba3aeb50f4d00e2f261021c5f69137c6157aa`
- 位置：`src/booktrans/production_worker/worker.py:744`
- Symbol：`build_parser`
- 结构锚点：`python:function:build_parser/add_argument:--authorize-production-checkpoint-write`
- 语法位置：`function:build_parser/call:add_argument:--authorize-production-checkpoint-write#1`
- 片段 SHA-256：`d56c5bfd8e94d4a2f3d91336785b45ba4bdf57717d90590661e117e4ea797c79`
- 文件 SHA-256：`7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`

```text
parser.add_argument("--authorize-production-checkpoint-write", action="store_true")
```

### `worker_argument_parse` — 生产事实

- Evidence ID：`evidence:sha256:abb2483c873946cca5de88a0e9f8405f69d5be723c190eb7ced3f0b5d4771fc2`
- 位置：`src/booktrans/production_worker/worker.py:799`
- Symbol：`main`
- 结构锚点：`python:function:main/assignment:args=parse_args`
- 语法位置：`function:main/assignment:args=parse_args#1`
- 片段 SHA-256：`f55240734847b7cf280028f27f7d849aeba44e666b48f5fdec92e459718d96ff`
- 文件 SHA-256：`7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`

```text
args = build_parser().parse_args(argv)
```

### `worker_canary_exit_guard` — 生产事实

- Evidence ID：`evidence:sha256:0f0af96ec0e1e8cefac855e55e6618e30355053e0d0f85e66c1f09cf342b010d`
- 位置：`src/booktrans/production_worker/worker.py:801`
- Symbol：`main`
- 结构锚点：`python:function:main/if:args.mode==canary/exit`
- 语法位置：`function:main/if:args.mode==canary/exit#1`
- 片段 SHA-256：`ea06b467ef053a3c945e92a83ad19f2e7c11f5248b56f89db528747009be5c68`
- 文件 SHA-256：`7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`

```text
if args.mode == "canary":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = args.isolated_output or (args.root / "runs" / f"qwen-production-canary-{stamp}")
        report = run_canary_command(
            root=args.root,
            run=args.run_path,
            isolated_output=output,
            continue_from_raw=args.continue_from_raw,
            resume=args.resume,
            recalculate_from_evidence=args.recalculate_from_evidence,
            semantic_review=args.semantic_review,
            semantic_review_ids=[item.strip() for item in str(args.semantic_review_ids or "").split(",") if item.strip()],
            corrected_second_review=args.corrected_second_review,
            group_repair=args.group_repair,
            human_adjudicate=args.human_adjudicate,
            human_adjudication_path=args.human_adjudication_path,
            watchdog_seconds=args.watchdog_seconds,
        )
        print(json.dumps({"output": str(output), "gate": report.get("gate")}, ensure_ascii=False), flush=True)
        return 0 if report.get("gate", {}).get("passed") else 2
```

### `worker_id_guard` — 生产事实

- Evidence ID：`evidence:sha256:ebb4142d4597073e24236794886dd8365f271e21672af5f25af8152f283ad8a5`
- 位置：`src/booktrans/production_worker/worker.py:821`
- Symbol：`main`
- 结构锚点：`python:function:main/guard:worker-id-required`
- 语法位置：`function:main/guard:worker-id-required#1`
- 片段 SHA-256：`473b6da3b36a31fbc5ac10355fd56d9f668add3bc5d7978eb77ec33a91e7d9dd`
- 文件 SHA-256：`7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`

```text
if not args.worker_id:
        raise SystemExit("WORKER_ID_REQUIRED")
```

### `worker_batch_checkpoint_guard` — 生产事实

- Evidence ID：`evidence:sha256:d3006bdb27376812bd512a822f55db071eef6cf77bdbe700ecb69417bff21914`
- 位置：`src/booktrans/production_worker/worker.py:823`
- Symbol：`main`
- 结构锚点：`python:function:main/guard:batch-checkpoint-pair`
- 语法位置：`function:main/guard:batch-checkpoint-pair#1`
- 片段 SHA-256：`08ca045007f09d74331a890f263c9f5953b84ef509391c7361c8450a5a5bedd7`
- 文件 SHA-256：`7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`

```text
if bool(args.batch_label) != bool(args.checkpoint):
        raise SystemExit("WORKER_LAUNCH_BINDING_INCOMPLETE")
```

### `worker_production_branch` — 生产事实

- Evidence ID：`evidence:sha256:8174f96a8fd30d19c2a08a3d1da987f018eba6d3ea294b391c7d8986f23e715d`
- 位置：`src/booktrans/production_worker/worker.py:825`
- Symbol：`main`
- 结构锚点：`python:function:main/call:run_production_checkpoint`
- 语法位置：`function:main/non-canary/call:run_production_checkpoint#1`
- 片段 SHA-256：`32cc6599a3b8a8984432c887289ad0efa7ab7a9516d9671979b610e4bc4d9e87`
- 文件 SHA-256：`7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`

```text
run_production_checkpoint(
        root=args.root,
        run=args.run_path,
        worker_id=args.worker_id,
        handoff_path=handoff,
        authorize=args.authorize_production_checkpoint_write,
        semantic_rescue=args.semantic_rescue,
        human_adjudication_path=args.human_adjudication_path,
        d051_approval_path=args.d051_approval_path,
        d051_reuse_approval_path=args.d051_reuse_approval_path,
        d051_reuse_display_id=args.d051_reuse_display_id,
        review_policy=args.review_policy,
        batch_label=args.batch_label,
        checkpoint=args.checkpoint,
    )
```

### `auxiliary_launch_boundary_test` — 辅助测试

- Evidence ID：`evidence:sha256:d4307dd3cd7fafa73753a47904cc34a8d97f86001e4f65dfe1ee959898ca40b5`
- 位置：`tests/production/test_supervisor_launch_boundary.ps1:55`
- Symbol：`Assert-True`
- 结构锚点：`powershell:test-assertion:inherited-child-created`
- 语法位置：`script/assert:inherited-child-created#1`
- 片段 SHA-256：`ee0d5df8310489d65a5e179c3e9f0ffd46037e31ff54226e645da93693a87f5b`
- 文件 SHA-256：`aaab3044c6b0d4f2122823812db3575542ad1a5024844d7ccd58c9c508b1ced5`

```text
    Assert-True ($inheritChild.ExitCode -eq 0 -and $inheritDiagnostics.child_created) 'inherited-environment launch creates child'

```

### `auxiliary_wrapper_bootstrap_test` — 辅助测试

- Evidence ID：`evidence:sha256:6923a936fd716d2a81f67ca76e8161e48778f6a88d2eee67e950ccafa2d9930a`
- 位置：`tests/unit/test_production_entrypoint_bootstrap.py:10`
- Symbol：`test_supervisor_worker_entrypoint_imports_providers_and_reaches_preflight`
- 结构锚点：`python:test-function:test_supervisor_worker_entrypoint_imports_providers_and_reaches_preflight`
- 语法位置：`module/function:test_supervisor_worker_entrypoint_imports_providers_and_reaches_preflight#1`
- 片段 SHA-256：`29ed528a3c638011aedb657642d7f95a5364c29cb95bf32c3207d4456f7d52a9`
- 文件 SHA-256：`a157a6e55fac1c49b395fc16beac40cbb1f9c2547ce7375776f3b2600d8a44e5`

```text
def test_supervisor_worker_entrypoint_imports_providers_and_reaches_preflight() -> None:
    path = Path(r"D:\translate\scripts\production\run_qwen_deterministic_worker.py")
    spec = importlib.util.spec_from_file_location("run_qwen_deterministic_worker_bootstrap", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "main")
    assert QwenMTProvider.__name__ == "QwenMTProvider"
    args = build_parser().parse_args(
        [
            "--mode",
            "production",
            "--worker-id",
            "bootstrap-preflight",
            "--run-path",
            str(DEFAULT_RUN),
            "--batch-label",
            "FULL_BOOK_BATCH_025",
            "--checkpoint",
            "checkpoint_07",
        ]
    )
    assert args.mode == "production"
    assert args.authorize_production_checkpoint_write is False
    assert args.batch_label == "FULL_BOOK_BATCH_025"
    assert args.checkpoint == "checkpoint_07"
    payload = frozen_b022_checkpoint03_inputs(DEFAULT_RUN)
    assert payload["source_sha256"] == EXPECTED_SOURCE_SHA
    assert payload["order"][0] == "body.p.06939"
    assert len(payload["order"]) == 25
```

### `auxiliary_worker_binding_test` — 辅助测试

- Evidence ID：`evidence:sha256:54c6950772cddd265eecc3c89b43d87cb8070081d43ecd7d46f66cebb29ab7ff`
- 位置：`tests/unit/test_qwen_deterministic_worker.py:1764`
- Symbol：`test_production_worker_package_does_not_call_codex`
- 结构锚点：`python:test-function:test_production_worker_package_does_not_call_codex`
- 语法位置：`module/function:test_production_worker_package_does_not_call_codex#1`
- 片段 SHA-256：`8e0350b6f81aade1846943561e19d72a7add8f13c8e83d1c7c1360316718da54`
- 文件 SHA-256：`6a915fc7f50fba02939b04e486e543d46ef9b3d526558c9de75aaac5ccc5128d`

```text
def test_production_worker_package_does_not_call_codex() -> None:
    root = Path(r"D:\translate\src\booktrans\production_worker")
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "codex exec" not in text
        assert "CodexExecutable" not in text
    text = Path(r"D:\translate\scripts\production\production_campaign_supervisor.ps1").read_text(encoding="utf-8")
    assert "run_qwen_deterministic_worker.py" in text
    assert "run_allowlisted_recovery.py" in text
    assert "workerKind=deterministic" in text
    assert 'if ($CodexExecutable -eq "codex")' in text
    assert "QWEN_NARROW_REPAIR" in text
    assert "DETERMINISTIC_EVIDENCE" in text
    assert "--forbid-production-write" in text
    assert "serialized_repair_contract_json" in text
    recovery_block = text.split("AUTO_RECOVERY_START fingerprint=")[1].split("AUTO_RECOVERY_EXHAUSTED")[0]
    assert "CodexExecutable exec" not in recovery_block
    assert "$allowlistedRecoveryScript" in recovery_block
```

## 人工审核门禁

以下模板只能由授权审核者在 GitHub Issue #3 中提交。实施 Agent 不得填写批准结论。

```text
ARCHITECTURE_REVIEW_DECISION
decision: APPROVED | REJECTED
reviewed_source_commit: e494c1a25347d72ae9c279dc8ddc875ec87af144
reviewed_worktree_state: clean
candidate_report_sha256: 0f0f016de85759c20193b05b342da643fc59738ce376de0443ac4aa4f4debf5c
scanner_version: architecture-explorer-t2/1.1
approved_evidence_ids:
  - <authorized reviewer selects exact IDs>
source_module: production-control
target_module: translation-execution
direction: production-control -> translation-execution
relationship_type: process-launch
approved_reachability: <condition or UNKNOWN>
runtime_state: NOT_COLLECTED
retained_unknowns: <explicit list>
review_notes: <notes>
```
