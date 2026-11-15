"""
streamlit_app.py
================
CSIR Thunderstorm Nowcast System — Operational Dashboard
Dark theme, premium weather display styling.
"""

import streamlit as st
import pandas as pd
import numpy as np
import joblib
import math
import plotly.graph_objects as go
import plotly.express as px
from pathlib import Path
from datetime import datetime, timedelta
from sklearn.isotonic import IsotonicRegression

st.set_page_config(
    page_title="CSIR Thunderstorm Nowcast — Bengaluru Airport",
    page_icon="⛈️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── DARK WEATHER DASHBOARD THEME ─────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif !important;
}

.stApp {
    background: #0a0e1a !important;
    color: #e8eaf6 !important;
}

section[data-testid="stSidebar"] {
    background: #0d1117 !important;
    border-right: 1px solid #1e2a45 !important;
}

section[data-testid="stSidebar"] * {
    color: #b0bec5 !important;
}

section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3,
section[data-testid="stSidebar"] strong {
    color: #e8eaf6 !important;
}

.stTabs [data-baseweb="tab-list"] {
    background: #0d1117 !important;
    border-bottom: 1px solid #1e2a45 !important;
    gap: 0 !important;
}

.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    color: #607d8b !important;
    border-radius: 0 !important;
    padding: 12px 24px !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    border-bottom: 2px solid transparent !important;
}

.stTabs [aria-selected="true"] {
    color: #42a5f5 !important;
    border-bottom: 2px solid #42a5f5 !important;
    background: transparent !important;
}

