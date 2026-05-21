import json
import os
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from forge_mcp.state import RunState, StateLiteral, read_state, write_state


def test_state_literal_excludes_pw_probe():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    assert "iter_removed_probe" not in StateLiteral.__args__


def test_runstate_extra_forbidden():
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    with pytest.raises(ValidationError):
        RunState.model_validate(
            {"state": "init", "run_id": "abcd1234", "iteration": 0, "extra": "nope"}
        )


def test_write_state_atomic_mode_and_roundtrip(tmp_path):
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    path = tmp_path / "state.json"
    started = datetime.now(UTC)
    state = RunState(
        state="init",
        run_id="abcd1234",
        target_dir=str(tmp_path),
        iteration=0,
        started_at=started,
    )
    write_state(path, state)
    assert read_state(path) == state
    if os.name == "posix":
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert not list(tmp_path.glob(".state.json.*"))
    body = json.loads(path.read_text())
    assert body["state"] == "init"


def test_write_state_overwrites_atomically(tmp_path):
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    path = tmp_path / "state.json"
    started = datetime.now(UTC)
    write_state(
        path,
        RunState(
            state="init",
            run_id="abcd1234",
            target_dir=str(tmp_path),
            iteration=0,
            started_at=started,
        ),
    )
    before = path.read_bytes()
    write_state(
        path,
        RunState(
            state="planning",
            run_id="abcd1234",
            target_dir=str(tmp_path),
            iteration=0,
            started_at=started,
        ),
    )
    after = path.read_bytes()
    assert before != after
    assert read_state(path).state == "planning"


def test_runstate_requires_target_dir_and_started_at() -> None:
    """Pin a forge-mcp behavior.

    Design: §7 requires state.json to carry target root and run start time.
    Implementation: omit both required fields and assert Pydantic rejects it.
    Example: pytest raises ValidationError for incomplete RunState payloads.
    """
    with pytest.raises(ValidationError):
        RunState(state="init", run_id="abcd1234", iteration=0)  # type: ignore[call-arg]


def test_runstate_round_trips_target_dir_and_started_at(tmp_path) -> None:
    """Pin §7 RunState shape required for forensic state.json.

    Design: target_dir and started_at must survive JSON persistence.
    Implementation: serialize and validate through Pydantic JSON APIs.
    Example: parsed.started_at equals the original aware timestamp.
    """
    started = datetime.now(UTC)
    state = RunState(
        state="init",
        run_id="abcd1234",
        iteration=0,
        target_dir=str(tmp_path),
        started_at=started,
        last_updated_at=started,
    )
    parsed = RunState.model_validate_json(state.model_dump_json())
    assert parsed.target_dir == str(tmp_path)
    assert parsed.started_at == started


def test_runstate_defaults_timestamp(tmp_path):
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    state = RunState(
        state="init",
        run_id="abcd1234",
        target_dir=str(tmp_path),
        iteration=0,
        started_at=datetime.now(UTC),
    )
    assert state.last_updated_at.tzinfo is UTC
    assert state.last_updated_at <= datetime.now(UTC)


def test_write_state_fsyncs_file(tmp_path, monkeypatch) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    import os
    from datetime import UTC, datetime

    from forge_mcp import state as state_mod
    from forge_mcp.state import RunState, write_state

    calls: list[int] = []
    real_fsync = os.fsync

    def spy(fd):
        """Record fsync calls then delegate to the real fsync.

        Design: §H2 requires write_state to fsync the file before replace.
        Implementation: append the fd and call through to os.fsync.
        Example: spy(3) records and forwards.
        """
        calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(state_mod.os, "fsync", spy)
    now = datetime.now(UTC)
    write_state(
        tmp_path / "state.json",
        RunState(
            state="iter_done",
            run_id="abcd1234",
            target_dir=str(tmp_path),
            iteration=1,
            started_at=now,
            last_completed_iteration=1,
        ),
    )
    assert len(calls) >= 1


def test_last_completed_iteration_round_trips(tmp_path) -> None:
    """Pin a forge-mcp behavior.

    Design: CI catches regressions for this behavior.
    Implementation: call focused production code and assert output.
    Example: pytest runs this test in the non-slow suite.
    """
    from datetime import UTC, datetime

    from forge_mcp.state import RunState, read_state, write_state

    now = datetime.now(UTC)
    path = tmp_path / "state.json"
    write_state(
        path,
        RunState(
            state="iter_done",
            run_id="abcd1234",
            target_dir=str(tmp_path),
            iteration=7,
            started_at=now,
            last_completed_iteration=7,
        ),
    )
    assert read_state(path).last_completed_iteration == 7
