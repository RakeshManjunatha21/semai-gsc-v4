"""GA4 BigQuery event-export discovery and transformation service."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from googleapiclient.discovery import build


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class BigQueryExportUnavailable(RuntimeError):
    """Raised when a property has no usable BigQuery event export."""


def list_bigquery_links(creds, property_id: str) -> list[dict]:
    """Return all BigQuery export links configured for a GA4 property."""
    service = build("analyticsadmin", "v1alpha", credentials=creds)
    parent = f"properties/{property_id}"
    links: list[dict] = []
    page_token = None

    while True:
        request = service.properties().bigQueryLinks().list(
            parent=parent,
            pageSize=200,
            pageToken=page_token,
        )
        response = request.execute()
        links.extend(response.get("bigqueryLinks", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return links


def list_key_event_names(creds, property_id: str) -> list[str]:
    """Return the event names currently marked as GA4 key events."""
    service = build("analyticsadmin", "v1alpha", credentials=creds)
    parent = f"properties/{property_id}"
    names: list[str] = []
    page_token = None

    while True:
        request = service.properties().keyEvents().list(
            parent=parent,
            pageSize=200,
            pageToken=page_token,
        )
        response = request.execute()
        names.extend(
            item["eventName"]
            for item in response.get("keyEvents", [])
            if item.get("eventName")
        )
        page_token = response.get("nextPageToken")
        if not page_token:
            return sorted(set(names))


def _validated_identifier(value: str, label: str) -> str:
    if not value or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid BigQuery {label}: {value!r}")
    return value


def _resolve_project_id(creds, project_reference: str) -> str:
  """Resolve the numeric project resource returned by GA Admin."""
  if not project_reference.isdigit():
    return project_reference

  from google.cloud import bigquery

  client = bigquery.Client(project=project_reference, credentials=creds)
  for project in client.list_projects(max_results=1_000):
    if str(getattr(project, "numeric_id", "")) == project_reference:
      return project.project_id

  raise BigQueryExportUnavailable(
    "The linked Google Cloud project is not visible to this account."
  )


def _query_rows(client, sql: str, parameters: list) -> list[dict]:
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(query_parameters=parameters)
    return [
        {key: _json_value(value) for key, value in row.items()}
        for row in client.query(sql, job_config=job_config).result(
            page_size=10_000
        )
    ]


def _json_value(value):
    """Convert BigQuery scalar values to JSON-safe Python values."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _date_parameters(start_date, end_date) -> list:
    from google.cloud import bigquery

    return [
        bigquery.ScalarQueryParameter(
            "start_suffix", "STRING", start_date.strftime("%Y%m%d")
        ),
        bigquery.ScalarQueryParameter(
            "end_suffix", "STRING", end_date.strftime("%Y%m%d")
        ),
    ]


def _events_cte(table_pattern: str) -> str:
    return f"""
WITH source_events AS (
  SELECT
    PARSE_DATE('%Y%m%d', event_date) AS event_date,
    TIMESTAMP_MICROS(event_timestamp) AS event_timestamp,
    event_name,
    user_pseudo_id,
    (SELECT value.int_value FROM UNNEST(event_params)
      WHERE key = 'ga_session_id' LIMIT 1) AS ga_session_id,
    (SELECT value.string_value FROM UNNEST(event_params)
      WHERE key = 'page_location' LIMIT 1) AS page_location,
    (SELECT value.string_value FROM UNNEST(event_params)
      WHERE key = 'page_title' LIMIT 1) AS page_title,
    (SELECT value.string_value FROM UNNEST(event_params)
      WHERE key = 'page_referrer' LIMIT 1) AS page_referrer,
    COALESCE((SELECT value.int_value FROM UNNEST(event_params)
      WHERE key = 'engagement_time_msec' LIMIT 1), 0)
      AS engagement_time_msec,
    COALESCE(
      (SELECT value.double_value FROM UNNEST(event_params)
        WHERE key = 'value' LIMIT 1),
      CAST((SELECT value.int_value FROM UNNEST(event_params)
        WHERE key = 'value' LIMIT 1) AS FLOAT64),
      (SELECT value.float_value FROM UNNEST(event_params)
        WHERE key = 'value' LIMIT 1)
    ) AS event_value,
    (SELECT value.string_value FROM UNNEST(event_params)
      WHERE key = 'currency' LIMIT 1) AS currency,
    (SELECT value.string_value FROM UNNEST(event_params)
      WHERE key = 'transaction_id' LIMIT 1) AS transaction_id,
    device.category AS device_category,
    device.operating_system AS operating_system,
    device.web_info.browser AS browser,
    geo.country,
    geo.region,
    geo.city,
    COALESCE(
      collected_traffic_source.manual_source,
      traffic_source.source,
      '(direct)'
    ) AS source,
    COALESCE(
      collected_traffic_source.manual_medium,
      traffic_source.medium,
      '(none)'
    ) AS medium,
    COALESCE(
      collected_traffic_source.manual_campaign_name,
      traffic_source.name,
      '(not set)'
    ) AS campaign
  FROM `{table_pattern}`
  WHERE _TABLE_SUFFIX BETWEEN @start_suffix AND @end_suffix
)
"""


