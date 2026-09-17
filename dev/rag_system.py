"""
rag_system.py
=============
RAG system for CSIR Thunderstorm Nowcast — Groq/Llama3 version.
Three capabilities:
  1. Meteorological knowledge base Q&A
  2. Forecast explainer (why did slot fire?)
  3. Historical analog retrieval

Author: Aprameya, CSIR Thunderstorm Project
"""

import os
import json
import argparse
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

BASE       = Path(r"C:\Users\Aprameya\OneDrive\Pictures\Desktop\CSIR_Thunderstorm")
DATA       = BASE / "data"
RESULTS    = BASE / "results"
CHROMA_DIR = BASE / "rag" / "chroma_db"
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

SLOT_NAMES = {
    0: "0001-0600 IST (Late Night)",
    1: "0601-1200 IST (Morning)",
    2: "1201-1800 IST (Afternoon)",
    3: "1801-2400 IST (Evening)",
}

KNOWLEDGE_DOCS = [
    {
        "id": "cape_001",
        "title": "CAPE — Convective Available Potential Energy",
        "content": """CAPE measures energy available to accelerate air parcels upward.
At Bengaluru Airport (Station 43295) CAPE thresholds:
- CAPE < 100 J/kg: Very low risk.
- CAPE 100-500 J/kg: Moderate instability, storms possible with trigger.
- CAPE 500-1500 J/kg: High instability, storms likely pre-monsoon.
- CAPE > 1500 J/kg: Extreme instability.
Monsoon season CAPE is suppressed by cloud cover — K-Index more useful then.
Pre-monsoon (March-May) sees highest CAPE, often exceeding 1000 J/kg.
CAPE is the #1 SHAP feature for Slot 2 (1201-1800 IST).
cape_x_kindex interaction term ranks #2 in v3 model (SHAP 0.755).""",
        "category": "stability_index",
        "tags": ["CAPE", "instability", "thunderstorm", "pre-monsoon"]
    },
    {
        "id": "kindex_001",
        "title": "K-Index — Atmospheric Moisture and Instability",
        "content": """K-Index = (T850 - T500) + Td850 - (T700 - Td700)
Thresholds for Bengaluru:
- K < 20: Very low TS potential
- K 20-25: Isolated storms possible
- K 25-30: Scattered storms likely
- K 30-35: Numerous storms expected
- K > 35: Extreme outbreak conditions
K-Index is #1 SHAP feature for Slot 3 (1801-2400 IST) and #3 for Slot 2.
During monsoon breaks K remains high (>35) even when CAPE is suppressed.""",
        "category": "stability_index",
        "tags": ["K-Index", "moisture", "monsoon", "stability"]
    },
    {
        "id": "totals_001",
        "title": "Totals-Totals Index",
        "content": """TT = (T850 + Td850) - 2*T500
Thresholds: TT < 44 unlikely, 44-50 possible, 50-55 scattered, >55 severe.
At Bengaluru, TS days typically show TT > 46.
TT is the #1 SHAP feature overall in v3 model (SHAP 0.823 for Slot 2).
Most effective pre-monsoon and post-monsoon when mid-level contrast is high.""",
        "category": "stability_index",
        "tags": ["Totals-Totals", "stability", "thermal", "instability"]
    },
    {
        "id": "lifted_001",
        "title": "Lifted Index — Parcel Stability",
        "content": """LI = temperature difference between lifted surface parcel and environment at 500 hPa.
Negative = unstable. Thresholds: LI > 0 stable, 0 to -2 marginal, -2 to -4 moderate,
-4 to -6 very unstable, < -6 extreme.
October post-monsoon storms often occur with LI -0.5 to -2.0 — too weak for model
to reliably detect. Primary source of missed storms (FN) in error analysis.""",
        "category": "stability_index",
        "tags": ["Lifted Index", "stability", "parcel", "forced convection"]
    },
    {
        "id": "slot2_met_001",
        "title": "Slot 2 Meteorology — Afternoon Convection (1201-1800 IST)",
        "content": """Slot 2 is the primary operational window — 30% of all TS events 2015-2025.
Why afternoon peaks: surface heating 1400-1500 IST maximises CAPE; sea breeze from
Bay of Bengal arrives 1200-1300 IST providing low-level convergence; differential
heating between Deccan Plateau and Karnataka plains enhances convective initiation.
Key SHAP features: TOTALS_TOTALS 0.823, cape_x_kindex 0.755, K_INDEX 0.707,
CAPE 0.625, ERA5_T2M 0.540.
Model performance v3 calibrated: AUROC 0.821, POD 0.356, FAR 0.623, HSS 0.318.
Best rolling year: 2023 — AUROC 0.896, HSS 0.428.""",
        "category": "slot_meteorology",
        "tags": ["Slot 2", "afternoon", "convection", "Bengaluru", "operational"]
    },
    {
        "id": "slot3_met_001",
        "title": "Slot 3 Meteorology — Evening Convection (1801-2400 IST)",
        "content": """Slot 3 = evening thunderstorms from residual instability + nocturnal low-level jet.
ERA5_CAPE at 18Z is critical — jumped from rank 42 to rank 2 with 6-hourly ERA5.
thetae_850 is #3 SHAP feature for Slot 3, capturing boundary layer moisture buildup.
Model performance v3 calibrated: AUROC 0.854, Brier improved 64% after calibration.
Only 21 test positives in 2023-2025 — most data-limited slot.""",
        "category": "slot_meteorology",
        "tags": ["Slot 3", "evening", "nocturnal", "ERA5", "calibration"]
    },
    {
        "id": "synoptic_r5_001",
        "title": "R5 Pre-Monsoon Convective Burst — Hardest Regime",
        "content": """R5 = pre-monsoon convective burst, primarily April-May.
CAPE median 998 J/kg, q850 = 14.43 g/kg, TS rate 52.1%.
Despite highest storm frequency, model AUROC only 0.773 — lowest of all regimes.
When almost every day has a storm, marginal signal distinguishing storm from non-storm
becomes very subtle. Solution: 200hPa upper-level divergence features being added.
Synoptic drivers: heat low over Deccan Plateau, pre-monsoon trough from NW India,
mid-tropospheric cyclonic vortex over Karnataka.""",
        "category": "synoptic_regime",
        "tags": ["R5", "pre-monsoon", "convective burst", "May", "hardest"]
    },
    {
        "id": "october_miss_001",
        "title": "October Post-Monsoon Miss Pattern",
        "content": """October is worst month — 17 of 53 missed Slot 2 storms (32% of all misses).
Why: post-monsoon NE monsoon onset brings synoptically-forced storms with low CAPE
(median 186 J/kg for misses vs 538 J/kg for hits). Convection driven by upper-level
forcing not surface heating. Model is thermodynamics-driven and cannot detect
dynamically-forced events. Solution: ERA5 200hPa u/v winds being added for v4 model.
October pattern: retreating SW monsoon leaves residual moisture, NE monsoon cyclonic
circulation from Bay of Bengal, upper-level jet over South India triggers lifting.""",
        "category": "error_analysis",
        "tags": ["October", "post-monsoon", "forced convection", "missed storms"]
    },
    {
        "id": "model_perf_001",
        "title": "Model Performance Summary",
        "content": """Daily XGBoost baseline: AUROC 0.871, POD 0.500, FAR 0.586, HSS 0.389.
v1 slot models (daily ERA5): weighted AUROC 0.839, Slot 2 HSS 0.303.
v2 slot models (6-hourly ERA5): weighted AUROC 0.835, Slot 2 HSS 0.318.
v3 slot models (6-hrly ERA5 + derived features): Slot 2 AUROC 0.821 HSS 0.318,
Slot 3 AUROC 0.854, Brier improved 64% after calibration.
Ensemble A9: daily AUROC 0.846, HSS 0.365.
LSTM/CNN: both AUROC ~0.79 — XGBoost wins (dataset too small for deep learning).
Synoptic regime skill: R1 winter AUROC 1.0 (trivial), R2 moist monsoon 0.934,
R5 pre-monsoon burst 0.773 (hardest).
Training: 2015-2022. Test: 2023-2025.""",
        "category": "model_performance",
        "tags": ["AUROC", "HSS", "POD", "FAR", "model comparison", "calibration"]
    },
    {
        "id": "cape_x_kindex_001",
        "title": "cape_x_kindex — New Interaction Feature (v3)",
        "content": """cape_x_kindex = CAPE × K_INDEX, introduced in v3 (A12).
Captures simultaneous thermodynamic instability + moisture.
High CAPE alone = dry instability (no storm). High K alone = stable moist (no storm).
High CAPE × High K = moist instability = high storm probability.
SHAP: Slot 0 #3 (0.504), Slot 1 #5 (0.434), Slot 2 #2 (0.755).
Correlation with Slot 2 TS label r=0.325 — strongest of all new v3 features.""",
        "category": "derived_features",
        "tags": ["cape_x_kindex", "interaction", "v3", "new feature", "SHAP"]
    },
    {
        "id": "thetae_001",
        "title": "thetae_850 — Equivalent Potential Temperature",
        "content": """thetae_850 = T850 + (Lv/Cp) * q850, where Lv/Cp ≈ 2491 K/(kg/kg).
High thetae = warm moist boundary layer = high convective potential.
thetae > 340K: active convection favoured. thetae > 350K: explosive convection.
At Bengaluru, thetae_850 is #3 SHAP feature for Slot 3 (SHAP 0.633).
Evening convection driven by boundary layer moisture built during day — thetae
captures this directly unlike CAPE which requires full parcel calculation.""",
        "category": "derived_features",
        "tags": ["thetae", "equivalent potential temperature", "v3", "Slot 3", "moisture"]
    },
]


