"""Create or verify the immutable, recoverable B036 code baseline."""
from __future__ import annotations
import hashlib, json, shutil
from pathlib import Path

ROOT=Path(r"D:\translate"); ARCHIVE=Path(r"D:\booktrans-baselines")
DIRS=("src","scripts","tests","config","docs")
FILES=("AGENTS.md","pyproject.toml","README.md","Start-Booktrans-Production.cmd","Stop-Booktrans-Production.cmd","Stop-Booktrans-Production.ps1","Watch-Booktrans-Production.cmd","Watch-Booktrans-Production.ps1")
EXCLUDED={"runs","output","_test_tmp","runs_test_tmp",".venv",".pytest_cache","__pycache__","tmp"}
def files(root: Path):
    for directory in DIRS:
        base=root/directory
        if base.is_dir():
            yield from (p for p in base.rglob("*") if p.is_file() and not any(part in EXCLUDED or part.startswith(".pytest") for part in p.relative_to(root).parts))
    for name in FILES:
        if (p:=root/name).is_file(): yield p
def digest(path: Path)->str: return hashlib.sha256(path.read_bytes()).hexdigest()
def manifest(root: Path): return [{"relative_path":p.relative_to(root).as_posix(),"size":p.stat().st_size,"sha256":digest(p)} for p in sorted(files(root),key=lambda p:p.relative_to(root).as_posix())]
def main():
    entries=manifest(ROOT); payload=json.dumps(entries,ensure_ascii=False,sort_keys=True,separators=(",",":"),).encode(); source_digest=hashlib.sha256(payload).hexdigest(); baseline_id=f"booktrans-b036-baseline-{source_digest[:16]}"; base=ARCHIVE/baseline_id; target=base/"snapshot"; manifest_path=base/"source_manifest.json"
    if not target.exists():
        for entry in entries:
            source=ROOT/entry["relative_path"]; destination=target/entry["relative_path"]; destination.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,destination)
        manifest_path.write_bytes(payload+b"\n")
    if not manifest_path.is_file() or manifest_path.read_bytes().rstrip(b"\n")!=payload: raise RuntimeError("B036_BASELINE_CONFLICT")
    restored=manifest(target)
    if entries!=restored: raise RuntimeError("B036_BASELINE_RESTORE_MISMATCH")
    print(json.dumps({"baseline_id":baseline_id,"code_baseline_digest":source_digest,"file_count":len(entries),"restore_mismatch":0,"manifest":str(manifest_path)},ensure_ascii=False))
if __name__=="__main__": main()
