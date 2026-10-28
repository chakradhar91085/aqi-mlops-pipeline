"""
dashboard/app.py
─────────────────
AQI Analysis Dashboard — Main Entry Point

A multi-page Streamlit application showing:
  Page 1 — AQI Trend
  Page 2 — Pollutant Contribution
  Page 3 — City Comparison
  Page 4 — Hourly & Monthly Patterns
  Page 5 — Weather Impact on AQI

Run locally:
    streamlit run dashboard/app.py

Via Docker:
    docker-compose up streamlit
    Open http://localhost:8501
"""

import sys
from pathlib import Path

# Make project root importable when running locally outside Docker
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── Page configuration ────────────────────────────────────────────────────────

st.set_page_config(
    page_title="AQI Analytics Dashboard",
    page_icon="🌬️",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": "AQI Analysis & Prediction Pipeline — Data Engineering & MLOps Project",
    },
)

# ── Global CSS ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* Root & typography */
html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* Dark gradient background */
.stApp {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #0d1117 100%);
}

/* Sidebar */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #161b22 0%, #0d1117 100%);
    border-right: 1px solid #30363d;
}
[data-testid="stSidebarNavItems"] {
    max-height: none !important;
}
[data-testid="stSidebarNavViewButton"],
[data-testid="stSidebarNavSeparator"] {
    display: none !important;
}

/* Metric cards */
[data-testid="stMetric"] {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 16px;
    backdrop-filter: blur(10px);
    transition: transform 0.2s ease, border-color 0.2s ease;
}
[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    border-color: rgba(76, 201, 240, 0.4);
}

/* Card containers */
.aqi-card {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 20px 24px;
    margin-bottom: 16px;
    backdrop-filter: blur(10px);
}

/* Page header gradient */
.page-header {
    background: linear-gradient(90deg, #4cc9f0, #4361ee, #7209b7);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    font-size: 2rem;
    font-weight: 700;
    margin-bottom: 4px;
}

/* AQI category pills */
.aqi-pill {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.5px;
}

/* Divider */
.section-divider {
    border: none;
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent);
    margin: 24px 0;
}

