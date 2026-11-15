# Creado por Aldo Garcia.
"""Rechaza builds modificados o incompletos sin requerir Git en el host instalado."""

from pathlib import Path

import pytest

from scripts.verify_frontend_dist import check_dist, compare_dist

pytestmark = pytest.mark.unit
EXPECTED = {"frontend/dist/index.html": b"committed build\n"}


@pytest.fixture
def release(tmp_path):
    dist = tmp_path / "frontend/dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_bytes(EXPECTED["frontend/dist/index.html"])
    (tmp_path / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
    return tmp_path


def test_identical_build_passes_without_changing_files(release):
    assert compare_dist(release, EXPECTED) == []
    assert (release / "frontend/dist/index.html").read_bytes() == EXPECTED["frontend/dist/index.html"]


def test_changed_build_bytes_are_rejected(release):
    (release / "frontend/dist/index.html").write_bytes(b"different build\n")
    assert compare_dist(release, EXPECTED) == [
        "frontend/dist/index.html: bytes diferentes al commit; reconstruya y revise dist.",
    ]


@pytest.mark.parametrize("name", ["extra.js", "extra.ignored"])
def test_untracked_and_ignored_build_assets_are_rejected(release, name):
    (release / "frontend/dist" / name).write_bytes(b"extra build output")
    assert compare_dist(release, EXPECTED) == [f"frontend/dist/{name}: archivo extra en el build."]


def test_missing_build_file_is_rejected(release):
    (release / "frontend/dist/index.html").unlink()
    assert compare_dist(release, EXPECTED) == ["frontend/dist/index.html: archivo no generado."]


def test_symlink_in_build_cannot_pass_as_matching_bytes(release, monkeypatch):
    # Simula el enlace sin exigir privilegios de creacion de symlinks en Windows.
    index = release / "frontend/dist/index.html"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == index or original(path))
    errors = compare_dist(release, EXPECTED)
    assert any("enlaces simbolicos" in error for error in errors)


def test_missing_dist_in_commit_cannot_be_certified(release):
    assert compare_dist(release, {}) == ["El commit no contiene frontend/dist; no se puede certificar el build."]


def test_reference_comes_from_head_not_the_staging_index(release, monkeypatch):
    calls = []

    def fake_git(root, *args):
        assert root == release
        calls.append(args)
        if args == ("ls-tree", "-r", "-z", "HEAD", "--", "frontend/dist"):
            return b"100644 blob synthetic-oid\tfrontend/dist/index.html\0"
        assert args == ("cat-file", "blob", "synthetic-oid")
        return EXPECTED["frontend/dist/index.html"]

    monkeypatch.setattr("scripts.verify_frontend_dist.git", fake_git)
    assert check_dist(release) == []
    assert len(calls) == 2
