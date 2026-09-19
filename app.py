"""
SEMAI Analytics Intelligence Platform - Streamlit UI.

This module contains **only** the Streamlit front-end logic.  All business
logic, authentication, data-fetching, report generation, and export helpers
live in their respective packages (``config``, ``auth``, ``services``).
"""

import json
import zipfile
from datetime import date, timedelta
from io import BytesIO

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Application modules
# ---------------------------------------------------------------------------
from config import (
    ADMIN_PASSWORD,
    GEMINI_API_KEY,
    configure_model,
    ENABLE_TOKEN_PERSISTENCE,
)
from auth.oauth import (
    build_flow,
    delete_user_credentials,
    get_all_saved_users,
    get_user_email,
    load_credentials,
    refresh_credentials,
    save_credentials,
)
from services.gsc import (
    calculate_comparison_metrics,
    extract_payload,
    list_properties,
)
from services.report_generator import ReportGenerator
from services.export import (
    create_ga4_excel_export,
    create_word_document,
    parse_markdown_table,
    process_uploaded_files,
    process_direct_files,
    DOCX_AVAILABLE,
)

# ---------------------------------------------------------------------------
# Gemini model & report generator
# ---------------------------------------------------------------------------
MODEL = configure_model()
if not GEMINI_API_KEY:
    st.error(
        "Gemini API key not found. "
        "Set GOOGLE_GEMINI_KEY or GEMINI_API_KEY via environment variables "
        "or Streamlit Secrets."
    )
    st.stop()

report_gen = ReportGenerator(model=MODEL)

# ═══════════════════════════════════════════════════════════════════════════════
#  SESSION STATE INITIALISATION
# ═══════════════════════════════════════════════════════════════════════════════

_SESSION_DEFAULTS = {
    "authenticated": None,
    "creds": None,
    "auth_error": None,
    "current_user": None,
    "comparison_mode": False,
    "selected_property": None,
    "admin_authenticated": False,
    "last_payload": None,
    "data_source": None,
    "ga_property_id": None,
    "deep_audit_report": None,
    "action_report": None,
    "deep_audit_payload": None,
    "deep_audit_site_url": None,
    "deep_audit_start_date": None,
    "deep_audit_end_date": None,
    "deep_audit_days_diff": None,
    "cluster_audit_report": None,
    "cluster_audit_payload": None,
    "cluster_audit_site_url": None,
    "cluster_audit_start_date": None,
    "cluster_audit_end_date": None,
    "cluster_audit_days_diff": None,
    "ga4_report": None,
    "ga4_property_name": None,
    "ga4_payload": None,
    "ga4_start_date": None,
    "ga4_end_date": None,
    "ga4_days_diff": None,
    "file_deep_report": None,
    "file_cluster_report": None,
    "file_report_metadata": None,
    "file_source_df": None,
    "oauth_code_verifier": None,
    "oauth_state": None,
    "oauth_flow": None,
}

for key, default in _SESSION_DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ═══════════════════════════════════════════════════════════════════════════════
#  UI HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_ga4_service_functions():
    """Import GA4 service helpers lazily to avoid startup-time hard failures."""
    try:
        from services.ga4 import extract_ga4_payload, list_ga4_properties

        return extract_ga4_payload, list_ga4_properties
    except Exception as exc:
        raise RuntimeError(f"Unable to load GA4 service module: {exc}") from exc


def parse_ga4_exploration_upload(uploaded_file) -> list[dict]:
    """Convert a GA4 Explore CSV upload into JSON-safe row dictionaries."""
    dataframe = pd.read_csv(uploaded_file, encoding="utf-8-sig")
    dataframe.columns = [str(column).strip() for column in dataframe.columns]
    return json.loads(dataframe.to_json(orient="records", date_format="iso"))


def login_button():
    """Render the Google sign-in link button."""
    flow = build_flow()

    # For confidential web clients (client_id + client_secret), avoid PKCE
    # challenge generation to prevent callback failures when app sessions
    # restart between auth redirect and token exchange.
    if hasattr(flow, "autogenerate_code_verifier"):
        flow.autogenerate_code_verifier = False
    flow.code_verifier = None

    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    # Persist PKCE/state values so callback token exchange can succeed even
    # when Streamlit reruns the script between redirect and callback.
    st.session_state.oauth_code_verifier = getattr(flow, "code_verifier", None)
    st.session_state.oauth_state = state
    st.session_state.oauth_flow = flow

    st.link_button("Sign in with Google", auth_url)


def handle_callback():
    """Process the OAuth callback and authenticate the user."""
    params = st.query_params
    if "code" not in params:
        return

    auth_code = params.get("code")
    callback_state = params.get("state")
    st.query_params.clear()

    if st.session_state.authenticated:
        return

    try:
        expected_state = st.session_state.oauth_state
        if expected_state and callback_state and callback_state != expected_state:
            raise ValueError("OAuth state mismatch. Please try signing in again.")

        flow = st.session_state.oauth_flow or build_flow()

        if hasattr(flow, "autogenerate_code_verifier"):
            flow.autogenerate_code_verifier = False


        saved_verifier = st.session_state.oauth_code_verifier
        if saved_verifier:
            flow.code_verifier = saved_verifier

        flow.fetch_token(code=auth_code)
        creds = flow.credentials

        user_email = get_user_email(creds)

        if user_email and user_email != "unknown":
            st.session_state.creds = creds
            st.session_state.current_user = user_email
            st.session_state.authenticated = True
            save_credentials(creds, user_email)
            st.session_state.oauth_code_verifier = None
            st.session_state.oauth_state = None
            st.session_state.oauth_flow = None
        else:
            raise ValueError(
                "Unable to retrieve user email. "
                "Please ensure you've granted email access permissions."
            )

    except Exception as exc:
        st.session_state.auth_error = f"Authentication failed: {exc}"
        st.session_state.authenticated = False
        st.session_state.creds = None
        st.session_state.oauth_code_verifier = None
        st.session_state.oauth_state = None
        st.session_state.oauth_flow = None


def render_report_clean(
    report: str,
    container_key: str | None = None,
    skip_first_h1: bool = False,
):
    """Render a markdown report with custom table handling."""
    report_container = st.container(key=container_key) if container_key else st

    with report_container:
        st.markdown('<div class="report-container">', unsafe_allow_html=True)

        lines = report.split("\n")
        i = 0
        first_h1_skipped = False

        while i < len(lines):
            line = lines[i]

            if (
                skip_first_h1
                and not first_h1_skipped
                and line.lstrip().startswith("# ")
            ):
                first_h1_skipped = True
                i += 1
                continue

            # Detect markdown tables
            if "|" in line and i + 1 < len(lines) and "|" in lines[i + 1]:
                table_html, new_idx = parse_markdown_table(lines, i)
                if table_html:
                    st.markdown(table_html, unsafe_allow_html=True)
                    i = new_idx
                    continue

            st.markdown(line)
            i += 1

        st.markdown("</div>", unsafe_allow_html=True)


def render_gsc_source_data(payload: dict, key_prefix: str):
    """Render expandable source data for GSC-based outputs."""
    with st.expander("📂 Source Data (Click to Expand)", expanded=False):
        if not payload:
            st.info("No source data available for this report.")
            return

        st.caption("This report was generated from the source data shown below.")
        st.download_button(
            "📥 Download Source JSON",
            json.dumps(payload, indent=2),
            f"{key_prefix}_source_{date.today().strftime('%Y%m%d')}.json",
            mime="application/json",
            use_container_width=True,
            key=f"{key_prefix}_source_json_dl",
        )

        tab1, tab2, tab3 = st.tabs(["Summary", "Top Data", "Raw JSON"])

        with tab1:
            metrics = payload.get("summary_metrics", {})
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Clicks", f"{metrics.get('total_clicks', 0):,}")
            with col2:
                st.metric("Total Impressions", f"{metrics.get('total_impressions', 0):,}")
            with col3:
                st.metric("Average CTR", f"{metrics.get('avg_ctr', 0):.2%}")
            with col4:
                st.metric("Average Position", f"{metrics.get('avg_position', 0):.1f}")

        with tab2:
            queries_df = pd.DataFrame(payload.get("top_queries_by_impressions", []))
            pages_df = pd.DataFrame(payload.get("top_pages", []))

            st.markdown("#### Top Queries by Impressions")
            if not queries_df.empty:
                st.dataframe(queries_df.head(100), use_container_width=True, height=260)
            else:
                st.info("No query source rows available.")

            st.markdown("#### Top Pages by Impressions")
            if not pages_df.empty:
                st.dataframe(pages_df.head(100), use_container_width=True, height=260)
            else:
                st.info("No page source rows available.")

        with tab3:
            st.json(payload)


def render_ga4_source_data(payload: dict, key_prefix: str):
    """Render expandable source data for GA4 outputs."""
    with st.expander("📂 Source Data (Click to Expand)", expanded=False):
        if not payload:
            st.info("No GA4 source data available for this report.")
            return

        st.caption("This report was generated from the GA4 source data shown below.")
        st.download_button(
            "📥 Download Source JSON",
            json.dumps(payload, indent=2),
            f"{key_prefix}_source_{date.today().strftime('%Y%m%d')}.json",
            mime="application/json",
            use_container_width=True,
            key=f"{key_prefix}_source_json_dl",
        )

        tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
            "Summary",
            "Channels",
            "Top Pages",
            "Countries",
            "Page × Event",
            "Funnel Segments",
            "Raw JSON",
        ])

        with tab1:
            metrics = payload.get("summary_metrics", {})
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Sessions", f"{metrics.get('total_sessions', 0):,}")
            with col2:
                st.metric("Users", f"{metrics.get('total_users', 0):,}")
            with col3:
                st.metric("Engagement Rate", f"{metrics.get('engagement_rate', 0):.2%}")
            with col4:
                st.metric("Conversions", f"{metrics.get('total_conversions', 0):,}")

            devices_df = pd.DataFrame(payload.get("device_breakdown", []))
            if not devices_df.empty:
                st.markdown("#### Device Breakdown")
                st.dataframe(devices_df, use_container_width=True, height=220)

            extraction_metadata = payload.get("extraction_metadata", {})
            report_quality = extraction_metadata.get("report_quality", {})
            thresholded_reports = sorted(
                name for name, quality in report_quality.items()
                if quality.get("subject_to_thresholding", False)
            )
            sampled_reports = sorted(
                name for name, quality in report_quality.items()
                if quality.get("sampling_metadatas", [])
            )
            st.markdown("#### Extraction Quality")
            quality_col1, quality_col2, quality_col3 = st.columns(3)
            quality_col1.metric(
                "All API Rows Retrieved",
                "Yes" if extraction_metadata.get(
                    "all_datasets_complete", False
                ) else "No",
            )
            quality_col2.metric(
                "Sampled Reports",
                len(sampled_reports),
            )
            quality_col3.metric(
                "Thresholded Reports",
                len(thresholded_reports),
            )
            if thresholded_reports:
                st.caption(
                    "Google privacy thresholding applies to: "
                    + ", ".join(thresholded_reports)
                )
            recovery_datasets = extraction_metadata.get(
                "datasets_using_metric_recovery", []
            )
            if recovery_datasets:
                st.caption(
                    "Metrics were fetched separately and merged by dimensions "
                    "for: " + ", ".join(recovery_datasets)
                )
            reconciliation_df = pd.DataFrame(
                extraction_metadata.get("reconciliation", [])
            )
            if not reconciliation_df.empty:
                st.dataframe(
                    reconciliation_df,
                    use_container_width=True,
                    hide_index=True,
                    height=220,
                )

            key_event_configuration = payload.get(
                "key_event_configuration", {}
            )
            key_event_rows = []
            for event_name, activity in key_event_configuration.get(
                "configured_activity", {}
            ).items():
                key_event_rows.append({
                    "event_name": event_name,
                    "configured_as_key_event": True,
                    **activity,
                })
            configured_names = set(
                key_event_configuration.get("configured_names", [])
            )
            for event_name, activity in key_event_configuration.get(
                "core_funnel_activity", {}
            ).items():
                if event_name not in configured_names:
                    key_event_rows.append({
                        "event_name": event_name,
                        **activity,
                    })
            if key_event_rows:
                st.markdown("#### Key Event Configuration")
                st.dataframe(
                    pd.DataFrame(key_event_rows),
                    use_container_width=True,
                    hide_index=True,
                    height=220,
                )

        with tab2:
            channels_df = pd.DataFrame(payload.get("channel_performance", []))
            if not channels_df.empty:
                st.dataframe(channels_df, use_container_width=True, height=350)
            else:
                st.info("No channel source rows available.")

        with tab3:
            pages_df = pd.DataFrame(payload.get("top_pages", []))
            if not pages_df.empty:
                st.dataframe(pages_df, use_container_width=True, height=350)
            else:
                st.info("No page source rows available.")

        with tab4:
            countries_df = pd.DataFrame(payload.get("country_performance", []))
            if not countries_df.empty:
                st.dataframe(countries_df, use_container_width=True, height=350)
            else:
                st.info("No country source rows available.")

        datasets = payload.get("datasets", {})

        with tab5:
            page_events_df = pd.DataFrame(
                datasets.get("page_event_matrix", [])
            )
            if not page_events_df.empty:
                st.caption(
                    "Diagnostic event counts by page. This table does not "
                    "prove session-scoped event order."
                )
                st.dataframe(
                    page_events_df, use_container_width=True, height=420
                )
            else:
                st.info("No page × event source rows available.")

        with tab6:
            ordered_funnel_df = pd.DataFrame(
                datasets.get("bq_ordered_funnel", [])
            )
            if not ordered_funnel_df.empty:
                st.markdown("#### Same-Session Ordered Funnel")
                st.caption(
                    "BigQuery event sequence, broken down by landing page, "
                    "device category, and visitor type."
                )
                st.dataframe(
                    ordered_funnel_df, use_container_width=True, height=320
                )

            funnel_segments_df = pd.DataFrame(
                datasets.get("funnel_event_segments", [])
            )
            if not funnel_segments_df.empty:
                st.markdown("#### Aggregate Event Segments")
                st.caption(
                    "Event counts by device and new/returning visitor. "
                    "These rows are diagnostic and not a sequential funnel."
                )
                st.dataframe(
                    funnel_segments_df, use_container_width=True, height=320
                )
            elif ordered_funnel_df.empty:
                st.info("No funnel segment source rows available.")

        with tab7:
            st.json(payload)


