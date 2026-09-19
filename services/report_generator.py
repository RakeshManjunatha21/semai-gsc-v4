"""
SEMAI Analytics Intelligence Platform - Report Generator.

Pure-Python class that wraps all Gemini-powered report generation.
No Streamlit dependency.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from config import MODEL
from prompts import (
    ACTION_REPORT_PROMPT,
    CLUSTER_AUDIT_PROMPT,
    COMPARISON_PROMPT,
    DEEP_AUDIT_PROMPT,
    GA4_AUDIT_PROMPT,
)


class ReportGenerator:
    """Generates AI-powered reports using the configured Gemini model.

    All public methods accept structured data dictionaries and return
    the generated markdown report as a string.

    Raises:
        RuntimeError: If the Gemini model is not configured.
    """

    def __init__(self, model=None):
        """Initialise with an optional override *model*.

        Args:
            model: A ``google.generativeai.GenerativeModel`` instance.
                   Defaults to the globally configured ``MODEL``.
        """
        self._model = model or MODEL
        if self._model is None:
            raise RuntimeError(
                "Gemini model is not configured. "
                "Ensure GEMINI_API_KEY is set."
            )

    # -----------------------------------------------------------------
    # GSC Reports
    # -----------------------------------------------------------------

    def generate_deep_audit(self, payload: dict) -> str:
        """Generate a Deep Audit Report from GSC data.

        Args:
            payload: Structured GSC payload from
                     ``services.gsc.extract_payload``.

        Returns:
            Markdown report string.
        """
        prompt_text = f"""
{DEEP_AUDIT_PROMPT}

--- ACTUAL GSC DATA TO ANALYZE ---

{json.dumps(payload, indent=2)}

--- BEGIN ANALYSIS NOW ---

Analyze the GSC data above and generate the complete executive report immediately.
If the data shows "No GSC data returned" or has minimal metrics, provide the empty data guidance.
Otherwise, generate all 9 sections of the Executive Addendum.

START YOUR RESPONSE NOW:
"""
        return self._model.generate_content(prompt_text).text

    def generate_cluster_audit(self, payload: dict) -> str:
        """Generate a Cluster Audit Report from GSC data.

        Args:
            payload: Structured GSC payload.

        Returns:
            Markdown report string.
        """
        prompt_text = f"""
{CLUSTER_AUDIT_PROMPT}

--- FACTUAL GSC DATA ---

This is the COMPLETE Google Search Console dataset.
Do NOT hallucinate or infer missing data.

{json.dumps(payload, indent=2)}

--- TASK ---

Generate the FULL Cluster Audit Report.
Follow the OUTPUT FORMAT EXACTLY.
Provide actionable, micro-level recommendations.

BEGIN REPORT:
"""
        return self._model.generate_content(prompt_text).text

    def generate_action_report(
        self,
        deep_audit_report: str,
        payload: dict,
    ) -> str:
        """Generate a GSC Action Report from a completed Deep Audit.

        Args:
            deep_audit_report: The markdown Deep Audit report text.
            payload: Raw GSC payload for cross-referencing.

        Returns:
            Markdown report string.
        """
        prompt_text = f"""
{ACTION_REPORT_PROMPT}

--- DEEP AUDIT REPORT (INPUT) ---

Below is the completed Deep Audit Report. Use this as your primary data source
to generate the GSC Action Report.

{deep_audit_report}

--- RAW GSC DATA (REFERENCE) ---

Additional raw GSC data for cross-referencing:

{json.dumps(payload, indent=2)}

--- BEGIN GSC ACTION REPORT NOW ---

Using the Deep Audit Report above and the raw GSC data, generate the complete
GSC Action Report following the template structure EXACTLY.

Focus on:
1. Forensic diagnosis of failure modes (zero-click, CTR mismatch, missing pages)
2. Specific page-level fixes with exact title rewrites
3. Priority matrix (P0-P3) with ROI estimates
4. 30/60/90 day execution plan

START YOUR RESPONSE NOW:
"""
        return self._model.generate_content(prompt_text).text

    def generate_comparison_report(
        self,
        payload1: dict,
        payload2: dict,
        comparison_metrics: dict,
    ) -> str:
        """Generate a Period Comparison Report.

        Args:
            payload1: First-period GSC payload.
            payload2: Second-period GSC payload.
            comparison_metrics: Pre-computed delta metrics from
                ``services.gsc.calculate_comparison_metrics``.

        Returns:
            Markdown report string.
        """
        prompt_text = f"""
{COMPARISON_PROMPT}

