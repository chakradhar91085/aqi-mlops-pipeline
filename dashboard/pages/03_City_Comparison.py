"""
dashboard/pages/03_City_Comparison.py
──────────────────────────────────────
Page 3 — City / Station Comparison

Compares AQI across all 5 target cities:
  - City AQI heatmap (city × date)
  - Grouped bar chart: avg/max/min AQI per city
  - City ranking table
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
import pandas as pd
from datetime import date, timedelta

from dashboard.utils.db_queries import get_date_range, get_city_comparison, get_city_stats
from dashboard.utils.charts import city_comparison_heatmap, city_avg_aqi_bar

st.set_page_config(page_title="City Comparison | AQI Dashboard", page_icon="🏙️", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.stApp { background: linear-gradient(135deg, #0d1117 0%, #161b22 100%); }
[data-testid="stSidebar"] { background: #161b22; border-right: 1px solid #30363d; }
[data-testid="stSidebarNavItems"] { max-height: none !important; }
[data-testid="stSidebarNavViewButton"], [data-testid="stSidebarNavSeparator"] { display: none !important; }
</style>
""", unsafe_allow_html=True)

st.markdown("## 🏙️ City Comparison")
st.markdown("Compare AQI performance across Delhi, Mumbai, Bengaluru, Chennai, and Kolkata.")

# ── Sidebar controls ──────────────────────────────────────────────────────────

min_date, max_date = get_date_range()
today = date.today()
if min_date is None: min_date = today - timedelta(days=30)
if max_date is None: max_date = today
default_start = max(min_date, max_date - timedelta(days=30))

with st.sidebar:
    st.markdown("### 🎛️ Filters")
    date_range = st.date_input(
        "Date Range",
        value=(default_start, max_date),
        min_value=min_date,
        max_value=max_date,
    )

if len(date_range) != 2:
    st.info("Select both a start date and an end date to continue.")
    st.stop()
start_date, end_date = date_range

# ── Fetch data ────────────────────────────────────────────────────────────────

with st.spinner("Loading comparison data…"):
    df_comparison = get_city_comparison(start_date, end_date)
    df_stats = get_city_stats(start_date, end_date)

if df_comparison.empty:
    st.info("📭 No data available. Run the ingestion + ETL pipeline first.")
    st.stop()

# ── City ranking ──────────────────────────────────────────────────────────────

st.markdown("### 🏆 City Rankings (Selected Period)")

CATEGORY_COLORS = {
    "Good": "🟢", "Moderate": "🟡",
    "Unhealthy for Sensitive Groups": "🟠",
    "Unhealthy": "🔴", "Very Unhealthy": "🟣", "Hazardous": "⚫",
}

if not df_stats.empty:
    rank_cols = st.columns(len(df_stats))
    for i, (_, row) in enumerate(df_stats.iterrows()):
        icon = CATEGORY_COLORS.get(str(row.get("dominant_category", "")), "⚪")
        with rank_cols[i]:
            st.metric(
                label=f"#{i+1} {row['city']}",
                value=f"{row['avg_aqi']}",
                help=f"Most common category: {icon} {row.get('dominant_category', 'Unknown')}",
            )
            st.caption(f"Max {row['max_aqi']} · Min {row['min_aqi']}")

st.markdown("---")

# ── Heatmap ───────────────────────────────────────────────────────────────────

st.markdown("### 🗓️ AQI Heatmap: City × Date")
fig_heatmap = city_comparison_heatmap(df_comparison)
st.plotly_chart(fig_heatmap, use_container_width=True)

# ── Bar chart ─────────────────────────────────────────────────────────────────

st.markdown("### 📊 AQI Statistics by City")
col1, col2 = st.columns([2, 1])

with col1:
    fig_bar = city_avg_aqi_bar(df_stats)
    st.plotly_chart(fig_bar, use_container_width=True)

with col2:
    st.markdown("#### 📋 Summary Table")
    display_stats = df_stats[["city", "avg_aqi", "max_aqi", "min_aqi", "data_days", "dominant_category"]].copy()
    display_stats.columns = ["City", "Avg AQI", "Max AQI", "Min AQI", "Days", "Dominant Category"]
    st.dataframe(display_stats, use_container_width=True, hide_index=True)

# ── AQI category breakdown ────────────────────────────────────────────────────

st.markdown("### 🎨 AQI Category Distribution by City")
if not df_comparison.empty:
    cat_counts = (
        df_comparison.groupby(["city", "aqi_category"])
        .size()
        .reset_index(name="days")
    )
    import plotly.express as px
    fig_cat = px.bar(
        cat_counts, x="city", y="days", color="aqi_category",
        color_discrete_map={
            "Good": "#00e400", "Moderate": "#b8b800",
            "Unhealthy for Sensitive Groups": "#ff7e00",
            "Unhealthy": "#ff0000", "Very Unhealthy": "#8f3f97", "Hazardous": "#7e0023",
        },
        labels={"days": "Number of Days", "city": "City", "aqi_category": "AQI Category"},
        template="plotly_dark",
    )
    fig_cat.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif"),
        barmode="stack",
    )
    st.plotly_chart(fig_cat, use_container_width=True)
