"""
dashboard/pages/07_Model_Monitoring.py
───────────────────────────────────────
Page 7 — Model & data monitoring (Part 2)

Reads ml.monitoring_metrics written daily by the aqi_ml_monitoring DAG:
forecast error, feature drift (PSI), missing data, API latency and failures.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from dashboard.utils.charts import metric_line, psi_heatmap
from dashboard.utils.db_queries import get_monitoring_metrics

st.set_page_config(page_title="Model Monitoring | AQI Dashboard", page_icon="🩺", layout="wide")

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

st.markdown("## 🩺 Model & Data Monitoring")
st.markdown("Forecast error, feature drift, input quality and API health — refreshed daily by Airflow.")

with st.sidebar:
    st.markdown("### 🎛️ Filters")
    days = st.slider("History (days)", 7, 90, 30)

df = get_monitoring_metrics(days)
if df.empty:
    st.info("No monitoring data yet. Trigger the `aqi_ml_monitoring` DAG in Airflow.")
    st.stop()

latest = df[df["run_date"] == df["run_date"].max()]


def tile(col, metric, label, fmt):
    r = latest[(latest["metric"] == metric) & (latest["scope"] == "all")]
    if r.empty or r["value"].iloc[0] is None or r["value"].isna().iloc[0]:
        col.metric(label, "—")
        return
    breached = bool(r["breached"].iloc[0])
    col.metric(label, fmt.format(r["value"].iloc[0]), delta="breached" if breached else "ok",
               delta_color="inverse" if breached else "normal")


c1, c2, c3, c4, c5 = st.columns(5)
tile(c1, "live_mae_14d", "Live MAE (14d)", "{:.1f}")
tile(c2, "missing_pct", "Missing inputs", "{:.1f}%")
tile(c3, "latency_p95_ms", "API p95 latency", "{:.0f} ms")
tile(c4, "error_rate", "API error rate", "{:.1%}")
tile(c5, "data_age_days", "Data age", "{:.0f} days")

left, right = st.columns(2)
left.plotly_chart(metric_line(df, "live_mae_14d", "Forecasting Error (live MAE, 14d)", "MAE (AQI)"), use_container_width=True)
right.plotly_chart(metric_line(df, "missing_pct", "Missing Input Features", "% NaN"), use_container_width=True)

st.plotly_chart(psi_heatmap(df), use_container_width=True)

left, right = st.columns(2)
left.plotly_chart(metric_line(df, "latency_p95_ms", "API Latency (p95)", "ms"), use_container_width=True)
right.plotly_chart(metric_line(df, "error_rate", "API Error Rate (24h)", "share of requests"), use_container_width=True)

with st.expander("🚨 Threshold breaches"):
    breaches = df[df["breached"] == True][["run_date", "metric", "scope", "value", "threshold"]]  # noqa: E712
    if breaches.empty:
        st.write("None")
    else:
        st.dataframe(breaches.sort_values("run_date", ascending=False), use_container_width=True)