def _sessions_query(table_pattern: str) -> str:
    return _events_cte(table_pattern) + """
SELECT
  CONCAT(user_pseudo_id, '.', CAST(ga_session_id AS STRING)) AS session_key,
  user_pseudo_id,
  ga_session_id AS session_id,
  MIN(event_timestamp) AS session_start,
  MAX(event_timestamp) AS session_end,
  TIMESTAMP_DIFF(MAX(event_timestamp), MIN(event_timestamp), SECOND)
    AS session_duration_seconds,
  ARRAY_AGG(page_location IGNORE NULLS ORDER BY event_timestamp LIMIT 1)
    [SAFE_OFFSET(0)] AS entrance_page,
  ARRAY_AGG(page_location IGNORE NULLS ORDER BY event_timestamp DESC LIMIT 1)
    [SAFE_OFFSET(0)] AS exit_page,
  ARRAY_AGG(source IGNORE NULLS ORDER BY event_timestamp LIMIT 1)
    [SAFE_OFFSET(0)] AS source,
  ARRAY_AGG(medium IGNORE NULLS ORDER BY event_timestamp LIMIT 1)
    [SAFE_OFFSET(0)] AS medium,
  ARRAY_AGG(campaign IGNORE NULLS ORDER BY event_timestamp LIMIT 1)
    [SAFE_OFFSET(0)] AS campaign,
  ARRAY_AGG(device_category IGNORE NULLS ORDER BY event_timestamp LIMIT 1)
    [SAFE_OFFSET(0)] AS device_category,
  ARRAY_AGG(country IGNORE NULLS ORDER BY event_timestamp LIMIT 1)
    [SAFE_OFFSET(0)] AS country,
  COUNT(*) AS event_count,
  COUNTIF(event_name = 'page_view') AS pageviews,
  SUM(engagement_time_msec) AS engagement_time_msec,
  COUNTIF(event_name IN UNNEST(@key_event_names)) AS key_event_count
FROM source_events
WHERE user_pseudo_id IS NOT NULL AND ga_session_id IS NOT NULL
GROUP BY user_pseudo_id, ga_session_id
ORDER BY session_start
"""


def _conversions_query(table_pattern: str) -> str:
    return _events_cte(table_pattern) + """
SELECT
  event_date,
  event_timestamp,
  user_pseudo_id,
  ga_session_id AS session_id,
  event_name,
  event_value,
  currency,
  transaction_id,
  page_location,
  source,
  medium,
  campaign,
  country,
  region,
  city,
  device_category
FROM source_events
WHERE event_name IN UNNEST(@key_event_names)
ORDER BY event_timestamp
"""


