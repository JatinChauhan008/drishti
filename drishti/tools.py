import functools
from typing import Optional

from drishti import config
from drishti.api_client import CivicDataClient
from drishti.logbus import log_error, log_event
from drishti.mcp_compat import create_mcp


mcp = create_mcp("civicdataspace")
client = CivicDataClient(config.BASE_URL)


# --- Tracing / error handling -------------------------------------------


def _trim(value, limit: int = 300):
    s = repr(value)
    return s if len(s) <= limit else s[:limit] + "...<truncated>"


def traced_tool(fn):
    """Wrap an MCP tool: logs the call, its result (or error), and timing --
    and guarantees the tool never raises. Any unexpected exception is caught,
    logged with a full traceback, and turned into a structured error dict so
    the LLM (and the person, in the trace console) sees a clear message
    instead of the whole chat request blowing up.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        log_event("tool_call", tool=fn.__name__, args={k: _trim(v) for k, v in kwargs.items()})
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            log_error("tool_error", exc, tool=fn.__name__, args={k: _trim(v) for k, v in kwargs.items()})
            return {"error": True, "status": "internal_error", "detail": f"{fn.__name__} failed: {exc}"}

        is_err = isinstance(result, dict) and result.get("error")
        log_event(
            "tool_result",
            level="warning" if is_err else "info",
            tool=fn.__name__,
            result=_trim(result, 800),
        )
        return result

    return wrapper


# --- Helpers --------------------------------------------------------------


def _rows_to_dicts(payload: dict) -> list:
    cols = payload.get("columns", [])
    return [dict(zip(cols, row)) for row in payload.get("rows", [])]


def _source_of(payload: dict, dataset_id: str, resource_id: Optional[str] = None) -> dict:
    """Build the source record a judge LLM can use to verify an answer."""
    src = payload.get("_source") or {}
    return {
        "dataset_id": src.get("dataset_id", dataset_id),
        "resource_id": src.get("resource_id", resource_id),
        "api_url": src.get("api_url"),
        "retrieved_at": src.get("retrieved_at"),
    }


def _publisher_of(result: dict) -> dict:
    organization = result.get("organization") or {}
    user = result.get("user") or {}
    return {
        "organization": {
            "name": organization.get("name"),
            "logo": organization.get("logo"),
        } if organization else None,
        "user": {
            "name": user.get("name"),
            "bio": user.get("bio"),
            "profile_picture": user.get("profile_picture"),
        } if user else None,
    }


def _common_search_result(result: dict) -> dict:
    return {
        "id": result.get("id"),
        "type": result.get("type"),
        "title": result.get("title"),
        "description": result.get("description"),
        "slug": result.get("slug"),
        "status": result.get("status"),
        "tags": result.get("tags"),
        "sectors": result.get("sectors"),
        "geographies": result.get("geographies"),
        "created": result.get("created"),
        "modified": result.get("modified"),
        "publisher": _publisher_of(result),
    }


def _dataset_result(result: dict) -> dict:
    normalized = _common_search_result(result)
    normalized["dataset_id"] = result.get("id")
    normalized["formats"] = result.get("formats")
    normalized["has_charts"] = result.get("has_charts")
    normalized["download_count"] = result.get("download_count")
    normalized["is_individual_dataset"] = result.get("is_individual_dataset")
    return normalized


def _usecase_result(result: dict) -> dict:
    normalized = _common_search_result(result)
    normalized["usecase_id"] = result.get("id")
    normalized["running_status"] = result.get("running_status")
    normalized["logo"] = result.get("logo")
    normalized["is_individual_usecase"] = result.get("is_individual_usecase")
    return normalized


def _catalog_result(result: dict) -> dict:
    if result.get("type") == "dataset":
        return _dataset_result(result)
    if result.get("type") == "usecase":
        return _usecase_result(result)

    normalized = _common_search_result(result)
    normalized["model_id"] = result.get("id")
    normalized["name"] = result.get("name")
    normalized["display_name"] = result.get("display_name")
    normalized["model_type"] = result.get("model_type")
    normalized["provider"] = result.get("provider")
    normalized["is_individual_model"] = result.get("is_individual_model")
    return normalized


# --- Tools ------------------------------------------------------------


@mcp.tool()
@traced_tool
def search_datasets(query: str, size: int = 10) -> dict:
    """Search CivicDataSpace datasets by keyword.

    Use this first when the user asks a data question but you do not yet know
    which dataset_id contains the answer. Returns matching dataset ids and
    metadata such as title, description, sectors, geographies, formats, and
    publisher information.
    """
    data = client.search_datasets(query, size=size)
    if data.get("error"):
        return data

    results = [_dataset_result(r) for r in data.get("results", [])]
    return {
        "total": data.get("total"),
        "results": results,
        "source": {"api": "unified_search", "query": query},
    }


@mcp.tool()
@traced_tool
def search_usecases(query: str, size: int = 10) -> dict:
    """Search CivicDataSpace use cases by keyword.

    Use this when the user asks about examples, applications, projects, or use
    cases built around datasets. Returns use case metadata and publisher
    information from the unified search API.
    """
    data = client.search_usecases(query, size=size)
    if data.get("error"):
        return data

    return {
        "total": data.get("total"),
        "results": [_usecase_result(r) for r in data.get("results", [])],
        "source": {"api": "unified_search", "query": query, "types": "usecase"},
    }


@mcp.tool()
@traced_tool
def search_catalog(query: str, size: int = 10) -> dict:
    """Search datasets, use cases, and AI models together.

    Use this when the user asks broadly across the CivicDataSpace catalogue or
    asks for publisher/owner context across entity types. Results include each
    entity's type plus available publisher information.
    """
    data = client.search_catalog(query, size=size)
    if data.get("error"):
        return data

    return {
        "total": data.get("total"),
        "types_searched": data.get("types_searched"),
        "aggregations": data.get("aggregations"),
        "results": [_catalog_result(r) for r in data.get("results", [])],
        "source": {"api": "unified_search", "query": query, "types": "dataset,usecase,aimodel"},
    }


@mcp.tool()
@traced_tool
def list_available_datasets(max_results: int = 200) -> dict:
    """List dataset titles in the CivicDataSpace catalogue.

    Use this when the user asks which datasets are available or asks for all
    dataset names. This returns catalogue metadata only, not dataset records.
    """
    page_size = 100
    page = 1
    results = []
    total = None

    while len(results) < max_results:
        data = client.list_datasets(size=page_size, page=page)
        if data.get("error"):
            if not results:
                return data
            break  # return what we already gathered rather than losing it

        total = data.get("total", total)
        batch = data.get("results", [])
        if not batch:
            break

        for r in batch:
            results.append(
                {
                    **_dataset_result(r),
                }
            )
            if len(results) >= max_results:
                break

        if total is not None and len(results) >= total:
            break
        page += 1

    return {"total": total, "returned": len(results), "results": results}


@mcp.tool()
@traced_tool
def get_dataset_schema(dataset_id: str, resource_id: Optional[str] = None) -> dict:
    """Inspect the available columns for a dataset.

    Use this after finding a likely dataset and before querying it, so you know
    the exact column names available for filters, selected columns, and sorting.
    If the dataset has multiple resources, pass resource_id.
    """
    data = client.get_dataset_data(dataset_id, resource_id=resource_id, limit=1, count=False)
    if data.get("error"):
        return data
    return {
        "dataset_id": data.get("dataset_id"),
        "resource_id": data.get("resource_id"),
        "available_columns": data.get("available_columns", []),
        "max_limit": data.get("max_limit"),
        "source": _source_of(data, dataset_id, resource_id),
    }


@mcp.tool()
@traced_tool
def preview_dataset(dataset_id: str, resource_id: Optional[str] = None, limit: int = 5) -> dict:
    """Return a few sample rows from a dataset.

    Use this when column names alone are not enough to understand the data
    values or formats. Keep limit small unless the user explicitly asks for a
    larger preview.
    """
    data = client.get_dataset_data(dataset_id, resource_id=resource_id, limit=limit, count=False)
    if data.get("error"):
        return data
    return {
        "columns": data.get("columns", []),
        "rows": _rows_to_dicts(data),
        "source": _source_of(data, dataset_id, resource_id),
    }


@mcp.tool()
@traced_tool
def count_dataset_records(
    dataset_id: str,
    filters: Optional[dict] = None,
    resource_id: Optional[str] = None,
) -> dict:
    """Count records in a dataset, optionally with filters.

    Use this for "how many" questions. Filters must use exact CivicDataSpace API
    filter keys and values based on the dataset schema/data.
    """
    data = client.get_dataset_data(
        dataset_id, resource_id=resource_id, filters=filters, limit=0, count=True
    )
    if data.get("error"):
        return data
    source = _source_of(data, dataset_id, resource_id)
    source["filters"] = filters or {}
    return {"total": data.get("total"), "source": source}


@mcp.tool()
@traced_tool
def query_dataset(
    dataset_id: str,
    filters: Optional[dict] = None,
    columns: Optional[list] = None,
    order_by: Optional[list] = None,
    limit: int = 100,
    offset: int = 0,
    resource_id: Optional[str] = None,
    include_total: bool = True,
) -> dict:
    """Query rows from a dataset.

    Use this to answer questions that need actual dataset records. You can pass
    filters, select columns, order rows, set limit/offset, and include the total
    count. Inspect schema first unless the column names are already known.
    """
    data = client.get_dataset_data(
        dataset_id,
        resource_id=resource_id,
        filters=filters,
        columns=columns,
        order_by=order_by,
        limit=limit,
        offset=offset,
        count=include_total,
    )
    if data.get("error"):
        return data

    source = _source_of(data, dataset_id, resource_id)
    source["filters"] = filters or {}
    source["order_by"] = order_by or []
    return {
        "columns": data.get("columns", []),
        "rows": _rows_to_dicts(data),
        "total": data.get("total"),
        "limit": data.get("limit"),
        "offset": data.get("offset"),
        "source": source,
    }