def init_chroma():
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        return client
    except ImportError:
        print("ChromaDB not installed. Run: pip install chromadb")
        return None


def get_embedder():
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer('all-MiniLM-L6-v2')
    except ImportError:
        print("sentence-transformers not installed.")
        return None


def build_knowledge_base(client, embedder):
    print("\n[1/3] Building meteorological knowledge base...")
    try:
        client.delete_collection("met_knowledge")
    except Exception:
        pass
    collection = client.create_collection("met_knowledge")
    docs   = [d['content'] for d in KNOWLEDGE_DOCS]
    ids    = [d['id'] for d in KNOWLEDGE_DOCS]
    metas  = [{"title": d['title'], "category": d['category'],
               "tags": ','.join(d['tags'])} for d in KNOWLEDGE_DOCS]
    embeds = embedder.encode(docs).tolist()
    collection.add(documents=docs, ids=ids, metadatas=metas, embeddings=embeds)
    print(f"  Added {len(docs)} meteorological documents")
    return collection


def build_historical_index(client, embedder):
    print("\n[2/3] Building historical analog index...")
    try:
        client.delete_collection("historical_days")
    except Exception:
        pass
    collection = client.create_collection("historical_days")

    dataset_path = DATA / "bengaluru_6hr_training_dataset_v3.csv"
    if not dataset_path.exists():
        dataset_path = DATA / "bengaluru_6hr_training_dataset_v2.csv"
    if not dataset_path.exists():
        print("  Dataset not found — skipping historical index")
        return None

    df    = pd.read_csv(dataset_path, parse_dates=['date'])
    slot2 = df[df['slot'] == 2].copy()

    ANALOG_FEATURES = ['CAPE', 'K_INDEX', 'LIFTED_INDEX', 'TOTALS_TOTALS',
                       'ERA5_T2M', 'ERA5_CAPE', 'ERA5_q_850hPa', 'ERA5_t_850hPa',
                       'MONTH_sin', 'MONTH_cos']
    available = [f for f in ANALOG_FEATURES if f in slot2.columns]

    from sklearn.preprocessing import StandardScaler
    X      = slot2[available].fillna(0).values
    scaler = StandardScaler()
    X_sc   = scaler.fit_transform(X)

    docs, ids, metas, embeds = [], [], [], []
    for i, (_, row) in enumerate(slot2.iterrows()):
        date_str = str(row['date'].date()) if hasattr(row['date'], 'date') else str(row['date'])[:10]
        label    = int(row['ts_label'])
        text = (f"Date: {date_str}. Slot 2 afternoon. "
                f"CAPE={row.get('CAPE', 0):.0f} J/kg. "
                f"K-Index={row.get('K_INDEX', 0):.1f}. "
                f"Lifted Index={row.get('LIFTED_INDEX', 0):.2f}. "
                f"Totals-Totals={row.get('TOTALS_TOTALS', 0):.1f}. "
                f"ERA5 T2M={row.get('ERA5_T2M', 0):.1f} K. "
                f"Month={int(row.get('month', 6))}. "
                f"Thunderstorm: {'YES' if label else 'NO'}.")
        docs.append(text)
        ids.append(f"day_{date_str}_s2")
        metas.append({"date": date_str, "ts_label": label,
                      "CAPE": float(row.get('CAPE', 0)),
                      "K_INDEX": float(row.get('K_INDEX', 0)),
                      "month": int(row.get('month', 6))})
        embeds.append(X_sc[i].tolist())

    for start in range(0, len(docs), 500):
        collection.add(
            documents=docs[start:start+500],
            ids=ids[start:start+500],
            metadatas=metas[start:start+500],
            embeddings=embeds[start:start+500],
        )

    import joblib
    joblib.dump(scaler, BASE / "rag" / "analog_scaler.pkl")
    print(f"  Indexed {len(docs)} historical Slot 2 days")
    return collection


