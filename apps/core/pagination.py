"""Pagination classes.

Two styles are offered because they solve different problems:

``CursorPageNumberPagination``
    Page numbers.  Good for folder children and admin lists where the client
    shows "page 3 of 12" and the data set is small and stable.

``TimelineCursorPagination``
    Opaque keyset cursor over ``-created_at``.  Used for feeds that grow while
    the user scrolls (recent files, search results).  Page numbers there would
    skip or repeat rows whenever a new upload lands mid-scroll.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.response import Response


class CursorPageNumberPagination(PageNumberPagination):
    """Page-number pagination wrapped in the standard response envelope."""

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200
    page_query_param = "page"

    def get_paginated_response(self, data: Any) -> Response:
        request_id = getattr(self.request, "request_id", None)
        return Response(
            {
                "success": True,
                "data": data,
                "error": None,
                "meta": {
                    "request_id": request_id,
                    "pagination": OrderedDict(
                        [
                            ("count", self.page.paginator.count),
                            ("page", self.page.number),
                            ("page_size", self.get_page_size(self.request)),
                            ("total_pages", self.page.paginator.num_pages),
                            ("has_next", self.page.has_next()),
                            ("has_previous", self.page.has_previous()),
                            ("next", self.get_next_link()),
                            ("previous", self.get_previous_link()),
                        ]
                    ),
                },
            }
        )

    def get_paginated_response_schema(self, schema: dict) -> dict:
        return {
            "type": "object",
            "properties": {
                "success": {"type": "boolean", "example": True},
                "data": schema,
                "error": {"type": "object", "nullable": True},
                "meta": {"type": "object"},
            },
        }


class TimelineCursorPagination(CursorPagination):
    """Keyset pagination for append-heavy feeds."""

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = "-created_at"
    cursor_query_param = "cursor"

    def get_paginated_response(self, data: Any) -> Response:
        request_id = getattr(self.request, "request_id", None)
        return Response(
            {
                "success": True,
                "data": data,
                "error": None,
                "meta": {
                    "request_id": request_id,
                    "pagination": {
                        "page_size": self.get_page_size(self.request),
                        "next": self.get_next_link(),
                        "previous": self.get_previous_link(),
                        "has_next": self.has_next,
                    },
                },
            }
        )

    def get_paginated_response_schema(self, schema: dict) -> dict:
        return {
            "type": "object",
            "properties": {
                "success": {"type": "boolean", "example": True},
                "data": schema,
                "error": {"type": "object", "nullable": True},
                "meta": {"type": "object"},
            },
        }


class SearchResultPagination(TimelineCursorPagination):
    """Search results order by relevance, then by recency as a tie-breaker."""

    page_size = 20
    max_page_size = 50
    ordering = ("-rank", "-created_at")
