"""Smoke test: confirm the app modules import under the test harness."""


def test_modules_import():
    import config
    import embedder
    import indexer
    import searcher
    import brain
    import tasks
    import moc_linker
    import ledger_update

    assert config.VAULT_PATH  # points at the temp vault from conftest