/* Info box */
.info-box {
    background: rgba(76, 201, 240, 0.08);
    border: 1px solid rgba(76, 201, 240, 0.2);
    border-radius: 8px;
    padding: 12px 16px;
    font-size: 0.875rem;
    color: #4cc9f0;
}
</style>
""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("""
    <div style="text-align:center; padding: 8px 0 20px 0;">
        <div style="font-size:2.5rem;">🌬️</div>
        <div style="font-size:1.1rem; font-weight:700; color:#4cc9f0;">AQI Analytics</div>
        <div style="font-size:0.75rem; color:#8b949e;">Data Engineering & MLOps</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("**📍 Data Sources**")
    st.markdown("""
    - 🌍 [OpenAQ v3 API](https://api.openaq.org/v3/)
    - 🌤️ [Open-Meteo API](https://open-meteo.com/)
    - 📅 Cities: Delhi · Mumbai · Bengaluru · Chennai · Kolkata
    """)

    st.markdown("---")
    st.markdown("**⚙️ Pipeline Status**")

    # Try to show DB connectivity
    try:
        from dashboard.utils.db_queries import get_latest_kpis, get_date_range
        kpis_df = get_latest_kpis()
        min_dt, max_dt = get_date_range()
        st.success(f"✅ Database connected")
        st.caption(f"Data: {min_dt} → {max_dt}")
    except Exception as e:
        st.warning("⚠️ Database not connected")
        st.caption("Run `docker-compose up` to start services")

    st.markdown("---")
    st.markdown("""
    <div style="font-size:0.75rem; color:#8b949e; text-align:center;">
        AQI Data Pipeline v1.0<br>
        Part 1 — Data Engineering
    </div>
    """, unsafe_allow_html=True)

# ── Home page content ─────────────────────────────────────────────────────────

st.markdown('<div class="page-header">🌬️ Air Quality Index Analytics</div>', unsafe_allow_html=True)
st.markdown(
    "**Real-time and historical AQI monitoring across major Indian cities** — "
    "powered by OpenAQ and Open-Meteo data through an automated ETL pipeline."
)

st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

# ── KPI Cards ─────────────────────────────────────────────────────────────────

AQI_BADGE_COLORS = {
    "Good":                           "#00e400",
    "Moderate":                        "#b8b800",
    "Unhealthy for Sensitive Groups":  "#ff7e00",
    "Unhealthy":                       "#ff0000",
    "Very Unhealthy":                  "#8f3f97",
    "Hazardous":                       "#7e0023",
    "Unknown":                         "#555",
}

try:
    from dashboard.utils.db_queries import get_latest_kpis
    kpis = get_latest_kpis()

    if not kpis.empty:
        from datetime import date as _date
        import pandas as _pd

        latest_day = _pd.to_datetime(kpis["date_ist"]).max().date()
        age_days = (_date.today() - latest_day).days
        if age_days > 2:
            st.warning(
                f"⚠️ Latest data is from **{latest_day}** ({age_days} days old). "
                "Check the `aqi_ingestion` DAG in Airflow (http://localhost:8081) — a rejected OpenAQ API key "
                "or a stopped scheduler are the usual causes."
            )
        st.markdown("### 📊 Latest AQI by City")
        cols = st.columns(len(kpis))
        for col, (_, row) in zip(cols, kpis.iterrows()):
            badge_color = AQI_BADGE_COLORS.get(str(row.get("aqi_category", "Unknown")), "#555")
            aqi_val = int(row["aqi"]) if row.get("aqi") is not None else "N/A"
            with col:
                st.markdown(f"""
                <div class="aqi-card" style="text-align:center;">
                    <div style="font-size:0.85rem; color:#8b949e; margin-bottom:4px;">{row['city']}</div>
                    <div style="font-size:2.2rem; font-weight:700; color:{badge_color};">{aqi_val}</div>
                    <div style="font-size:0.7rem; color:{badge_color}; margin-top:4px;">
                        {row.get('aqi_category', 'Unknown')}
                    </div>
                    <div style="font-size:0.65rem; color:#8b949e; margin-top:4px;">
                        Dominant: {str(row.get('dominant_pollutant', '—')).upper()}
                    </div>
                    <div style="font-size:0.65rem; color:#555; margin-top:2px;">
                        {row.get('date_ist', '')}
                    </div>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.info("📭 No AQI data available yet. Run the ingestion pipeline first.")

except Exception as exc:
    st.markdown("### 📊 Latest AQI by City")
    st.info("⚠️ Could not load AQI data. Is the database up? You can still use the pages in the sidebar.")
    st.caption(f"{type(exc).__name__}: {exc}")

st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

# ── Navigation grid ───────────────────────────────────────────────────────────

st.markdown("### 🗺️ Dashboard Pages")

pages = [
    ("📈", "AQI Trend", "pages/01_AQI_Trend.py", "Daily AQI over time per station and city"),
    ("🧪", "Pollutant Contribution", "pages/02_Pollutant_Contribution.py", "PM2.5, PM10, NO2, O3 breakdown"),
    ("🏙️", "City Comparison", "pages/03_City_Comparison.py", "Side-by-side heatmap across 5 cities"),
    ("⏰", "Hourly & Monthly Patterns", "pages/04_Hourly_Monthly_Pattern.py", "Time-of-day and seasonal AQI patterns"),
    ("🌡️", "Weather Impact", "pages/05_Weather_Impact.py", "How temperature, humidity & wind affect AQI"),
    ("🔮", "AQI Forecast", "pages/06_AQI_Forecast.py", "Next-day AQI predicted by the MLflow-registered model"),
    ("🩺", "Model Monitoring", "pages/07_Model_Monitoring.py", "Forecast error, feature drift, API health"),
]

cols = st.columns(3)
for i, (icon, name, path, desc) in enumerate(pages):
    with cols[i % 3]:
        st.markdown(f"""
        <div class="aqi-card">
            <div style="font-size:1.5rem; margin-bottom:8px;">{icon}</div>
            <div style="font-size:1rem; font-weight:600; color:#e6edf3; margin-bottom:4px;">{name}</div>
            <div style="font-size:0.8rem; color:#8b949e;">{desc}</div>
        </div>
        """, unsafe_allow_html=True)
        st.page_link(path, label=f"Open {name}", icon=icon, use_container_width=True)

st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

# ── Pipeline architecture snippet ─────────────────────────────────────────────

st.markdown("### 🏗️ Pipeline Architecture")
st.code("""
OpenAQ API  ──┐
              ├──▶ Python Collectors ──▶ Raw JSON Landing Zone
Open-Meteo  ──┘
                          │
                          ▼
                    Apache Airflow ETL
                          │
               ┌──────────┼──────────┐
               ▼          ▼          ▼
            staging    cleaned      gold
           (normalize) (validate) (aggregate)
                          │
                          ▼
                  PostgreSQL 4-layer
                          │
                          ▼
               Streamlit Dashboard ← YOU ARE HERE
""", language="text")