--- PERIOD 1 DATA ---

{json.dumps(payload1, indent=2)}

--- PERIOD 2 DATA ---

{json.dumps(payload2, indent=2)}

--- CALCULATED COMPARISON METRICS ---

{json.dumps(comparison_metrics, indent=2)}

--- TASK ---

Generate the FULL Period Comparison Report.
Follow the OUTPUT FORMAT EXACTLY.
Provide data-driven insights and actionable recommendations.

BEGIN COMPARISON REPORT:
"""
        return self._model.generate_content(prompt_text).text

    # -----------------------------------------------------------------
    # GA4 Reports
    # -----------------------------------------------------------------

    @staticmethod
    def _ga4_model_payload(payload: dict) -> dict:
        """Return all unique GA4 evidence without duplicated display views."""
        model_payload = {
            key: value
            for key, value in payload.items()
            if key not in {
                "channel_performance",
                "top_pages",
                "device_breakdown",
                "country_performance",
            }
        }
        model_payload["report_view_mappings"] = {
            "channels": "datasets.traffic_acquisition",
            "pages": "datasets.pages_and_screens",
            "devices": "datasets.devices",
            "countries": "datasets.countries",
        }
        datasets = model_payload.get("datasets", {})
        prior_period = model_payload.get("prior_period", {})
        prior_summary = prior_period.get("summary_metrics", {})
        prior_period_present = bool(prior_summary) and any(
            float(value or 0) != 0 for value in prior_summary.values()
        )
        prior_extraction = model_payload.get(
            "prior_period_extraction", {}
        )
        bigquery_extraction = model_payload.get(
            "bigquery_extraction", {}
        )
        bigquery_datasets = {
            name: rows
            for name, rows in datasets.items()
            if name.startswith("bq_")
        }
        bigquery_rows_present = any(bigquery_datasets.values())
        key_event_configuration = model_payload.get(
            "key_event_configuration", {}
        )
        configured_key_events = key_event_configuration.get(
            "configured_names", []
        )
        model_payload["analysis_scope"] = {
            "type": "ga4_only",
            "excluded_sources": ["Google Search Console"],
        }
        model_payload["analysis_input_manifest"] = {
            "ga4_key_event_configuration": {
                "status": (
                    "api_error"
                    if key_event_configuration.get("error")
                    else "available"
                    if configured_key_events
                    else "configuration_required"
                ),
                "configured_names": configured_key_events,
                "core_funnel_activity": key_event_configuration.get(
                    "core_funnel_activity", {}
                ),
                "error": key_event_configuration.get("error"),
            },
            "input_1_gsc_queries": {
                "present": False,
                "extraction_status": "not_applicable",
                "unavailable_reason": (
                    "Google Search Console is outside this GA4-only report."
                ),
            },
            "input_2_gsc_landing_pages": {
                "present": False,
                "extraction_status": "not_applicable",
                "unavailable_reason": (
                    "Google Search Console is outside this GA4-only report."
                ),
            },
            "input_7_events_report": {
                "dataset": "datasets.events",
                "present": bool(datasets.get("events")),
                "sequential": False,
            },
            "input_12_path_exploration_equivalent": {
                "dataset": "datasets.bq_form_start_paths",
                "present": bool(datasets.get("bq_form_start_paths")),
                "scope": "same-session BigQuery path context",
                "authoritative_for_funnel_rates": False,
                "extraction_status": (
                    "available" if datasets.get("bq_form_start_paths")
                    else bigquery_extraction.get("status", "unavailable")
                ),
                "unavailable_reason": (
                    bigquery_extraction.get("message")
                    if not datasets.get("bq_form_start_paths")
                    else None
                ),
            },
            "input_13_funnel_exploration_equivalent": {
                "dataset": "datasets.bq_ordered_funnel",
                "present": bool(datasets.get("bq_ordered_funnel")),
                "scope": "same-session timestamp-ordered BigQuery funnel",
                "page_level_segmentation": bool(
                    datasets.get("bq_ordered_funnel")
                ),
                "extraction_status": (
                    "available" if datasets.get("bq_ordered_funnel")
                    else bigquery_extraction.get("status", "unavailable")
                ),
                "unavailable_reason": (
                    bigquery_extraction.get("message")
                    if not datasets.get("bq_ordered_funnel")
                    else None
                ),
            },
            "input_14_page_event_free_form_equivalent": {
                "dataset": "datasets.page_event_matrix",
                "present": bool(datasets.get("page_event_matrix")),
                "sequential": False,
                "diagnostic_only": True,
            },
            "device_visitor_event_diagnostic": {
                "dataset": "datasets.funnel_event_segments",
                "present": bool(datasets.get("funnel_event_segments")),
                "sequential": False,
                "diagnostic_only": True,
            },
            "input_15_prior_period": {
                "dataset": "prior_period",
                "present": prior_period_present,
                "date_range": prior_period.get("date_range"),
                "scope": prior_period.get("scope"),
                "extraction_status": prior_extraction.get(
                    "status", "not_selected"
                ),
                "unavailable_reason": prior_extraction.get("message"),
            },
            "input_16_bigquery_event_export": {
                "datasets": {
                    name: len(rows)
                    for name, rows in bigquery_datasets.items()
                },
                "present": bigquery_rows_present,
                "extraction_status": (
                    "available" if bigquery_rows_present
                    else bigquery_extraction.get("status", "unavailable")
                ),
                "unavailable_reason": (
                    bigquery_extraction.get("message")
                    if not bigquery_rows_present else None
                ),
            },
        }
        return model_payload

    def _generate_with_quota_retry(self, prompt_text: str) -> str:
        """Retry one transient Gemini per-minute quota response."""
        try:
            return self._model.generate_content(prompt_text).text
        except Exception as exc:
            if exc.__class__.__name__ != "ResourceExhausted":
                raise

            retry_delay = getattr(exc, "retry_delay", None)
            retry_seconds = getattr(retry_delay, "seconds", None) or 10
            time.sleep(min(max(float(retry_seconds), 1), 15))
            return self._model.generate_content(prompt_text).text

    def generate_ga4_deep_audit(self, payload: dict) -> str:
        """Generate a GA4 Deep Audit Report.

        Args:
            payload: Structured GA4 payload from
                     ``services.ga4.extract_ga4_payload``.

        Returns:
            Markdown report string.
        """
        model_payload = self._ga4_model_payload(payload)
        prompt_text = f"""
{GA4_AUDIT_PROMPT}

