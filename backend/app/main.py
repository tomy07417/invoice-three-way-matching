"""Composition root for the HTTP application."""

from fastapi import FastAPI

from app.interfaces.api.health import router as health_router

app = FastAPI(title="Invoice Three-Way Matching")
app.include_router(health_router)