def _pages_query(table_pattern: str) -> str:
    return _events_cte(table_pattern) + """
, page_events AS (
  SELECT
    event_date,
    page_location,
    ANY_VALUE(page_title HAVING MAX event_timestamp) AS page_title,
    COUNTIF(event_name = 'page_view') AS pageviews,
    COUNT(DISTINCT user_pseudo_id) AS unique_users,
    SUM(engagement_time_msec) AS engagement_time_msec,
    COUNTIF(event_name IN UNNEST(@key_event_names)) AS key_event_count
  FROM source_events
  WHERE page_location IS NOT NULL
  GROUP BY event_date, page_location
), session_pages AS (
  SELECT
    DATE(MIN(event_timestamp)) AS event_date,
    ARRAY_AGG(page_location IGNORE NULLS ORDER BY event_timestamp LIMIT 1)
      [SAFE_OFFSET(0)] AS entrance_page,
    ARRAY_AGG(page_location IGNORE NULLS ORDER BY event_timestamp DESC LIMIT 1)
      [SAFE_OFFSET(0)] AS exit_page
  FROM source_events
  WHERE user_pseudo_id IS NOT NULL AND ga_session_id IS NOT NULL
  GROUP BY user_pseudo_id, ga_session_id
), entrances AS (
  SELECT event_date, entrance_page AS page_location, COUNT(*) AS entrances
  FROM session_pages WHERE entrance_page IS NOT NULL
  GROUP BY event_date, page_location
), exits AS (
  SELECT event_date, exit_page AS page_location, COUNT(*) AS exits
  FROM session_pages WHERE exit_page IS NOT NULL
  GROUP BY event_date, page_location
)
SELECT
  pages.*,
  SAFE_DIVIDE(pages.engagement_time_msec, pages.pageviews)
    AS avg_engagement_time_msec,
  COALESCE(entrances.entrances, 0) AS entrances,
  COALESCE(exits.exits, 0) AS exits
FROM page_events AS pages
LEFT JOIN entrances USING (event_date, page_location)
LEFT JOIN exits USING (event_date, page_location)
ORDER BY event_date, pageviews DESC
"""


def _users_query(table_pattern: str) -> str:
    return _events_cte(table_pattern) + """
SELECT
  user_pseudo_id,
  MIN(event_date) AS first_seen_in_period,
  MAX(event_date) AS last_seen_in_period,
  COUNT(DISTINCT ga_session_id) AS total_sessions,
  IF(COUNTIF(event_name IN ('first_visit', 'first_open')) > 0,
    'new', 'returning') AS user_type,
  APPROX_TOP_COUNT(device_category, 1)[SAFE_OFFSET(0)].value
    AS primary_device_category,
  APPROX_TOP_COUNT(country, 1)[SAFE_OFFSET(0)].value AS primary_country,
  COUNT(*) AS event_count,
  SUM(engagement_time_msec) AS engagement_time_msec,
  COUNTIF(event_name IN UNNEST(@key_event_names)) AS key_event_count
FROM source_events
WHERE user_pseudo_id IS NOT NULL
GROUP BY user_pseudo_id
ORDER BY first_seen_in_period
"""


def _funnel_query(table_pattern: str) -> str:
    return _events_cte(table_pattern) + """
, session_base AS (
  SELECT
    user_pseudo_id,
    ga_session_id,
    ARRAY_AGG(IF(event_name = 'page_view', page_location, NULL)
      IGNORE NULLS ORDER BY event_timestamp LIMIT 1)[SAFE_OFFSET(0)]
      AS landing_page,
    ARRAY_AGG(device_category IGNORE NULLS ORDER BY event_timestamp LIMIT 1)
      [SAFE_OFFSET(0)] AS device_category,
    IF(COUNTIF(event_name IN ('first_visit', 'first_open')) > 0,
      'new', 'returning') AS visitor_type,
    MIN(IF(event_name = 'session_start', event_timestamp, NULL))
      AS session_start_ts,
    MIN(IF(event_name = 'form_start', event_timestamp, NULL))
      AS first_form_start_ts
  FROM source_events
  WHERE user_pseudo_id IS NOT NULL AND ga_session_id IS NOT NULL
  GROUP BY user_pseudo_id, ga_session_id
), ordered_submits AS (
  SELECT
    session_base.*,
    MIN(IF(
      event_name = 'form_submit'
      AND event_timestamp >= first_form_start_ts,
      event_timestamp,
      NULL
    )) AS first_form_submit_ts
  FROM session_base
  JOIN source_events USING (user_pseudo_id, ga_session_id)
  GROUP BY
    user_pseudo_id, ga_session_id, landing_page, device_category,
    visitor_type, session_start_ts, first_form_start_ts
), ordered_funnel AS (
  SELECT
    ordered_submits.*,
    MIN(IF(
      event_name = 'new_registration'
      AND event_timestamp >= first_form_submit_ts,
      event_timestamp,
      NULL
    )) AS first_registration_ts
  FROM ordered_submits
  JOIN source_events USING (user_pseudo_id, ga_session_id)
  GROUP BY
    user_pseudo_id, ga_session_id, landing_page, device_category,
    visitor_type, session_start_ts, first_form_start_ts,
    first_form_submit_ts
)
SELECT
  COALESCE(landing_page, '(not set)') AS landing_page,
  COALESCE(device_category, '(not set)') AS device_category,
  visitor_type,
  COUNTIF(session_start_ts IS NOT NULL) AS session_start_sessions,
  COUNTIF(first_form_start_ts >= session_start_ts) AS form_start_sessions,
  COUNTIF(first_form_submit_ts IS NOT NULL) AS form_submit_sessions,
  COUNTIF(first_registration_ts IS NOT NULL) AS new_registration_sessions,
  SAFE_DIVIDE(
    COUNTIF(first_form_start_ts >= session_start_ts),
    COUNTIF(session_start_ts IS NOT NULL)
  ) AS session_to_form_start_rate,
  SAFE_DIVIDE(
    COUNTIF(first_form_submit_ts IS NOT NULL),
    COUNTIF(first_form_start_ts >= session_start_ts)
  ) AS form_start_to_submit_rate,
  SAFE_DIVIDE(
    COUNTIF(first_registration_ts IS NOT NULL),
    COUNTIF(first_form_submit_ts IS NOT NULL)
  ) AS form_submit_to_registration_rate
FROM ordered_funnel
GROUP BY landing_page, device_category, visitor_type
HAVING session_start_sessions > 0
ORDER BY session_start_sessions DESC
"""