--- ACTUAL GA4 DATA TO ANALYZE ---

    {json.dumps(model_payload, separators=(",", ":"))}

--- BEGIN ANALYSIS NOW ---

Analyze the GA4 data above and generate the complete executive report immediately.
If the data shows an error or has minimal metrics, provide the empty data guidance.
Otherwise, generate all sections of the GA4 Deep Audit Report following the template structure.
Include a country-wise performance analysis using datasets.countries, covering
traffic, engagement, key events, and revenue without inventing unavailable values.

GA4-ONLY SCOPE:
This report must use GA4 and its linked BigQuery event export only. Do not
request, analyze, recommend supplying, or create findings from Google Search
Console. Omit inputs 1 and 2 from the input-validation table because they are
outside this report's scope. Never label them Absent or Blocked. Omit
branded/non-branded CTR, search-query clustering, ranking, and
organic-impression conclusions.

STATUS TERMINOLOGY:
"Blocked" means a requested conclusion cannot be computed from the evidence
supplied in this run. It does not mean Google blocked the API request unless
analysis_input_manifest explicitly reports an API or permission error. Every
Blocked label must include one of these causes and the concrete reason:
- Missing selection/data: an optional property or dataset was not supplied.
- No exported rows: the integration exists but returned no usable rows for the
    selected dates.
- Configuration required: tracking or a GA4 setting is not configured.
- External evidence required: the conclusion needs CRM, revenue, target, or
    manually exported attribution evidence outside the available APIs.
- API/permission error: only when the extraction status explicitly says error.
In the input-validation table, use Available, Not available: <exact reason>,
or Not applicable. Never use the bare status Absent. For inputs 12 and 13,
state that the app uses the named BigQuery equivalent and include its
unavailable reason. For input 15, state whether the automatic equal-length
previous-period extraction returned data. For input 16, report the linked
BigQuery export status and row counts.
Use an available BigQuery ordered funnel as the input 13 equivalent and an
available BigQuery event export as input 16. Do not call either absent when its
manifest entry says present.

