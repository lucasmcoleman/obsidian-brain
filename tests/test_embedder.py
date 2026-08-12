"""Embedder batching, retry, and timeout hardening (M4 / M5 / M6)."""
import pytest

import embedder


class _FakeEmb:
    def __init__(self, vec):
        self.embedding = vec


class _FakeResp:
    def __init__(self, vecs):
        # Real embeddings responses carry a per-item `index`; model it in order.
        self.data = [_FakeEmb(v) for v in vecs]
        for i, item in enumerate(self.data):
            item.index = i


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


class _ShuffledResp:
    """A response whose .data is returned OUT of input order, each item carrying
    the correct OpenAI `index` field — exactly what a client must sort by."""
    def __init__(self, vecs):
        items = [_FakeEmb(v) for v in vecs]
        for i, it in enumerate(items):
            it.index = i
        # Return them reversed to simulate an out-of-order endpoint response.
        self.data = list(reversed(items))


def test_reorders_response_data_by_index(monkeypatch):
    class ShufflingClient:
        def __init__(self):
            self.embeddings = self

        def create(self, model, input):
            # Encode input order in the vector so a mispairing is detectable.
            return _ShuffledResp([[float(i)] for i in range(len(input))])

    monkeypatch.setattr(embedder, "get_client", lambda: ShufflingClient())

    out = embedder.embed_texts(["a", "b", "c", "d"], batch_size=8)

    # Must be restored to input order via the `index` field, not left reversed.
    assert [v[0] for v in out] == [0.0, 1.0, 2.0, 3.0]


def test_embed_texts_clamps_oversized_input(monkeypatch):
    # Endpoints can REJECT over-context input with HTTP 400 (measured on LM Studio
    # + Qwen3-Embedding) — one rejected batch would abort the whole build. Inputs
    # must be clamped to EMBED_MAX_INPUT_CHARS before sending.
    captured = {}

    class Client:
        def __init__(self):
            self.embeddings = self

        def create(self, model, input):
            captured["lens"] = [len(t) for t in input]
            return _FakeResp([[0.0]] * len(input))

    monkeypatch.setattr(embedder, "get_client", lambda: Client())
    monkeypatch.setattr(embedder, "EMBED_MAX_INPUT_CHARS", 100)

    embedder.embed_texts(["short", "x" * 5000], batch_size=8)

    assert captured["lens"][0] == len("short")
    assert captured["lens"][1] == 100  # clamped, not sent oversized


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