def _form_paths_query(table_pattern: str) -> str:
    return _events_cte(table_pattern) + """
, path_nodes AS (
  SELECT
    user_pseudo_id,
    ga_session_id,
    event_timestamp,
    event_name,
    page_location,
    LAG(page_location) OVER (
      PARTITION BY user_pseudo_id, ga_session_id ORDER BY event_timestamp
    ) AS preceding_page,
    LEAD(page_location) OVER (
      PARTITION BY user_pseudo_id, ga_session_id ORDER BY event_timestamp
    ) AS following_page
  FROM source_events
  WHERE event_name = 'page_view' OR event_name = 'form_start'
)
SELECT
  'preceding' AS direction,
  COALESCE(preceding_page, '(not set)') AS page_location,
  COUNT(*) AS occurrences,
  COUNT(DISTINCT user_pseudo_id) AS users,
  COUNT(DISTINCT CONCAT(user_pseudo_id, '.', CAST(ga_session_id AS STRING)))
    AS sessions
FROM path_nodes
WHERE event_name = 'form_start'
GROUP BY page_location
UNION ALL
SELECT
  'following' AS direction,
  COALESCE(following_page, '(not set)') AS page_location,
  COUNT(*) AS occurrences,
  COUNT(DISTINCT user_pseudo_id) AS users,
  COUNT(DISTINCT CONCAT(user_pseudo_id, '.', CAST(ga_session_id AS STRING)))
    AS sessions
FROM path_nodes
WHERE event_name = 'form_start'
GROUP BY page_location
ORDER BY direction, occurrences DESC
"""


def _inventory_tables(client, project_id: str, dataset_id: str) -> list[str]:
    from google.api_core.exceptions import NotFound

    sql = f"""
SELECT table_name
FROM `{project_id}.{dataset_id}.INFORMATION_SCHEMA.TABLES`
WHERE STARTS_WITH(table_name, 'events_')
ORDER BY table_name
"""
    try:
        rows = _query_rows(client, sql, [])
    except NotFound as exc:
        raise BigQueryExportUnavailable(
            "The GA4 BigQuery link is active, but Google has not created "
            "the export dataset yet. Daily export usually appears after "
            "the first processing cycle."
        ) from exc
    return [row["table_name"] for row in rows]