START YOUR RESPONSE NOW:
"""
        return self._generate_with_quota_retry(prompt_text)

    # -----------------------------------------------------------------
    # File Upload Reports
    # -----------------------------------------------------------------

    def generate_file_deep_audit(
        self,
        df: pd.DataFrame,
        file_list: list[str],
    ) -> str:
        """Generate a Deep Audit Report from uploaded file data.

        Args:
            df: Combined DataFrame from all uploaded files.
            file_list: List of original file names.

        Returns:
            Markdown report string.
        """
        data_summary = {
            "files_uploaded": file_list,
            "total_files": len(file_list),
            "total_rows": len(df),
            "total_columns": len(df.columns),
            "columns": list(df.columns),
            "data_types": {
                col: str(dtype) for col, dtype in df.dtypes.items()
            },
            "missing_values": {
                col: int(df[col].isnull().sum()) for col in df.columns
            },
            "summary_statistics": df.describe().to_dict(),
            "sample_data": df.head(20).to_dict("records"),
        }

        prompt_text = f"""
{DEEP_AUDIT_PROMPT}

--- UPLOADED FILE DATA ANALYSIS ---

You are analyzing data from uploaded files (CSV/Excel).
This is NOT Google Search Console data.
Perform a comprehensive data quality and content audit.

FILES UPLOADED:
{json.dumps(data_summary["files_uploaded"], indent=2)}

DATA STRUCTURE:
- Total Rows: {data_summary["total_rows"]:,}
- Total Columns: {data_summary["total_columns"]}
- Columns: {", ".join(data_summary["columns"])}

DATA SUMMARY:
{json.dumps(data_summary, indent=2)}

--- TASK ---

Analyze this uploaded data and generate a comprehensive Deep Audit Report covering:

1. **Data Quality Assessment**
   - Completeness analysis
   - Data type consistency
   - Missing values analysis
   - Duplicate detection
   - Data integrity issues

2. **Content Analysis**
   - Key patterns and trends
   - Statistical insights
   - Data distribution analysis
   - Outlier detection
   - Correlations between columns

3. **Actionable Recommendations**
   - Data cleaning steps needed
   - Data enrichment opportunities
   - Quality improvement actions
   - Next steps for optimization

Provide specific, evidence-based insights based on the actual data provided.

BEGIN REPORT:
"""
        return self._model.generate_content(prompt_text).text

    def generate_file_cluster_audit(
        self,
        df: pd.DataFrame,
        file_list: list[str],
    ) -> str:
        """Generate a Cluster Audit Report from uploaded file data.

        Args:
            df: Combined DataFrame from all uploaded files.
            file_list: List of original file names.

        Returns:
            Markdown report string.
        """
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        categorical_cols = df.select_dtypes(include=["object"]).columns.tolist()

        data_summary = {
            "files_uploaded": file_list,
            "total_rows": len(df),
            "numeric_columns": numeric_cols,
            "categorical_columns": categorical_cols,
            "numeric_summary": (
                df[numeric_cols].describe().to_dict() if numeric_cols else {}
            ),
            "categorical_summary": {
                col: df[col].value_counts().head(10).to_dict()
                for col in categorical_cols[:5]
            },
            "sample_data": df.head(20).to_dict("records"),
        }

        prompt_text = f"""
{CLUSTER_AUDIT_PROMPT}

--- UPLOADED FILE DATA FOR CLUSTERING ---

You are analyzing data from uploaded files (CSV/Excel) for clustering patterns.
This is NOT Google Search Console data.
Identify natural groupings, patterns, and segments in the data.

FILES UPLOADED:
{json.dumps(data_summary["files_uploaded"], indent=2)}

DATA STRUCTURE:
- Total Rows: {data_summary["total_rows"]:,}
- Numeric Columns: {", ".join(numeric_cols) if numeric_cols else "None"}
- Categorical Columns: {", ".join(categorical_cols[:5]) if categorical_cols else "None"}

DATA SUMMARY:
{json.dumps(data_summary, indent=2)}

--- TASK ---

Analyze this uploaded data and generate a Cluster Audit Report covering:

1. **Cluster Identification**
   - Natural groupings in the data
   - Segment patterns
   - Key differentiators between groups
   - Cluster characteristics

2. **Pattern Analysis**
   - Trends within each cluster
   - Relationships between variables
   - Anomalies and outliers
   - Distribution patterns

3. **Strategic Insights**
   - Cluster-specific recommendations
   - Prioritization framework
   - Action plan for each segment
   - Optimization opportunities

4. **Implementation Roadmap**
   - 7-day action plan
   - 30-day strategic plan
   - Success metrics per cluster

Provide specific, actionable insights based on the actual data patterns.

BEGIN REPORT:
"""
        return self._model.generate_content(prompt_text).text
