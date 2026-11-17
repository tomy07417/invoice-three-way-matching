from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event

from app.infrastructure.processing import composition

from app.main import create_app


@pytest.mark.parametrize('use_database_url', [False, True])
def test_processing_factory_accepts_component_database_configuration_without_network(monkeypatch, use_database_url):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    settings = dict(POSTGRES_HOST='db', POSTGRES_DB='test_processing', POSTGRES_USER='test',
                    POSTGRES_PASSWORD='synthetic-test-password', POSTGRES_PORT='5433',
                    AZURE_OPENAI_ENDPOINT='https://example.invalid/exact-responses',
                    AZURE_OPENAI_API_KEY='synthetic-test-key', AZURE_OPENAI_DEPLOYMENT='test-deployment',
                    INVOICE_STORAGE_ROOT='/unused-private')
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    expected = ('postgresql+psycopg', 'db', 5433, 'test_processing', 'test', 'synthetic-test-password')
    if use_database_url:
        monkeypatch.setenv('DATABASE_URL', 'postgresql://url-user:synthetic-url-password@url-db:5434/test_url')
        expected = ('postgresql+psycopg', 'url-db', 5434, 'test_url', 'url-user', 'synthetic-url-password')
    engines = []

    def no_network(*args, **kwargs):
        raise AssertionError('Factory must not connect to a database')

    def engine_factory(url, **kwargs):
        engine = create_engine(url, **kwargs)
        event.listen(engine, 'do_connect', no_network)
        engines.append(engine)
        return engine

    monkeypatch.setattr(composition, 'create_engine', engine_factory)
    monkeypatch.setattr(composition, 'LocalDocumentStorage', lambda path: SimpleNamespace())
    monkeypatch.setattr(composition.AzureOpenAIExtractor, 'create', no_network)
    try:
        service, lifespan = composition.build_processing()
        assert callable(lifespan)
        assert service.repository.engine is engines[0]
        url = engines[0].url
        assert (url.drivername, url.host, url.port, url.database, url.username, url.password) == expected
        assert engines[0].pool.size() == 4
        assert engines[0].pool.checkedout() == 0
    finally:
        for engine in engines:
            engine.dispose()


def test_default_app_keeps_health_and_no_processing_routes():
    with TestClient(create_app(processing_enabled=False)) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/invoices').status_code == 404


def test_injected_app_preserves_health_and_po_errors():
    class Service:
        sessions = None
    with TestClient(create_app(invoice_service=Service())) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/purchase-orders/does-not-exist').status_code in (404, 503)
