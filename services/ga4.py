"""
SEMAI Analytics Intelligence Platform - Google Analytics 4 Service.

Pure-Python module for fetching and structuring GA4 data.
No Streamlit dependency.
"""

from __future__ import annotations

from googleapiclient.discovery import build


_PAGE_SIZE = 100_000

# Each report is deliberately kept to a compatible GA4 reporting surface.
# A failure in an optional report is recorded without losing other datasets.
_REPORT_DEFINITIONS = {
    "daily_overview": {
        "dimensions": ["date"],
        "metrics": [
            "sessions", "totalUsers", "activeUsers", "newUsers",
            "engagedSessions", "engagementRate", "bounceRate",
            "averageSessionDuration", "screenPageViews", "eventCount",
            "keyEvents", "totalRevenue",
        ],
    },
    "traffic_acquisition": {
        "dimensions": [
            "sessionDefaultChannelGroup", "sessionSource",
            "sessionMedium", "sessionCampaignName",
        ],
        "metrics": [
            "sessions", "totalUsers", "newUsers", "engagedSessions",
            "engagementRate", "bounceRate", "eventCount", "keyEvents",
            "totalRevenue",
        ],
    },
    "user_acquisition": {
        "dimensions": [
            "firstUserDefaultChannelGroup", "firstUserSource",
            "firstUserMedium", "firstUserCampaignName",
        ],
        "metrics": [
            "totalUsers", "newUsers", "engagedSessions", "engagementRate",
            "eventCount", "keyEvents", "totalRevenue",
        ],
    },
    "landing_pages": {
        "dimensions": ["landingPagePlusQueryString"],
        "metrics": [
            "sessions", "totalUsers", "newUsers", "engagedSessions",
            "engagementRate", "bounceRate", "averageSessionDuration",
            "screenPageViews", "eventCount", "keyEvents", "totalRevenue",
        ],
    },
    "pages_and_screens": {
        "dimensions": ["pagePath", "pageTitle"],
        "metrics": [
            "screenPageViews", "activeUsers", "sessions", "eventCount",
            "keyEvents", "userEngagementDuration",
        ],
    },
    "events": {
        "dimensions": ["eventName"],
        "metrics": [
            "eventCount", "totalUsers", "eventCountPerUser", "eventValue",
            "keyEvents",
        ],
    },
    "devices": {
        "dimensions": [
            "deviceCategory", "operatingSystem", "browser", "platform",
        ],
        "metrics": [
            "sessions", "totalUsers", "newUsers", "engagementRate",
            "bounceRate", "eventCount", "keyEvents", "totalRevenue",
        ],
    },
    "countries": {
        "dimensions": ["country"],
        "metrics": [
            "sessions", "totalUsers", "activeUsers", "newUsers",
            "engagedSessions", "engagementRate", "bounceRate",
            "averageSessionDuration", "screenPageViews", "eventCount",
            "keyEvents", "totalRevenue",
        ],
    },
    "geography": {
        "dimensions": ["country", "region", "city"],
        "metrics": [
            "sessions", "totalUsers", "newUsers", "engagementRate",
            "eventCount", "keyEvents", "totalRevenue",
        ],
    },
    "demographics": {
        "dimensions": ["userAgeBracket", "userGender"],
        "metrics": [
            "sessions", "totalUsers", "newUsers", "engagementRate",
            "eventCount", "keyEvents", "totalRevenue",
        ],
    },
    "ecommerce_items": {
        "dimensions": ["itemName", "itemCategory", "itemBrand"],
        "metrics": [
            "itemsViewed", "itemsAddedToCart", "itemsPurchased",
            "itemRevenue",
        ],
    },
    "ecommerce_transactions": {
        "dimensions": ["date", "transactionId"],
        "metrics": [
            "purchaseRevenue", "ecommercePurchases",
        ],
    },
}


# =============================================================================
# Public API
# =============================================================================

