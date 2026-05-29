"""
Precomputes all regional averages and exports station data as JSON.
Run locally or via GitHub Actions. Output goes to docs/data/.

Scans each decade-ZIP exactly once (not once per region) for speed.
"""
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from config import REGIONS, CITIES, DECADES, SEP, COL_ID_WS, COL_DATUM, COL_WASSERSTAND, CACHE_DIR
from fetcher import get_stations, _load_decade_zip

OUT = Path(__file__).parent / "docs" / "data"
OUT.mkdir(parents=True, exist_ok=True)

START_YEAR = 1990
NEEDED_DECADES = ["1990-1999", "2000-2009", "2010-2019", "2020-2029"]


def slug(label: str) -> str:
    for ch in " /()\\":
        label = label.replace(ch, "_")
    return label


print("=== Grundwasser Precompute ===")
print(f"Zeitraum: ab {START_YEAR}")

# ── 1. Stationen exportieren ─────────────────────────────────────────────────
print("\n[1/4] Exportiere Stationen …")
df_all = get_stations()
df_map = df_all.dropna(subset=["lat", "lon"])
keep = [c for c in ["messstelle_id", "name", "gemeinde_name", "lat", "lon"] if c in df_map.columns]
(OUT / "stations.json").write_text(json.dumps(df_map[keep].to_dict("records"), ensure_ascii=False))
print(f"  {len(df_map)} Stationen exportiert")

# ── 2. Stationsmengen je Gebiet vorberechnen ──────────────────────────────────
print("\n[2/4] Berechne Stationszuordnungen …")
all_areas = {**REGIONS, **CITIES}
area_type = {k: "region" for k in REGIONS} | {k: "city" for k in CITIES}
area_sids: dict[str, set[int]] = {}
all_sids: set[int] = set()

for label, bbox in all_areas.items():
    df_area = get_stations(bbox_wgs84=bbox)
    sids = set()
    for sid in df_area["messstelle_id"]:
        try:
            sids.add(int(sid))
        except (ValueError, TypeError):
            pass
    area_sids[label] = sids
    all_sids.update(sids)
    print(f"  {label}: {len(sids)} Stationen")

# ── 3. ZIPs einmal scannen ───────────────────────────────────────────────────
print(f"\n[3/4] Scanne {len(NEEDED_DECADES)} ZIPs …")
frames: list[pd.DataFrame] = []

for decade in NEEDED_DECADES:
    print(f"  {decade} …", end="", flush=True)
    _load_decade_zip(decade)
    zip_path = CACHE_DIR / f"wasserstand_{decade}.zip"
    raw = zip_path.read_bytes()
    rows_found = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(csv_name) as f:
            txt = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
            for chunk in pd.read_csv(
                txt, sep=SEP, chunksize=200_000, low_memory=False, decimal=",",
                usecols=lambda c: c in [COL_ID_WS, COL_DATUM, COL_WASSERSTAND],
            ):
                ids_num = pd.to_numeric(chunk[COL_ID_WS], errors="coerce")
                mask = ids_num.isin(all_sids)
                if mask.any():
                    frames.append(chunk[mask])
                    rows_found += mask.sum()
    print(f" {rows_found:,} Zeilen ✓")

print("  Zusammenführen …", end="", flush=True)
df = pd.concat(frames, ignore_index=True)
df[COL_DATUM]       = pd.to_datetime(df[COL_DATUM], errors="coerce")
df[COL_WASSERSTAND] = pd.to_numeric(df[COL_WASSERSTAND], errors="coerce")
df = df.dropna(subset=[COL_DATUM, COL_WASSERSTAND])
df = df[df[COL_DATUM] >= pd.Timestamp(f"{START_YEAR}-01-01")]
df[COL_ID_WS] = pd.to_numeric(df[COL_ID_WS], errors="coerce")
print(f" {len(df):,} Zeilen gesamt ✓")

# ── 4. Regionsdurchschnitte berechnen ────────────────────────────────────────
print(f"\n[4/4] Aggregiere {len(all_areas)} Gebiete …")
meta_areas = []

for label, sids in area_sids.items():
    if not sids:
        continue
    print(f"  {label} …", end="", flush=True)

    df_a = df[df[COL_ID_WS].isin(sids)].copy()
    if df_a.empty:
        print(" keine Daten")
        continue

    # MAD-Ausreißerfilter (Tagebau-Stationen entfernen)
    station_meds = df_a.groupby(COL_ID_WS)[COL_WASSERSTAND].median()
    gmed = station_meds.median()
    mad  = (station_meds - gmed).abs().median()
    if mad > 0:
        keep_ids = station_meds[
            (station_meds >= gmed - 4 * mad) &
            (station_meds <= gmed + 4 * mad)
        ].index
        df_a = df_a[df_a[COL_ID_WS].isin(keep_ids)]

    if df_a.empty:
        print(" nach Filter leer")
        continue

    # Monatsmittel je Station, dann Stationsquerschnitt
    df_a["monat"] = df_a[COL_DATUM].dt.to_period("M")
    monthly = df_a.groupby([COL_ID_WS, "monat"])[COL_WASSERSTAND].mean().reset_index()
    agg = monthly.groupby("monat")[COL_WASSERSTAND].agg(
        mittel_m="mean", n_stationen="count"
    ).reset_index()
    agg["datum"] = agg["monat"].dt.to_timestamp(how="end")
    agg = agg.sort_values("datum")

    records = [
        {
            "datum":      row["datum"].strftime("%Y-%m-%d"),
            "mittel_m":   round(float(row["mittel_m"]), 3),
            "n_stationen": int(row["n_stationen"]),
        }
        for _, row in agg.iterrows()
    ]

    filename = f"avg_{slug(label)}.json"
    (OUT / filename).write_text(json.dumps(
        {"label": label, "type": area_type[label], "data": records},
        ensure_ascii=False,
    ))
    meta_areas.append({"label": label, "file": filename, "type": area_type[label]})
    print(f" {len(records)} Monate, max {int(agg['n_stationen'].max())} Stationen ✓")

(OUT / "meta.json").write_text(json.dumps(
    {"areas": meta_areas, "updated": datetime.now().strftime("%Y-%m-%d"), "start_year": START_YEAR},
    ensure_ascii=False, indent=2,
))
print(f"\nFertig. {len(meta_areas)} Gebiete → {OUT}")
