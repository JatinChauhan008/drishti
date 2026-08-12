import time

import httpx

from drishti.logbus import log_error, log_event


class CivicDataClient:
    """Thin wrapper around the CivicDataSpace HTTP API.

    Every request/response is traced via logbus, and network failures never
    raise -- they come back as ``{"error": True, "status": ..., "detail": ...}``
    so callers (the MCP tools) can hand a clear message to the LLM instead of
    crashing the whole chat turn.
    """

    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout)

    def _get(self, path: str, params: dict) -> tuple[httpx.Response | None, dict | None]:
        url = f"{self.base_url}{path}"
        log_event("http_request", method="GET", url=url, params=params)
        start = time.perf_counter()
        try:
            response = self.client.get(url, params=params)
        except httpx.TimeoutException as exc:
            log_error("http_error", exc, method="GET", url=url, params=params, kind="timeout")
            return None, {"error": True, "status": "timeout", "detail": f"Request to CivicDataSpace timed out: {exc}"}
        except httpx.RequestError as exc:
            log_error("http_error", exc, method="GET", url=url, params=params, kind="connection")
            return None, {"error": True, "status": "connection_error", "detail": f"Could not reach CivicDataSpace: {exc}"}

        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        log_event(
            "http_response",
            level="info" if response.status_code < 400 else "warning",
            method="GET",
            url=str(response.request.url),
            status=response.status_code,
            elapsed_ms=elapsed_ms,
        )
        return response, None

    def _error_body(self, response: httpx.Response) -> str:
        try:
            data = response.json()
            return str(data.get("error", data))
        except ValueError:
            return response.text[:500]

    def search(self, query: str, size: int = 10, page: int = 1, types: str | None = None) -> dict:
        params = {"query": query, "size": size, "page": page}
        if types:
            params["types"] = types
        response, err = self._get("/api/search/unified/", params)
        if err:
            return err
        if response.status_code >= 400:
            detail = self._error_body(response)
            log_event("http_error_status", level="error", url=str(response.request.url), status=response.status_code, detail=detail)
            return {"error": True, "status": response.status_code, "detail": detail}
        try:
            return response.json()
        except ValueError as exc:
            log_error("http_parse_error", exc, url=str(response.request.url))
            return {"error": True, "status": "bad_response", "detail": f"CivicDataSpace returned invalid JSON: {exc}"}

    def search_datasets(self, query: str, size: int = 10, page: int = 1) -> dict:
        return self.search(query=query, size=size, page=page, types="dataset")

    def search_usecases(self, query: str, size: int = 10, page: int = 1) -> dict:
        return self.search(query=query, size=size, page=page, types="usecase")

    def search_catalog(self, query: str, size: int = 10, page: int = 1) -> dict:
        return self.search(query=query, size=size, page=page, types="dataset,usecase,aimodel")

    def list_datasets(self, size: int = 100, page: int = 1) -> dict:
        return self.search_datasets(query="", size=size, page=page)

    def get_dataset_data(
        self,
        dataset_id: str,
        resource_id: str = None,
        filters: dict = None,
        columns: list = None,
        order_by: list = None,
        limit: int = 100,
        offset: int = 0,
        count: bool = True,
    ) -> dict:
        params = {}
        if filters:
            params.update(filters)
        if columns:
            params["columns"] = ",".join(columns)
        if order_by:
            params["order_by"] = ",".join(order_by)
        params["limit"] = limit
        params["offset"] = offset
        params["count"] = str(count).lower()
        if resource_id:
            params["resource_id"] = resource_id

        path = f"/api/datasets/{dataset_id}/data/"
        response, err = self._get(path, params)
        if err:
            return err

        if response.status_code == 400:
            detail = self._error_body(response)
            log_event("http_bad_request", level="warning", url=str(response.request.url), detail=detail)
            return {"error": True, "status": 400, "detail": detail}
        if response.status_code >= 400:
            detail = self._error_body(response)
            log_event("http_error_status", level="error", url=str(response.request.url), status=response.status_code, detail=detail)
            return {"error": True, "status": response.status_code, "detail": detail}

        try:
            data = response.json()
        except ValueError as exc:
            log_error("http_parse_error", exc, url=str(response.request.url))
            return {"error": True, "status": "bad_response", "detail": f"CivicDataSpace returned invalid JSON: {exc}"}

        # Source metadata attached to every successful fetch, so the judge LLM
        # (and the person, via the frontend) can see exactly what backs the answer.
        data["_source"] = {
            "api_url": str(response.request.url),
            "dataset_id": data.get("dataset_id", dataset_id),
            "resource_id": data.get("resource_id", resource_id),
            "retrieved_at": time.time(),
        }
        return data