def list_ga4_properties(creds) -> list[dict]:
    """Return all GA4 properties accessible with *creds*.

    Tries the ``google-analytics-admin`` package first, falling back to the
    REST API if the package is not installed.

    Args:
        creds: Google ``Credentials`` object with analytics scopes.

    Returns:
        List of dicts with keys ``property_id``, ``display_name``, and
        ``full_name``.
    """
    try:
        from google.analytics.admin import AnalyticsAdminServiceClient

        client = AnalyticsAdminServiceClient(credentials=creds)
        properties: list[dict] = []

        for account in client.list_accounts():
            account_name = account.name
            for prop in client.list_properties(
                request={"filter": f"parent:{account_name}"}
            ):
                properties.append(
                    {
                        "property_id": prop.name.split("/")[-1],
                        "display_name": prop.display_name,
                        "full_name": prop.name,
                    }
                )
        return properties

    except ImportError:
        pass  # Fall through to REST API

    # Fallback: REST API
    try:
        service = build("analyticsadmin", "v1beta", credentials=creds)
        accounts_response = service.accounts().list().execute()

        properties = []
        for account in accounts_response.get("accounts", []):
            props_response = (
                service.properties()
                .list(filter=f"parent:{account['name']}")
                .execute()
            )
            for prop in props_response.get("properties", []):
                properties.append(
                    {
                        "property_id": prop["name"].split("/")[-1],
                        "display_name": prop.get("displayName", prop["name"]),
                        "full_name": prop["name"],
                    }
                )
        return properties

    except Exception:
        return []


# ---------------------------------------------------------------------------
# Internal helpers – native package variant
# ---------------------------------------------------------------------------

def _extract_ga4_native(creds, property_id: str, start_date, end_date) -> dict:
    """Fetch GA4 data using the ``google-analytics-data`` package."""
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        RunReportRequest,
        DateRange,
        Dimension,
        Metric,
    )

    client = BetaAnalyticsDataClient(credentials=creds)
    prop = f"properties/{property_id}"

    # 1) Summary metrics ------------------------------------------------
    summary_request = RunReportRequest(
        property=prop,
        date_ranges=[
            DateRange(start_date=str(start_date), end_date=str(end_date))
        ],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name="newUsers"),
            Metric(name="engagementRate"),
            Metric(name="averageSessionDuration"),
            Metric(name="bounceRate"),
            Metric(name="screenPageViews"),
            Metric(name="conversions"),
            Metric(name="eventCount"),
        ],
    )
    summary_response = client.run_report(summary_request)

    summary_metrics: dict[str, float] = {}
    if summary_response.rows:
        row = summary_response.rows[0]
        metric_names = [
            "sessions",
            "totalUsers",
            "newUsers",
            "engagementRate",
            "averageSessionDuration",
            "bounceRate",
            "screenPageViews",
            "conversions",
            "eventCount",
        ]
        for i, metric in enumerate(row.metric_values):
            summary_metrics[metric_names[i]] = (
                float(metric.value) if metric.value else 0
            )

    # 2) Channel performance --------------------------------------------
    channel_request = RunReportRequest(
        property=prop,
        date_ranges=[
            DateRange(start_date=str(start_date), end_date=str(end_date))
        ],
        dimensions=[Dimension(name="sessionDefaultChannelGroup")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name="engagementRate"),
            Metric(name="conversions"),
        ],
        limit=20,
    )
    channel_response = client.run_report(channel_request)

    channels: list[dict] = []
    for row in channel_response.rows:
        channels.append(
            {
                "channel": row.dimension_values[0].value,
                "sessions": int(float(row.metric_values[0].value)),
                "users": int(float(row.metric_values[1].value)),
                "engagement_rate": round(
                    float(row.metric_values[2].value), 4
                ),
                "conversions": int(float(row.metric_values[3].value)),
            }
        )

    # 3) Top pages -------------------------------------------------------
    pages_request = RunReportRequest(
        property=prop,
        date_ranges=[
            DateRange(start_date=str(start_date), end_date=str(end_date))
        ],
        dimensions=[Dimension(name="pagePath")],
        metrics=[
            Metric(name="screenPageViews"),
            Metric(name="sessions"),
            Metric(name="engagementRate"),
            Metric(name="averageSessionDuration"),
        ],
        limit=20,
    )
    pages_response = client.run_report(pages_request)

    pages: list[dict] = []
    for row in pages_response.rows:
        pages.append(
            {
                "page": row.dimension_values[0].value,
                "pageviews": int(float(row.metric_values[0].value)),
                "sessions": int(float(row.metric_values[1].value)),
                "engagement_rate": round(
                    float(row.metric_values[2].value), 4
                ),
                "avg_session_duration": round(
                    float(row.metric_values[3].value), 2
                ),
            }
        )

    # 4) Device breakdown ------------------------------------------------
    device_request = RunReportRequest(
        property=prop,
        date_ranges=[
            DateRange(start_date=str(start_date), end_date=str(end_date))
        ],
        dimensions=[Dimension(name="deviceCategory")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name="engagementRate"),
        ],
    )
    device_response = client.run_report(device_request)

    devices: list[dict] = []
    for row in device_response.rows:
        devices.append(
            {
                "device": row.dimension_values[0].value,
                "sessions": int(float(row.metric_values[0].value)),
                "users": int(float(row.metric_values[1].value)),
                "engagement_rate": round(
                    float(row.metric_values[2].value), 4
                ),
            }
        )

    return _build_ga4_payload(
        property_id, start_date, end_date,
        summary_metrics, channels, pages, devices,
    )


