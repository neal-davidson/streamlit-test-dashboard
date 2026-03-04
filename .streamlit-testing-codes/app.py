import io
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px

# =========================
# Password gate
# =========================
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input(
            "Enter password to access dashboard:",
            type="password",
            on_change=password_entered,
            key="password",
        )
        return False
    elif not st.session_state["password_correct"]:
        st.text_input(
            "Enter password to access dashboard:",
            type="password",
            on_change=password_entered,
            key="password",
        )
        st.error("Incorrect password")
        return False
    else:
        return True


if not check_password():
    st.stop()

# =========================
# Page setup
# =========================
st.set_page_config(page_title="Campaign Snapshot", layout="wide")
st.title("Campaign Snapshot Dashboard")
st.caption("Weekly campaign pacing + performance overview")

# =========================
# Data helpers
# =========================
MEDIA_SHEET = "Media Results"

def _safe_sum(series: pd.Series) -> float:
    if series is None:
        return 0.0
    return float(np.nansum(pd.to_numeric(series, errors="coerce").to_numpy()))

def parse_media_results(excel_bytes: bytes) -> pd.DataFrame:
    """Parse and clean the Media Results sheet into a normalized DataFrame."""
    df = pd.read_excel(io.BytesIO(excel_bytes), sheet_name=MEDIA_SHEET)

    # Clean column names
    df.columns = [str(c).strip() for c in df.columns]

    # Normalize common column names (handles stray spaces)
    # If your sheet has slightly different naming, add mappings here.
    col_map = {
        "Vendor ": "Vendor",
        "Gross Opens/Viewed Impressions": "Gross Opens/Viewed Impressions",
        "Expected # of clicks (based on media)": "Expected Clicks",
        "Expected Opens": "Expected Opens",
        "Partner/Platform": "Partner/Platform",
        "On Pace Y/N": "On Pace Y/N",
        "Pacing %": "Pacing %",
        "Pacing Goal To Date": "Pacing Goal To Date",
        "Investment": "Investment",
        "Delivered": "Delivered",
        "Reported Clicks": "Reported Clicks",
        "Placement": "Placement",
        "Medium": "Medium",
        "Date": "Date",
    }
    # Apply renames only if the source column exists
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # Parse date
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    # Ensure numeric columns are numeric
    numeric_cols = [
        "Investment",
        "Delivered",
        "Gross Opens/Viewed Impressions",
        "Reported Clicks",
        "Expected Opens",
        "Expected Clicks",
        "Pacing %",
        "Pacing Goal To Date",
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Derived metrics (recomputed for consistency)
    df["Open Rate (calc)"] = np.where(
        (df.get("Delivered") > 0) & df.get("Gross Opens/Viewed Impressions").notna(),
        df["Gross Opens/Viewed Impressions"] / df["Delivered"],
        np.nan,
    )
    df["CTR (calc)"] = np.where(
        (df.get("Delivered") > 0) & df.get("Reported Clicks").notna(),
        df["Reported Clicks"] / df["Delivered"],
        np.nan,
    )
    df["CPC (calc)"] = np.where(
        (df.get("Reported Clicks") > 0) & df.get("Investment").notna(),
        df["Investment"] / df["Reported Clicks"],
        np.nan,
    )

    # Flag: rows with missing spend are often sub-rows
    if "Investment" in df.columns:
        df["Top-level row"] = df["Investment"].notna()
    else:
        df["Top-level row"] = True

    return df


# =========================
# Upload (v1 data source)
# =========================
st.sidebar.header("Data Source")
uploaded = st.sidebar.file_uploader("Upload the latest Excel file (.xlsx)", type=["xlsx"])

if uploaded is None:
    st.info("Upload your Excel file to generate the dashboard.")
    st.stop()

excel_bytes = uploaded.getvalue()

try:
    df = parse_media_results(excel_bytes)
except ValueError as e:
    # Typical cause: sheet name mismatch
    st.error(f"Could not read sheet '{MEDIA_SHEET}'. Double-check the tab name. Details: {e}")
    st.stop()

# =========================
# Sidebar filters
# =========================
st.sidebar.header("Filters")

# Date range
df_dates = df["Date"].dropna() if "Date" in df.columns else pd.Series([], dtype="datetime64[ns]")
if not df_dates.empty:
    min_d = df_dates.min().date()
    max_d = df_dates.max().date()
    date_range = st.sidebar.date_input("Date range", value=(min_d, max_d), min_value=min_d, max_value=max_d)
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
        df = df[(df["Date"].dt.date >= start_date) & (df["Date"].dt.date <= end_date)]

# Clean view toggle
top_level_only = st.sidebar.checkbox("Top-level placements only (recommended)", value=True)
if top_level_only:
    df_view = df[df["Top-level row"]].copy()
else:
    df_view = df.copy()

def apply_multiselect_filter(frame: pd.DataFrame, label: str, col: str) -> pd.DataFrame:
    if col not in frame.columns:
        return frame
    options = sorted([x for x in frame[col].dropna().unique()])
    if not options:
        return frame
    selected = st.sidebar.multiselect(label, options, default=options)
    return frame[frame[col].isin(selected)] if selected else frame.iloc[0:0]

df_view = apply_multiselect_filter(df_view, "Vendor", "Vendor")
df_view = apply_multiselect_filter(df_view, "Partner/Platform", "Partner/Platform")
df_view = apply_multiselect_filter(df_view, "Medium", "Medium")

# =========================
# KPI strip (weighted)
# =========================
total_spend = _safe_sum(df_view.get("Investment"))
total_delivered = _safe_sum(df_view.get("Delivered"))
total_opens = _safe_sum(df_view.get("Gross Opens/Viewed Impressions"))
total_clicks = _safe_sum(df_view.get("Reported Clicks"))

open_rate = (total_opens / total_delivered) if total_delivered > 0 else np.nan
ctr = (total_clicks / total_delivered) if total_delivered > 0 else np.nan
blended_cpc = (total_spend / total_clicks) if total_clicks > 0 else np.nan

# On-pace summary
pct_on_pace = np.nan
if "On Pace Y/N" in df_view.columns:
    yn = df_view["On Pace Y/N"].astype(str).str.strip().str.upper()
    denom = int(df_view["On Pace Y/N"].notna().sum())
    if denom > 0:
        pct_on_pace = float((yn == "Y").sum() / denom)

# "Last updated" based on data date
last_updated = df_view["Date"].max() if "Date" in df_view.columns else None

k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
k1.metric("Total Spend", f"${total_spend:,.0f}")
k2.metric("Delivered", f"{total_delivered:,.0f}")
k3.metric("Opens / Impr.", f"{total_opens:,.0f}")
k4.metric("Clicks", f"{total_clicks:,.0f}")
k5.metric("Open Rate", f"{open_rate:.1%}" if pd.notna(open_rate) else "—")
k6.metric("CTR", f"{ctr:.2%}" if pd.notna(ctr) else "—")
k7.metric("Blended CPC", f"${blended_cpc:,.2f}" if pd.notna(blended_cpc) else "—")

meta_cols = st.columns(2)
with meta_cols[0]:
    if pd.notna(pct_on_pace):
        st.caption(f"On-pace placements: {pct_on_pace:.0%}")
with meta_cols[1]:
    if last_updated is not None and pd.notna(last_updated):
        st.caption(f"Last updated (latest date in file): {last_updated.date()}")

st.divider()

# =========================
# Trend over time (weekly)
# =========================
st.subheader("Trend Over Time")

metric_choice = st.radio("Trend metric", ["Spend", "Clicks", "CTR", "Delivered"], horizontal=True)

trend_df = df_view[df_view.get("Date").notna()] if "Date" in df_view.columns else pd.DataFrame()
if not trend_df.empty:
    trend_df = trend_df.copy()
    trend_df["Week"] = trend_df["Date"].dt.to_period("W").dt.start_time

    weekly = trend_df.groupby("Week", as_index=False).agg(
        Investment=("Investment", "sum"),
        Delivered=("Delivered", "sum"),
        Opens=("Gross Opens/Viewed Impressions", "sum"),
        Clicks=("Reported Clicks", "sum"),
    )
    weekly["CTR"] = np.where(weekly["Delivered"] > 0, weekly["Clicks"] / weekly["Delivered"], np.nan)

    y_map = {"Spend": "Investment", "Clicks": "Clicks", "CTR": "CTR", "Delivered": "Delivered"}
    fig = px.line(weekly, x="Week", y=y_map[metric_choice], markers=True)
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No valid dates found after filtering, so trend charts are unavailable.")

st.divider()

# =========================
# Vendor performance
# =========================
st.subheader("Vendor Performance")

if "Vendor" in df_view.columns and not df_view.empty:
    by_vendor = df_view.groupby("Vendor", as_index=False).agg(
        Spend=("Investment", "sum"),
        Delivered=("Delivered", "sum"),
        Clicks=("Reported Clicks", "sum"),
    )
    by_vendor["CTR"] = np.where(by_vendor["Delivered"] > 0, by_vendor["Clicks"] / by_vendor["Delivered"], np.nan)
    by_vendor["CPC"] = np.where(by_vendor["Clicks"] > 0, by_vendor["Spend"] / by_vendor["Clicks"], np.nan)

    left, right = st.columns(2)
    with left:
        st.caption("Spend by Vendor")
        fig1 = px.bar(by_vendor.sort_values("Spend", ascending=False), x="Spend", y="Vendor", orientation="h")
        st.plotly_chart(fig1, use_container_width=True)
    with right:
        st.caption("CPC by Vendor (lower is better)")
        fig2 = px.bar(by_vendor.sort_values("CPC", ascending=True), x="CPC", y="Vendor", orientation="h")
        st.plotly_chart(fig2, use_container_width=True)
else:
    st.info("Vendor column not available or no rows after filtering.")

st.divider()

# =========================
# Pacing + Placement detail
# =========================
st.subheader("Pacing & Placement Detail")

if "Pacing %" in df_view.columns and "Placement" in df_view.columns and df_view["Pacing %"].notna().any():
    pacing = df_view[["Placement", "Pacing %"]].dropna().copy()
    pacing = pacing.groupby("Placement", as_index=False)["Pacing %"].mean()
    pacing = pacing.sort_values("Pacing %", ascending=True)

    st.caption("Average Pacing % by Placement")
    figp = px.bar(pacing, x="Pacing %", y="Placement", orientation="h")
    st.plotly_chart(figp, use_container_width=True)

# Detail table
table_cols = [
    "Vendor",
    "Partner/Platform",
    "Medium",
    "Placement",
    "Date",
    "Investment",
    "Delivered",
    "Gross Opens/Viewed Impressions",
    "Reported Clicks",
    "Open Rate (calc)",
    "CTR (calc)",
    "CPC (calc)",
    "Pacing %",
    "On Pace Y/N",
]
present_cols = [c for c in table_cols if c in df_view.columns]

sort_cols = [c for c in ["Investment", "Reported Clicks"] if c in df_view.columns]
if sort_cols:
    df_table = df_view[present_cols].sort_values(sort_cols, ascending=False, na_position="last")
else:
    df_table = df_view[present_cols].copy()

st.dataframe(df_table, use_container_width=True, hide_index=True)