"""
Precomputes all regional averages and exports station data as JSON.
Run locally or via GitHub Actions. Output goes to docs/data/.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import REGIONS, CITIES, DECADES
from fetcher import get_stations, get_average_timeseries, _load_decade_zip

OUT = Path(__file__).parent / "docs" / "data"
OUT.mkdir(parents=True, exist_ok=True)


def slug(label: str) -> str:
    return label.replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_")


print("=== Grundwasser Precompute ===")

# Ensure current decade ZIP is cached
print(f"\n[1/3] Lade {DECADES[-1]}-ZIP …")
_load_decade_zip(DECADES[-1])

# Stations
print("\n[2/3] Exportiere Stationen …")
df = get_stations()
df_map = df.dropna(subset=["lat", "lon"])
keep = [c for c in ["messstelle_id", "name", "gemeinde_name", "lat", "lon"] if c in df_map.columns]
stations = df_map[keep].to_dict("records")
(OUT / "stations.json").write_text(json.dumps(stations, ensure_ascii=False))
print(f"  {len(stations)} Stationen exportiert")

# Regional averages
print("\n[3/3] Berechne Regionsdurchschnitte …")
meta_areas = []
all_areas = {**REGIONS, **CITIES}
area_type = {k: "region" for k in REGIONS} | {k: "city" for k in CITIES}

for label, bbox in all_areas.items():
    print(f"  {label} …", end="", flush=True)
    try:
        df_area = get_stations(bbox_wgs84=bbox)
        if df_area.empty:
            print(" keine Stationen")
            continue
        ids = df_area["messstelle_id"].tolist()
        agg = get_average_timeseries(ids, start=datetime(2020, 1, 1))
        if agg.empty:
            print(" keine Daten")
            continue

        records = [
            {
                "datum": row["datum"].strftime("%Y-%m-%d"),
                "mittel_m": round(float(row["mittel_m"]), 3),
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
        print(f" {len(records)} Monate, max {int(agg['n_stationen'].max())} Stationen")
    except Exception as e:
        print(f" Fehler: {e}")

(OUT / "meta.json").write_text(json.dumps(
    {"areas": meta_areas, "updated": datetime.now().strftime("%Y-%m-%d")},
    ensure_ascii=False,
    indent=2,
))
print(f"\nFertig. {len(meta_areas)} Gebiete exportiert → {OUT}")