# ---------------------------------------------------------------------------
# Internal helpers – REST API fallback
# ---------------------------------------------------------------------------

def _extract_ga4_rest(creds, property_id: str, start_date, end_date) -> dict:
    """Fetch GA4 data using the REST API (no extra packages required)."""
    service = build("analyticsdata", "v1beta", credentials=creds)
    prop = f"properties/{property_id}"

    # 1) Summary metrics ------------------------------------------------
    summary_body = {
        "dateRanges": [
            {"startDate": str(start_date), "endDate": str(end_date)}
        ],
        "metrics": [
            {"name": "sessions"},
            {"name": "totalUsers"},
            {"name": "newUsers"},
            {"name": "engagementRate"},
            {"name": "averageSessionDuration"},
            {"name": "bounceRate"},
            {"name": "screenPageViews"},
            {"name": "conversions"},
            {"name": "eventCount"},
        ],
    }
    summary_resp = (
        service.properties().runReport(property=prop, body=summary_body).execute()
    )

    summary_metrics: dict[str, float] = {}
    if summary_resp.get("rows"):
        row = summary_resp["rows"][0]
        metric_names = [
            "sessions",
            "totalUsers",
            "newUsers",
            "engagementRate",
            "averageSessionDuration",
            "bounceRate",
            "screenPageViews",
            "conversions",
            "eventCount",
        ]
        for i, metric_val in enumerate(row.get("metricValues", [])):
            summary_metrics[metric_names[i]] = float(
                metric_val.get("value", 0)
            )

    # 2) Channel performance --------------------------------------------
    channel_body = {
        "dateRanges": [
            {"startDate": str(start_date), "endDate": str(end_date)}
        ],
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "metrics": [
            {"name": "sessions"},
            {"name": "totalUsers"},
            {"name": "engagementRate"},
            {"name": "conversions"},
        ],
        "limit": 20,
    }
    channel_resp = (
        service.properties().runReport(property=prop, body=channel_body).execute()
    )

    channels: list[dict] = []
    for row in channel_resp.get("rows", []):
        channels.append(
            {
                "channel": row["dimensionValues"][0]["value"],
                "sessions": int(float(row["metricValues"][0]["value"])),
                "users": int(float(row["metricValues"][1]["value"])),
                "engagement_rate": round(
                    float(row["metricValues"][2]["value"]), 4
                ),
                "conversions": int(float(row["metricValues"][3]["value"])),
            }
        )

    # 3) Top pages -------------------------------------------------------
    pages_body = {
        "dateRanges": [
            {"startDate": str(start_date), "endDate": str(end_date)}
        ],
        "dimensions": [{"name": "pagePath"}],
        "metrics": [
            {"name": "screenPageViews"},
            {"name": "sessions"},
            {"name": "engagementRate"},
            {"name": "averageSessionDuration"},
        ],
        "limit": 20,
    }
    pages_resp = (
        service.properties().runReport(property=prop, body=pages_body).execute()
    )

    pages: list[dict] = []
    for row in pages_resp.get("rows", []):
        pages.append(
            {
                "page": row["dimensionValues"][0]["value"],
                "pageviews": int(float(row["metricValues"][0]["value"])),
                "sessions": int(float(row["metricValues"][1]["value"])),
                "engagement_rate": round(
                    float(row["metricValues"][2]["value"]), 4
                ),
                "avg_session_duration": round(
                    float(row["metricValues"][3]["value"]), 2
                ),
            }
        )

    # 4) Device breakdown ------------------------------------------------
    device_body = {
        "dateRanges": [
            {"startDate": str(start_date), "endDate": str(end_date)}
        ],
        "dimensions": [{"name": "deviceCategory"}],
        "metrics": [
            {"name": "sessions"},
            {"name": "totalUsers"},
            {"name": "engagementRate"},
        ],
    }
    device_resp = (
        service.properties().runReport(property=prop, body=device_body).execute()
    )

    devices: list[dict] = []
    for row in device_resp.get("rows", []):
        devices.append(
            {
                "device": row["dimensionValues"][0]["value"],
                "sessions": int(float(row["metricValues"][0]["value"])),
                "users": int(float(row["metricValues"][1]["value"])),
                "engagement_rate": round(
                    float(row["metricValues"][2]["value"]), 4
                ),
            }
        )

    return _build_ga4_payload(
        property_id, start_date, end_date,
        summary_metrics, channels, pages, devices,
    )


