"""
SEMAI Analytics Intelligence Platform - Google Search Console Service.

Pure-Python module for fetching and structuring GSC data.
No Streamlit dependency.
"""

from __future__ import annotations

import pandas as pd
from googleapiclient.discovery import build


# =============================================================================
# Public API
# =============================================================================

def list_properties(creds) -> list[str]:
    """Return all verified GSC property URLs accessible with *creds*.

    Args:
        creds: Google ``Credentials`` object with webmasters scope.

    Returns:
        List of site URL strings (e.g. ``["https://example.com/"]``).
    """
    service = build("searchconsole", "v1", credentials=creds)
    sites = service.sites().list().execute().get("siteEntry", [])
    return [
        s["siteUrl"]
        for s in sites
        if s["permissionLevel"] != "siteUnverifiedUser"
    ]


def fetch_all_rows(
    creds,
    site_url: str,
    start: str,
    end: str,
) -> list[dict]:
    """Paginate through the Search Analytics API and return all rows.

    Args:
        creds: Google ``Credentials`` object.
        site_url: The GSC property URL.
        start: ISO-formatted start date (``YYYY-MM-DD``).
        end: ISO-formatted end date (``YYYY-MM-DD``).

    Returns:
        List of raw row dicts from the API.
    """
    service = build("searchconsole", "v1", credentials=creds)
    rows: list[dict] = []
    start_row = 0

    while True:
        response = (
            service.searchanalytics()
            .query(
                siteUrl=site_url,
                body={
                    "startDate": start,
                    "endDate": end,
                    "dimensions": ["query", "page"],
                    "rowLimit": 25000,
                    "startRow": start_row,
                },
            )
            .execute()
        )
        batch = response.get("rows", [])
        if not batch:
            break

        rows.extend(batch)
        start_row += 25000

    return rows


def extract_payload(
    creds,
    site_url: str,
    start_date,
    end_date,
) -> dict:
    """Fetch GSC data and return a structured payload dict.

    Args:
        creds: Google ``Credentials`` object.
        site_url: The GSC property URL.
        start_date: Start date (``date`` or ``str``).
        end_date: End date (``date`` or ``str``).

    Returns:
        Dictionary with ``site_url``, ``date_range``, ``summary_metrics``,
        ``top_queries_by_impressions``, ``top_queries_by_clicks``, and
        ``top_pages``.
    """
    rows = fetch_all_rows(creds, site_url, str(start_date), str(end_date))

    if not rows:
        return {"note": "No GSC data returned for this period."}

    df = pd.DataFrame(
        [
            {
                "query": r["keys"][0],
                "page": r["keys"][1],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": r["ctr"],
                "position": r["position"],
            }
            for r in rows
        ]
    )

    return {
        "site_url": site_url,
        "date_range": {"start": str(start_date), "end": str(end_date)},
        "summary_metrics": {
            "total_clicks": int(df["clicks"].sum()),
            "total_impressions": int(df["impressions"].sum()),
            "avg_ctr": round(df["ctr"].mean(), 4),
            "avg_position": round(df["position"].mean(), 2),
        },
        "top_queries_by_impressions": (
            df.sort_values("impressions", ascending=False)
            .head(20)
            .to_dict("records")
        ),
        "top_queries_by_clicks": (
            df.sort_values("clicks", ascending=False).head(20).to_dict("records")
        ),
        "top_pages": (
            df.groupby("page")
            .agg(
                {
                    "clicks": "sum",
                    "impressions": "sum",
                    "ctr": "mean",
                    "position": "mean",
                }
            )
            .reset_index()
            .sort_values("impressions", ascending=False)
            .head(20)
            .to_dict("records")
        ),
        "raw_query_page_data": df.to_dict("records"),
    }


def calculate_comparison_metrics(
    payload1: dict,
    payload2: dict,
) -> dict:
    """Compute delta metrics between two GSC payloads.

    Args:
        payload1: First-period payload (baseline).
        payload2: Second-period payload (current).

    Returns:
        Dictionary containing metrics comparison, top gainers/losers, and
        new/lost queries.
    """
    metrics1 = payload1.get("summary_metrics", {})
    metrics2 = payload2.get("summary_metrics", {})

    def _pct_change(val1: float, val2: float) -> float:
        if val1 == 0:
            return 0.0 if val2 == 0 else 100.0
        return round(((val2 - val1) / val1) * 100, 2)

    comparison: dict = {
        "period1": payload1.get("date_range", {}),
        "period2": payload2.get("date_range", {}),
        "metrics_comparison": {
            "clicks": {
                "period1": metrics1.get("total_clicks", 0),
                "period2": metrics2.get("total_clicks", 0),
                "absolute_change": (
                    metrics2.get("total_clicks", 0)
                    - metrics1.get("total_clicks", 0)
                ),
                "percent_change": _pct_change(
                    metrics1.get("total_clicks", 0),
                    metrics2.get("total_clicks", 0),
                ),
            },
            "impressions": {
                "period1": metrics1.get("total_impressions", 0),
                "period2": metrics2.get("total_impressions", 0),
                "absolute_change": (
                    metrics2.get("total_impressions", 0)
                    - metrics1.get("total_impressions", 0)
                ),
                "percent_change": _pct_change(
                    metrics1.get("total_impressions", 0),
                    metrics2.get("total_impressions", 0),
                ),
            },
            "ctr": {
                "period1": metrics1.get("avg_ctr", 0),
                "period2": metrics2.get("avg_ctr", 0),
                "absolute_change": round(
                    metrics2.get("avg_ctr", 0) - metrics1.get("avg_ctr", 0), 4
                ),
                "percent_change": _pct_change(
                    metrics1.get("avg_ctr", 0),
                    metrics2.get("avg_ctr", 0),
                ),
            },
            "position": {
                "period1": metrics1.get("avg_position", 0),
                "period2": metrics2.get("avg_position", 0),
                "absolute_change": round(
                    metrics2.get("avg_position", 0)
                    - metrics1.get("avg_position", 0),
                    2,
                ),
                "percent_change": _pct_change(
                    metrics1.get("avg_position", 0),
                    metrics2.get("avg_position", 0),
                ),
            },
        },
    }

    # Query-level comparison
    queries1 = {
        q["query"]: q for q in payload1.get("top_queries_by_clicks", [])
    }
    queries2 = {
        q["query"]: q for q in payload2.get("top_queries_by_clicks", [])
    }

    query_changes: list[dict] = []
    for query, data2 in queries2.items():
        if query in queries1:
            data1 = queries1[query]
            change = data2["clicks"] - data1["clicks"]
            if change != 0:
                query_changes.append(
                    {
                        "query": query,
                        "period1_clicks": data1["clicks"],
                        "period2_clicks": data2["clicks"],
                        "change": change,
                        "percent_change": _pct_change(
                            data1["clicks"], data2["clicks"]
                        ),
                    }
                )

    query_changes.sort(key=lambda x: x["change"], reverse=True)
    comparison["top_gainers"] = query_changes[:10]
    comparison["top_losers"] = query_changes[-10:]

    # New and lost queries
    comparison["new_queries"] = [
        q for q in queries2 if q not in queries1
    ][:20]
    comparison["lost_queries"] = [
        q for q in queries1 if q not in queries2
    ][:20]

    return comparison
