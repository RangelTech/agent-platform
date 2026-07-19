import pytest
from app.migrations import MIGRATIONS_DIR, run_migrations


def test_migration_files_are_well_named():
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert files, "at least one migration must exist"
    for f in files:
        prefix = f.name.split("_", 1)[0]
        assert prefix.isdigit() and len(prefix) == 4, f"bad migration name: {f.name}"


@pytest.mark.integration
def test_migrations_apply_and_are_idempotent():
    run_migrations()  # first pass may apply
    assert run_migrations() == []  # second pass applies nothing