def build_all():
    client   = init_chroma()
    embedder = get_embedder()
    if not client or not embedder:
        return
    build_knowledge_base(client, embedder)
    build_historical_index(client, embedder)
    print("\n[3/3] RAG knowledge base built successfully.")
    print(f"  Location: {CHROMA_DIR}")


def query_knowledge(question, n_results=3):
    client   = init_chroma()
    embedder = get_embedder()
    if not client or not embedder:
        return []
    try:
        collection  = client.get_collection("met_knowledge")
        query_embed = embedder.encode([question]).tolist()
        results     = collection.query(query_embeddings=query_embed, n_results=n_results)
        return results['documents'][0]
    except Exception:
        return []


def find_analogs(cape, k_index, li, tt, t2m, era5_cape, q850, t850, month, n=5):
    client   = init_chroma()
    embedder = get_embedder()
    if not client or not embedder:
        return []
    try:
        import joblib
        scaler     = joblib.load(BASE / "rag" / "analog_scaler.pkl")
        collection = client.get_collection("historical_days")
        query_vec  = scaler.transform([[cape, k_index, li, tt, t2m,
                                        era5_cape, q850, t850,
                                        np.sin(2*np.pi*month/12),
                                        np.cos(2*np.pi*month/12)]])
        results = collection.query(query_embeddings=query_vec.tolist(), n_results=n)
        analogs = []
        for doc, meta in zip(results['documents'][0], results['metadatas'][0]):
            analogs.append({
                'date':     meta['date'],
                'ts_label': meta['ts_label'],
                'CAPE':     meta.get('CAPE', 0),
                'K_INDEX':  meta.get('K_INDEX', 0),
                'month':    meta.get('month', 0),
                'summary':  doc,
            })
        return analogs
    except Exception as e:
        print(f"Analog search error: {e}")
        return []


