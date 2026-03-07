"""
FastAPI application entry-point.

Run locally:
    uvicorn api:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import controller
from chat import build_agent
from constants import ALLOWED_ORIGINS
from router import router

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    controller.agent = build_agent()
    logger.info("LangGraph agent initialised")
    yield


app = FastAPI(
    title="UF Course Assistant API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