def extract_bigquery_payload(
    creds,
    property_id: str,
    start_date: date,
    end_date: date,
    project_id_override: str | None = None,
) -> dict:
    """Extract full selected-period GA4 event transformations from BigQuery."""
    try:
        from google.cloud import bigquery
    except ImportError as exc:
        raise BigQueryExportUnavailable(
            "google-cloud-bigquery is not installed"
        ) from exc

    links = list_bigquery_links(creds, property_id)
    if not links and not project_id_override:
        raise BigQueryExportUnavailable(
            "This GA4 property has no BigQuery export link."
        )

    link = links[0] if links else {}
    linked_project = link.get("project", "").removeprefix("projects/")
    project_id = _validated_identifier(
      _resolve_project_id(
        creds, project_id_override or linked_project
      ),
      "project",
    )
    dataset_id = _validated_identifier(
        f"analytics_{property_id}", "dataset"
    )
    location = link.get("datasetLocation") or None
    client = bigquery.Client(
        project=project_id,
        credentials=creds,
        location=location,
    )

    available_tables = _inventory_tables(client, project_id, dataset_id)
    daily_tables = [
        name for name in available_tables
        if re.fullmatch(r"events_\d{8}", name)
    ]
    if not daily_tables:
        raise BigQueryExportUnavailable(
            "The linked BigQuery dataset has no completed daily event tables."
        )

    available_start = daily_tables[0].removeprefix("events_")
    available_end = daily_tables[-1].removeprefix("events_")
    requested_start = start_date.strftime("%Y%m%d")
    requested_end = end_date.strftime("%Y%m%d")
    query_start = max(requested_start, available_start)
    query_end = min(requested_end, available_end)
    if query_start > query_end:
        raise BigQueryExportUnavailable(
            "The selected dates are outside the BigQuery export history "
            f"({available_start} to {available_end})."
        )

    effective_start = date.fromisoformat(
        f"{query_start[:4]}-{query_start[4:6]}-{query_start[6:]}"
    )
    effective_end = date.fromisoformat(
        f"{query_end[:4]}-{query_end[4:6]}-{query_end[6:]}"
    )
    key_event_names = list_key_event_names(creds, property_id)
    table_pattern = f"{project_id}.{dataset_id}.events_*"
    parameters = _date_parameters(effective_start, effective_end)
    parameters.append(
        bigquery.ArrayQueryParameter(
            "key_event_names", "STRING", key_event_names
        )
    )

    datasets = {
        "bq_sessions": _query_rows(
            client, _sessions_query(table_pattern), parameters
        ),
        "bq_conversions": _query_rows(
            client, _conversions_query(table_pattern), parameters
        ),
        "bq_page_performance": _query_rows(
            client, _pages_query(table_pattern), parameters
        ),
        "bq_users": _query_rows(
            client, _users_query(table_pattern), parameters
        ),
        "bq_ordered_funnel": _query_rows(
          client, _funnel_query(table_pattern), parameters
        ),
        "bq_form_start_paths": _query_rows(
          client, _form_paths_query(table_pattern), parameters
        ),
    }

    return {
        "datasets": datasets,
        "metadata": {
            "source": "GA4 BigQuery event export",
            "project": project_id,
            "dataset": dataset_id,
            "location": location,
            "requested_start": str(start_date),
            "requested_end": str(end_date),
            "extracted_start": str(effective_start),
            "extracted_end": str(effective_end),
            "available_start": available_start,
            "available_end": available_end,
            "key_event_names": key_event_names,
            "funnel_configuration": {
              "scope": "same session",
              "funnel_type": "closed",
              "step_order": "direct or indirect, timestamp ordered",
              "maximum_time_between_steps": "session boundary",
              "steps": [
                "session_start", "form_start", "form_submit",
                "new_registration",
              ],
              "breakdowns": [
                "landing_page", "device_category", "visitor_type",
              ],
              "breakdown_attribution": (
                "Landing page and device use the first applicable value "
                "in the session."
              ),
              "page_level_segmentation": True,
            },
            "row_counts": {
                name: len(rows) for name, rows in datasets.items()
            },
            "complete_requested_range": (
                query_start == requested_start and query_end == requested_end
            ),
            "daily_export_enabled": link.get("dailyExportEnabled"),
            "streaming_export_enabled": link.get(
                "streamingExportEnabled"
            ),
        },
    }


def enrich_payload_with_bigquery(base_payload: dict, bigquery_payload: dict) -> dict:
    """Merge BigQuery transformations into the report and Excel payload."""
    bigquery_datasets = bigquery_payload.get("datasets", {})
    bigquery_metadata = bigquery_payload.get("metadata", {})
    base_payload.setdefault("datasets", {}).update(bigquery_datasets)
    extraction_metadata = base_payload.setdefault("extraction_metadata", {})
    dataset_row_counts = extraction_metadata.setdefault(
        "dataset_row_counts", {}
    )
    dataset_row_counts.update(
        {
            name: {
                "api_row_count": len(rows),
                "extracted_row_count": len(rows),
                "complete": True,
            }
            for name, rows in bigquery_datasets.items()
        }
    )
    base_payload["bigquery_extraction"] = bigquery_metadata
    base_payload["data_source"] = "GA4 Data API + BigQuery event export"
    return base_payload