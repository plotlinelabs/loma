import observability.db as odb


class _FakeClient(dict):
    def __init__(self, uri):
        super().__init__()
        self.uri = uri

    def __getitem__(self, name):
        self.name = name
        return self


def test_get_db_connects_lazily_without_init(monkeypatch):
    created = []

    def fake_client(uri):
        created.append(uri)
        return _FakeClient(uri)

    monkeypatch.setattr(odb, "_client", None)
    monkeypatch.setattr(odb, "_db", None)
    monkeypatch.setenv("OBSERVABILITY_MONGODB_URI", "mongodb://example:27017/loma")
    monkeypatch.setattr(odb, "AsyncIOMotorClient", fake_client)
    monkeypatch.setattr(odb, "OBSERVABILITY_DB_NAME", "loma_observability")

    db = odb.get_db()

    assert db is not None
    assert created == ["mongodb://example:27017/loma"]
    assert db.name == "loma_observability"


def test_get_db_returns_none_without_uri(monkeypatch):
    monkeypatch.setattr(odb, "_client", None)
    monkeypatch.setattr(odb, "_db", None)
    monkeypatch.delenv("OBSERVABILITY_MONGODB_URI", raising=False)
    monkeypatch.setattr(odb, "_observability_uri", lambda: "")

    assert odb.get_db() is None


def test_get_db_reuses_existing_connection(monkeypatch):
    existing = object()
    monkeypatch.setattr(odb, "_db", existing)
    monkeypatch.setattr(
        odb,
        "AsyncIOMotorClient",
        lambda uri: (_ for _ in ()).throw(AssertionError("should not reconnect")),
    )

    assert odb.get_db() is existing