def call_groq(prompt, max_tokens=400):
    try:
        from groq import Groq
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            return "GROQ_API_KEY not set. Run: set GROQ_API_KEY=your_key"
        client  = Groq(api_key=api_key)
        message = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.choices[0].message.content
    except Exception as e:
        return f"Groq API error: {e}"


def explain_forecast(date_str, slot_id, probability, threshold,
                     cape=None, k_index=None, li=None, tt=None,
                     era5_t2m=None, era5_cape=None, shap_top5=None):
    query            = f"thunderstorm prediction slot {slot_id} CAPE {cape} K-Index {k_index} Bengaluru"
    knowledge_chunks = query_knowledge(query, n_results=3)

    analogs = []
    if cape is not None and k_index is not None:
        month   = int(date_str[5:7]) if date_str else datetime.now().month
        analogs = find_analogs(
            cape=cape or 0, k_index=k_index or 30,
            li=li or -2, tt=tt or 44,
            t2m=era5_t2m or 299, era5_cape=era5_cape or cape or 0,
            q850=0.013, t850=293, month=month, n=5
        )

    analog_text = ""
    if analogs:
        ts_analogs  = [a for a in analogs if a['ts_label'] == 1]
        nts_analogs = [a for a in analogs if a['ts_label'] == 0]
        analog_text = (f"\nHistorical analogs: {len(ts_analogs)} of {len(analogs)} "
                       f"similar days had thunderstorms.\n"
                       f"TS days: {', '.join([a['date'] for a in ts_analogs[:3]])}.\n"
                       f"No-TS days: {', '.join([a['date'] for a in nts_analogs[:2]])}.\n")

    shap_text = ""
    if shap_top5:
        shap_text = "Top SHAP features:\n" + "".join(
            f"  {f}: {v:.3f}\n" for f, v in shap_top5)

    knowledge_text = "\n\n".join(knowledge_chunks[:2]) if knowledge_chunks else ""
    fired          = probability >= threshold

    prompt = f"""You are a meteorological AI assistant for the CSIR Thunderstorm Nowcast System
at Bengaluru Airport (IMD Station 43295). Explain this forecast professionally.

Forecast:
- Date: {date_str}, Slot {slot_id} ({SLOT_NAMES.get(slot_id, '')})
- Probability: {probability*100:.1f}%, Threshold: {threshold*100:.0f}%
- Alert: {'FIRED' if fired else 'NO ALERT'}
- CAPE: {cape} J/kg, K-Index: {k_index}, LI: {li}, TT: {tt}, ERA5 T2M: {era5_t2m} K

{shap_text}{analog_text}
Meteorological context:
{knowledge_text}

Write 3 paragraphs: (1) what the model predicted and why based on atmospheric parameters,
(2) what historical analogs suggest, (3) recommended forecaster action.
Be specific with numbers. Under 200 words. No bullet points."""

    explanation = call_groq(prompt, max_tokens=400)

    return {
        "date":              date_str,
        "slot":              slot_id,
        "probability":       probability,
        "threshold":         threshold,
        "alert_fired":       fired,
        "explanation":       explanation,
        "analogs":           analogs,
        "analogs_with_ts":   len([a for a in analogs if a['ts_label'] == 1]),
        "total_analogs":     len(analogs),
        "knowledge_chunks":  len(knowledge_chunks),
    }


