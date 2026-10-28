"""
dashboard/pages/04_Hourly_Monthly_Pattern.py
─────────────────────────────────────────────
Page 4 — Hourly and Monthly AQI Patterns

Reveals temporal patterns in air quality:
  - Hour-of-day × day-of-week heatmap (when is AQI worst?)
  - Monthly AQI box plot (seasonal trends)
  - Peak hour annotation
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
import pandas as pd
from datetime import date, timedelta

from dashboard.utils.db_queries import get_cities, get_date_range, get_hourly_pattern, get_monthly_pattern
from dashboard.utils.charts import hourly_heatmap, monthly_box_plot

st.set_page_config(page_title="Temporal Patterns | AQI Dashboard", page_icon="⏰", layout="wide")

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

st.markdown("## ⏰ Hourly & Monthly AQI Patterns")
st.markdown("Discover when air quality is worst — by hour, day of week, and season.")

# ── Sidebar controls ──────────────────────────────────────────────────────────

cities = get_cities()
min_date, max_date = get_date_range()
today = date.today()
if min_date is None: min_date = today - timedelta(days=90)
if max_date is None: max_date = today
default_start = max(min_date, max_date - timedelta(days=90))

with st.sidebar:
    st.markdown("### 🎛️ Filters")
    selected_city = st.selectbox("Select City", cities, index=0)
    date_range = st.date_input(
        "Date Range (Hourly Analysis)",
        value=(default_start, max_date),
        min_value=min_date,
        max_value=max_date,
    )

if len(date_range) != 2:
    st.info("Select both a start date and an end date to continue.")
    st.stop()
start_date, end_date = date_range

# ── Hourly pattern ────────────────────────────────────────────────────────────

st.markdown(f"### ⏱️ Hour-of-Day AQI Heatmap — {selected_city}")

with st.spinner("Loading hourly patterns…"):
    df_hourly = get_hourly_pattern(selected_city, start_date, end_date)

if df_hourly.empty:
    st.info("📭 No hourly data available. The hourly gold table requires the full ETL pipeline.")
else:
    fig_hourly = hourly_heatmap(df_hourly)
    st.plotly_chart(fig_hourly, use_container_width=True)

    # Worst hour annotation
    if not df_hourly.empty:
        worst = df_hourly.loc[df_hourly["avg_aqi"].idxmax()]
        day_labels = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        worst_day = day_labels[int(worst["day_of_week"])]
        st.info(
            f"🔴 **Peak AQI** typically occurs on **{worst_day} at "
            f"{int(worst['hour_of_day']):02d}:00 IST** with avg AQI of **{worst['avg_aqi']:.0f}**"
        )

st.markdown("---")

# ── Monthly pattern ───────────────────────────────────────────────────────────

st.markdown(f"### 📅 Monthly AQI Distribution — {selected_city}")

with st.spinner("Loading monthly patterns…"):
    df_monthly = get_monthly_pattern(selected_city)

if df_monthly.empty:
    st.info("📭 Not enough data for monthly analysis.")
else:
    fig_monthly = monthly_box_plot(df_monthly)
    st.plotly_chart(fig_monthly, use_container_width=True)

    # Month stats
    with st.expander("📊 Monthly Statistics Table"):
        monthly_stats = (
            df_monthly.groupby("month_label")["aqi"]
            .agg(["mean", "median", "min", "max", "count"])
            .reset_index()
        )
        monthly_stats.columns = ["Month", "Mean AQI", "Median AQI", "Min AQI", "Max AQI", "Days"]
        monthly_stats["Mean AQI"] = monthly_stats["Mean AQI"].round(1)
        monthly_stats["Median AQI"] = monthly_stats["Median AQI"].round(1)
        st.dataframe(monthly_stats, use_container_width=True, hide_index=True)

st.markdown("---")

# ── Interpretation notes ──────────────────────────────────────────────────────

with st.expander("💡 How to Read These Charts"):
    st.markdown("""
    **Hour-of-Day Heatmap**
    - Each cell shows the **average AQI** for that hour + day combination
    - 🔴 Red = worse air quality, 🟢 Green = better air quality
    - Common patterns: morning rush (7–10am) and evening peak (6–9pm) are often worst

    **Monthly Box Plot**
    - Box spans the **interquartile range** (25th–75th percentile)
    - Horizontal line = **median** AQI for that month
    - Whiskers = full observed range
    - In India, **winter months (Oct–Feb)** typically show the worst AQI due to temperature inversions
    """)