def _coerce_metric(value: str):
    """Convert a Data API metric string to a number when possible."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else number


def _run_report_all_rows(
    service,
    property_name: str,
    start_date,
    end_date,
    dimensions: list[str],
    metrics: list[str],
) -> tuple[list[dict], int]:
    """Run one GA4 report and paginate until every available row is read."""
    rows: list[dict] = []
    offset = 0
    reported_row_count = 0

    while True:
        body = {
            "dateRanges": [
                {"startDate": str(start_date), "endDate": str(end_date)}
            ],
            "dimensions": [{"name": name} for name in dimensions],
            "metrics": [{"name": name} for name in metrics],
            "limit": _PAGE_SIZE,
            "offset": offset,
            "keepEmptyRows": True,
        }
        response = (
            service.properties()
            .runReport(property=property_name, body=body)
            .execute()
        )
        page_rows = response.get("rows", [])
        reported_row_count = int(response.get("rowCount", len(page_rows)))

        for api_row in page_rows:
            row = {}
            dimension_values = api_row.get("dimensionValues", [])
            metric_values = api_row.get("metricValues", [])
            for index, name in enumerate(dimensions):
                row[name] = (
                    dimension_values[index].get("value", "")
                    if index < len(dimension_values)
                    else ""
                )
            for index, name in enumerate(metrics):
                raw_value = (
                    metric_values[index].get("value", "0")
                    if index < len(metric_values)
                    else "0"
                )
                row[name] = _coerce_metric(raw_value)
            rows.append(row)

        offset += len(page_rows)
        if not page_rows or offset >= reported_row_count:
            break

    return rows, reported_row_count


def _run_report_resilient(
    service,
    property_name: str,
    start_date,
    end_date,
    dimensions: list[str],
    metrics: list[str],
) -> tuple[list[dict], int, dict[str, str]]:
    """Run a report, recovering compatible metrics if the full set fails."""
    try:
        rows, row_count = _run_report_all_rows(
            service,
            property_name,
            start_date,
            end_date,
            dimensions,
            metrics,
        )
        return rows, row_count, {}
    except Exception as combined_error:
        merged_rows: dict[tuple, dict] = {}
        metric_errors: dict[str, str] = {}
        largest_row_count = 0

        for metric in metrics:
            try:
                rows, row_count = _run_report_all_rows(
                    service,
                    property_name,
                    start_date,
                    end_date,
                    dimensions,
                    [metric],
                )
                largest_row_count = max(largest_row_count, row_count)
                for row in rows:
                    key = tuple(row.get(name, "") for name in dimensions)
                    merged_rows.setdefault(
                        key, {name: row.get(name, "") for name in dimensions}
                    ).update({metric: row.get(metric, 0)})
            except Exception as metric_error:
                metric_errors[metric] = str(metric_error)

        if len(metric_errors) == len(metrics):
            raise RuntimeError(str(combined_error)) from combined_error

        return list(merged_rows.values()), largest_row_count, metric_errors


def _extract_ga4_comprehensive(
    creds,
    property_id: str,
    start_date,
    end_date,
) -> dict:
    """Extract complete paginated rows across standard GA4 report surfaces."""
    service = build("analyticsdata", "v1beta", credentials=creds)
    property_name = f"properties/{property_id}"

    summary_rows, summary_count, summary_metric_errors = _run_report_resilient(
        service,
        property_name,
        start_date,
        end_date,
        [],
        [
            "sessions", "totalUsers", "newUsers", "engagementRate",
            "averageSessionDuration", "bounceRate", "screenPageViews",
            "keyEvents", "eventCount", "totalRevenue",
        ],
    )
    summary_metrics = summary_rows[0] if summary_rows else {}

    datasets: dict[str, list[dict]] = {}
    extraction_errors: dict[str, str] = {}
    metric_errors: dict[str, dict[str, str]] = {}
    row_counts: dict[str, dict[str, int | bool]] = {}

    for dataset_name, definition in _REPORT_DEFINITIONS.items():
        try:
            rows, api_row_count, omitted_metrics = _run_report_resilient(
                service,
                property_name,
                start_date,
                end_date,
                definition["dimensions"],
                definition["metrics"],
            )
            datasets[dataset_name] = rows
            if omitted_metrics:
                metric_errors[dataset_name] = omitted_metrics
            row_counts[dataset_name] = {
                "api_row_count": api_row_count,
                "extracted_row_count": len(rows),
                "complete": (
                    len(rows) == api_row_count and not omitted_metrics
                ),
            }
        except Exception as exc:
            datasets[dataset_name] = []
            extraction_errors[dataset_name] = str(exc)
            row_counts[dataset_name] = {
                "api_row_count": 0,
                "extracted_row_count": 0,
                "complete": False,
            }

    channels = [
        {
            "channel": row.get("sessionDefaultChannelGroup", ""),
            "source": row.get("sessionSource", ""),
            "medium": row.get("sessionMedium", ""),
            "campaign": row.get("sessionCampaignName", ""),
            "sessions": int(row.get("sessions", 0)),
            "users": int(row.get("totalUsers", 0)),
            "engagement_rate": row.get("engagementRate", 0),
            "conversions": int(row.get("keyEvents", 0)),
            "revenue": row.get("totalRevenue", 0),
        }
        for row in datasets.get("traffic_acquisition", [])
    ]
    pages = [
        {
            "page": row.get("pagePath", ""),
            "title": row.get("pageTitle", ""),
            "pageviews": int(row.get("screenPageViews", 0)),
            "sessions": int(row.get("sessions", 0)),
            "users": int(row.get("activeUsers", 0)),
            "events": int(row.get("eventCount", 0)),
            "conversions": int(row.get("keyEvents", 0)),
            "engagement_seconds": row.get("userEngagementDuration", 0),
        }
        for row in datasets.get("pages_and_screens", [])
    ]
    devices = [
        {
            "device": row.get("deviceCategory", ""),
            "operating_system": row.get("operatingSystem", ""),
            "browser": row.get("browser", ""),
            "platform": row.get("platform", ""),
            "sessions": int(row.get("sessions", 0)),
            "users": int(row.get("totalUsers", 0)),
            "engagement_rate": row.get("engagementRate", 0),
        }
        for row in datasets.get("devices", [])
    ]
    countries = [
        {
            "country": row.get("country", ""),
            "sessions": int(row.get("sessions", 0)),
            "users": int(row.get("totalUsers", 0)),
            "active_users": int(row.get("activeUsers", 0)),
            "new_users": int(row.get("newUsers", 0)),
            "engaged_sessions": int(row.get("engagedSessions", 0)),
            "engagement_rate": row.get("engagementRate", 0),
            "bounce_rate": row.get("bounceRate", 0),
            "avg_session_duration": row.get("averageSessionDuration", 0),
            "pageviews": int(row.get("screenPageViews", 0)),
            "events": int(row.get("eventCount", 0)),
            "conversions": int(row.get("keyEvents", 0)),
            "revenue": row.get("totalRevenue", 0),
        }
        for row in datasets.get("countries", [])
    ]

    payload = _build_ga4_payload(
        property_id,
        start_date,
        end_date,
        summary_metrics,
        channels,
        pages,
        devices,
    )
    payload["summary_metrics"]["total_revenue"] = round(
        summary_metrics.get("totalRevenue", 0), 2
    )
    payload["country_performance"] = sorted(
        countries, key=lambda row: row["sessions"], reverse=True
    )
    payload["datasets"] = datasets
    payload["extraction_metadata"] = {
        "page_size": _PAGE_SIZE,
        "summary_api_row_count": summary_count,
        "dataset_row_counts": row_counts,
        "errors": extraction_errors,
        "metric_errors": {
            **({"summary": summary_metric_errors}
               if summary_metric_errors else {}),
            **metric_errors,
        },
        "all_datasets_complete": (
            not extraction_errors
            and not summary_metric_errors
            and not metric_errors
            and all(item["complete"] for item in row_counts.values())
        ),
        "api_note": (
            "GA4 Data API returns aggregated report rows. Event-level raw data "
            "requires a linked GA4 BigQuery export."
        ),
    }
    return payload


# ---------------------------------------------------------------------------
# Shared payload builder
# ---------------------------------------------------------------------------

def _build_ga4_payload(
    property_id: str,
    start_date,
    end_date,
    summary_metrics: dict,
    channels: list[dict],
    pages: list[dict],
    devices: list[dict],
) -> dict:
    """Assemble the final GA4 payload dictionary."""
    return {
        "property_id": property_id,
        "date_range": {"start": str(start_date), "end": str(end_date)},
        "summary_metrics": {
            "total_sessions": int(summary_metrics.get("sessions", 0)),
            "total_users": int(summary_metrics.get("totalUsers", 0)),
            "new_users": int(summary_metrics.get("newUsers", 0)),
            "engagement_rate": round(
                summary_metrics.get("engagementRate", 0), 4
            ),
            "avg_session_duration": round(
                summary_metrics.get("averageSessionDuration", 0), 2
            ),
            "bounce_rate": round(
                summary_metrics.get("bounceRate", 0), 4
            ),
            "total_pageviews": int(
                summary_metrics.get("screenPageViews", 0)
            ),
            "total_conversions": int(
                summary_metrics.get(
                    "keyEvents", summary_metrics.get("conversions", 0)
                )
            ),
            "total_events": int(summary_metrics.get("eventCount", 0)),
        },
        "channel_performance": sorted(
            channels, key=lambda x: x["sessions"], reverse=True
        ),
        "top_pages": sorted(
            pages, key=lambda x: x["pageviews"], reverse=True
        ),
        "device_breakdown": devices,
    }


# =============================================================================
# Main entry point
# =============================================================================

def extract_ga4_payload(
    creds,
    property_id: str,
    start_date,
    end_date,
) -> dict:
    """Extract and format GA4 data into a structured payload.

    Uses paginated GA4 Data API reports so every available aggregate row is
    returned for each supported reporting surface.

    Args:
        creds: Google ``Credentials`` object.
        property_id: GA4 property ID (numeric string).
        start_date: Start date.
        end_date: End date.

    Returns:
        Structured dictionary with summary metrics, compatibility views,
        complete datasets, and extraction metadata. Contains an ``"error"``
        key on failure.
    """
    try:
        return _extract_ga4_comprehensive(
            creds, property_id, start_date, end_date
        )
    except Exception as exc:
        return {"error": str(exc), "note": "Failed to fetch GA4 data."}