def create_rag_router():
    try:
        from fastapi import APIRouter
        from pydantic import BaseModel
        from typing import Optional

        router = APIRouter(tags=["RAG Explainability"])

        class ExplainRequest(BaseModel):
            date: str
            slot: int
            probability: float
            threshold: float
            cape: Optional[float] = None
            k_index: Optional[float] = None
            lifted_index: Optional[float] = None
            totals_totals: Optional[float] = None
            era5_t2m: Optional[float] = None
            era5_cape: Optional[float] = None

        class QuestionRequest(BaseModel):
            question: str
            n_results: Optional[int] = 3

        class AnalogRequest(BaseModel):
            cape: float
            k_index: float
            lifted_index: float = -2.0
            totals_totals: float = 44.0
            era5_t2m: float = 299.0
            era5_cape: float = 0.0
            month: int = 6
            n: int = 5

        @router.post("/explain")
        def rag_explain(req: ExplainRequest):
            return explain_forecast(
                date_str=req.date, slot_id=req.slot,
                probability=req.probability, threshold=req.threshold,
                cape=req.cape, k_index=req.k_index,
                li=req.lifted_index, tt=req.totals_totals,
                era5_t2m=req.era5_t2m, era5_cape=req.era5_cape,
            )

        @router.post("/question")
        def rag_question(req: QuestionRequest):
            chunks = query_knowledge(req.question, req.n_results)
            if not chunks:
                return {"answer": "Knowledge base not available.", "sources": []}
            prompt  = (f"Using this meteorological knowledge about CSIR Thunderstorm "
                       f"Nowcast at Bengaluru Airport:\n\n"
                       f"{chr(10).join(chunks)}\n\n"
                       f"Answer concisely: {req.question}")
            answer  = call_groq(prompt, max_tokens=300)
            return {"answer": answer, "sources": chunks}

        @router.post("/analogs")
        def rag_analogs(req: AnalogRequest):
            analogs = find_analogs(
                cape=req.cape, k_index=req.k_index,
                li=req.lifted_index, tt=req.totals_totals,
                t2m=req.era5_t2m, era5_cape=req.era5_cape,
                q850=0.013, t850=293, month=req.month, n=req.n,
            )
            ts_rate = len([a for a in analogs if a['ts_label'] == 1]) / max(len(analogs), 1)
            return {
                "analogs": analogs,
                "ts_rate_in_analogs": round(ts_rate, 3),
                "interpretation": (
                    f"{len([a for a in analogs if a['ts_label']==1])} of {len(analogs)} "
                    f"similar days had thunderstorms ({ts_rate*100:.0f}%)"
                )
            }

        @router.get("/status")
        def rag_status():
            client = init_chroma()
            if not client:
                return {"status": "not_available"}
            try:
                kb   = client.get_collection("met_knowledge")
                hist = client.get_collection("historical_days")
                return {
                    "status": "operational",
                    "knowledge_docs": kb.count(),
                    "historical_days": hist.count(),
                    "capabilities": ["explain", "question", "analogs"],
                }
            except Exception:
                return {"status": "not_built",
                        "message": "Run: python rag_system.py --build"}

        return router
    except ImportError:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--build',    action='store_true')
    parser.add_argument('--explain',  action='store_true')
    parser.add_argument('--question', type=str)
    parser.add_argument('--analogs',  action='store_true')
    parser.add_argument('--date',     type=str, default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument('--slot',     type=int, default=2)
    parser.add_argument('--prob',     type=float, default=0.298)
    parser.add_argument('--thresh',   type=float, default=0.16)
    parser.add_argument('--cape',     type=float, default=177.25)
    parser.add_argument('--k',        type=float, default=39.38)
    parser.add_argument('--li',       type=float, default=-6.15)
    parser.add_argument('--tt',       type=float, default=46.78)
    args = parser.parse_args()

    if args.build:
        build_all()

    elif args.explain:
        print(f"\nGenerating explanation for Slot {args.slot} on {args.date}...")
        result = explain_forecast(
            date_str=args.date, slot_id=args.slot,
            probability=args.prob, threshold=args.thresh,
            cape=args.cape, k_index=args.k, li=args.li, tt=args.tt,
        )
        print(f"\n{'='*60}")
        print(f"FORECAST EXPLANATION — Slot {args.slot} | {args.date}")
        print(f"{'='*60}")
        print(f"Probability: {result['probability']*100:.1f}% | "
              f"Alert: {'FIRED' if result['alert_fired'] else 'NO'}")
        print(f"Analogs: {result['analogs_with_ts']}/{result['total_analogs']} "
              f"similar days had TS")
        print(f"\n{result['explanation']}")

    elif args.question:
        chunks = query_knowledge(args.question)
        if not chunks:
            print("Knowledge base empty — run --build first")
            return
        prompt = (f"Using this meteorological knowledge:\n\n"
                  f"{chr(10).join(chunks)}\n\n"
                  f"Answer: {args.question}")
        print(f"\n{call_groq(prompt, max_tokens=300)}")

    elif args.analogs:
        print(f"\nFinding analogs for CAPE={args.cape}, K={args.k}...")
        analogs = find_analogs(
            cape=args.cape, k_index=args.k, li=args.li, tt=args.tt,
            t2m=299, era5_cape=args.cape, q850=0.013, t850=293,
            month=int(args.date[5:7]), n=5
        )
        print(f"\n{'='*60}\nHISTORICAL ANALOGS\n{'='*60}")
        for a in analogs:
            label = "TS" if a['ts_label'] else "No TS"
            print(f"  {a['date']} — {label} | CAPE={a['CAPE']:.0f} K={a['K_INDEX']:.1f}")
        ts_rate = len([a for a in analogs if a['ts_label'] == 1]) / max(len(analogs), 1)
        print(f"\n  {ts_rate*100:.0f}% of similar days had thunderstorms")


if __name__ == "__main__":
    main()
