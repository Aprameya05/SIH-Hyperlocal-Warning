"""
pages/radar_panel.py
====================
Step 6b — Streamlit page: Storm Proximity Radar Panel.

Sits inside the existing dashboard's pages/ directory.
Add it by placing this file at:
  C:\\Users\\Atul\\Desktop\\csir-repo\\pages\\radar_panel.py

The page shows:
  • Alert badge (RED/ORANGE/YELLOW/GREEN) with storm distance
  • Himawari-9 BT thumbnail with 50km ring and VOBL marker
  • 6-frame BT animation (last 60 min)
  • IMERG precipitation corroboration card
  • Raw data expander for debugging

Usage:
  streamlit run main_dashboard.py
  → navigate to "Storm Proximity Radar" in sidebar
"""

import json, datetime, time
from pathlib import Path

import numpy as np
import streamlit as st

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Storm Proximity — VOBL",
    page_icon="🌩️",
    layout="wide",
)

# ── Data paths ────────────────────────────────────────────────────────────────
HIMAWARI_DIR = Path("data") / "himawari_realtime"
IMERG_DIR    = Path("data") / "imerg_realtime"

ALERT_COLORS = {
    "RED":    "#e74c3c",
    "ORANGE": "#e67e22",
    "YELLOW": "#f39c12",
    "GREEN":  "#27ae60",
    "UNKNOWN": "#7f8c8d",
}

ALERT_ICONS = {
    "RED": "🔴", "ORANGE": "🟠", "YELLOW": "🟡", "GREEN": "🟢", "UNKNOWN": "⚪"
}


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADERS
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)   # re-fetch every 60 seconds
def load_latest_himawari() -> dict:
    p = HIMAWARI_DIR / "himawari_latest.json"
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


@st.cache_data(ttl=60)
def load_latest_imerg() -> dict:
    p = IMERG_DIR / "imerg_latest.json"
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


@st.cache_data(ttl=60)
def load_history(n: int = 6) -> list[dict]:
    log_path = HIMAWARI_DIR / "himawari_vobl_log.jsonl"
    if not log_path.exists():
        return []
    with open(log_path) as f:
        lines = [l.strip() for l in f if l.strip()]
    frames = []
    for line in lines[-n:]:
        try:
            frames.append(json.loads(line))
        except Exception:
            pass
    return frames


def load_latest_png() -> Path | None:
    pngs = sorted(HIMAWARI_DIR.glob("himawari_vobl_2*.png"))
    return pngs[-1] if pngs else None


def load_all_pngs(n: int = 6) -> list[Path]:
    return sorted(HIMAWARI_DIR.glob("himawari_vobl_2*.png"))[-n:]


def data_age_minutes(fetched_str: str) -> float:
    try:
        fetched = datetime.datetime.strptime(fetched_str, "%Y-%m-%dT%H:%M:%SZ")
        return round((datetime.datetime.utcnow() - fetched).total_seconds() / 60, 1)
    except Exception:
        return -1.0


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PAGE
# ─────────────────────────────────────────────────────────────────────────────

