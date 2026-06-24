"""Embedder batching, retry, and timeout hardening (M4 / M5 / M6)."""
import pytest

import embedder


class _FakeEmb:
    def __init__(self, vec):
        self.embedding = vec


class _FakeResp:
    def __init__(self, vecs):
        self.data = [_FakeEmb(v) for v in vecs]


class FakeClient:
    """Records each embeddings.create call's input batch; returns 1-dim vectors
    encoding the input string length so order is verifiable."""
    def __init__(self, fail_times=0):
        self.calls = []
        self.fail_times = fail_times
        self.embeddings = self

    def create(self, model, input):
        self.calls.append(list(input))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("transient endpoint error")
        return _FakeResp([[float(len(t))] for t in input])


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(embedder.time, "sleep", lambda *_: None, raising=True)


def test_batches_into_sub_batches(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(embedder, "get_client", lambda: fake)
    texts = [f"t{i}" for i in range(10)]

    out = embedder.embed_texts(texts, batch_size=4)

    assert len(out) == 10
    # 10 items / batch 4 → batches of 4, 4, 2
    assert [len(c) for c in fake.calls] == [4, 4, 2]


def test_preserves_order_across_batches(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(embedder, "get_client", lambda: fake)
    texts = ["a", "bb", "ccc", "dddd", "eeeee"]

    out = embedder.embed_texts(texts, batch_size=2)

    assert [v[0] for v in out] == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_retries_transient_failure_then_succeeds(monkeypatch):
    fake = FakeClient(fail_times=2)
    monkeypatch.setattr(embedder, "get_client", lambda: fake)

    out = embedder.embed_texts(["x", "y"], batch_size=8, max_retries=3)

    assert [v[0] for v in out] == [1.0, 1.0]


def test_raises_after_retries_exhausted(monkeypatch):
    fake = FakeClient(fail_times=99)
    monkeypatch.setattr(embedder, "get_client", lambda: fake)

    with pytest.raises(RuntimeError):
        embedder.embed_texts(["x"], batch_size=8, max_retries=2)


def test_empty_input_makes_no_calls(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(embedder, "get_client", lambda: fake)

    assert embedder.embed_texts([]) == []
    assert fake.calls == []


def test_client_configured_with_timeout(monkeypatch):
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(embedder, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(embedder, "_client", None)

    embedder.get_client()

    assert "timeout" in captured and captured["timeout"] is not None
    assert "max_retries" in captured