.stRadio label { color: #b0bec5 !important; }
.stRadio [data-testid="stMarkdownContainer"] p { color: #b0bec5 !important; }

.stDateInput input {
    background: #0d1117 !important;
    border: 1px solid #1e2a45 !important;
    color: #e8eaf6 !important;
    border-radius: 6px !important;
}

.stSelectbox > div > div {
    background: #0d1117 !important;
    border: 1px solid #1e2a45 !important;
    color: #e8eaf6 !important;
}

.stNumberInput input {
    background: #0d1117 !important;
    border: 1px solid #1e2a45 !important;
    color: #e8eaf6 !important;
}

.stDataFrame {
    background: #0d1117 !important;
}

div[data-testid="metric-container"] {
    background: #0d1117 !important;
    border: 1px solid #1e2a45 !important;
    border-radius: 8px !important;
    padding: 12px !important;
}

.stExpander {
    background: #0d1117 !important;
    border: 1px solid #1e2a45 !important;
    border-radius: 8px !important;
}

hr { border-color: #1e2a45 !important; }

.main .block-container {
    padding-top: 1rem !important;
    padding-bottom: 2rem !important;
}

.hero-banner {
    position: relative;
    background: linear-gradient(135deg, #0a0e1a 0%, #0d1f3c 50%, #0a1628 100%);
    border: 1px solid #1e2a45;
    border-radius: 12px;
    padding: 0;
    margin-bottom: 1.5rem;
    overflow: hidden;
}

.hero-banner img {
    width: 100%;
    border-radius: 12px;
    display: block;
}

.system-status {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 1.5rem;
}

.status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #4caf50;
    box-shadow: 0 0 8px #4caf50;
    animation: pulse 2s infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}

.slot-card {
    background: #0d1117;
    border: 1px solid #1e2a45;
    border-radius: 12px;
    padding: 1.5rem 1rem;
    text-align: center;
    position: relative;
    overflow: hidden;
    transition: border-color 0.3s;
}

.slot-card.alert {
    border-color: #ef5350;
    box-shadow: 0 0 20px rgba(239,83,80,0.15);
}

.slot-card.clear {
    border-color: #1e2a45;
}

.slot-card.high {
    border-color: #ef5350;
    box-shadow: 0 0 20px rgba(239,83,80,0.15);
}

.slot-card.moderate {
    border-color: #ff9800;
    box-shadow: 0 0 15px rgba(255,152,0,0.1);
}

.slot-card.low {
    border-color: #ffc107;
}

.prob-number {
    font-size: 2.8rem;
    font-weight: 700;
    font-family: 'JetBrains Mono', monospace;
    line-height: 1;
    margin: 0.5rem 0;
}

.prob-high   { color: #ef5350; text-shadow: 0 0 20px rgba(239,83,80,0.5); }
.prob-mod    { color: #ff9800; text-shadow: 0 0 20px rgba(255,152,0,0.4); }
.prob-low    { color: #ffc107; }
.prob-none   { color: #4caf50; }

.slot-label {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: #607d8b;
    font-weight: 600;
    margin-bottom: 4px;
}

.slot-time {
    font-size: 0.85rem;
    color: #90a4ae;
    font-family: 'JetBrains Mono', monospace;
    margin-bottom: 0.5rem;
}

.alert-badge {
    display: inline-block;
    padding: 4px 14px;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-top: 0.5rem;
}

.badge-alert { background: rgba(239,83,80,0.15); color: #ef5350; border: 1px solid rgba(239,83,80,0.3); }
.badge-clear { background: rgba(76,175,80,0.15); color: #4caf50; border: 1px solid rgba(76,175,80,0.3); }

.outcome-badge {
    font-size: 0.7rem;
    color: #607d8b;
    margin-top: 4px;
    font-family: 'JetBrains Mono', monospace;
}

.outcome-hit    { color: #4caf50; }
.outcome-miss   { color: #ef5350; }
.outcome-fa     { color: #ff9800; }
.outcome-tn     { color: #607d8b; }

.alert-banner-full {
    background: linear-gradient(90deg, rgba(239,83,80,0.15) 0%, rgba(239,83,80,0.05) 100%);
    border: 1px solid rgba(239,83,80,0.3);
    border-left: 4px solid #ef5350;
    border-radius: 8px;
    padding: 1rem 1.5rem;
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 12px;
    color: #ef9a9a;
    font-weight: 500;
}

.clear-banner-full {
    background: linear-gradient(90deg, rgba(76,175,80,0.1) 0%, rgba(76,175,80,0.03) 100%);
    border: 1px solid rgba(76,175,80,0.2);
    border-left: 4px solid #4caf50;
    border-radius: 8px;
    padding: 1rem 1.5rem;
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 12px;
    color: #81c784;
    font-weight: 500;
}

.section-header {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: #42a5f5;
    font-weight: 600;
    margin-bottom: 1rem;
    padding-bottom: 8px;
    border-bottom: 1px solid #1e2a45;
}

.info-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 0;
    border-bottom: 1px solid #0d1117;
    font-size: 0.82rem;
}

.info-label { color: #607d8b; }
.info-value { color: #e8eaf6; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; }

.prob-bar-container {
    background: #0a0e1a;
    border-radius: 4px;
    height: 6px;
    width: 100%;
    margin: 8px 0;
    overflow: hidden;
    position: relative;
}

.prob-bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.5s ease;
}

.threshold-line {
    position: absolute;
    top: 0;
    height: 100%;
    width: 2px;
    background: rgba(255,255,255,0.4);
}
</style>
""", unsafe_allow_html=True)

BASE = Path(__file__).parent
SLOT_NAMES  = {0:"0001–0600 IST",1:"0601–1200 IST",2:"1201–1800 IST",3:"1801–2400 IST"}
SLOT_LABELS = {0:"Late Night",1:"Morning",2:"Afternoon",3:"Evening"}
SLOT_EMOJI  = {0:"🌙",1:"🌅",2:"☀️",3:"🌆"}

@st.cache_resource
def load_models():
    models = {}
    for slot_id in range(4):
        for suffix in ['_xgb_v3_calibrated','_xgb_v3','_xgb_v2_calibrated','_xgb_v2']:
            path = BASE / "models" / f"nowcast_slot{slot_id}{suffix}.pkl"
            if path.exists():
                models[slot_id] = joblib.load(path)
                break
    return models

@st.cache_data
def load_dataset():
    for fname in ['bengaluru_6hr_training_dataset_v3.csv','bengaluru_6hr_training_dataset_v2.csv']:
        path = BASE / "data" / fname
        if path.exists():
            return pd.read_csv(path, parse_dates=['date'])
    return pd.DataFrame()

@st.cache_data
def load_shap():
    for fname in ['shap_per_slot_importance_v3.csv','shap_per_slot_importance_v2.csv']:
        path = BASE / "results" / fname
        if path.exists():
            return pd.read_csv(path)
    return pd.DataFrame()

@st.cache_data
def load_metrics():
    path = BASE / "results" / "evaluation_results_per_slot_v3.csv"
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame()

@st.cache_data
def load_synoptic():
    path = BASE / "results" / "synoptic_skill_per_regime.csv"
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame()

def load_forecast_log():
    path = BASE / "data" / "forecast_log.csv"
    if path.exists():
        return pd.read_csv(path, parse_dates=['date'])
    return pd.DataFrame()

def apply_calibrator(artifact, raw_prob):
    cal = artifact.get('calibrator')
    if cal is None: return raw_prob
    if artifact.get('calib_method') == 'sigmoid':
        return cal.predict_proba(np.array([[raw_prob]]))[0][1]
    return float(cal.predict([raw_prob])[0])

def compute_derived(obs, slot_id):
    m   = int(obs.get('month', datetime.now().month))
    doy = int(obs.get('doy',   datetime.now().timetuple().tm_yday))
    obs['DTR'] = obs.get('MAX',30) - obs.get('MIN',20)
    obs['HA_flag'] = 0; obs['RF_nonzero'] = 1 if obs.get('RF',0)>0 else 0
    obs['SEASON'] = 1 if m in [3,4,5] else 2 if m in [6,7,8,9] else 3 if m in [10,11] else 0
    obs['MONTH_sin'] = math.sin(2*math.pi*m/12); obs['MONTH_cos'] = math.cos(2*math.pi*m/12)
    obs['DOY_sin']   = math.sin(2*math.pi*doy/365); obs['DOY_cos'] = math.cos(2*math.pi*doy/365)
    obs['doy_sin']   = obs['DOY_sin']; obs['doy_cos'] = obs['DOY_cos']
    obs['slot_sin']  = math.sin(2*math.pi*slot_id/4); obs['slot_cos'] = math.cos(2*math.pi*slot_id/4)
    CLIM = {(4,2):0.129,(5,2):0.194,(6,2):0.096,(7,2):0.032,(8,2):0.052,(9,2):0.079,(10,2):0.077}
    obs['slot_month_clim'] = CLIM.get((m,slot_id), 0.02)
    obs['ERA5_CAPE'] = obs.get('ERA5_CAPE', obs.get('CAPE', 0.0))
    obs['CIN'] = obs.get('CIN', 0.0)
    q850=obs.get('ERA5_q_850hPa',0.013); q700=obs.get('ERA5_q_700hPa',0.009); q500=obs.get('ERA5_q_500hPa',0.003)
    t850=obs.get('ERA5_t_850hPa',293.0); t500=obs.get('ERA5_t_500hPa',268.0)
    u850=obs.get('ERA5_u_850hPa',-3.0); v850=obs.get('ERA5_v_850hPa',2.0)
    u700=obs.get('ERA5_u_700hPa',2.0);  v700=obs.get('ERA5_v_700hPa',1.0)
    u500=obs.get('ERA5_u_500hPa',5.0);  v500=obs.get('ERA5_v_500hPa',2.0)
    CAPE=obs.get('CAPE',0.0); K=obs.get('K_INDEX',30.0); LI=obs.get('LIFTED_INDEX',-2.0); TT=obs.get('TOTALS_TOTALS',44.0)
    obs['cape_x_kindex']      = CAPE*K
    obs['li_x_totals']        = abs(LI)*TT
    obs['q_gradient_500_850'] = q850-q500
    obs['thetae_850']         = t850+2491*q850
    obs['wind_shear_500_850'] = ((u500-u850)**2+(v500-v850)**2)**0.5
    obs['wind_shear_700_850'] = ((u700-u850)**2+(v700-v850)**2)**0.5
    obs['moisture_flux_850']  = q850*(u850**2+v850**2)**0.5
    obs['moisture_flux_700']  = q700*(u700**2+v700**2)**0.5
    obs['thickness_500_850']  = t850-t500
    obs['mid_level_drying']   = q700/(q850+1e-9)
    return obs

def predict_all_slots(models, obs_base):
    results = {}
    for slot_id in range(4):
        artifact = models.get(slot_id)
        if artifact is None:
            results[slot_id] = {'probability':0.0,'predicted':False,'threshold':0.5}
            continue
        obs = {**obs_base, 'slot': slot_id}
        obs = compute_derived(obs, slot_id)
        X   = np.array([[float(obs.get(c,0.0)) for c in artifact['feature_cols']]])
        raw = float(artifact['model'].predict_proba(X)[0][1])
        cal = apply_calibrator(artifact, raw)
        results[slot_id] = {'probability':float(cal),'predicted':float(cal)>=artifact['threshold'],'threshold':artifact['threshold']}
    return results

def prob_color_class(prob):
    if prob >= 0.60: return "high",   "prob-high",   "#ef5350"
    if prob >= 0.40: return "moderate","prob-mod",    "#ff9800"
    if prob >= 0.20: return "low",     "prob-low",    "#ffc107"
    return "clear", "prob-none", "#4caf50"

def main():
    models  = load_models()
    dataset = load_dataset()
    shap_df = load_shap()
    metrics_df  = load_metrics()
    synoptic_df = load_synoptic()

    if len(models) == 0:
        st.error("No models found.")
        return

    # ── SIDEBAR ───────────────────────────────────────────────────────────────
    with st.sidebar:
        hero_path = BASE / "hero.png"
        if hero_path.exists():
            st.image(str(hero_path), use_container_width=True)

        st.markdown('<div class="section-header">Forecast Settings</div>', unsafe_allow_html=True)
        mode = st.radio("Mode", ["📅 Date Lookup", "✏️ Manual Input"], label_visibility="collapsed")

        st.markdown('<div class="section-header" style="margin-top:1.5rem">System Status</div>', unsafe_allow_html=True)
        st.markdown(f"""
        <div class="system-status">
            <div class="status-dot"></div>
            <span style="font-size:0.8rem;color:#4caf50;font-weight:600">System Operational</span>
        </div>
        <div style="font-size:0.75rem;color:#546e7a">Last updated: {datetime.now().strftime('%H:%M IST')}</div>
        """, unsafe_allow_html=True)

        st.markdown('<div class="section-header" style="margin-top:1.5rem">Model Info</div>', unsafe_allow_html=True)
        info_items = [
            ("Models", f"{len(models)}/4 loaded"),
            ("Version", "Calibrated v3"),
            ("Features", "64 (ERA5 6-hrly)"),
            ("Training", "2015–2022"),
            ("Test", "2023–2025"),
        ]
        for label, val in info_items:
            st.markdown(f'<div class="info-row"><span class="info-label">{label}</span><span class="info-value">{val}</span></div>', unsafe_allow_html=True)

        st.markdown('<div class="section-header" style="margin-top:1.5rem">Thresholds</div>', unsafe_allow_html=True)
        for slot_id in range(4):
            if slot_id in models:
                t = models[slot_id]['threshold']
                st.markdown(f'<div class="info-row"><span class="info-label">{SLOT_EMOJI[slot_id]} Slot {slot_id}</span><span class="info-value">{t}</span></div>', unsafe_allow_html=True)

        st.markdown('<div style="margin-top:2rem;font-size:0.7rem;color:#37474f;line-height:1.6">CSIR Thunderstorm Prediction System<br>IMD Station 43295, Bengaluru<br>Collaboration: Dr. Geeta Agnihotri<br>Scientist F, IMD Bengaluru</div>', unsafe_allow_html=True)

    # ── TABS ──────────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs(["⛈️  Forecast", "📈  Performance", "🔬  SHAP Analysis", "🗺️  Synoptic Regimes"])

    # ── TAB 1: FORECAST ───────────────────────────────────────────────────────
    with tab1:
        if mode == "📅 Date Lookup" and len(dataset) > 0:
            col_d, col_i = st.columns([1,2])
            with col_d:
                selected_date = st.date_input("Select date",
                    value=datetime(2023,4,29).date(),
                    min_value=dataset[dataset['year']>=2023]['date'].min().date(),
                    max_value=dataset['date'].max().date())
            with col_i:
                st.markdown(f'<div style="padding:0.8rem;background:#0d1117;border:1px solid #1e2a45;border-radius:8px;font-size:0.82rem;color:#90a4ae;margin-top:1.6rem">Historical forecast for <span style="color:#42a5f5;font-weight:600">{selected_date}</span> — test set 2023–2025</div>', unsafe_allow_html=True)

            date_str = str(selected_date)
            day_data = dataset[dataset['date']==date_str]
            if len(day_data)==0:
                st.warning(f"No data for {date_str}")
                return
            FCOLS = [c for c in dataset.columns if c not in ['date','year','month','slot','slot_label','ts_label']]
            obs_base = {c: float(day_data.iloc[0][c]) for c in FCOLS if c in day_data.columns}
            obs_base['month'] = int(pd.Timestamp(date_str).month)
            obs_base['doy']   = int(pd.Timestamp(date_str).dayofyear)
            actual_labels = {int(r['slot']): int(r['ts_label']) for _,r in day_data.iterrows()}
        else:
            st.markdown('<div class="section-header">Manual Input</div>', unsafe_allow_html=True)
            c1,c2,c3 = st.columns(3)
            with c1:
                MAX=st.number_input("Max Temp (°C)",20.0,45.0,29.0,0.5)
                MIN=st.number_input("Min Temp (°C)",10.0,35.0,21.0,0.5)
                RF=st.number_input("Rainfall (mm)",0.0,200.0,0.0,0.5)
            with c2:
                CAPE=st.number_input("CAPE (J/kg)",0.0,5000.0,300.0,50.0)
                K_IDX=st.number_input("K-Index",0.0,50.0,35.0,0.5)
                LI=st.number_input("Lifted Index",-10.0,5.0,-2.0,0.5)
            with c3:
                TT=st.number_input("Totals-Totals",30.0,60.0,45.0,0.5)
                T2M=st.number_input("ERA5 T2M (K)",280.0,315.0,299.0,0.5)
                ECAPE=st.number_input("ERA5 CAPE (J/kg)",0.0,5000.0,300.0,50.0)
            obs_base = {
                'MAX':MAX,'MIN':MIN,'RF':RF,'AW':3.0,'EVP':5.0,'DRNRF':0.0,'SSH':300.0,
                'RF_3d':0.0,'RF_7d':0.0,'MAX_3d_avg':MAX,'MIN_3d_avg':MIN,'DTR_3d_avg':MAX-MIN,
                'RF_lag1':0.0,'MAX_lag1':MAX,'MIN_lag1':MIN,'LABEL_lag1':0,
                'CAPE':CAPE,'K_INDEX':K_IDX,'LIFTED_INDEX':LI,'TOTALS_TOTALS':TT,'PRECIP_WATER':40.0,
                'ERA5_T2M':T2M,'ERA5_D2M':293.0,'ERA5_U10':-3.0,'ERA5_V10':2.0,
                'ERA5_CAPE':ECAPE,'ERA5_SP':91500.0,
                'ERA5_t_500hPa':268.0,'ERA5_t_700hPa':283.0,'ERA5_t_850hPa':293.0,
                'ERA5_q_500hPa':0.003,'ERA5_q_700hPa':0.009,'ERA5_q_850hPa':0.013,
                'ERA5_u_500hPa':5.0,'ERA5_u_700hPa':2.0,'ERA5_u_850hPa':-3.0,
                'ERA5_v_500hPa':2.0,'ERA5_v_700hPa':1.0,'ERA5_v_850hPa':2.0,
                'ts_label_lag1_slot':0,'ts_any_yesterday':0,'CIN':0.0,
                'month':datetime.now().month,'doy':datetime.now().timetuple().tm_yday,
            }
            actual_labels = None
            date_str = datetime.now().strftime("%Y-%m-%d")

        results = predict_all_slots(models, obs_base)
        alert_slots = [s for s,r in results.items() if r['predicted']]

        # Alert/Clear banner
        if alert_slots:
            peak = max(alert_slots, key=lambda s: results[s]['probability'])
            st.markdown(f"""
            <div class="alert-banner-full">
                <span style="font-size:1.4rem">⚡</span>
                <div>
                    <div style="font-size:0.9rem;font-weight:700;color:#ef5350">THUNDERSTORM ALERT</div>
                    <div style="font-size:0.8rem;color:#ef9a9a">{len(alert_slots)} window(s) at risk · Peak risk: {SLOT_NAMES[peak]} ({results[peak]['probability']*100:.1f}%)</div>
                </div>
            </div>""", unsafe_allow_html=True)
        else:
            max_slot = max(results, key=lambda s: results[s]['probability'])
            st.markdown(f"""
            <div class="clear-banner-full">
                <span style="font-size:1.4rem">✅</span>
                <div>
                    <div style="font-size:0.9rem;font-weight:700;color:#4caf50">ALL CLEAR</div>
                    <div style="font-size:0.8rem;color:#a5d6a7">No thunderstorm predicted · Highest: {results[max_slot]['probability']*100:.1f}% ({SLOT_NAMES[max_slot]})</div>
                </div>
            </div>""", unsafe_allow_html=True)

        # Slot cards
        cols = st.columns(4)
        for slot_id in range(4):
            with cols[slot_id]:
                res   = results[slot_id]
                prob  = res['probability']
                pct   = prob * 100
                card_class, prob_class, bar_color = prob_color_class(prob)
                thresh_pct = res['threshold'] * 100

                # Outcome badge
                outcome_html = ""
                if actual_labels:
                    actual = actual_labels.get(slot_id, 0)
                    pred   = int(res['predicted'])
                    if pred==1 and actual==1:   outcome_html = '<div class="outcome-badge outcome-hit">✓ HIT</div>'
                    elif pred==0 and actual==1: outcome_html = '<div class="outcome-badge outcome-miss">✗ MISS</div>'
                    elif pred==1 and actual==0: outcome_html = '<div class="outcome-badge outcome-fa">✗ FALSE ALARM</div>'
                    else:                        outcome_html = '<div class="outcome-badge outcome-tn">✓ CORRECT NO</div>'

                badge_class = "badge-alert" if res['predicted'] else "badge-clear"
                badge_text  = "⚠ ALERT" if res['predicted'] else "✓ CLEAR"

                st.markdown(f"""
                <div class="slot-card {card_class}">
                    <div class="slot-label">{SLOT_EMOJI[slot_id]} {SLOT_LABELS[slot_id]}</div>
                    <div class="slot-time">{SLOT_NAMES[slot_id]}</div>
                    <div class="prob-number {prob_class}">{pct:.1f}%</div>
                    <div class="prob-bar-container">
                        <div class="prob-bar-fill" style="width:{min(pct,100)}%;background:{bar_color}"></div>
                        <div class="threshold-line" style="left:{thresh_pct}%"></div>
                    </div>
                    <div style="font-size:0.65rem;color:#37474f;margin-bottom:6px">threshold: {thresh_pct:.0f}%</div>
                    <span class="alert-badge {badge_class}">{badge_text}</span>
                    {outcome_html}
                </div>""", unsafe_allow_html=True)

        # Timeline
        st.markdown('<div style="margin-top:1.5rem"><div class="section-header">24-Hour Risk Timeline</div></div>', unsafe_allow_html=True)
        fig = go.Figure()
        for slot_id in range(4):
            prob  = results[slot_id]['probability']*100
            _, _, color = prob_color_class(results[slot_id]['probability'])
            fig.add_trace(go.Bar(
                x=[prob], y=[SLOT_NAMES[slot_id]], orientation='h',
                marker_color=color, marker_opacity=0.85,
                text=[f"{prob:.1f}%"], textposition='inside',
                textfont=dict(color='white', size=12),
                name=SLOT_NAMES[slot_id], showlegend=False,
            ))
        fig.add_vline(x=45, line_dash="dot", line_color="rgba(255,255,255,0.3)",
                      annotation_text="Default threshold", annotation_font_color="#607d8b",
                      annotation_position="top right")
        fig.update_layout(
            height=180, margin=dict(t=10,b=10,l=10,r=10),
            xaxis=dict(range=[0,100], title="Probability (%)", color="#607d8b",
                       gridcolor="#1e2a45", tickfont=dict(color="#607d8b")),
            yaxis=dict(color="#90a4ae", tickfont=dict(color="#90a4ae")),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='#0d1117',
        )
        st.plotly_chart(fig, use_container_width=True)

        with st.expander("📋 JSON Output"):
            import json
            output = [{"date":date_str,"slot":s,"slot_label":SLOT_NAMES[s],
                       "ts_probability":round(r['probability'],4),
                       "ts_predicted":r['predicted'],"threshold_used":r['threshold'],
                       "model":"calibrated_v3"} for s,r in results.items()]
            st.json(output)

    # ── TAB 2: PERFORMANCE ────────────────────────────────────────────────────
    with tab2:
        st.markdown('<div class="section-header">Model Performance — Test Set 2023–2025</div>', unsafe_allow_html=True)
        if len(metrics_df) > 0:
            c1,c2 = st.columns(2)
            with c1:
                st.markdown('<div class="section-header">Per-Slot Metrics</div>', unsafe_allow_html=True)
                disp = metrics_df[['Slot_label','AUROC','POD','FAR','CSI','HSS','Threshold']].copy()
                disp.columns = ['Slot','AUROC','POD','FAR','CSI','HSS','Threshold']
                st.dataframe(disp, use_container_width=True, hide_index=True)
            with c2:
                st.markdown('<div class="section-header">vs Baselines</div>', unsafe_allow_html=True)
                comp = pd.DataFrame([
                    {'Model':'Daily XGBoost','AUROC':0.8715,'HSS':0.389,'CSI':0.293},
                    {'Model':'Ensemble A9',  'AUROC':0.8456,'HSS':0.365,'CSI':0.266},
                    {'Model':'Slot 2 v3',    'AUROC':0.833, 'HSS':0.318,'CSI':0.224},
                ])
                fig = px.bar(comp, x='Model', y=['AUROC','HSS','CSI'], barmode='group',
                             color_discrete_sequence=['#42a5f5','#4caf50','#ff9800'])
                fig.update_layout(height=280, margin=dict(t=10,b=10),
                                  paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='#0d1117',
                                  font=dict(color='#90a4ae'),
                                  legend=dict(font=dict(color='#90a4ae')),
                                  xaxis=dict(gridcolor='#1e2a45'),
                                  yaxis=dict(gridcolor='#1e2a45'))
                st.plotly_chart(fig, use_container_width=True)

        log = load_forecast_log()
        if len(log) > 0:
            st.markdown('<div class="section-header" style="margin-top:1rem">Recent Forecast Log</div>', unsafe_allow_html=True)
            st.dataframe(log.tail(12), use_container_width=True, hide_index=True)

    # ── TAB 3: SHAP ───────────────────────────────────────────────────────────
    with tab3:
        st.markdown('<div class="section-header">SHAP Feature Importance — v3 Models</div>', unsafe_allow_html=True)
        if len(shap_df) > 0:
            slot_sel = st.selectbox("Slot", [0,1,2,3], format_func=lambda x: f"Slot {x} — {SLOT_NAMES[x]}")
            slot_shap = shap_df[shap_df['slot']==slot_sel].head(15)
            NEW = ['cape_x_kindex','li_x_totals','q_gradient_500_850','thetae_850',
                   'wind_shear_700_850','wind_shear_500_850','moisture_flux_850',
                   'moisture_flux_700','mid_level_drying','thickness_500_850']
            colors = ['#ef5350' if f in NEW else '#42a5f5' for f in slot_shap['feature'].values[::-1]]
            fig = go.Figure(go.Bar(
                x=slot_shap['mean_shap'].values[::-1],
                y=slot_shap['feature'].values[::-1],
                orientation='h', marker_color=colors, marker_opacity=0.85,
            ))
            fig.update_layout(
                height=460, margin=dict(t=10,b=10,l=10,r=10),
                xaxis=dict(title="Mean |SHAP|", color="#607d8b", gridcolor="#1e2a45"),
                yaxis=dict(color="#90a4ae"),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='#0d1117',
            )
            st.plotly_chart(fig, use_container_width=True)
            st.markdown('<div style="font-size:0.75rem;color:#607d8b">🔴 Red = new derived features (v3) · 🔵 Blue = original features</div>', unsafe_allow_html=True)

    # ── TAB 4: SYNOPTIC ───────────────────────────────────────────────────────
    with tab4:
        st.markdown('<div class="section-header">Synoptic Weather Regime Analysis</div>', unsafe_allow_html=True)
        if len(synoptic_df) > 0:
            c1,c2 = st.columns([3,2])
            with c1:
                fig = px.bar(synoptic_df.sort_values('ts_rate',ascending=True),
                    x='ts_rate', y='regime_name', orientation='h',
                    color='AUROC', color_continuous_scale='RdYlGn',
                    title='TS Frequency & Model Skill per Regime',
                    text='ts_rate')
                fig.update_traces(texttemplate='%{text:.1f}%', textposition='inside',
                                  textfont=dict(color='white'))
                fig.update_layout(height=320, margin=dict(t=40,b=10),
                                  paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='#0d1117',
                                  font=dict(color='#90a4ae'),
                                  xaxis=dict(gridcolor='#1e2a45'),
                                  yaxis=dict(gridcolor='#1e2a45'))
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                st.markdown('<div class="section-header">Key Findings</div>', unsafe_allow_html=True)
                for _,row in synoptic_df.sort_values('ts_rate',ascending=False).iterrows():
                    ts = row['ts_rate']; auroc = row.get('AUROC',0)
                    dot = "🔴" if ts>30 else "🟠" if ts>15 else "🟡" if ts>5 else "🟢"
                    st.markdown(f"""
                    <div style="padding:8px 0;border-bottom:1px solid #0d1117">
                        <div style="font-size:0.82rem;color:#e8eaf6;font-weight:500">{dot} {row['regime_name']}</div>
                        <div style="font-size:0.75rem;color:#607d8b;font-family:monospace">TS: {ts:.1f}% · AUROC: {auroc:.3f}</div>
                    </div>""", unsafe_allow_html=True)
                st.markdown('<div style="margin-top:1rem;padding:0.8rem;background:#0d1117;border:1px solid #1e2a45;border-radius:8px;font-size:0.78rem;color:#546e7a">Pre-monsoon convective burst (R5) has 52% TS rate but lowest model AUROC (0.773) — the hardest regime to predict.</div>', unsafe_allow_html=True)

    st.markdown('<div style="margin-top:2rem;padding-top:1rem;border-top:1px solid #1e2a45;text-align:center;font-size:0.7rem;color:#37474f">CSIR Thunderstorm Prediction System · IMD Station 43295 Bengaluru Airport · Dr. Geeta Agnihotri, Scientist F, IMD Bengaluru</div>', unsafe_allow_html=True)

if __name__ == "__main__":
    main()
