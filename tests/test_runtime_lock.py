import pytest

from infra.check_runtime_lock import check_lock


def files(tmp_path, requirements, lock):
    source = tmp_path / "requirements-web.txt"
    pinned = tmp_path / "requirements-web.lock"
    source.write_text(requirements)
    pinned.write_text(lock)
    return source, pinned


def test_checks_included_requirements_and_hash_continuations(tmp_path):
    (tmp_path / "core.txt").write_text("numpy>=2.5.3,<3\n")
    source, pinned = files(tmp_path, "-r core.txt\nuvicorn[standard]>=0.53,<1\n",
                           "numpy==2.5.3 \\\n    --hash=sha256:abc\nuvicorn[standard]==0.53.0\n")
    check_lock(source, pinned)


@pytest.mark.parametrize("lock,match", [
    ("numpy==2.5.2\n", "does not satisfy"),
    ("fastapi==0.141.1\n", "Missing runtime dependency"),
    ("numpy>=2.5.3\n", "exact versions"),
    ("numpy==2.5.3\nnumpy==2.5.4\n", "Duplicate"),
])
def test_rejects_stale_or_invalid_lock(tmp_path, lock, match):
    with pytest.raises(ValueError, match=match):
        check_lock(*files(tmp_path, "numpy>=2.5.3,<3\n", lock))


def test_rejects_missing_extras(tmp_path):
    with pytest.raises(ValueError, match="Missing locked extras"):
        check_lock(*files(tmp_path, "uvicorn[standard]>=0.53\n", "uvicorn==0.53.0\n"))


def test_skips_inactive_environment_markers(tmp_path):
    check_lock(*files(tmp_path, 'example>=1; python_version < "2"\n', ""))


def test_rejects_recursive_includes(tmp_path):
    with pytest.raises(ValueError, match="Circular"):
        check_lock(*files(tmp_path, "-r requirements-web.txt\n", "example==1\n"))


def test_detects_tooling_changing_locked_versions(tmp_path, monkeypatch):
    monkeypatch.setattr("infra.check_runtime_lock.metadata.version", lambda _: "2.5.4")
    with pytest.raises(ValueError, match="differs from locked"):
        check_lock(*files(tmp_path, "numpy>=2.5.3,<3\n", "numpy==2.5.3\n"), installed=True)
