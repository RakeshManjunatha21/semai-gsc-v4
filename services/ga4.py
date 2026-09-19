"""
SEMAI Analytics Intelligence Platform - Google Analytics 4 Service.

Pure-Python module for fetching and structuring GA4 data.
No Streamlit dependency.
"""

from __future__ import annotations

from google.analytics.data_v1alpha import AlphaAnalyticsDataClient
from google.analytics.data_v1alpha.types import (
    DateRange,
    Dimension,
    Funnel,
    FunnelBreakdown,
    FunnelEventFilter,
    FunnelFilterExpression,
    FunnelNextAction,
    FunnelStep,
    RunFunnelReportRequest,
    RunFunnelReportResponse,
)
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
    "page_event_matrix": {
        "dimensions": ["pagePath", "eventName"],
        "metrics": ["eventCount", "totalUsers", "keyEvents"],
    },
    "funnel_event_segments": {
        "dimensions": [
            "eventName", "deviceCategory", "newVsReturning",
        ],
        "metrics": ["eventCount", "totalUsers", "keyEvents"],
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


def list_ga4_key_event_names(creds, property_id: str) -> list[str]:
    """Return event names currently marked as key events for a property."""
    service = build("analyticsadmin", "v1alpha", credentials=creds)
    parent = f"properties/{property_id}"
    names: list[str] = []
    page_token = None

    while True:
        response = service.properties().keyEvents().list(
            parent=parent,
            pageSize=200,
            pageToken=page_token,
        ).execute()
        names.extend(
            item["eventName"]
            for item in response.get("keyEvents", [])
            if item.get("eventName")
        )
        page_token = response.get("nextPageToken")
        if not page_token:
            return sorted(set(names))


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
) -> tuple[list[dict], int, dict]:
    """Run one GA4 report and paginate until every available row is read."""
    rows: list[dict] = []
    offset = 0
    reported_row_count = 0
    report_quality = {
        "data_loss_from_other_row": False,
        "subject_to_thresholding": False,
        "sampling_metadatas": [],
        "time_zone": "",
        "currency_code": "",
    }

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
        response_metadata = response.get("metadata", {})
        report_quality["data_loss_from_other_row"] = (
            report_quality["data_loss_from_other_row"]
            or bool(response_metadata.get("dataLossFromOtherRow", False))
        )
        report_quality["subject_to_thresholding"] = (
            report_quality["subject_to_thresholding"]
            or bool(response_metadata.get("subjectToThresholding", False))
        )
        report_quality["sampling_metadatas"].extend(
            response_metadata.get("samplingMetadatas", [])
        )
        report_quality["time_zone"] = (
            report_quality["time_zone"]
            or response_metadata.get("timeZone", "")
        )
        report_quality["currency_code"] = (
            report_quality["currency_code"]
            or response_metadata.get("currencyCode", "")
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

    return rows, reported_row_count, report_quality


def _run_report_resilient(
    service,
    property_name: str,
    start_date,
    end_date,
    dimensions: list[str],
    metrics: list[str],
) -> tuple[list[dict], int, dict[str, str], dict]:
    """Run a report, recovering compatible metrics if the full set fails."""
    try:
        rows, row_count, report_quality = _run_report_all_rows(
            service,
            property_name,
            start_date,
            end_date,
            dimensions,
            metrics,
        )
        return rows, row_count, {}, report_quality
    except Exception as combined_error:
        merged_rows: dict[tuple, dict] = {}
        metric_errors: dict[str, str] = {}
        quality_by_metric: dict[str, dict] = {}
        largest_row_count = 0

        for metric in metrics:
            try:
                rows, row_count, report_quality = _run_report_all_rows(
                    service,
                    property_name,
                    start_date,
                    end_date,
                    dimensions,
                    [metric],
                )
                quality_by_metric[metric] = report_quality
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

        recovered_qualities = list(quality_by_metric.values())
        return (
            list(merged_rows.values()),
            largest_row_count,
            metric_errors,
            {
                "data_loss_from_other_row": any(
                    quality.get("data_loss_from_other_row", False)
                    for quality in recovered_qualities
                ),
                "subject_to_thresholding": any(
                    quality.get("subject_to_thresholding", False)
                    for quality in recovered_qualities
                ),
                "sampling_metadatas": [
                    sampling
                    for quality in recovered_qualities
                    for sampling in quality.get("sampling_metadatas", [])
                ],
                "time_zone": next((
                    quality.get("time_zone", "")
                    for quality in recovered_qualities
                    if quality.get("time_zone")
                ), ""),
                "currency_code": next((
                    quality.get("currency_code", "")
                    for quality in recovered_qualities
                    if quality.get("currency_code")
                ), ""),
                "metric_recovery_used": True,
                "recovered_metrics": sorted(quality_by_metric),
            },
        )


def _funnel_subreport_rows(subreport: dict) -> list[dict]:
    """Convert a serialized funnel subreport into ordinary row dictionaries."""
    dimensions = [
        header.get("name", "")
        for header in subreport.get("dimension_headers", [])
    ]
    metrics = [
        header.get("name", "")
        for header in subreport.get("metric_headers", [])
    ]
    rows = []
    for api_row in subreport.get("rows", []):
        row = {}
        for index, name in enumerate(dimensions):
            values = api_row.get("dimension_values", [])
            row[name] = (
                values[index].get("value", "")
                if index < len(values)
                else ""
            )
        for index, name in enumerate(metrics):
            values = api_row.get("metric_values", [])
            row[name] = _coerce_metric(
                values[index].get("value", "0")
                if index < len(values)
                else "0"
            )
        rows.append(row)
    return rows


def _run_funnel_report(
    creds,
    property_name: str,
    start_date,
    end_date,
) -> tuple[list[dict], list[dict], dict]:
    """Run the official GA4 alpha funnel report for the core form journey."""
    steps = [
        ("Session start", "session_start"),
        ("Form start", "form_start"),
        ("Form submit", "form_submit"),
        ("New registration", "new_registration"),
    ]
    request = RunFunnelReportRequest(
        property=property_name,
        date_ranges=[DateRange(
            start_date=str(start_date),
            end_date=str(end_date),
        )],
        funnel=Funnel(
            is_open_funnel=False,
            steps=[
                FunnelStep(
                    name=step_name,
                    filter_expression=FunnelFilterExpression(
                        funnel_event_filter=FunnelEventFilter(
                            event_name=event_name
                        )
                    ),
                )
                for step_name, event_name in steps
            ],
        ),
        funnel_breakdown=FunnelBreakdown(
            breakdown_dimension=Dimension(name="deviceCategory"),
            limit=15,
        ),
        funnel_next_action=FunnelNextAction(
            next_action_dimension=Dimension(name="eventName"),
            limit=5,
        ),
        limit=250_000,
        return_property_quota=True,
    )
    response = AlphaAnalyticsDataClient(
        credentials=creds
    ).run_funnel_report(request=request)
    serialized = RunFunnelReportResponse.to_dict(response)
    table = serialized.get("funnel_table", {})
    visualization = serialized.get("funnel_visualization", {})
    sampling = [
        *table.get("metadata", {}).get("sampling_metadatas", []),
        *visualization.get("metadata", {}).get("sampling_metadatas", []),
    ]
    return (
        _funnel_subreport_rows(table),
        _funnel_subreport_rows(visualization),
        {
            "source": "GA4 Data API v1alpha runFunnelReport",
            "stability": "alpha",
            "is_open_funnel": False,
            "steps": [event_name for _, event_name in steps],
            "breakdown": "deviceCategory",
            "next_action_dimension": "eventName",
            "sampling_metadatas": sampling,
        },
    )


def _extract_ga4_comprehensive(
    creds,
    property_id: str,
    start_date,
    end_date,
) -> dict:
    """Extract complete paginated rows across standard GA4 report surfaces."""
    service = build("analyticsdata", "v1beta", credentials=creds)
    property_name = f"properties/{property_id}"

    (
        summary_rows,
        summary_count,
        summary_metric_errors,
        summary_quality,
    ) = _run_report_resilient(
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
    report_quality: dict[str, dict] = {"summary": summary_quality}

    for dataset_name, definition in _REPORT_DEFINITIONS.items():
        try:
            (
                rows,
                api_row_count,
                omitted_metrics,
                dataset_quality,
            ) = _run_report_resilient(
                service,
                property_name,
                start_date,
                end_date,
                definition["dimensions"],
                definition["metrics"],
            )
            datasets[dataset_name] = rows
            report_quality[dataset_name] = dataset_quality
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

    try:
        funnel_rows, funnel_next_actions, funnel_quality = (
            _run_funnel_report(
                creds, property_name, start_date, end_date
            )
        )
        datasets["api_funnel_report"] = funnel_rows
        datasets["api_funnel_next_actions"] = funnel_next_actions
        report_quality["api_funnel_report"] = funnel_quality
        row_counts["api_funnel_report"] = {
            "api_row_count": len(funnel_rows),
            "extracted_row_count": len(funnel_rows),
            "complete": True,
        }
        row_counts["api_funnel_next_actions"] = {
            "api_row_count": len(funnel_next_actions),
            "extracted_row_count": len(funnel_next_actions),
            "complete": True,
        }
    except Exception as exc:
        datasets["api_funnel_report"] = []
        datasets["api_funnel_next_actions"] = []
        extraction_errors["api_funnel_report"] = str(exc)
        for dataset_name in [
            "api_funnel_report", "api_funnel_next_actions",
        ]:
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
    try:
        configured_key_events = list_ga4_key_event_names(creds, property_id)
        event_activity = {
            row.get("eventName", ""): {
                "event_count": int(row.get("eventCount", 0)),
                "key_event_count": int(row.get("keyEvents", 0)),
            }
            for row in datasets.get("events", [])
        }
        payload["key_event_configuration"] = {
            "configured_names": configured_key_events,
            "configured_activity": {
                name: event_activity.get(
                    name, {"event_count": 0, "key_event_count": 0}
                )
                for name in configured_key_events
            },
            "core_funnel_activity": {
                name: {
                    **event_activity.get(
                        name, {"event_count": 0, "key_event_count": 0}
                    ),
                    "configured_as_key_event": name in configured_key_events,
                }
                for name in [
                    "form_start", "form_submit", "new_registration",
                ]
            },
        }
    except Exception as exc:
        payload["key_event_configuration"] = {
            "error": str(exc),
            "configured_names": [],
        }
    reconciliation = []
    for metric, summary_key, dataset_name in [
        ("sessions", "total_sessions", "daily_overview"),
        ("eventCount", "total_events", "daily_overview"),
        ("screenPageViews", "total_pageviews", "daily_overview"),
        ("sessions", "total_sessions", "traffic_acquisition"),
        ("sessions", "total_sessions", "countries"),
    ]:
        summary_value = payload["summary_metrics"].get(summary_key, 0)
        dataset_value = sum(
            row.get(metric, 0) for row in datasets.get(dataset_name, [])
        )
        reconciliation.append({
            "metric": metric,
            "summary_value": summary_value,
            "dataset": dataset_name,
            "dataset_sum": dataset_value,
            "difference": dataset_value - summary_value,
            "matches": dataset_value == summary_value,
        })
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
        "report_quality": report_quality,
        "reconciliation": reconciliation,
        "datasets_using_metric_recovery": sorted(
            name for name, quality in report_quality.items()
            if quality.get("metric_recovery_used", False)
        ),
        "all_reports_unrestricted": all(
            not quality.get("data_loss_from_other_row", False)
            and not quality.get("subject_to_thresholding", False)
            and not quality.get("sampling_metadatas", [])
            for quality in report_quality.values()
        ),
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