def render_comparison_source_data(
    payload1: dict,
    payload2: dict,
    comparison_metrics: dict,
    key_prefix: str,
):
    """Render expandable source data for comparison outputs."""
    with st.expander("📂 Source Data (Click to Expand)", expanded=False):
        source_data = {
            "period1": payload1,
            "period2": payload2,
            "comparison_metrics": comparison_metrics,
        }

        st.caption("This comparison report was generated from both period datasets and computed deltas.")
        st.download_button(
            "📥 Download Comparison Source JSON",
            json.dumps(source_data, indent=2),
            f"{key_prefix}_source_{date.today().strftime('%Y%m%d')}.json",
            mime="application/json",
            use_container_width=True,
            key=f"{key_prefix}_source_json_dl",
        )

        tab1, tab2, tab3, tab4 = st.tabs([
            "Period 1",
            "Period 2",
            "Computed Metrics",
            "Raw JSON",
        ])

        with tab1:
            st.json(payload1)
        with tab2:
            st.json(payload2)
        with tab3:
            st.json(comparison_metrics)
        with tab4:
            st.json(source_data)


def render_file_source_data(df: pd.DataFrame | None, key_prefix: str):
    """Render expandable source data for file-based outputs."""
    with st.expander("📂 Source Data (Click to Expand)", expanded=False):
        if df is None or df.empty:
            st.info("No processed source rows available for this file report.")
            return

        st.caption("These reports were generated from the processed uploaded dataset below.")
        st.dataframe(df.head(200), use_container_width=True, height=360)

        csv_data = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Download Processed Source CSV",
            csv_data,
            f"{key_prefix}_source_{date.today().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True,
            key=f"{key_prefix}_source_csv_dl",
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE CONFIG & CSS
# ═══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Analytics Intelligence Platform - SEMAI",
    page_icon="S",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    /* Modern Color Palette */
    :root {
        --primary: #4F46E5;
        --primary-dark: #4338CA;
        --secondary: #06B6D4;
        --success: #10B981;
        --danger: #EF4444;
        --dark: #1E293B;
        --light: #F8FAFC;
        --border: #E2E8F0;
    }

    /* Main Container */
    .main {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 0 !important;
    }

    .block-container {
        max-width: 1400px;
        padding: 2rem 3rem !important;
        background: white;
        border-radius: 20px;
        margin: 2rem auto;
        box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
    }

    /* Header Styling */
    .app-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2.5rem;
        border-radius: 16px;
        margin-bottom: 2rem;
        text-align: center;
        box-shadow: 0 10px 30px rgba(102, 126, 234, 0.3);
    }

    .app-title {
        color: white;
        font-size: 2.5rem;
        font-weight: 800;
        margin: 0;
        text-shadow: 0 2px 10px rgba(0,0,0,0.2);
    }

    .app-subtitle {
        color: rgba(255,255,255,0.95);
        font-size: 1.1rem;
        margin-top: 0.5rem;
        font-weight: 400;
    }

    /* Login Page Styling */
    .login-container {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 20px;
        padding: 3rem;
        text-align: center;
        box-shadow: 0 20px 60px rgba(102, 126, 234, 0.4);
        margin: 2rem 0;
    }

    .login-title {
        color: white;
        font-size: 2rem;
        font-weight: 700;
        margin-bottom: 1rem;
    }

    .login-subtitle {
        color: rgba(255,255,255,0.9);
        font-size: 1.1rem;
        margin-bottom: 2rem;
    }

    /* User Card */
    .user-card {
        background: white;
        border: 2px solid var(--border);
        border-radius: 12px;
        padding: 1.5rem;
        text-align: center;
        transition: all 0.3s ease;
        cursor: pointer;
        margin-bottom: 1rem;
    }

    .user-card:hover {
        border-color: var(--primary);
        box-shadow: 0 8px 25px rgba(79, 70, 229, 0.2);
        transform: translateY(-2px);
    }

    .user-icon {
        font-size: 3rem;
        margin-bottom: 0.5rem;
    }

    .user-email {
        color: var(--dark);
        font-weight: 600;
        font-size: 0.95rem;
        word-break: break-all;
    }

    /* Report Container */
    .report-container {
        background: white;
        padding: 2rem;
        border-radius: 12px;
        line-height: 1.8;
    }

    /* Markdown Headers */
    .report-container h1 {
        color: var(--primary);
        font-size: 2.2rem;
        font-weight: 800;
        margin: 2.5rem 0 1.5rem 0;
        padding-bottom: 0.8rem;
        border-bottom: 4px solid var(--primary);
    }

    .report-container h2 {
        color: var(--primary);
        font-size: 1.9rem;
        font-weight: 700;
        margin: 2.2rem 0 1.2rem 0;
        padding-bottom: 0.6rem;
        border-bottom: 3px solid var(--primary);
    }

    .report-container h3 {
        color: #4338CA;
        font-size: 1.5rem;
        font-weight: 600;
        margin: 1.8rem 0 1rem 0;
        padding-left: 0.8rem;
        border-left: 4px solid var(--primary);
    }

    .report-container h4 {
        color: #6366F1;
        font-size: 1.2rem;
        font-weight: 600;
        margin: 1.5rem 0 0.8rem 0;
    }

    /* Markdown Paragraphs */
    .report-container p {
        color: #334155;
        font-size: 1.05rem;
        line-height: 1.8;
        margin: 1rem 0;
    }

    /* Markdown Lists */
    .report-container ul {
        margin: 1rem 0;
        padding-left: 2rem;
    }

    .report-container li {
        color: #475569;
        font-size: 1.02rem;
        line-height: 1.8;
        margin: 0.6rem 0;
        padding-left: 0.5rem;
    }

    .report-container li::marker {
        color: var(--primary);
        font-weight: bold;
    }

    /* Markdown Bold */
    .report-container strong {
        color: var(--primary);
        font-weight: 700;
    }

    /* Markdown Italic */
    .report-container em {
        color: #6366F1;
        font-style: italic;
    }

    /* Markdown Code */
    .report-container code {
        background: #F1F5F9;
        color: #DC2626;
        padding: 0.2rem 0.5rem;
        border-radius: 4px;
        font-family: 'Courier New', monospace;
        font-size: 0.95rem;
    }

    .report-container pre {
        background: #F8FAFC;
        border: 1px solid var(--border);
        border-left: 4px solid var(--primary);
        padding: 1.5rem;
        border-radius: 8px;
        overflow-x: auto;
        margin: 1.5rem 0;
    }

    .report-container pre code {
        background: transparent;
        color: #334155;
        padding: 0;
    }

    /* Markdown Blockquotes */
    .report-container blockquote {
        background: linear-gradient(135deg, #667eea10 0%, #764ba210 100%);
        border-left: 4px solid var(--primary);
        padding: 1.2rem 1.5rem;
        margin: 1.5rem 0;
        border-radius: 8px;
        color: #475569;
        font-style: italic;
    }

    /* Markdown Tables */
    .report-container table,
    .markdown-table {
        width: 100%;
        border-collapse: collapse;
        margin: 1.5rem 0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        border-radius: 8px;
        overflow: hidden;
    }

    .report-container th,
    .markdown-table th {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 1rem;
        text-align: left;
        font-weight: 600;
        font-size: 1rem;
    }

    .report-container td,
    .markdown-table td {
        padding: 0.9rem 1rem;
        border: 1px solid var(--border);
        color: #475569;
        background: white;
        vertical-align: top;
    }

    .report-container tbody tr:nth-child(even) td,
    .markdown-table tbody tr:nth-child(even) td {
        background: #F8FAFC;
    }

    .report-container tbody tr:hover td,
    .markdown-table tbody tr:hover td {
        background: #F1F5F9;
    }

    .markdown-table thead {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    }

    /* Horizontal Rules */
    .report-container hr {
        border: none;
        height: 2px;
        background: linear-gradient(90deg, transparent, var(--border), transparent);
        margin: 2rem 0;
    }

    /* Special Sections */
    .report-container > p:first-of-type {
        font-size: 1.1rem;
        color: #64748B;
        padding: 1rem 1.5rem;
        background: linear-gradient(135deg, #667eea08 0%, #764ba208 100%);
        border-radius: 8px;
        margin: 1.5rem 0;
    }

    /* Metrics Cards */
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 12px;
        color: white;
        text-align: center;
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
    }

    .metric-value {
        font-size: 2rem;
        font-weight: 700;
        margin: 0.5rem 0;
    }

    .metric-label {
        font-size: 0.9rem;
        opacity: 0.95;
    }

    /* Info Box */
    .info-meta {
        background: #F0F9FF;
        border-left: 4px solid var(--secondary);
        padding: 1.5rem;
        border-radius: 8px;
        margin: 1.5rem 0;
    }

    .info-meta b {
        color: var(--primary);
    }

    /* Buttons */
    .stButton > button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        border-radius: 10px;
        padding: 0.75rem 2rem;
        font-weight: 600;
        font-size: 1rem;
        transition: all 0.3s ease;
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
    }

    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
    }

    /* Sidebar Styling */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #667eea 0%, #764ba2 100%);
    }

    [data-testid="stSidebar"] .stMarkdown {
        color: white;
    }

    /* Select Box */
    .stSelectbox {
        margin: 1.5rem 0;
    }

    /* Download Button */
    .stDownloadButton > button {
        background: var(--success) !important;
        border: none;
    }

    .stDownloadButton > button:hover {
        background: #059669 !important;
    }

    /* Divider */
    hr {
        margin: 2rem 0;
        border: none;
        height: 2px;
        background: linear-gradient(90deg, transparent, var(--border), transparent);
    }

    /* Animations */
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
    }

    .block-container > div {
        animation: none;
    }

    /* Professional UI overrides */
    :root {
        --primary: #1D4ED8;
        --primary-dark: #1E40AF;
        --secondary: #475569;
        --success: #166534;
        --danger: #B91C1C;
        --dark: #172033;
        --light: #F8FAFC;
        --border: #D9E0E8;
    }

    .main,
    [data-testid="stAppViewContainer"] {
        background: #F4F6F8;
    }

    .block-container {
        max-width: 1280px;
        padding: 2rem 2.5rem !important;
        margin: 0 auto;
        background: #FFFFFF;
        border-radius: 0;
        box-shadow: none;
    }

    .app-header {
        background: #FFFFFF;
        padding: 1rem 0 1.5rem;
        margin-bottom: 1.5rem;
        text-align: left;
        border: 0;
        border-bottom: 1px solid var(--border);
        border-radius: 0;
        box-shadow: none;
    }

    .app-title {
        color: var(--dark);
        font-size: 2rem;
        font-weight: 700;
        text-shadow: none;
        letter-spacing: 0;
    }

    .app-subtitle {
        color: #64748B;
        font-size: 0.95rem;
        margin-top: 0.35rem;
    }

    .login-container {
        background: #FFFFFF;
        border: 1px solid var(--border);
        border-radius: 6px;
        padding: 2rem;
        margin: 1.5rem 0;
        box-shadow: none;
    }

    .login-title {
        color: var(--dark);
        font-size: 1.5rem;
        margin-bottom: 0.5rem;
    }

    .login-subtitle {
        color: #64748B;
        font-size: 0.95rem;
        margin-bottom: 1rem;
    }

    /* Authentication experience */
    .auth-brand {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        padding: 0.25rem 0 1.5rem;
        border-bottom: 1px solid #DCE3EA;
    }

    .auth-brand-mark {
        display: grid;
        place-items: center;
        width: 2.35rem;
        height: 2.35rem;
        background: #172033;
        color: #FFFFFF;
        border-radius: 5px;
        font-family: Georgia, 'Times New Roman', serif;
        font-size: 1.25rem;
        font-weight: 700;
    }

    .auth-brand-name {
        color: #172033;
        font-size: 0.9rem;
        font-weight: 800;
    }

    .auth-brand-product {
        color: #64748B;
        font-size: 0.78rem;
        margin-top: 0.05rem;
    }

    .st-key-auth-screen {
        position: relative;
        min-height: 610px;
        display: flex;
        align-items: center;
        padding: 3.5rem 0 4rem;
    }

    .st-key-auth-screen::before {
        content: "";
        position: absolute;
        inset: 1.5rem -2.5rem 0;
        z-index: 0;
        background-color: #F8FAFC;
        background-image:
            linear-gradient(#E8EDF3 1px, transparent 1px),
            linear-gradient(90deg, #E8EDF3 1px, transparent 1px);
        background-size: 40px 40px;
        mask-image: linear-gradient(to right, rgba(0,0,0,0.55), transparent 62%);
        pointer-events: none;
    }

    .st-key-auth-screen > div {
        position: relative;
        z-index: 1;
        width: 100%;
    }

    .auth-intro {
        max-width: 650px;
        padding: 1rem 2rem 1rem 0;
    }

    .auth-eyebrow,
    .auth-panel-kicker {
        color: #2563EB;
        font-size: 0.72rem;
        font-weight: 800;
        margin-bottom: 0.85rem;
    }

    .auth-intro h1 {
        max-width: 620px;
        color: #111827;
        font-family: Georgia, 'Times New Roman', serif;
        font-size: 3.25rem;
        font-weight: 700;
        line-height: 1.08;
        margin: 0;
        padding: 0;
        border: 0;
    }

    .auth-intro > p {
        max-width: 590px;
        color: #526075;
        font-size: 1.05rem;
        line-height: 1.7;
        margin: 1.25rem 0 2rem;
    }

    .auth-capabilities {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 0;
        border-top: 1px solid #CBD5E1;
    }

    .auth-capabilities > div {
        display: grid;
        align-content: start;
        min-width: 0;
        padding: 1.2rem 1rem 0 0;
    }

    .auth-capabilities span {
        color: #2563EB;
        font-family: Georgia, 'Times New Roman', serif;
        font-size: 0.8rem;
        font-weight: 700;
        margin-bottom: 0.65rem;
    }

    .auth-capabilities strong {
        color: #172033;
        font-size: 0.88rem;
        line-height: 1.35;
    }

    .auth-capabilities small {
        color: #64748B;
        font-size: 0.76rem;
        line-height: 1.45;
        margin-top: 0.25rem;
    }

    .st-key-auth-panel {
        background: #FFFFFF;
        border: 1px solid #D5DDE7;
        border-top: 4px solid #2563EB;
        border-radius: 6px;
        padding: 2.2rem;
        box-shadow: 0 18px 45px rgba(23, 32, 51, 0.10);
    }

    .auth-panel-heading {
        margin-bottom: 1.5rem;
    }

    .auth-panel-heading h2 {
        color: #172033;
        font-family: Georgia, 'Times New Roman', serif;
        font-size: 1.8rem;
        line-height: 1.2;
        margin: 0;
        padding: 0;
        border: 0;
    }

    .auth-panel-heading p {
        color: #64748B;
        font-size: 0.9rem;
        line-height: 1.55;
        margin: 0.65rem 0 0;
    }

    .auth-field-label {
        color: #334155;
        font-size: 0.82rem;
        font-weight: 700;
        margin: 0 0 0.45rem;
    }

    .st-key-auth-panel .stButton > button,
    .st-key-auth-panel .stLinkButton > a {
        width: 100%;
        min-height: 3rem;
    }

    .st-key-auth-panel .stLinkButton > a {
        justify-content: center;
        background: #2563EB !important;
        color: #FFFFFF !important;
        border-color: #2563EB !important;
    }

    .st-key-auth-panel .stLinkButton > a:hover {
        background: #1D4ED8 !important;
        color: #FFFFFF !important;
        border-color: #1D4ED8 !important;
    }

    .auth-divider {
        display: flex;
        align-items: center;
        gap: 0.8rem;
        color: #94A3B8;
        font-size: 0.75rem;
        margin: 1.2rem 0;
    }

    .auth-divider::before,
    .auth-divider::after {
        content: "";
        flex: 1;
        height: 1px;
        background: #E2E8F0;
    }

    .auth-privacy {
        color: #7C899B;
        font-size: 0.75rem;
        line-height: 1.5;
        text-align: center;
        margin: 1rem 0 0;
    }

    @media (max-width: 900px) {
        .st-key-auth-screen {
            min-height: auto;
            padding: 2.25rem 0 3rem;
        }

        .st-key-auth-screen [data-testid="stHorizontalBlock"] {
            flex-direction: column;
            gap: 1.75rem;
        }

        .st-key-auth-screen [data-testid="column"] {
            width: 100% !important;
            flex: 1 1 auto !important;
        }

        .auth-intro {
            max-width: 100%;
            padding-right: 0;
        }

        .auth-intro h1 {
            font-size: 2.5rem;
        }

        .st-key-auth-panel {
            max-width: none;
        }
    }

    @media (max-width: 560px) {
        .block-container {
            padding: 1.25rem 1rem !important;
        }

        .auth-brand {
            padding-bottom: 1rem;
        }

        .st-key-auth-screen {
            padding-top: 1.75rem;
        }

        .auth-intro h1 {
            font-size: 2.15rem;
        }

        .auth-intro > p {
            font-size: 0.96rem;
            margin-bottom: 1.4rem;
        }

        .auth-capabilities {
            grid-template-columns: 1fr;
        }

        .auth-capabilities > div {
            grid-template-columns: 2rem 1fr;
            padding-top: 0.85rem;
        }

        .auth-capabilities span {
            grid-row: 1 / span 2;
        }

        .st-key-auth-panel {
            padding: 1.5rem 1.25rem;
        }
    }

    [data-testid="stSidebar"] {
        background: #172033;
        border-right: 1px solid #293449;
    }

    [data-testid="stSidebar"] .stButton > button {
        background: transparent !important;
        color: #E2E8F0 !important;
        border-color: #475569 !important;
    }

    [data-testid="stSidebar"] .stButton > button:hover {
        background: #253047 !important;
        border-color: #64748B !important;
    }

    .stButton > button,
    .stDownloadButton > button,
    .stLinkButton > a {
        min-height: 2.65rem;
        background: #FFFFFF !important;
        color: #243047 !important;
        border: 1px solid #CBD5E1 !important;
        border-radius: 5px !important;
        padding: 0.6rem 1rem !important;
        font-size: 0.95rem !important;
        font-weight: 600 !important;
        box-shadow: none !important;
        transition: border-color 120ms ease, background 120ms ease !important;
    }

    .stButton > button[kind="primary"] {
        background: var(--primary) !important;
        color: #FFFFFF !important;
        border-color: var(--primary) !important;
    }

    .stButton > button:hover,
    .stDownloadButton > button:hover,
    .stLinkButton > a:hover {
        background: #F8FAFC !important;
        color: #172033 !important;
        border-color: #94A3B8 !important;
        transform: none !important;
        box-shadow: none !important;
    }

    .stButton > button[kind="primary"]:hover {
        background: var(--primary-dark) !important;
        color: #FFFFFF !important;
        border-color: var(--primary-dark) !important;
    }

    .report-container {
        padding: 0;
        border-radius: 0;
        line-height: 1.65;
    }

    .report-container h1,
    .report-container h2 {
        color: var(--dark);
        border-bottom: 1px solid var(--border);
    }

    .report-container h1 { font-size: 1.75rem; border-bottom-width: 1px; }
    .report-container h2 { font-size: 1.45rem; border-bottom-width: 1px; }
    .report-container h3 {
        color: #243047;
        font-size: 1.2rem;
        border-left: 3px solid var(--primary);
    }
    .report-container h4 { color: #334155; }
    .report-container strong { color: #172033; }
    .report-container em { color: #475569; }

    .report-container table,
    .markdown-table {
        border: 1px solid var(--border);
        border-radius: 4px;
        box-shadow: none;
    }

    .report-container th,
    .markdown-table th,
    .markdown-table thead {
        background: #243047;
        color: #FFFFFF;
    }

    .metric-card,
    .report-container blockquote,
    .report-container > p:first-of-type {
        background: #F8FAFC;
        color: #243047;
        border: 1px solid var(--border);
        border-radius: 5px;
        box-shadow: none;
    }

    .info-meta {
        background: #F8FAFC;
        border: 1px solid var(--border);
        border-left: 3px solid var(--primary);
        border-radius: 4px;
    }

    .report-title {
        color: var(--dark);
        font-size: 1.6rem;
        font-weight: 700;
        text-align: left;
        margin: 2rem 0 1rem;
        letter-spacing: 0;
    }

    /* GA4 executive report */
    .ga4-report-header {
        position: relative;
        overflow: hidden;
        background: #172033;
        color: #FFFFFF;
        border-left: 5px solid #3B82F6;
        border-radius: 6px;
        padding: 1.75rem 2rem;
        margin: 0 0 1rem;
    }

    .ga4-report-header::after {
        content: "";
        position: absolute;
        top: 0;
        right: 0;
        width: 26%;
        height: 100%;
        background: repeating-linear-gradient(
            135deg,
            rgba(255, 255, 255, 0.035) 0,
            rgba(255, 255, 255, 0.035) 1px,
            transparent 1px,
            transparent 12px
        );
        pointer-events: none;
    }

    .ga4-report-eyebrow {
        color: #93C5FD;
        font-size: 0.75rem;
        font-weight: 700;
        margin-bottom: 0.55rem;
    }

    .ga4-report-header h2 {
        position: relative;
        z-index: 1;
        color: #FFFFFF;
        font-size: 1.85rem;
        line-height: 1.2;
        margin: 0;
        padding: 0;
        border: 0;
    }

    .ga4-report-header p {
        position: relative;
        z-index: 1;
        color: #CBD5E1;
        font-size: 0.95rem;
        margin: 0.6rem 0 0;
    }

    .ga4-report-header + .info-meta {
        margin-top: 0;
    }

    .info-meta > div > div {
        min-width: 0;
        padding-right: 1rem;
        border-right: 1px solid #E2E8F0;
    }

    .info-meta > div > div:last-child {
        padding-right: 0;
        border-right: 0;
    }

    .st-key-ga4-report {
        margin-top: 2.25rem;
        padding: 0 0 1.5rem;
        border-top: 1px solid #CBD5E1;
        counter-reset: ga4-section;
    }

    .st-key-ga4-report .report-container {
        display: none;
    }

    .st-key-ga4-report [data-testid="stMarkdownContainer"] {
        max-width: 100%;
    }

    .st-key-ga4-report h1 {
        color: #172033;
        font-size: 1.8rem;
        line-height: 1.25;
        margin: 2rem 0 0.75rem;
        padding: 0 0 0.8rem;
        border-bottom: 2px solid #172033;
    }

    .st-key-ga4-report h2 {
        color: #172033;
        font-size: 1.4rem;
        line-height: 1.35;
        margin: 2.75rem 0 1rem;
        padding: 0 0 0.7rem 1rem;
        border-bottom: 1px solid #CBD5E1;
        border-left: 4px solid #2563EB;
    }

    .st-key-ga4-report h3 {
        color: #243047;
        font-size: 1.12rem;
        line-height: 1.4;
        margin: 2rem 0 0.8rem;
        padding: 0;
        border: 0;
    }

    .st-key-ga4-report h4 {
        color: #334155;
        font-size: 1rem;
        margin: 1.5rem 0 0.6rem;
    }

    .st-key-ga4-report p,
    .st-key-ga4-report li {
        color: #3F4B5F;
        font-size: 0.98rem;
        line-height: 1.75;
    }

    .st-key-ga4-report p {
        margin: 0.65rem 0;
    }

    .st-key-ga4-report ul,
    .st-key-ga4-report ol {
        margin: 0.75rem 0 1.25rem;
        padding-left: 1.4rem;
    }

    .st-key-ga4-report li {
        margin: 0.4rem 0;
        padding-left: 0.25rem;
    }

    .st-key-ga4-report li::marker {
        color: #2563EB;
        font-weight: 700;
    }

    .st-key-ga4-report strong {
        color: #172033;
        font-weight: 700;
    }

    .st-key-ga4-report blockquote {
        background: #F1F5F9;
        color: #334155;
        border: 0;
        border-left: 4px solid #2563EB;
        border-radius: 0 4px 4px 0;
        padding: 1rem 1.2rem;
        margin: 1.25rem 0;
        font-style: normal;
    }

    .st-key-ga4-report table,
    .st-key-ga4-report .markdown-table {
        width: 100%;
        margin: 1rem 0 1.75rem;
        border: 1px solid #CBD5E1;
        border-radius: 4px;
        box-shadow: none;
        font-size: 0.88rem;
    }

    .st-key-ga4-report th,
    .st-key-ga4-report .markdown-table th {
        background: #243047;
        color: #FFFFFF;
        padding: 0.8rem 0.85rem;
        font-size: 0.82rem;
        line-height: 1.4;
        vertical-align: bottom;
    }

    .st-key-ga4-report td,
    .st-key-ga4-report .markdown-table td {
        color: #3F4B5F;
        padding: 0.75rem 0.85rem;
        border-color: #E2E8F0;
        line-height: 1.5;
    }

    .st-key-ga4-report tbody tr:nth-child(even) td,
    .st-key-ga4-report .markdown-table tbody tr:nth-child(even) td {
        background: #F8FAFC;
    }

    .st-key-ga4-report tbody tr:hover td,
    .st-key-ga4-report .markdown-table tbody tr:hover td {
        background: #EFF6FF;
    }

    .st-key-ga4-report code {
        background: #EEF2F7;
        color: #9F1239;
        border-radius: 3px;
        padding: 0.15rem 0.35rem;
        font-size: 0.88em;
    }

    .st-key-ga4-report hr {
        height: 1px;
        margin: 2rem 0;
        background: #CBD5E1;
    }

    @media (max-width: 768px) {
        .ga4-report-header {
            padding: 1.35rem 1.2rem;
        }

        .ga4-report-header h2 {
            font-size: 1.5rem;
        }

        .info-meta > div {
            grid-template-columns: 1fr !important;
            gap: 0.85rem !important;
        }

        .info-meta > div > div {
            padding: 0 0 0.85rem;
            border-right: 0;
            border-bottom: 1px solid #E2E8F0;
        }

        .info-meta > div > div:last-child {
            padding-bottom: 0;
            border-bottom: 0;
        }

        .st-key-ga4-report h1 {
            font-size: 1.5rem;
        }

        .st-key-ga4-report h2 {
            font-size: 1.22rem;
            margin-top: 2.25rem;
            padding-left: 0.75rem;
        }

        .st-key-ga4-report [data-testid="stMarkdownContainer"] {
            overflow-x: auto;
        }

        .st-key-ga4-report table,
        .st-key-ga4-report .markdown-table {
            min-width: 680px;
        }
    }

    [data-testid="stMetric"] {
        background: #FFFFFF;
        border: 1px solid var(--border);
        border-radius: 5px;
        padding: 0.85rem 1rem;
    }

    div[data-baseweb="select"] > div,
    div[data-baseweb="input"] > div,
    [data-testid="stFileUploaderDropzone"] {
        border-radius: 5px !important;
    }

    hr {
        height: 1px;
        background: var(--border);
    }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  ADMIN PAGE (checked BEFORE auth)
# ═══════════════════════════════════════════════════════════════════════════════

params = st.query_params
if params.get("page") in ["admin", "semaiadmin"]:
    st.markdown("""
    <div class="app-header">
        <h1 class="app-title">Admin Dashboard</h1>
        <p class="app-subtitle">GSC Data Monitoring & Export</p>
    </div>
    """, unsafe_allow_html=True)

    if not st.session_state.admin_authenticated:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown("### Admin Authentication")
            admin_password = st.text_input(
                "Enter Admin Password",
                type="password",
                key="admin_login",
            )

            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                if st.button("Sign In", use_container_width=True, type="primary"):
                    if admin_password == ADMIN_PASSWORD:
                        st.session_state.admin_authenticated = True
                        st.rerun()
                    else:
                        st.error("Invalid password")

            with col_btn2:
                if st.button("Back to App", use_container_width=True):
                    st.query_params.clear()
                    st.rerun()
        st.stop()
    else:
        # ----- Admin authenticated -----
        col1, col2 = st.columns([3, 1])
        with col1:
            st.success("Admin access granted")
        with col2:
            if st.button("Sign Out", use_container_width=True):
                st.session_state.admin_authenticated = False
                st.rerun()

        st.markdown("")

        if st.session_state.last_payload:
            payload_data = st.session_state.last_payload
            is_comparison = payload_data.get("comparison_mode", False)

            if is_comparison:
                # ---------- Comparison admin view ----------
                st.markdown("### Period Comparison Data")

                st.markdown("#### Period 1 Data")
                p1_data = payload_data["period1"]
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total Clicks", f"{p1_data.get('summary_metrics', {}).get('total_clicks', 0):,}")
                with col2:
                    st.metric("Total Impressions", f"{p1_data.get('summary_metrics', {}).get('total_impressions', 0):,}")
                with col3:
                    st.metric("Avg CTR", f"{p1_data.get('summary_metrics', {}).get('avg_ctr', 0):.2%}")
                with col4:
                    st.metric("Avg Position", f"{p1_data.get('summary_metrics', {}).get('avg_position', 0):.1f}")

                with st.expander("View Period 1 JSON"):
                    st.json(p1_data)

                st.markdown("")
                st.markdown("#### Period 2 Data")
                p2_data = payload_data["period2"]
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total Clicks", f"{p2_data.get('summary_metrics', {}).get('total_clicks', 0):,}")
                with col2:
                    st.metric("Total Impressions", f"{p2_data.get('summary_metrics', {}).get('total_impressions', 0):,}")
                with col3:
                    st.metric("Avg CTR", f"{p2_data.get('summary_metrics', {}).get('avg_ctr', 0):.2%}")
                with col4:
                    st.metric("Avg Position", f"{p2_data.get('summary_metrics', {}).get('avg_position', 0):.1f}")

                with st.expander("View Period 2 JSON"):
                    st.json(p2_data)

                st.markdown("")
                st.divider()

                st.markdown("### Download Options")
                col1, col2, col3 = st.columns(3)

                with col1:
                    st.download_button(
                        "Download JSON",
                        json.dumps(payload_data, indent=2),
                        f"gsc_comparison_{date.today().strftime('%Y%m%d')}.json",
                        mime="application/json",
                        use_container_width=True,
                    )

                with col2:
                    try:
                        df_p1 = pd.DataFrame(p1_data.get("top_queries_by_impressions", []))
                        excel_buffer = BytesIO()
                        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                            df_p1.to_excel(writer, sheet_name="Period 1 Queries", index=False)
                            pd.DataFrame(p2_data.get("top_queries_by_impressions", [])).to_excel(
                                writer, sheet_name="Period 2 Queries", index=False
                            )
                        excel_buffer.seek(0)

                        st.download_button(
                            "Download Excel",
                            excel_buffer,
                            f"gsc_comparison_{date.today().strftime('%Y%m%d')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                        )
                    except Exception as exc:
                        st.error(f"Excel export error: {exc}")

                with col3:
                    if st.button("Back to App", use_container_width=True, type="primary", key="admin_back_comparison"):
                        st.query_params.clear()
                        st.rerun()

            else:
                # ---------- Single-period admin view ----------
                st.markdown("### GSC Extraction Data")

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.markdown("""
                    <div class="metric-card">
                        <div class="metric-label">Property</div>
                        <div class="metric-value" style="font-size: 1.2rem;">{}</div>
                    </div>
                    """.format(payload_data.get("site_url", "N/A")), unsafe_allow_html=True)

                with col2:
                    date_range = payload_data.get("date_range", {})
                    st.markdown("""
                    <div class="metric-card">
                        <div class="metric-label">Date Range</div>
                        <div class="metric-value" style="font-size: 1rem;">{} - {}</div>
                    </div>
                    """.format(date_range.get("start", "N/A"), date_range.get("end", "N/A")), unsafe_allow_html=True)

                with col3:
                    st.markdown("""
                    <div class="metric-card">
                        <div class="metric-label">Total Queries</div>
                        <div class="metric-value">{:,}</div>
                    </div>
                    """.format(len(payload_data.get("top_queries_by_impressions", []))), unsafe_allow_html=True)

                st.markdown("")
                st.markdown("### Summary Metrics")
                metrics = payload_data.get("summary_metrics", {})
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total Clicks", f"{metrics.get('total_clicks', 0):,}")
                with col2:
                    st.metric("Total Impressions", f"{metrics.get('total_impressions', 0):,}")
                with col3:
                    st.metric("Average CTR", f"{metrics.get('avg_ctr', 0):.2%}")
                with col4:
                    st.metric("Average Position", f"{metrics.get('avg_position', 0):.1f}")

                st.markdown("")
                st.divider()

                tab1, tab2, tab3 = st.tabs(["Top Queries", "Top Pages", "Full JSON"])

                with tab1:
                    st.markdown("#### Top Queries by Impressions")
                    queries_df = pd.DataFrame(payload_data.get("top_queries_by_impressions", []))
                    if not queries_df.empty:
                        st.dataframe(queries_df, use_container_width=True, height=400)
                    else:
                        st.info("No query data available")

                with tab2:
                    st.markdown("#### Top Pages by Impressions")
                    pages_df = pd.DataFrame(payload_data.get("top_pages", []))
                    if not pages_df.empty:
                        st.dataframe(pages_df, use_container_width=True, height=400)
                    else:
                        st.info("No page data available")

                with tab3:
                    st.json(payload_data)

                st.markdown("")
                st.divider()

                st.markdown("### Download Options")
                col1, col2, col3 = st.columns(3)

                with col1:
                    st.download_button(
                        "Download JSON",
                        json.dumps(payload_data, indent=2),
                        f"gsc_data_{date.today().strftime('%Y%m%d')}.json",
                        mime="application/json",
                        use_container_width=True,
                        key="admin_json_download",
                    )

                with col2:
                    try:
                        excel_buffer = BytesIO()
                        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                            queries_df = pd.DataFrame(payload_data.get("top_queries_by_impressions", []))
                            if not queries_df.empty:
                                queries_df.to_excel(writer, sheet_name="Queries by Impressions", index=False)

                            clicks_df = pd.DataFrame(payload_data.get("top_queries_by_clicks", []))
                            if not clicks_df.empty:
                                clicks_df.to_excel(writer, sheet_name="Queries by Clicks", index=False)

                            pages_df = pd.DataFrame(payload_data.get("top_pages", []))
                            if not pages_df.empty:
                                pages_df.to_excel(writer, sheet_name="Top Pages", index=False)

                            summary_df = pd.DataFrame([payload_data.get("summary_metrics", {})])
                            summary_df.to_excel(writer, sheet_name="Summary", index=False)

                        excel_buffer.seek(0)

                        st.download_button(
                            "Download Excel",
                            excel_buffer,
                            f"gsc_data_{date.today().strftime('%Y%m%d')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                            key="admin_excel_download",
                        )
                    except Exception as exc:
                        st.error(f"Excel export error: {exc}")

                with col3:
                    if st.button("Back to App", use_container_width=True, type="primary", key="admin_back_single"):
                        st.query_params.clear()
                        st.rerun()
        else:
            st.info("📭 No GSC data available yet. Run an analysis from the main app first.")
            if st.button("Go to App", use_container_width=True, type="primary", key="admin_go_app"):
                st.query_params.clear()
                st.rerun()

        st.stop()


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTHENTICATION FLOW
# ═══════════════════════════════════════════════════════════════════════════════

if not st.session_state.authenticated:
    handle_callback()

if not st.session_state.authenticated:
    saved_users = get_all_saved_users()

    st.markdown("""
    <header class="auth-brand">
        <div class="auth-brand-mark">S</div>
        <div>
            <div class="auth-brand-name">SEMAI</div>
            <div class="auth-brand-product">Analytics Intelligence</div>
        </div>
    </header>
    """, unsafe_allow_html=True)

    with st.container(key="auth-screen"):
        intro_col, access_col = st.columns([1.15, 0.85], gap="large")

        with intro_col:
            st.markdown("""
            <section class="auth-intro">
                <div class="auth-eyebrow">DECISIONS, BACKED BY EVIDENCE</div>
                <h1>See what your analytics are really saying.</h1>
                <p>Bring Search Console and GA4 into one rigorous audit workflow, built for clear priorities and defensible action.</p>
                <div class="auth-capabilities">
                    <div><span>01</span><strong>Complete extraction</strong><small>Paginated data with quality checks</small></div>
                    <div><span>02</span><strong>Forensic analysis</strong><small>SEO, GEO, AEO, and conversion evidence</small></div>
                    <div><span>03</span><strong>Action-ready reporting</strong><small>Prioritized findings with transparent limits</small></div>
                </div>
            </section>
            """, unsafe_allow_html=True)

        with access_col:
            with st.container(key="auth-panel"):
                st.markdown("""
                <div class="auth-panel-heading">
                    <div class="auth-panel-kicker">SECURE ACCESS</div>
                    <h2>Welcome back</h2>
                    <p>Continue with the Google account connected to your analytics properties.</p>
                </div>
                """, unsafe_allow_html=True)

                if st.session_state.auth_error:
                    st.error(st.session_state.auth_error)
                    st.info("Please sign in again to continue.")
                    st.session_state.auth_error = None

                if ENABLE_TOKEN_PERSISTENCE:
                    st.markdown('<div class="auth-field-label">Saved account</div>', unsafe_allow_html=True)
                    login_email = st.text_input(
                        "Gmail Address",
                        placeholder="name@company.com",
                        key="login_email_input",
                        label_visibility="collapsed",
                    )

                    if st.button("Continue with saved session", use_container_width=True, type="primary"):
                        if login_email:
                            login_email = login_email.strip().lower()
                            if login_email in [u.lower() for u in saved_users]:
                                matched_user = next(
                                    (u for u in saved_users if u.lower() == login_email), None
                                )
                                if matched_user:
                                    creds = load_credentials(matched_user)
                                    if creds:
                                        if creds.valid:
                                            st.session_state.creds = creds
                                            st.session_state.current_user = matched_user
                                            st.session_state.authenticated = True
                                            st.rerun()
                                        elif creds.expired and creds.refresh_token:
                                            refreshed_creds = refresh_credentials(creds, matched_user)
                                            if refreshed_creds:
                                                st.session_state.creds = refreshed_creds
                                                st.session_state.current_user = matched_user
                                                st.session_state.authenticated = True
                                                st.rerun()
                                            else:
                                                st.error("Session expired. Please sign in with Google again.")
                                                st.session_state.auth_error = "Session expired for this account."
                                        else:
                                            st.error("Session expired. Please sign in with Google again.")
                                    else:
                                        st.error("Failed to load credentials. Please sign in with Google.")
                                else:
                                    st.warning("No saved session found for this email. Please sign in with Google first.")
                        else:
                            st.warning("Please enter your Gmail address.")

                    st.markdown('<div class="auth-divider"><span>or</span></div>', unsafe_allow_html=True)

                login_button()
                st.markdown("""
                <p class="auth-privacy">Google OAuth provides secure, account-scoped access to your analytics data.</p>
                """, unsafe_allow_html=True)

    st.stop()


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN DASHBOARD (user is authenticated)
# ═══════════════════════════════════════════════════════════════════════════════

creds = st.session_state.creds

st.markdown("""
<div class="app-header">
    <h1 class="app-title">Analytics Intelligence Platform</h1>
    <p class="app-subtitle">Advanced SEO, GEO & AEO Analytics Powered by SEMAI</p>
</div>
""", unsafe_allow_html=True)

# ---- Sidebar ----
with st.sidebar:
    st.markdown("### Account")
    if st.session_state.current_user:
        st.markdown(f"""
        <div style='background: rgba(255,255,255,0.1); padding: 1rem; border-radius: 8px; margin: 1rem 0;'>
            <div style='color: white; font-size: 0.85rem; opacity: 0.9;'>Signed in as:</div>
            <div style='color: white; font-weight: 600; margin-top: 0.3rem; word-break: break-all;'>{st.session_state.current_user}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("")

    if st.button("Switch Account", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.creds = None
        st.session_state.current_user = None
        st.session_state.auth_error = None
        st.session_state.data_source = None
        st.rerun()

    if st.button("Sign Out", use_container_width=True):
        if st.session_state.current_user:
            delete_user_credentials(st.session_state.current_user)
        st.session_state.authenticated = False
        st.session_state.creds = None
        st.session_state.current_user = None
        st.session_state.auth_error = None
        st.session_state.data_source = None
        st.rerun()

    st.divider()
    st.markdown("### About")
    st.markdown("""
    <div style='color: rgba(255,255,255,0.9); font-size: 0.85rem; line-height: 1.6;'>
    This platform provides comprehensive analytics using Google Search Console or Google Analytics 4 data, powered by SEMAI AI-driven insights.
    </div>
    """, unsafe_allow_html=True)
    st.markdown("")
    st.markdown("")

# ═══════════════════════════════════════════════════════════════════════════════
#  DATA SOURCE SELECTION
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown("### Select Data Source")
st.markdown("Choose which Google service you want to analyze:")

col_src1, col_src2 = st.columns(2)

with col_src1:
    gsc_selected = st.button(
        "Google Search Console",
        use_container_width=True,
        type="primary" if st.session_state.data_source == "GSC" else "secondary",
        help="Analyze search performance, queries, and rankings",
    )
    if gsc_selected:
        st.session_state.data_source = "GSC"
        st.rerun()

with col_src2:
    ga_selected = st.button(
        "Google Analytics 4",
        use_container_width=True,
        type="primary" if st.session_state.data_source == "GA" else "secondary",
        help="Analyze website traffic, user behavior, and conversions",
    )
    if ga_selected:
        st.session_state.data_source = "GA"
        st.rerun()

if st.session_state.data_source:
    source_name = (
        "Google Search Console"
        if st.session_state.data_source == "GSC"
        else "Google Analytics 4"
    )
    st.success(f"Current data source: **{source_name}**")

    if st.button("Change Data Source", use_container_width=False):
        st.session_state.data_source = None
        st.rerun()
else:
    st.info("Select a data source to continue.")
    st.stop()

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
#  GOOGLE ANALYTICS 4 FLOW
# ═══════════════════════════════════════════════════════════════════════════════

if st.session_state.data_source == "GA":
    st.markdown("### Google Analytics 4 Analysis")

    try:
        extract_ga4_payload, list_ga4_properties = get_ga4_service_functions()
    except RuntimeError as exc:
        st.error(str(exc))
        st.info(
            "GA4 dependencies may have failed to load in the current deployment. "
            "Please check Streamlit Cloud logs and ensure GA4 packages are installed."
        )
        st.stop()

    with st.spinner("Loading GA4 properties..."):
        ga_properties = list_ga4_properties(creds)

    if not ga_properties:
        st.warning("No GA4 properties found or unable to access GA4 API.")
        st.info("""
        **Possible reasons:**
        1. You don't have any GA4 properties linked to this account
        2. The Google Analytics API might not be enabled in your Google Cloud Project
        3. You may need to re-authenticate with the required permissions

        **To fix:**
        - Ensure you have GA4 properties set up in Google Analytics
        - Enable the Google Analytics Data API in your Google Cloud Console
        - Try logging out and logging back in to refresh permissions
        """)
        st.stop()

    property_options = [f"{p['display_name']} ({p['property_id']})" for p in ga_properties]
    property_map = {f"{p['display_name']} ({p['property_id']})": p["property_id"] for p in ga_properties}

    selected_ga_property = st.selectbox(
        "Choose your GA4 property to analyze:",
        property_options,
        key="ga_property_selector",
    )
    ga_property_id = property_map.get(selected_ga_property)

    include_ga_prior_period = st.toggle(
        "Include previous-period benchmark",
        value=True,
        help=(
            "Fetches the immediately preceding date range of equal length "
            "for defensible GA4 trend comparisons."
        ),
        key="ga_include_prior_period",
    )

    st.markdown("### Select Date Range")
    col_date1, col_date2 = st.columns(2)

    with col_date1:
        default_start = date.today() - timedelta(days=30)
        ga_start_date = st.date_input(
            "Start Date",
            value=default_start,
            max_value=date.today(),
            help="Select the start date for analysis",
            key="ga_start_date",
        )

    with col_date2:
        ga_end_date = st.date_input(
            "End Date",
            value=date.today(),
            max_value=date.today(),
            help="Select the end date for analysis",
            key="ga_end_date",
        )

    if ga_start_date > ga_end_date:
        st.error("Start date must be before or equal to end date.")
        st.stop()

    ga_days_diff = (ga_end_date - ga_start_date).days + 1
    st.info(
        f"Analyzing **{ga_days_diff} days** of data from "
        f"**{ga_start_date.strftime('%B %d, %Y')}** to "
        f"**{ga_end_date.strftime('%B %d, %Y')}**"
    )

    st.markdown("### Exploration Evidence")
    path_upload_col, funnel_upload_col = st.columns(2)
    with path_upload_col:
        ga_path_exploration_file = st.file_uploader(
            "Path Exploration CSV",
            type=["csv"],
            help=(
                "Optional. Export the Path Exploration from GA4 Explore as "
                "CSV to include its complete historical output."
            ),
            key="ga_path_exploration_file",
        )
    with funnel_upload_col:
        ga_funnel_exploration_file = st.file_uploader(
            "Funnel Exploration CSV",
            type=["csv"],
            help=(
                "Optional. Upload an exact GA4 Explore funnel definition; "
                "otherwise the standard funnel is collected automatically."
            ),
            key="ga_funnel_exploration_file",
        )

    st.markdown("")

    ga_report_btn = st.button(
        "Generate GA4 Deep Audit Report",
        use_container_width=True,
        type="primary",
        help="Generate a comprehensive GA4 analysis report",
    )

    if ga_report_btn:
        with st.spinner(
            f"Extracting GA4 data from {ga_start_date.strftime('%b %d, %Y')} "
            f"to {ga_end_date.strftime('%b %d, %Y')}..."
        ):
            ga_payload = extract_ga4_payload(creds, ga_property_id, ga_start_date, ga_end_date)

        if "error" in ga_payload:
            st.error(f"Error fetching GA4 data: {ga_payload.get('error')}")
            st.stop()

        if include_ga_prior_period:
            prior_end_date = ga_start_date - timedelta(days=1)
            prior_start_date = prior_end_date - timedelta(
                days=ga_days_diff - 1
            )
            prior_payload = extract_ga4_payload(
                creds,
                ga_property_id,
                prior_start_date,
                prior_end_date,
            )
            prior_summary = prior_payload.get("summary_metrics", {})
            prior_has_data = any(
                float(value or 0) != 0
                for value in prior_summary.values()
            )
            if "error" not in prior_payload and prior_has_data:
                prior_datasets = prior_payload.get("datasets", {})
                ga_payload["prior_period"] = {
                    "date_range": prior_payload.get("date_range", {}),
                    "summary_metrics": prior_summary,
                    "datasets": {
                        name: prior_datasets.get(name, [])
                        for name in [
                            "traffic_acquisition",
                            "user_acquisition",
                            "devices",
                            "countries",
                            "events",
                        ]
                    },
                    "scope": (
                        "Equal-length benchmark covering summary, acquisition, "
                        "device, country, and event metrics."
                    ),
                }
                ga_payload["prior_period_extraction"] = {
                    "status": "available",
                    "start": str(prior_start_date),
                    "end": str(prior_end_date),
                }
            else:
                ga_payload["prior_period_extraction"] = {
                    "status": (
                        "error" if "error" in prior_payload
                        else "unavailable"
                    ),
                    "start": str(prior_start_date),
                    "end": str(prior_end_date),
                    "message": prior_payload.get("error") or (
                        "No GA4 rows were returned for the previous period."
                    ),
                }
        else:
            ga_payload["prior_period_extraction"] = {
                "status": "not_selected",
                "message": "Previous-period benchmarking was disabled.",
            }

        try:
            from services.ga4_bigquery import (
                BigQueryExportUnavailable,
                enrich_payload_with_bigquery,
                extract_bigquery_payload,
            )

            with st.spinner(
                "Checking for a linked GA4 BigQuery event export..."
            ):
                bigquery_payload = extract_bigquery_payload(
                    creds,
                    ga_property_id,
                    ga_start_date,
                    ga_end_date,
                )
            ga_payload = enrich_payload_with_bigquery(
                ga_payload, bigquery_payload
            )
            bq_metadata = bigquery_payload.get("metadata", {})
            bq_counts = bq_metadata.get("row_counts", {})
            st.success(
                "BigQuery event export included: "
                f"{sum(bq_counts.values()):,} transformed rows across "
                f"{len(bq_counts)} datasets."
            )
        except BigQueryExportUnavailable as exc:
            ga_payload["data_source"] = "GA4 Data API"
            ga_payload["bigquery_extraction"] = {
                "status": "unavailable",
                "message": str(exc),
            }
        except Exception as exc:
            ga_payload["data_source"] = "GA4 Data API"
            ga_payload["bigquery_extraction"] = {
                "status": "error",
                "message": str(exc),
            }

        exploration_uploads = {}
        for dataset_name, uploaded_file in [
            ("uploaded_path_exploration", ga_path_exploration_file),
            ("uploaded_funnel_exploration", ga_funnel_exploration_file),
        ]:
            if uploaded_file is None:
                continue
            try:
                uploaded_rows = parse_ga4_exploration_upload(uploaded_file)
            except Exception as exc:
                st.error(
                    f"Unable to read {uploaded_file.name}: {exc}"
                )
                st.stop()
            ga_payload.setdefault("datasets", {})[dataset_name] = uploaded_rows
            extraction_metadata = ga_payload.setdefault(
                "extraction_metadata", {}
            )
            extraction_metadata.setdefault(
                "dataset_row_counts", {}
            )[dataset_name] = {
                "api_row_count": len(uploaded_rows),
                "extracted_row_count": len(uploaded_rows),
                "complete": True,
            }
            extraction_metadata.setdefault(
                "report_quality", {}
            )[dataset_name] = {
                "source": "GA4 Explore CSV upload",
                "sampling_metadatas": [],
            }
            exploration_uploads[dataset_name] = {
                "file_name": uploaded_file.name,
                "row_count": len(uploaded_rows),
                "status": "available" if uploaded_rows else "empty",
            }
        ga_payload["exploration_uploads"] = exploration_uploads

        # Check for empty GA4 data (all summary metrics are zero)
        ga_summary = ga_payload.get("summary_metrics", {})
        if (
            ga_summary.get("total_sessions", 0) == 0
            and ga_summary.get("total_users", 0) == 0
            and ga_summary.get("total_pageviews", 0) == 0
        ):
            st.warning("Data is not available for the selected date range. Try changing the date range.")
            st.stop()

        with st.spinner("Generating GA4 Deep Audit Report..."):
            try:
                ga_report = report_gen.generate_ga4_deep_audit(ga_payload)
            except Exception as exc:
                if exc.__class__.__name__ != "ResourceExhausted":
                    raise
                st.error(
                    "Gemini's per-minute input quota is temporarily exhausted. "
                    "Please wait about a minute, then generate the report again."
                )
                st.stop()

        st.session_state.ga4_report = ga_report
        st.session_state.ga4_property_name = selected_ga_property
        st.session_state.ga4_payload = ga_payload
        st.session_state.ga4_start_date = ga_start_date
        st.session_state.ga4_end_date = ga_end_date
        st.session_state.ga4_days_diff = ga_days_diff

    # Display GA4 Report if available
    if st.session_state.ga4_report:
        ga_rpt = st.session_state.ga4_report
        ga_prop_name = st.session_state.ga4_property_name or selected_ga_property
        ga_pay = st.session_state.ga4_payload or {}
        ga_s = st.session_state.ga4_start_date or ga_start_date
        ga_e = st.session_state.ga4_end_date or ga_end_date
        ga_d = st.session_state.ga4_days_diff or ga_days_diff

        st.divider()
        st.markdown(
            """
            <section class="ga4-report-header">
                <div class="ga4-report-eyebrow">ANALYTICS INTELLIGENCE</div>
                <h2>GA4 Deep Audit Report</h2>
                <p>Performance diagnosis, conversion evidence, and prioritized recommendations</p>
            </section>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(f"""
        <div class="info-meta">
            <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;'>
                <div>
                    <div style='color: #64748B; font-size: 0.85rem;'>Property</div>
                    <div style='font-weight: 600; margin-top: 0.3rem;'>{ga_prop_name}</div>
                </div>
                <div>
                    <div style='color: #64748B; font-size: 0.85rem;'>Analysis Period</div>
                    <div style='font-weight: 600; margin-top: 0.3rem;'>{ga_s.strftime('%b %d, %Y')} - {ga_e.strftime('%b %d, %Y')} ({ga_d} days)</div>
                </div>
                <div>
                    <div style='color: #64748B; font-size: 0.85rem;'>Generated On</div>
                    <div style='font-weight: 600; margin-top: 0.3rem;'>{date.today().strftime('%B %d, %Y')}</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        ga_metrics = ga_pay.get("summary_metrics", {})
        st.caption(
            f"Data source: {ga_pay.get('data_source', 'GA4 Data API')}"
        )
        if ga_metrics:
            st.markdown("### Metrics Overview")
            metric_cols = st.columns(4)
            with metric_cols[0]:
                st.metric("Total Sessions", f"{ga_metrics.get('total_sessions', 0):,}")
            with metric_cols[1]:
                st.metric("Total Users", f"{ga_metrics.get('total_users', 0):,}")
            with metric_cols[2]:
                st.metric("Engagement Rate", f"{ga_metrics.get('engagement_rate', 0):.2%}")
            with metric_cols[3]:
                st.metric("Total Conversions", f"{ga_metrics.get('total_conversions', 0):,}")

            # Additional metrics row for clarity
            metric_cols2 = st.columns(4)
            with metric_cols2[0]:
                st.metric("New Users", f"{ga_metrics.get('new_users', 0):,}")
            with metric_cols2[1]:
                st.metric("Bounce Rate", f"{ga_metrics.get('bounce_rate', 0):.2%}")
            with metric_cols2[2]:
                st.metric("Avg Session Duration", f"{ga_metrics.get('avg_session_duration', 0):.1f}s")
            with metric_cols2[3]:
                st.metric("Total Pageviews", f"{ga_metrics.get('total_pageviews', 0):,}")

        st.markdown("")
        render_report_clean(
            ga_rpt,
            container_key="ga4-report",
            skip_first_h1=True,
        )

        st.markdown("")

        if DOCX_AVAILABLE:
            col1, col2 = st.columns(2)
            with col1:
                word_doc = create_word_document(
                    ga_rpt, ga_prop_name, ga_s, ga_e, "GA4 Deep Audit"
                )
                if word_doc:
                    st.download_button(
                        "Download Word Document",
                        word_doc,
                        f"ga4_deep_audit_report_{date.today().strftime('%Y%m%d')}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True,
                        key="ga4_word_dl",
                    )
            with col2:
                st.download_button(
                    "Download Markdown",
                    ga_rpt,
                    f"ga4_deep_audit_report_{date.today().strftime('%Y%m%d')}.md",
                    mime="text/markdown",
                    use_container_width=True,
                    key="ga4_md_dl",
                )
        else:
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                st.download_button(
                    "Download Report (Markdown)",
                    ga_rpt,
                    f"ga4_deep_audit_report_{date.today().strftime('%Y%m%d')}.md",
                    mime="text/markdown",
                    use_container_width=True,
                    key="ga4_md_dl_fallback",
                )
                st.info("Install python-docx to enable Word document export.")

        st.markdown("")
        ga4_excel = create_ga4_excel_export(ga_pay)
        st.download_button(
            "Download Complete Raw GA4 Extract (Excel)",
            ga4_excel,
            f"ga4_raw_extract_{ga_s.strftime('%Y%m%d')}_{ga_e.strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="ga4_raw_excel_dl",
        )

        metric_errors = ga_pay.get("extraction_metadata", {}).get("metric_errors", {})
        if metric_errors:
            partial_names = ", ".join(sorted(metric_errors))
            st.warning(
                "Google rejected some metrics for these report surfaces: "
                f"{partial_names}. Compatible metrics were still extracted; "
                "see the Extraction Status sheet for omitted metric names."
            )

        st.markdown("")
        if st.button("Clear Report", use_container_width=False, key="clear_ga4_report"):
            st.session_state.ga4_report = None
            st.session_state.ga4_property_name = None
            st.session_state.ga4_payload = None
            st.session_state.ga4_start_date = None
            st.session_state.ga4_end_date = None
            st.session_state.ga4_days_diff = None
            st.rerun()

    st.stop()

# ═══════════════════════════════════════════════════════════════════════════════
#  GOOGLE SEARCH CONSOLE FLOW
# ═══════════════════════════════════════════════════════════════════════════════

properties = list_properties(creds)

st.markdown("### Select Property")

if not properties:
    st.warning("No Google Search Console properties found for this account.")
    st.info(
        """
        **Possible reasons:**
        1. This Google account does not have verified GSC properties
        2. Search Console API access is not enabled for this OAuth project
        3. OAuth permissions need to be refreshed

        **To fix:**
        - Verify at least one property in Google Search Console
        - Ensure Search Console API is enabled in Google Cloud Console
        - Log out and sign in again to refresh permissions
        """
    )
    st.stop()

if not st.session_state.selected_property or st.session_state.selected_property not in properties:
    st.session_state.selected_property = properties[0] if properties else None


def on_property_change():
    st.session_state.selected_property = st.session_state.property_selector


site_url = st.selectbox(
    "Choose your GSC property to analyze:",
    properties,
    index=(
        properties.index(st.session_state.selected_property)
        if st.session_state.selected_property in properties
        else 0
    ),
    label_visibility="collapsed",
    key="property_selector",
    on_change=on_property_change,
)

if not site_url:
    st.error("Select a valid GSC property before running analysis.")
    st.stop()

# ---------- Analysis Mode ----------
st.markdown("### Analysis Mode")


def on_comparison_mode_change():
    st.session_state.comparison_mode = st.session_state.comparison_mode_checkbox


comparison_mode = st.checkbox(
    "Enable Period Comparison",
    value=st.session_state.comparison_mode if st.session_state.comparison_mode else False,
    help="Compare performance between two time periods",
    key="comparison_mode_checkbox",
    on_change=on_comparison_mode_change,
)

if comparison_mode:
    st.markdown("### Select Two Periods to Compare")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Period 1 (Baseline)**")
        col1_1, col1_2 = st.columns(2)
        with col1_1:
            default_p1_start = date.today() - timedelta(days=60)
            period1_start = st.date_input(
                "P1 Start", value=default_p1_start, max_value=date.today(),
                help="Period 1 start date", key="p1_start",
            )
        with col1_2:
            default_p1_end = date.today() - timedelta(days=31)
            period1_end = st.date_input(
                "P1 End", value=default_p1_end, max_value=date.today(),
                help="Period 1 end date", key="p1_end",
            )

    with col2:
        st.markdown("**Period 2 (Comparison)**")
        col2_1, col2_2 = st.columns(2)
        with col2_1:
            default_p2_start = date.today() - timedelta(days=30)
            period2_start = st.date_input(
                "P2 Start", value=default_p2_start, max_value=date.today(),
                help="Period 2 start date", key="p2_start",
            )
        with col2_2:
            period2_end = st.date_input(
                "P2 End", value=date.today(), max_value=date.today(),
                help="Period 2 end date", key="p2_end",
            )

    if period1_start > period1_end:
        st.error("Period 1 start date must be before or equal to the end date.")
        st.stop()
    if period2_start > period2_end:
        st.error("Period 2 start date must be before or equal to the end date.")
        st.stop()

    days_p1 = (period1_end - period1_start).days + 1
    days_p2 = (period2_end - period2_start).days + 1

    st.info(
        f"**Period 1**: {days_p1} days "
        f"({period1_start.strftime('%b %d, %Y')} - {period1_end.strftime('%b %d, %Y')}) | "
        f"**Period 2**: {days_p2} days "
        f"({period2_start.strftime('%b %d, %Y')} - {period2_end.strftime('%b %d, %Y')})"
    )

    start_date = period1_start
    end_date = period1_end

else:
    st.markdown("### Select Date Range")
    col_date1, col_date2 = st.columns(2)

    with col_date1:
        default_start = date.today() - timedelta(days=30)
        start_date = st.date_input(
            "Start Date", value=default_start, max_value=date.today(),
            help="Select the start date for analysis",
        )

    with col_date2:
        end_date = st.date_input(
            "End Date", value=date.today(), max_value=date.today(),
            help="Select the end date for analysis",
        )

    if start_date > end_date:
        st.error("Start date must be before or equal to the end date.")
        st.stop()

    days_diff = (end_date - start_date).days + 1
    st.info(
        f"Analyzing **{days_diff} days** of data from "
        f"**{start_date.strftime('%B %d, %Y')}** to "
        f"**{end_date.strftime('%B %d, %Y')}**"
    )

# ---------- Analysis Type Selection ----------
st.markdown("### Select Analysis Type")
st.markdown("")

if comparison_mode:
    analysis_type = "GSC Data Analysis"
    st.info("File Upload Analytics is disabled during period comparison.")
else:
    analysis_type = st.radio(
        "Choose analysis type:",
        ["GSC Data Analysis", "File Upload Analytics"],
        horizontal=True,
        label_visibility="collapsed",
    )

# ═══════════════════════════════════════════════════════════════════════════════
#  FILE UPLOAD ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════

if analysis_type == "File Upload Analytics" and not comparison_mode:
    st.markdown("### Upload Files for Analysis")

    upload_mode = st.radio(
        "Select upload method:",
        ["ZIP File", "Direct CSV / Excel Files"],
        horizontal=True,
        key="upload_mode_selector",
    )

    if upload_mode == "ZIP File":
        st.info("Upload a ZIP file containing CSV or Excel files to generate both reports.")

        uploaded_zip = st.file_uploader(
            "Select ZIP file to upload",
            type=["zip"],
            accept_multiple_files=False,
            key="file_uploader",
        )
        uploaded_direct = None

        if uploaded_zip:
            st.success(f"ZIP file uploaded: {uploaded_zip.name} ({uploaded_zip.size / 1024:.2f} KB)")

            try:
                with zipfile.ZipFile(uploaded_zip, "r") as zip_ref:
                    file_list = [
                        f for f in zip_ref.namelist()
                        if f.endswith((".csv", ".xlsx", ".xls")) and not f.startswith("__MACOSX")
                    ]

                    if file_list:
                        with st.expander("View Files in ZIP"):
                            for idx, file in enumerate(file_list, 1):
                                st.write(f"{idx}. {file}")
                        uploaded_files = file_list
                    else:
                        st.error("No CSV or Excel files found in the ZIP archive.")
                        uploaded_files = None
            except zipfile.BadZipFile:
                st.error("Invalid ZIP file. Upload a valid ZIP archive.")
                uploaded_files = None
            except Exception as exc:
                st.error(f"Error reading ZIP file: {exc}")
                uploaded_files = None

            st.markdown("")
            file_analytics_btn = st.button(
                "Generate Reports",
                use_container_width=True,
                type="primary",
                help="Generate Deep Audit and Cluster Audit reports using uploaded file data",
            )
        else:
            file_analytics_btn = False
            uploaded_files = None

    else:  # Direct CSV / Excel Files
        st.info("Upload one or more CSV or Excel files to generate both reports.")

        uploaded_direct = st.file_uploader(
            "Select CSV or Excel files to upload",
            type=["csv", "xlsx", "xls"],
            accept_multiple_files=True,
            key="direct_file_uploader",
        )
        uploaded_zip = None

        if uploaded_direct:
            total_size = sum(f.size for f in uploaded_direct)
            st.success(f"{len(uploaded_direct)} file(s) uploaded ({total_size / 1024:.2f} KB total)")

            with st.expander("View Uploaded Files"):
                for idx, f in enumerate(uploaded_direct, 1):
                    st.write(f"{idx}. {f.name} ({f.size / 1024:.2f} KB)")

            uploaded_files = [f.name for f in uploaded_direct]

            st.markdown("")
            file_analytics_btn = st.button(
                "Generate Reports",
                use_container_width=True,
                type="primary",
                help="Generate Deep Audit and Cluster Audit reports using uploaded file data",
            )
        else:
            file_analytics_btn = False
            uploaded_files = None

    deep_audit_btn = False
    cluster_audit_btn = False
    comparison_btn = False

elif comparison_mode:
    comparison_btn = st.button(
        "Generate Period Comparison Report",
        use_container_width=True,
        type="primary",
        help="Compare performance between two time periods with detailed insights",
    )
    deep_audit_btn = False
    cluster_audit_btn = False
    file_analytics_btn = False

else:
    col1, col2 = st.columns(2)

    with col1:
        deep_audit_btn = st.button(
            "Generate Deep Audit Report",
            use_container_width=True,
            type="primary",
            help="Comprehensive SEO/GEO/AEO analysis with detailed insights",
        )

    with col2:
        cluster_audit_btn = st.button(
            "Generate Cluster Audit Report",
            use_container_width=True,
            type="secondary",
            help="Cluster-based analysis with actionable recommendations",
        )
    comparison_btn = False
    file_analytics_btn = False

# ═══════════════════════════════════════════════════════════════════════════════
#  DEEP AUDIT + ACTION REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

if deep_audit_btn:
    with st.spinner(
        f"Extracting GSC data from {start_date.strftime('%b %d, %Y')} "
        f"to {end_date.strftime('%b %d, %Y')}..."
    ):
        payload = extract_payload(creds, site_url, start_date, end_date)
        st.session_state.last_payload = payload

    if "note" in payload and "summary_metrics" not in payload:
        st.warning("Data is not available for the selected date range. Try changing the date range.")
        st.stop()

    with st.spinner("Generating Deep Audit Report..."):
        report = report_gen.generate_deep_audit(payload)

    with st.spinner("Generating GSC Action Report..."):
        action_rpt = report_gen.generate_action_report(report, payload)

    st.session_state.deep_audit_report = report
    st.session_state.action_report = action_rpt
    st.session_state.deep_audit_payload = payload
    st.session_state.deep_audit_site_url = site_url
    st.session_state.deep_audit_start_date = start_date
    st.session_state.deep_audit_end_date = end_date
    st.session_state.deep_audit_days_diff = days_diff

# Display Deep Audit + Action Report if available
if st.session_state.deep_audit_report:
    da_report = st.session_state.deep_audit_report
    da_site_url = st.session_state.deep_audit_site_url or site_url
    da_start = st.session_state.deep_audit_start_date or start_date
    da_end = st.session_state.deep_audit_end_date or end_date
    da_days = st.session_state.deep_audit_days_diff or (
        (end_date - start_date).days + 1 if not comparison_mode else 0
    )

    st.divider()

    tab_deep, tab_action = st.tabs(["Deep Audit Report", "GSC Action Report"])

    with tab_deep:
        st.markdown(
            "<h2 class='report-title'>Deep Audit Report</h2>",
            unsafe_allow_html=True,
        )

        st.markdown(f"""
        <div class="info-meta">
            <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;'>
                <div>
                    <div style='color: #64748B; font-size: 0.85rem;'>Property</div>
                    <div style='font-weight: 600; margin-top: 0.3rem;'>{da_site_url}</div>
                </div>
                <div>
                    <div style='color: #64748B; font-size: 0.85rem;'>Analysis Period</div>
                    <div style='font-weight: 600; margin-top: 0.3rem;'>{da_start.strftime('%b %d, %Y')} - {da_end.strftime('%b %d, %Y')} ({da_days} days)</div>
                </div>
                <div>
                    <div style='color: #64748B; font-size: 0.85rem;'>Generated On</div>
                    <div style='font-weight: 600; margin-top: 0.3rem;'>{date.today().strftime('%B %d, %Y')}</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        render_report_clean(da_report)

        st.markdown("")

        if DOCX_AVAILABLE:
            col1, col2 = st.columns(2)
            with col1:
                word_doc = create_word_document(da_report, da_site_url, da_start, da_end, "Deep Audit")
                if word_doc:
                    st.download_button(
                        "Download Word Document",
                        word_doc,
                        f"deep_audit_report_{date.today().strftime('%Y%m%d')}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True,
                        key="deep_audit_word_dl",
                    )
            with col2:
                st.download_button(
                    "Download Markdown",
                    da_report,
                    f"deep_audit_report_{date.today().strftime('%Y%m%d')}.md",
                    mime="text/markdown",
                    use_container_width=True,
                    key="deep_audit_md_dl",
                )
        else:
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                st.download_button(
                    "Download Report (Markdown)",
                    da_report,
                    f"deep_audit_report_{date.today().strftime('%Y%m%d')}.md",
                    mime="text/markdown",
                    use_container_width=True,
                    key="deep_audit_md_dl_fallback",
                )
                st.info("Install python-docx to enable Word document export.")

    with tab_action:
        if st.session_state.action_report:
            st.markdown(
                "<h2 class='report-title'>GSC Action Report</h2>",
                unsafe_allow_html=True,
            )

            st.markdown(f"""
            <div class="info-meta" style='border-left: 4px solid #059669;'>
                <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;'>
                    <div>
                        <div style='color: #64748B; font-size: 0.85rem;'>Property</div>
                        <div style='font-weight: 600; margin-top: 0.3rem;'>{da_site_url}</div>
                    </div>
                    <div>
                        <div style='color: #64748B; font-size: 0.85rem;'>Report Type</div>
                        <div style='font-weight: 600; margin-top: 0.3rem;'>Forensic Diagnosis + Execution Plan</div>
                    </div>
                    <div>
                        <div style='color: #64748B; font-size: 0.85rem;'>Generated On</div>
                        <div style='font-weight: 600; margin-top: 0.3rem;'>{date.today().strftime('%B %d, %Y')}</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            render_report_clean(st.session_state.action_report)

            st.markdown("")

            if DOCX_AVAILABLE:
                col1, col2 = st.columns(2)
                with col1:
                    word_doc = create_word_document(
                        st.session_state.action_report, da_site_url, da_start, da_end, "GSC Action Report"
                    )
                    if word_doc:
                        st.download_button(
                            "Download Action Report (Word)",
                            word_doc,
                            f"gsc_action_report_{date.today().strftime('%Y%m%d')}.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            use_container_width=True,
                            key="action_report_word_dl",
                        )
                with col2:
                    st.download_button(
                        "Download Action Report (Markdown)",
                        st.session_state.action_report,
                        f"gsc_action_report_{date.today().strftime('%Y%m%d')}.md",
                        mime="text/markdown",
                        use_container_width=True,
                        key="action_report_md_dl",
                    )
            else:
                col1, col2, col3 = st.columns([1, 2, 1])
                with col2:
                    st.download_button(
                        "Download Action Report (Markdown)",
                        st.session_state.action_report,
                        f"gsc_action_report_{date.today().strftime('%Y%m%d')}.md",
                        mime="text/markdown",
                        use_container_width=True,
                        key="action_report_md_dl_fallback",
                    )
        else:
            st.info("⏳ Action Report is being generated...")

    st.markdown("")
    if st.button("Clear Reports", use_container_width=False, key="clear_deep_audit_reports"):
        st.session_state.deep_audit_report = None
        st.session_state.action_report = None
        st.session_state.deep_audit_payload = None
        st.session_state.deep_audit_site_url = None
        st.session_state.deep_audit_start_date = None
        st.session_state.deep_audit_end_date = None
        st.session_state.deep_audit_days_diff = None
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
#  CLUSTER AUDIT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

if cluster_audit_btn:
    with st.spinner(
        f"Extracting GSC data from {start_date.strftime('%b %d, %Y')} "
        f"to {end_date.strftime('%b %d, %Y')}..."
    ):
        payload = extract_payload(creds, site_url, start_date, end_date)
        st.session_state.last_payload = payload

    if "note" in payload and "summary_metrics" not in payload:
        st.warning("Data is not available for the selected date range. Try changing the date range.")
        st.stop()

    with st.spinner("Generating Cluster Audit Report..."):
        report = report_gen.generate_cluster_audit(payload)

    st.session_state.cluster_audit_report = report
    st.session_state.cluster_audit_payload = payload
    st.session_state.cluster_audit_site_url = site_url
    st.session_state.cluster_audit_start_date = start_date
    st.session_state.cluster_audit_end_date = end_date
    st.session_state.cluster_audit_days_diff = days_diff

# Display Cluster Audit Report if available
if st.session_state.cluster_audit_report:
    ca_report = st.session_state.cluster_audit_report
    ca_site_url = st.session_state.cluster_audit_site_url or site_url
    ca_start = st.session_state.cluster_audit_start_date or start_date
    ca_end = st.session_state.cluster_audit_end_date or end_date
    ca_days = st.session_state.cluster_audit_days_diff or (
        (end_date - start_date).days + 1 if not comparison_mode else 0
    )

    st.divider()
    st.markdown(
        "<h2 class='report-title'>Cluster Audit Report</h2>",
        unsafe_allow_html=True,
    )

    st.markdown(f"""
    <div class="info-meta">
        <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;'>
            <div>
                <div style='color: #64748B; font-size: 0.85rem;'>Property</div>
                <div style='font-weight: 600; margin-top: 0.3rem;'>{ca_site_url}</div>
            </div>
            <div>
                <div style='color: #64748B; font-size: 0.85rem;'>Analysis Period</div>
                <div style='font-weight: 600; margin-top: 0.3rem;'>{ca_start.strftime('%b %d, %Y')} - {ca_end.strftime('%b %d, %Y')} ({ca_days} days)</div>
            </div>
            <div>
                <div style='color: #64748B; font-size: 0.85rem;'>Generated On</div>
                <div style='font-weight: 600; margin-top: 0.3rem;'>{date.today().strftime('%B %d, %Y')}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    render_report_clean(ca_report)

    st.markdown("")

    if DOCX_AVAILABLE:
        col1, col2 = st.columns(2)
        with col1:
            word_doc = create_word_document(ca_report, ca_site_url, ca_start, ca_end, "Cluster Audit")
            if word_doc:
                st.download_button(
                    "Download Word Document",
                    word_doc,
                    f"cluster_audit_report_{date.today().strftime('%Y%m%d')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                    key="cluster_word_dl",
                )
        with col2:
            st.download_button(
                "Download Markdown",
                ca_report,
                f"cluster_audit_report_{date.today().strftime('%Y%m%d')}.md",
                mime="text/markdown",
                use_container_width=True,
                key="cluster_md_dl",
            )
    else:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.download_button(
                "Download Report (Markdown)",
                ca_report,
                f"cluster_audit_report_{date.today().strftime('%Y%m%d')}.md",
                mime="text/markdown",
                use_container_width=True,
                key="cluster_md_dl_fallback",
            )
            st.info("Install python-docx to enable Word document export.")

    st.markdown("")
    if st.button("Clear Report", use_container_width=False, key="clear_cluster_report"):
        st.session_state.cluster_audit_report = None
        st.session_state.cluster_audit_payload = None
        st.session_state.cluster_audit_site_url = None
        st.session_state.cluster_audit_start_date = None
        st.session_state.cluster_audit_end_date = None
        st.session_state.cluster_audit_days_diff = None
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
#  FILE ANALYTICS REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

if file_analytics_btn:
    with st.spinner("📂 Processing uploaded files..."):
        if uploaded_direct:
            combined_df = process_direct_files(uploaded_direct)
        else:
            combined_df = process_uploaded_files(uploaded_zip, uploaded_files)

    if combined_df is not None:
        st.success(f"Processed {len(uploaded_files)} file(s) with {len(combined_df):,} total rows.")

        with st.spinner("Generating Deep Audit Report from uploaded data..."):
            st.session_state.file_deep_report = report_gen.generate_file_deep_audit(
                combined_df, uploaded_files
            )

        with st.spinner("Generating Cluster Audit Report from uploaded data..."):
            st.session_state.file_cluster_report = report_gen.generate_file_cluster_audit(
                combined_df, uploaded_files
            )

        st.session_state.file_report_metadata = {
            "num_files": len(uploaded_files),
            "num_rows": len(combined_df),
            "generated_date": date.today(),
        }
        st.session_state.file_source_df = combined_df

# Display file reports if available
if st.session_state.file_deep_report and st.session_state.file_cluster_report:
    metadata = st.session_state.file_report_metadata
    deep_report = st.session_state.file_deep_report
    cluster_report = st.session_state.file_cluster_report

    st.divider()
    st.markdown(
        "<h2 class='report-title'>File Analytics Reports</h2>",
        unsafe_allow_html=True,
    )

    st.markdown(f"""
    <div class="info-meta">
        <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;'>
            <div>
                <div style='color: #64748B; font-size: 0.85rem;'>Files Analyzed</div>
                <div style='font-weight: 600; margin-top: 0.3rem;'>{metadata['num_files']} files</div>
            </div>
            <div>
                <div style='color: #64748B; font-size: 0.85rem;'>Total Records</div>
                <div style='font-weight: 600; margin-top: 0.3rem;'>{metadata['num_rows']:,} rows</div>
            </div>
            <div>
                <div style='color: #64748B; font-size: 0.85rem;'>Generated On</div>
                <div style='font-weight: 600; margin-top: 0.3rem;'>{metadata['generated_date'].strftime('%B %d, %Y')}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("Clear Reports", use_container_width=False):
        st.session_state.file_deep_report = None
        st.session_state.file_cluster_report = None
        st.session_state.file_report_metadata = None
        st.session_state.file_source_df = None
        st.rerun()

    tab1, tab2 = st.tabs(["Deep Audit Report", "Cluster Audit Report"])

    with tab1:
        st.markdown("### Deep Audit Report")
        render_report_clean(deep_report)

        st.markdown("")
        if DOCX_AVAILABLE:
            col1, col2 = st.columns(2)
            with col1:
                word_doc = create_word_document(
                    deep_report,
                    f"{metadata['num_files']} Files",
                    metadata["generated_date"],
                    metadata["generated_date"],
                    "File Deep Audit",
                )
                if word_doc:
                    st.download_button(
                        "Download Deep Audit (Word)",
                        word_doc,
                        f"file_deep_audit_{metadata['generated_date'].strftime('%Y%m%d')}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True,
                        key="download_deep_word",
                    )
            with col2:
                st.download_button(
                    "Download Deep Audit (Markdown)",
                    deep_report,
                    f"file_deep_audit_{metadata['generated_date'].strftime('%Y%m%d')}.md",
                    mime="text/markdown",
                    use_container_width=True,
                    key="download_deep_md",
                )
        else:
            st.download_button(
                "Download Deep Audit Report",
                deep_report,
                f"file_deep_audit_{metadata['generated_date'].strftime('%Y%m%d')}.md",
                mime="text/markdown",
                use_container_width=True,
                key="download_deep",
            )

    with tab2:
        st.markdown("### Cluster Audit Report")
        render_report_clean(cluster_report)

        st.markdown("")
        if DOCX_AVAILABLE:
            col1, col2 = st.columns(2)
            with col1:
                word_doc = create_word_document(
                    cluster_report,
                    f"{metadata['num_files']} Files",
                    metadata["generated_date"],
                    metadata["generated_date"],
                    "File Cluster Audit",
                )
                if word_doc:
                    st.download_button(
                        "Download Cluster Audit (Word)",
                        word_doc,
                        f"file_cluster_audit_{metadata['generated_date'].strftime('%Y%m%d')}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True,
                        key="download_cluster_word",
                    )
            with col2:
                st.download_button(
                    "Download Cluster Audit (Markdown)",
                    cluster_report,
                    f"file_cluster_audit_{metadata['generated_date'].strftime('%Y%m%d')}.md",
                    mime="text/markdown",
                    use_container_width=True,
                    key="download_cluster_md",
                )
        else:
            st.download_button(
                "Download Cluster Audit Report",
                cluster_report,
                f"file_cluster_audit_{metadata['generated_date'].strftime('%Y%m%d')}.md",
                mime="text/markdown",
                use_container_width=True,
                key="download_cluster",
            )

# ═══════════════════════════════════════════════════════════════════════════════
#  PERIOD COMPARISON REPORT
# ═══════════════════════════════════════════════════════════════════════════════

if comparison_btn:
    with st.spinner(
        f"Extracting Period 1 data "
        f"({period1_start.strftime('%b %d, %Y')} - {period1_end.strftime('%b %d, %Y')})..."
    ):
        payload1 = extract_payload(creds, site_url, period1_start, period1_end)

    with st.spinner(
        f"Extracting Period 2 data "
        f"({period2_start.strftime('%b %d, %Y')} - {period2_end.strftime('%b %d, %Y')})..."
    ):
        payload2 = extract_payload(creds, site_url, period2_start, period2_end)

    p1_empty = "note" in payload1 and "summary_metrics" not in payload1
    p2_empty = "note" in payload2 and "summary_metrics" not in payload2
    if p1_empty or p2_empty:
        missing = []
        if p1_empty:
            missing.append("Period 1")
        if p2_empty:
            missing.append("Period 2")
        st.warning(
            f"Data is not available for {' and '.join(missing)}. "
            "Try changing the date range."
        )
        st.stop()

    st.session_state.last_payload = {
        "comparison_mode": True,
        "period1": payload1,
        "period2": payload2,
    }

    with st.spinner("Calculating comparison metrics..."):
        comp_metrics = calculate_comparison_metrics(payload1, payload2)

    with st.spinner("Generating Period Comparison Report..."):
        report = report_gen.generate_comparison_report(payload1, payload2, comp_metrics)

    st.divider()
    st.markdown(
        "<h2 class='report-title'>Period Comparison Report</h2>",
        unsafe_allow_html=True,
    )

    st.markdown(f"""
    <div class="info-meta">
        <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;'>
            <div>
                <div style='color: #64748B; font-size: 0.85rem;'>Property</div>
                <div style='font-weight: 600; margin-top: 0.3rem;'>{site_url}</div>
            </div>
            <div>
                <div style='color: #64748B; font-size: 0.85rem;'>Period 1</div>
                <div style='font-weight: 600; margin-top: 0.3rem;'>{period1_start.strftime('%b %d, %Y')} - {period1_end.strftime('%b %d, %Y')} ({days_p1} days)</div>
            </div>
            <div>
                <div style='color: #64748B; font-size: 0.85rem;'>Period 2</div>
                <div style='font-weight: 600; margin-top: 0.3rem;'>{period2_start.strftime('%b %d, %Y')} - {period2_end.strftime('%b %d, %Y')} ({days_p2} days)</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### Metrics Overview")
    metric_cols = st.columns(4)

    with metric_cols[0]:
        clicks_change = comp_metrics["metrics_comparison"]["clicks"]["percent_change"]
        st.metric(
            "Total Clicks",
            f"{comp_metrics['metrics_comparison']['clicks']['period2']:,}",
            f"{clicks_change:+.1f}%",
            delta_color="normal" if clicks_change >= 0 else "inverse",
        )

    with metric_cols[1]:
        impr_change = comp_metrics["metrics_comparison"]["impressions"]["percent_change"]
        st.metric(
            "Total Impressions",
            f"{comp_metrics['metrics_comparison']['impressions']['period2']:,}",
            f"{impr_change:+.1f}%",
            delta_color="normal" if impr_change >= 0 else "inverse",
        )

    with metric_cols[2]:
        ctr_change = comp_metrics["metrics_comparison"]["ctr"]["percent_change"]
        st.metric(
            "Average CTR",
            f"{comp_metrics['metrics_comparison']['ctr']['period2']:.2%}",
            f"{ctr_change:+.1f}%",
            delta_color="normal" if ctr_change >= 0 else "inverse",
        )

    with metric_cols[3]:
        pos_change = comp_metrics["metrics_comparison"]["position"]["absolute_change"]
        st.metric(
            "Average Position",
            f"{comp_metrics['metrics_comparison']['position']['period2']:.1f}",
            f"{pos_change:+.1f}",
            delta_color="inverse" if pos_change < 0 else "normal",
        )

    st.markdown("")
    render_report_clean(report)

    st.markdown("")

    if DOCX_AVAILABLE:
        col1, col2 = st.columns(2)
        with col1:
            word_doc = create_word_document(
                report, site_url, period1_start, period1_end,
                "Period Comparison", period2_start, period2_end,
            )
            if word_doc:
                st.download_button(
                    "Download Word Document",
                    word_doc,
                    f"comparison_report_{date.today().strftime('%Y%m%d')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                )
        with col2:
            st.download_button(
                "Download Markdown",
                report,
                f"comparison_report_{date.today().strftime('%Y%m%d')}.md",
                mime="text/markdown",
                use_container_width=True,
            )
    else:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.download_button(
                "Download Report (Markdown)",
                report,
                f"comparison_report_{date.today().strftime('%Y%m%d')}.md",
                mime="text/markdown",
                use_container_width=True,
            )
            st.info("Install python-docx to enable Word document export.")
