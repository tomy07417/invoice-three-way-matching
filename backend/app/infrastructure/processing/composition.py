"""Opt-in production resources; no reads of configuration at module import."""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path

from sqlalchemy import create_engine

from app.application.dtos.processing import ProcessingConfig
from app.application.use_cases.process_invoice import BoundedOffload, ProcessInvoice
from app.application.use_cases.sessions import SessionService
from app.infrastructure.extraction.azure_openai import AzureOpenAIExtractor, AzureExtractionConfig
from app.infrastructure.extraction.document_validator import LocalDocumentValidator
from app.infrastructure.persistence.auth_repository import SqlAuthRepository
from app.infrastructure.persistence.connection import build_database_url
from app.infrastructure.persistence.processing_repository import SqlProcessingRepository
from app.infrastructure.processing.service import InvoiceService
from app.infrastructure.storage.local_documents import LocalDocumentStorage


def build_processing():
    """Require explicit backend settings; never derive/append the Azure URL."""
    engine = create_engine(build_database_url(), pool_size=4, max_overflow=0,
                           pool_timeout=5, connect_args={
                               'connect_timeout': 5,
                               'options': '-c statement_timeout=10000 -c lock_timeout=5000'})
    extractor = None
    try:
        config = ProcessingConfig(max_active=int(os.environ.get('MAX_ACTIVE_EXTRACTIONS', '1')),
                                  max_pending=int(os.environ.get('MAX_PENDING_INVOICES', '20')))
        azure_settings = dict(endpoint=os.environ['AZURE_OPENAI_ENDPOINT'],
                              api_key=os.environ['AZURE_OPENAI_API_KEY'],
                              deployment=os.environ['AZURE_OPENAI_DEPLOYMENT'],
                              config=AzureExtractionConfig(max_connections=config.max_active,
                                                           max_keepalive_connections=config.max_active))
        AzureOpenAIExtractor(None, **azure_settings)  # Validate without allocating a client.
        offload = BoundedOffload(4)
        sessions = SessionService(SqlAuthRepository(engine))
        storage = LocalDocumentStorage(Path(os.environ['INVOICE_STORAGE_ROOT']))
        repository = SqlProcessingRepository(engine, config=config)
        service = InvoiceService(sessions, repository, LocalDocumentValidator(), storage, offload=offload)
        worker = ProcessInvoice(repository, storage, extractor, offload=offload)
    except Exception:
        engine.dispose()
        raise ValueError('Invalid backend processing configuration') from None

    @asynccontextmanager
    async def lifespan(app):
        extractor = AzureOpenAIExtractor.create(**azure_settings)
        worker.extractor = extractor
        # This MVP intentionally uses one loop, not a thread/task per invoice.
        task = asyncio.create_task(worker.run())
        app.state.invoice_processor = worker
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await extractor.aclose()
            await offload(engine.dispose)

    return service, lifespan
