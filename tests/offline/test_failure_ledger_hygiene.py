from pathlib import Path

from failure_ledger import FailureLedger, _sha, _sha_file


def test_job_id_cannot_escape_ledger_root(tmp_path: Path) -> None:
    ledger = FailureLedger("../../outside", {}, root=tmp_path)

    assert ledger.job_dir.parent == tmp_path / "jobs"
    assert ledger.job_dir.name == f"job-{_sha('../../outside')}"


def test_file_hash_is_streamed_and_matches_text_hash(tmp_path: Path) -> None:
    source = tmp_path / "source.ass"
    source.write_text("legenda com acentuação", encoding="utf-8")

    assert _sha_file(source) == _sha("legenda com acentuação")


def test_complete_performs_only_one_runner_sync() -> None:
    ledger = object.__new__(FailureLedger)
    calls: list[tuple[str, bool]] = []

    def sync_runner(runner: object, summary: dict[str, object] | None = None) -> None:
        calls.append(("sync", False))

    def snapshot(
        runner: object,
        summary: dict[str, object] | None,
        *,
        stage: str,
        error: str | None = None,
        blocking: bool = True,
    ) -> str:
        sync_runner(runner, summary)
        calls.append((stage, blocking))
        return "snapshot.json"

    ledger.sync_runner = sync_runner  # type: ignore[method-assign]
    ledger.snapshot = snapshot  # type: ignore[method-assign]

    assert ledger.complete(object(), {}) == "snapshot.json"
    assert calls == [("sync", False), ("completed", False)]
