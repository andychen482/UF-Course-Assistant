"""
Analytics metrics controller.

Records daily counters for major selections, course views, and search terms
in the ``ufscheduler-stats`` DynamoDB table.  Logic mirrors server.js but uses
boto3 for the atomic counter updates.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status

from api.models.metrics import MetricsResponse
from utils.dynamo import metrics_table

logger = logging.getLogger("uvicorn.error")


def _increment_counter(type_key: str, name_key: str) -> None:
    """Atomically increment a daily counter in the metrics table."""
    time_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%fZ")

    try:
        metrics_table.update_item(
            Key={"type": type_key, "name": name_key},
            UpdateExpression=(
                "SET #cnt = if_not_exists(#cnt, :start) + :increment, "
                "lastUpdated = :lastUpdated"
            ),
            ExpressionAttributeNames={"#cnt": "count"},
            ExpressionAttributeValues={
                ":increment": 1,
                ":start": 0,
                ":lastUpdated": time_str,
            },
        )
    except Exception:
        logger.exception("Error updating metrics for %s / %s", type_key, name_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal Server Error",
        )


def track_major(major: str) -> MetricsResponse:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _increment_counter(f"Major-{today}", major)
    return MetricsResponse(message="Major metrics updated successfully.")


def track_course(code: str, name: str) -> MetricsResponse:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _increment_counter(f"Course-{today}", f"{code} | {name}")
    return MetricsResponse(message="Course metrics updated successfully.")


def track_search(search_term: str) -> MetricsResponse:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _increment_counter(f"Search-{today}", search_term)
    return MetricsResponse(message="Search metrics updated successfully.")