def main():
    st.title("🌩️ Storm Proximity — VOBL / Bengaluru Airport")
    st.caption(
        "Satellite-derived storm detection within 50 km of the airport. "
        "Source: Himawari-9 Band 13 (10.4 μm IR) via NOAA/JAXA. "
        "Updates every 10 minutes."
    )

    # Auto-refresh toggle
    col_ctrl1, col_ctrl2, col_ctrl3 = st.columns([2, 2, 6])
    with col_ctrl1:
        auto_refresh = st.toggle("Auto-refresh (60s)", value=True)
    with col_ctrl2:
        if st.button("🔄 Refresh now"):
            st.cache_data.clear()
            st.rerun()

    # ── Load data ─────────────────────────────────────────────────────────────
    h = load_latest_himawari()
    imerg = load_latest_imerg()
    history = load_history(6)

    if not h:
        st.error(
            "⚠️ No Himawari data found. "
            "Run `python fetch_himawari_realtime.py` first.",
            icon="🚨"
        )
        st.info(
            "**First-time setup:**\n"
            "```\n"
            "pip install boto3 requests numpy Pillow matplotlib\n"
            "python fetch_himawari_realtime.py\n"
            "```"
        )
        return

    alert      = h.get("alert_level", "UNKNOWN")
    alert_col  = ALERT_COLORS[alert]
    alert_icon = ALERT_ICONS[alert]
    age        = data_age_minutes(h.get("fetched_at_utc", ""))

    # ── ALERT BANNER ──────────────────────────────────────────────────────────
    banner_bg = {
        "RED":    "#2c0a0a", "ORANGE": "#2c1400",
        "YELLOW": "#2c2200", "GREEN":  "#0a2c0a", "UNKNOWN": "#1a1a1a",
    }[alert]

    st.markdown(
        f"""
        <div style="
            background: {banner_bg};
            border: 2px solid {alert_col};
            border-radius: 12px;
            padding: 18px 24px;
            margin-bottom: 20px;
        ">
          <span style="font-size: 2.2rem; font-weight: 900;
                       color: {alert_col};">
            {alert_icon} {alert}
          </span>
          <span style="font-size: 1.1rem; color: #ccc; margin-left: 16px;">
            {h.get('alert_description', '')}
          </span>
          <div style="color: #888; font-size: 0.85rem; margin-top: 6px;">
            Scene: {h.get('scene_time_ist', 'N/A')} &nbsp;|&nbsp;
            Source: {h.get('data_source', 'N/A')} &nbsp;|&nbsp;
            Data age: {age:.0f} min ago
            {"&nbsp; ⚠️ STALE" if age > 20 else ""}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── METRIC CARDS ROW ──────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)

    nearest = h.get("nearest_storm_km")
    c1.metric(
        "Nearest storm cell",
        f"{nearest:.0f} km" if nearest is not None else "None detected",
        delta="⚠️ Within 50 km!" if (nearest and nearest < 50) else None,
        delta_color="inverse",
    )
    c2.metric(
        "BT minimum",
        f"{h.get('bt_min_C', 'N/A')} °C",
        help="Brightness temp of coldest cloud top in 50 km box. < –40°C = active storm.",
    )
    c3.metric(
        "Deep conv. area",
        f"{h.get('area_deep_conv_km2', 0):.0f} km²",
        help="Area of cloud tops below –40°C within monitoring box.",
    )
    c4.metric(
        "Storm pixels (< –40°C)",
        h.get("n_pixels_below_m40C", 0),
    )
    if imerg:
        c5.metric(
            "IMERG precip max",
            f"{imerg.get('precip_max_mm_hr', 'N/A')} mm/hr",
            help=f"GPM IMERG Early — {imerg.get('scene_time_ist', 'N/A')} "
                 f"(lag {imerg.get('latency_hours', '?')} h)",
        )
    else:
        c5.metric("IMERG precip max", "No data",
                  help="Run fetch_imerg_realtime.py (needs Earthdata token)")

    st.divider()

    # ── MAIN CONTENT: Image + History side by side ─────────────────────────────
    col_img, col_hist = st.columns([3, 2], gap="large")

    with col_img:
        st.subheader("📡 Latest Satellite View")
        latest_png = load_latest_png()
        if latest_png:
            st.image(str(latest_png), use_column_width=True,
                     caption=f"Himawari-9 B13 (10.4 μm) — "
                             f"{h.get('scene_time_ist', '')}. "
                             "★ = VOBL. Dashed ring = 50 km radius. "
                             "Deep blue/purple = thunderstorm tops (< –40°C).")
        else:
            st.info("No thumbnail available — check that matplotlib is installed.")

        # Download button
        if latest_png:
            with open(latest_png, "rb") as f:
                st.download_button(
                    "⬇️ Download PNG",
                    data=f.read(),
                    file_name=f"himawari_vobl_{h.get('scene_time_utc', 'latest')}.png",
                    mime="image/png",
                )

    with col_hist:
        st.subheader("🕐 Last 60 Min History")

        if not history:
            st.info("History will appear after a few 10-min fetch cycles.")
        else:
            # Alert timeline
            for frame in reversed(history):
                fa     = frame.get("alert_level", "?")
                fa_col = ALERT_COLORS.get(fa, "#888")
                fa_ico = ALERT_ICONS.get(fa, "⚪")
                ist    = frame.get("scene_time_ist", "N/A")
                bt     = frame.get("bt_min_C")
                near   = frame.get("nearest_storm_km")

                st.markdown(
                    f"""
                    <div style="
                        border-left: 4px solid {fa_col};
                        padding: 8px 12px; margin-bottom: 8px;
                        background: rgba(255,255,255,0.03);
                        border-radius: 0 6px 6px 0;
                    ">
                        <span style="color:{fa_col}; font-weight:700;">
                            {fa_ico} {fa}
                        </span>
                        <span style="color:#aaa; font-size:0.85rem;
                                     margin-left:10px;">{ist}</span>
                        <br/>
                        <span style="color:#888; font-size:0.78rem;">
                            BT min: {f"{bt:.1f}°C" if bt is not None else "N/A"}
                            &nbsp;|&nbsp;
                            Nearest: {f"{near:.0f} km" if near is not None else "—"}
                        </span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        # 6-frame animation
        all_pngs = load_all_pngs(6)
        if len(all_pngs) > 1:
            st.subheader("🎞️ Animation (last 6 frames)")
            frame_idx = st.slider("Frame", 0, len(all_pngs) - 1,
                                  len(all_pngs) - 1, key="anim_slider")
            st.image(str(all_pngs[frame_idx]), use_column_width=True)

    st.divider()

    # ── IMERG CORROBORATION CARD ───────────────────────────────────────────────
    st.subheader("🛰️ GPM IMERG Precipitation Corroboration")
    if imerg:
        ic1, ic2, ic3, ic4 = st.columns(4)
        ic1.metric("Max rain rate",
                   f"{imerg.get('precip_max_mm_hr', 'N/A')} mm/hr")
        ic2.metric("Mean rain rate",
                   f"{imerg.get('precip_mean_mm_hr', 'N/A')} mm/hr")
        ic3.metric("Heavy rain area (>10 mm/hr)",
                   f"{imerg.get('heavy_area_km2', 0):.0f} km²")
        ic4.metric("Extreme rain area (>30 mm/hr)",
                   f"{imerg.get('extreme_area_km2', 0):.0f} km²")

        st.caption(
            f"Source: {imerg.get('data_source', 'GPM IMERG')} — "
            f"scene {imerg.get('scene_time_ist', 'N/A')} — "
            f"latency {imerg.get('latency_hours', '?')} h behind real-time. "
            "IMERG is not real-time — use as lagged corroboration only."
        )

        # Corroboration agreement check
        h_flag  = 1 if alert in ["RED", "ORANGE"] else 0
        i_flag  = imerg.get("convection_flag", 0)
        if h_flag and i_flag:
            st.success(
                "✅ Both Himawari IR and IMERG precipitation confirm convective activity.",
                icon="✅"
            )
        elif h_flag and not i_flag:
            st.info(
                "ℹ️ Himawari shows cold cloud tops but IMERG precip is low — "
                "could be anvil cloud or IMERG lag.",
                icon="ℹ️"
            )
        elif not h_flag and i_flag:
            st.warning(
                "⚠️ IMERG shows precipitation but Himawari BT is warm — "
                "check for shallow/warm-top rain systems.",
                icon="⚠️"
            )
    else:
        st.info(
            "No IMERG data. Setup: `python fetch_imerg_realtime.py --setup-auth`"
        )

    st.divider()

    # ── COORDINATE + SOURCE INFO ───────────────────────────────────────────────
    with st.expander("ℹ️ Data source & methodology"):
        st.markdown("""
**Primary source:** Himawari-9 Band 13 (10.4 μm clean IR window)
- Satellite position: 140.7°E geostationary
- Full-disk coverage: 11,000 × 11,000 pixels at 2 km/pixel
- Monitoring segment: Segment 5/10 (rows 4400–5499 of full disk)
- VOBL pixel: col=2524, row=4726
- Crop: ±50 km bounding box (cols 2515–2534, rows 4700–4753)
- Update cadence: every 10 minutes
- Source: NOAA S3 `s3://noaa-himawari9` (anonymous) → JAXA P-Tree → NICT PNG

**Alert thresholds:**
| Level | BT threshold | Meaning |
|-------|-------------|---------|
| 🔴 RED | < –40°C within 50 km | Deep convective cell confirmed near airport |
| 🟠 ORANGE | < –40°C anywhere in crop | Storm approaching |
| 🟡 YELLOW | < –20°C within 50 km | Elevated cloud, developing |
| 🟢 GREEN | No threshold met | Clear vicinity |

**Corroboration:** GPM IMERG Early Run (NASA), 0.1°, 30-min, ~4h latency.

**Limitations:** Satellite IR is a proxy — it detects cold cloud tops, not
direct radar reflectivity. Anvil cirrus from distant storms can produce false
ORANGE alerts. IMERG lag means it cannot confirm active storms in real time.
Contact Dr. Agnihotri (IMD Bengaluru) for VOBL DWR access once commissioned.
        """)

    with st.expander("🔧 Raw JSON data"):
        st.subheader("Himawari latest")
        st.json(h)
        if imerg:
            st.subheader("IMERG latest")
            st.json(imerg)

    # ── Auto-refresh ───────────────────────────────────────────────────────────
    if auto_refresh:
        time.sleep(60)
        st.cache_data.clear()
        st.rerun()


if __name__ == "__main__":
    main()