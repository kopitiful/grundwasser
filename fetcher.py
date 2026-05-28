"""
Datenbeschaffung aus OpenHygrisC (opengeodata.nrw.de).

Bulk-CSVs werden beim ersten Aufruf heruntergeladen und lokal gecacht.
Zeitreihen werden per Streaming aus den komprimierten ZIPs gefiltert –
kein komplettes Entpacken großer Dateien nötig.
"""

import hashlib
import io
import json
import math
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from config import (
    OPENGEO_BASE, FILE_MESSSTELLEN, FILE_WASSERSTAND_TMPL, DECADES,
    SEP, COL_ID_MS, COL_ID_WS, COL_NAME, COL_EASTING, COL_NORTHING,
    COL_DATUM, COL_WASSERSTAND, COL_FLURABSTAND, COL_ABSTICH,
    CACHE_DIR,
)

CACHE_DIR.mkdir(parents=True, exist_ok=True)
_STATIONS_CACHE = CACHE_DIR / "stations.parquet"
_MEASURE_CACHE  = CACHE_DIR / "messwerte"
_AVG_CACHE      = CACHE_DIR / "avg_cache"


# ---------------------------------------------------------------------------
# Koordinatenumrechnung UTM 32N → WGS84  (EPSG:25832 → EPSG:4326)
# ---------------------------------------------------------------------------

def _utm32_to_wgs84(e: float, n: float) -> tuple[float, float]:
    """Konvertiert UTM Zone 32N (EPSG:25832) in WGS84 (lon, lat)."""
    try:
        from pyproj import Transformer
        t = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
        return t.transform(e, n)  # returns (lon, lat)
    except ImportError:
        return _utm32_to_wgs84_manual(e, n)


def _utm32_to_wgs84_manual(e: float, n: float) -> tuple[float, float]:
    """
    Näherungsformel für UTM Zone 32N → WGS84.
    Genauigkeit: besser als 1 m im gesamten NRW-Bereich.
    """
    # WGS84-Ellipsoid
    a  = 6378137.0
    f  = 1 / 298.257223563
    b  = a * (1 - f)
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    k0 = 0.9996
    E0 = 500000.0
    N0 = 0.0
    lon0 = math.radians(9.0)  # Zone 32N central meridian

    M = (n - N0) / k0
    mu = M / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))

    phi1 = (mu
            + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))

    N1    = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    T1    = math.tan(phi1) ** 2
    C1    = ep2 * math.cos(phi1) ** 2
    R1    = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    D     = (e - E0) / (N1 * k0)

    lat = phi1 - (N1 * math.tan(phi1) / R1) * (
        D ** 2 / 2
        - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * ep2) * D ** 4 / 24
        + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * ep2 - 3 * C1 ** 2) * D ** 6 / 720
    )
    lon = lon0 + (
        D
        - (1 + 2 * T1 + C1) * D ** 3 / 6
        + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * ep2 + 24 * T1 ** 2) * D ** 5 / 120
    ) / math.cos(phi1)

    return math.degrees(lon), math.degrees(lat)


# ---------------------------------------------------------------------------
# Download-Helper
# ---------------------------------------------------------------------------

def _download(url: str, desc: str = "") -> bytes:
    """Lädt eine URL mit Fortschrittsanzeige herunter."""
    print(f"  Lade {desc or url.split('/')[-1]} …", end="", flush=True)
    r = requests.get(url, timeout=120, stream=True)
    r.raise_for_status()
    chunks = []
    total = int(r.headers.get("Content-Length", 0))
    done = 0
    for chunk in r.iter_content(chunk_size=1 << 20):
        chunks.append(chunk)
        done += len(chunk)
        if total:
            pct = int(done / total * 100)
            print(f"\r  Lade {desc or url.split('/')[-1]} … {pct}%  ", end="", flush=True)
    print(" ✓")
    return b"".join(chunks)


def _load_csv_from_zip(raw_zip: bytes, expected_suffix: str = ".csv") -> pd.DataFrame:
    """Entpackt den ersten CSV aus einem ZIP-Archiv im Speicher."""
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        csv_name = next(n for n in zf.namelist() if n.endswith(expected_suffix))
        with zf.open(csv_name) as raw_f:
            f = io.TextIOWrapper(raw_f, encoding="utf-8", errors="replace")
            return pd.read_csv(f, sep=SEP, low_memory=False)


# ---------------------------------------------------------------------------
# Stationen
# ---------------------------------------------------------------------------

def get_stations(
    name_filter: Optional[str] = None,
    bbox_wgs84: Optional[tuple] = None,
    force_refresh: bool = False,
    only_with_data: bool = False,
) -> pd.DataFrame:
    """
    Gibt alle NRW-Grundwassermessstellen zurück (mit WGS84-Koordinaten).

    Beim ersten Aufruf: Download ~28 MB, Konvertierung, Cache als Parquet.
    Danach: <1 s aus lokalem Cache.

    Parameters
    ----------
    name_filter  : Teilstring-Filter auf Stationsnamen (case-insensitiv)
    bbox_wgs84   : (min_lon, min_lat, max_lon, max_lat)
    force_refresh: Cache neu aufbauen
    """
    if not force_refresh and _STATIONS_CACHE.exists():
        df = pd.read_parquet(_STATIONS_CACHE)
    else:
        url = f"{OPENGEO_BASE}/{FILE_MESSSTELLEN}"
        raw = _download(url, "Messstellen")
        df  = _load_csv_from_zip(raw)
        df  = _process_stations(df)
        df.to_parquet(_STATIONS_CACHE, index=False)
        print(f"  {len(df)} Messstellen gecacht → {_STATIONS_CACHE}")

    if only_with_data:
        ids = get_station_ids_with_data()
        if ids:
            df = df[df["messstelle_id"].isin(ids)]

    if name_filter:
        df = df[df[COL_NAME].str.contains(name_filter, case=False, na=False)]

    if bbox_wgs84:
        min_lon, min_lat, max_lon, max_lat = bbox_wgs84
        df = df[
            (df["lon"] >= min_lon) & (df["lon"] <= max_lon) &
            (df["lat"] >= min_lat) & (df["lat"] <= max_lat)
        ]

    return df.reset_index(drop=True)


def _process_stations(df: pd.DataFrame) -> pd.DataFrame:
    """Bereinigt die Stations-CSV und fügt WGS84-Koordinaten hinzu."""
    # Koordinatenspalten – teilweise mit 'XX' maskiert → nur vollständige nutzen
    df = df.copy()
    df[COL_EASTING]  = pd.to_numeric(df[COL_EASTING],  errors="coerce")
    df[COL_NORTHING] = pd.to_numeric(df[COL_NORTHING], errors="coerce")
    has_coords = df[COL_EASTING].notna() & df[COL_NORTHING].notna()

    lons = pd.Series([float("nan")] * len(df), index=df.index)
    lats = pd.Series([float("nan")] * len(df), index=df.index)

    valid_idx = df.index[has_coords]
    print(f"  Konvertiere {has_coords.sum()} Koordinaten …", end="", flush=True)
    for idx in valid_idx:
        try:
            lon, lat = _utm32_to_wgs84(df.at[idx, COL_EASTING], df.at[idx, COL_NORTHING])
            # Sanity-Check: NRW liegt ca. 5.8–9.5 °O, 50.3–52.6 °N
            if 5.0 <= lon <= 10.0 and 49.5 <= lat <= 53.0:
                lons[idx] = lon
                lats[idx] = lat
        except Exception:
            pass
    print(" ✓")

    df["lon"] = lons
    df["lat"] = lats
    df[COL_NAME] = df[COL_NAME].astype(str).str.strip()
    return df


# ---------------------------------------------------------------------------
# Wasserstandsmessungen
# ---------------------------------------------------------------------------

def get_measurements(
    station_id: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    decades: Optional[list[str]] = None,
) -> pd.DataFrame:
    """
    Gibt Wasserstand-Zeitreihe für eine Messstelle zurück.

    Daten werden per Streaming aus den komprimierten Dekaden-ZIPs gefiltert.
    Ergebnisse werden pro Messstelle lokal gecacht.

    Parameters
    ----------
    station_id : Messstellen-ID (z.B. "080302944")
    start      : Startzeitpunkt (Standard: alle verfügbaren Daten)
    end        : Endzeitpunkt
    decades    : Liste der zu ladenden Dekaden (Standard: die jüngste)
    """
    _MEASURE_CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = _MEASURE_CACHE / f"{station_id}.parquet"

    if cache_file.exists():
        df = pd.read_parquet(cache_file)
    else:
        if decades is None:
            decades = [DECADES[-1]]
        # Beim erstmaligen Laden nur ab Jahresbeginn einlesen
        since = datetime(datetime.now().year, 1, 1)
        frames = []
        for decade in decades:
            frames.append(_fetch_decade(station_id, decade, since=since))
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not df.empty:
            df.to_parquet(cache_file, index=False)

    if df.empty:
        return df

    df[COL_DATUM] = pd.to_datetime(df[COL_DATUM], errors="coerce")
    df = df.dropna(subset=[COL_DATUM, COL_WASSERSTAND]).sort_values(COL_DATUM)

    if start:
        df = df[df[COL_DATUM] >= pd.Timestamp(start)]
    if end:
        df = df[df[COL_DATUM] <= pd.Timestamp(end)]

    return df.reset_index(drop=True)


def _load_decade_zip(decade: str) -> bytes:
    """Gibt Dekaden-ZIP als bytes zurück – aus lokalem Cache oder Download."""
    zip_cache = CACHE_DIR / f"wasserstand_{decade}.zip"
    if zip_cache.exists():
        return zip_cache.read_bytes()

    fname = FILE_WASSERSTAND_TMPL.format(decade=decade)
    url   = f"{OPENGEO_BASE}/{fname}"
    print(f"  Lade Wasserstand {decade} (~{_decade_size_mb(decade)} MB komprimiert) …")
    r = requests.get(url, timeout=300, stream=True)
    r.raise_for_status()
    raw = b""
    done = 0
    total = int(r.headers.get("Content-Length", 0))
    for chunk in r.iter_content(chunk_size=1 << 20):
        raw += chunk
        done += len(chunk)
        if total:
            pct = int(done / total * 100)
            print(f"\r  {decade}: {pct}%  ", end="", flush=True)
    print(" ✓")
    zip_cache.write_bytes(raw)
    return raw


def _fetch_decade(station_id: str, decade: str,
                  since: Optional[datetime] = None) -> pd.DataFrame:
    """
    Lädt eine Dekaden-ZIP und filtert die Messwerte für eine Messstelle.
    `since`: wenn gesetzt, werden nur Zeilen ab diesem Datum eingelesen.
    """
    raw = _load_decade_zip(decade)

    try:
        sid_int = int(station_id)
    except ValueError:
        sid_int = None

    since_ts = pd.Timestamp(since) if since else None

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(csv_name) as raw_f:
            f = io.TextIOWrapper(raw_f, encoding="utf-8", errors="replace")
            rows = []
            header = None
            for chunk_df in pd.read_csv(
                f, sep=SEP, chunksize=100_000, low_memory=False, decimal=",",
            ):
                if header is None:
                    header = list(chunk_df.columns)
                if sid_int is not None:
                    mask = pd.to_numeric(chunk_df[COL_ID_WS], errors="coerce") == sid_int
                else:
                    mask = chunk_df[COL_ID_WS].astype(str).str.strip() == str(station_id)
                filtered = chunk_df[mask]
                if not filtered.empty and since_ts is not None:
                    dates = pd.to_datetime(filtered[COL_DATUM], errors="coerce")
                    filtered = filtered[dates >= since_ts]
                if not filtered.empty:
                    rows.append(filtered)
            return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=header or [])


# ---------------------------------------------------------------------------
# Regionaler Durchschnitt
# ---------------------------------------------------------------------------

def _decades_for_range(
    start: Optional[datetime],
    end: Optional[datetime],
) -> list[str]:
    """Gibt alle Dekaden zurück, die mit [start, end] überlappen."""
    bounds = {
        "1900-1969": (1900, 1969),
        "1970-1989": (1970, 1989),
        "1990-1999": (1990, 1999),
        "2000-2009": (2000, 2009),
        "2010-2019": (2010, 2019),
        "2020-2029": (2020, 2029),
    }
    y0 = start.year if start else 1900
    y1 = end.year   if end   else datetime.now().year
    return [d for d, (lo, hi) in bounds.items() if lo <= y1 and hi >= y0]


def get_average_timeseries(
    station_ids: list,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    freq: str = "ME",
    filter_outliers: bool = True,
) -> pd.DataFrame:
    """
    Monatlich gemittelter Grundwasserstand über alle übergebenen Stationen.

    Scannt automatisch alle gecachten Dekaden-ZIPs, die [start, end] abdecken.
    Nicht gecachte ZIPs werden übersprungen (kein Auto-Download).

    Parameters
    ----------
    station_ids     : Liste von Messstellen-IDs (int oder str)
    start / end     : Optionaler Zeitraum (Standard: alle verfügbaren Daten)
    freq            : Resample-Frequenz ("ME" = Monatsende, "YE" = Jahresende)
    filter_outliers : MAD-Ausreißerbereinigung auf Stationsebene (Standard: True)

    Returns
    -------
    DataFrame mit Spalten: datum, mittel_m, min_m, max_m, std_m, n_stationen
    """
    if not station_ids:
        return pd.DataFrame()

    # ── Cache-Key ────────────────────────────────────────────────────────────
    _AVG_CACHE.mkdir(exist_ok=True)
    _key = json.dumps({
        "ids":   sorted(int(s) for s in station_ids
                        if str(s).strip().lstrip("-").isdigit()),
        "start": str(start)[:10] if start else None,
        "end":   str(end)[:10]   if end   else None,
        "freq":  freq,
    }, sort_keys=True)
    _cache_file = _AVG_CACHE / (hashlib.md5(_key.encode()).hexdigest() + ".parquet")

    if _cache_file.exists():
        # Für den laufenden Monat nie aus dem Cache lesen (Daten wachsen noch)
        import time
        age_h = (time.time() - _cache_file.stat().st_mtime) / 3600
        now   = datetime.now()
        end_in_current_month = (end is None) or (end.year == now.year and end.month == now.month)
        if not end_in_current_month or age_h < 6:
            return pd.read_parquet(_cache_file)

    sid_ints: set[int] = set()
    for sid in station_ids:
        try:
            sid_ints.add(int(sid))
        except (ValueError, TypeError):
            pass
    if not sid_ints:
        return pd.DataFrame()

    needed  = _decades_for_range(start, end)
    to_scan = [d for d in needed if (CACHE_DIR / f"wasserstand_{d}.zip").exists()]
    if not to_scan:
        return pd.DataFrame()

    all_rows: list[pd.DataFrame] = []
    for decade in to_scan:
        print(f"  Scanne {decade}-ZIP für {len(sid_ints)} Stationen …")
        raw = _load_decade_zip(decade)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
            with zf.open(csv_name) as raw_f:
                f = io.TextIOWrapper(raw_f, encoding="utf-8", errors="replace")
                done = 0
                for chunk_df in pd.read_csv(
                    f, sep=SEP, chunksize=200_000, low_memory=False, decimal=",",
                    usecols=lambda c: c in [COL_ID_WS, COL_DATUM, COL_WASSERSTAND],
                ):
                    ids_numeric = pd.to_numeric(chunk_df[COL_ID_WS], errors="coerce")
                    mask = ids_numeric.isin(sid_ints)
                    if mask.any():
                        all_rows.append(chunk_df[mask])
                    done += len(chunk_df)
                    print(f"\r  {decade}: {done:,} Zeilen …  ", end="", flush=True)
                print(" ✓")

    if not all_rows:
        return pd.DataFrame()

    df = pd.concat(all_rows, ignore_index=True)
    df[COL_DATUM]       = pd.to_datetime(df[COL_DATUM], errors="coerce")
    df[COL_WASSERSTAND] = pd.to_numeric(df[COL_WASSERSTAND], errors="coerce")
    df = df.dropna(subset=[COL_DATUM, COL_WASSERSTAND])

    if start:
        df = df[df[COL_DATUM] >= pd.Timestamp(start)]
    if end:
        df = df[df[COL_DATUM] <= pd.Timestamp(end)]

    if df.empty:
        return pd.DataFrame()

    if filter_outliers:
        # Stationen mit extremen Absolutwerten raus (Tagebau-Absenktrichter etc.)
        # Berechne je Station den Median; Stationen deren Median außerhalb
        # [globaler Median ± 4*MAD] liegt, werden komplett entfernt.
        station_medians = df.groupby(COL_ID_WS)[COL_WASSERSTAND].median()
        global_med = station_medians.median()
        mad = (station_medians - global_med).abs().median()
        if mad > 0:
            keep = station_medians[
                (station_medians >= global_med - 4 * mad) &
                (station_medians <= global_med + 4 * mad)
            ].index
            before = df[COL_ID_WS].nunique()
            df = df[df[COL_ID_WS].isin(keep)]
            after = df[COL_ID_WS].nunique()
            if before != after:
                print(f"  Ausreißerfilter: {before - after} Stationen entfernt "
                      f"(Median außerhalb [{global_med - 4*mad:.1f}, "
                      f"{global_med + 4*mad:.1f}] m)")

    # Monatlich pro Station mitteln, dann Stationsquerschnitt bilden
    df["monat"] = df[COL_DATUM].dt.to_period(freq[0])
    station_monthly = (
        df.groupby([COL_ID_WS, "monat"])[COL_WASSERSTAND]
        .mean()
        .reset_index()
    )

    agg = station_monthly.groupby("monat")[COL_WASSERSTAND].agg(
        mittel_m="mean",
        min_m="min",
        max_m="max",
        std_m="std",
        n_stationen="count",
    ).reset_index()

    agg["datum"] = agg["monat"].dt.to_timestamp(how="end")
    agg = agg.drop(columns="monat").sort_values("datum").reset_index(drop=True)

    n_found = df[COL_ID_WS].nunique()
    print(f"  {n_found} Stationen nach Filterung.")
    agg.to_parquet(_cache_file, index=False)
    return agg


def check_for_updates(force: bool = False) -> list[str]:
    """
    Prüft wöchentlich per HTTP-HEAD ob Dekaden-ZIPs auf dem Server neuer sind.
    Lädt nur bei Bedarf komplett neu und invalidiert betroffene Caches.
    Läuft typischerweise als Hintergrund-Thread beim Dashboard-Start.

    Returns: Liste der aktualisierten Dekaden.
    """
    import time, shutil

    check_file = CACHE_DIR / "update_check.json"
    checks: dict = {}
    if check_file.exists():
        try:
            checks = json.loads(check_file.read_text())
        except Exception:
            pass

    now  = time.time()
    week = 7 * 24 * 3600
    if not force and (now - checks.get("_last_check", 0)) < week:
        return []

    print("Wöchentlicher Update-Check …")
    updated: list[str] = []

    # Nur die aktuelle Dekade prüfen – historische Daten ändern sich nie
    current_decade = DECADES[-1]
    zip_cache = CACHE_DIR / f"wasserstand_{current_decade}.zip"
    if not zip_cache.exists():
        print(f"  {current_decade}: nicht lokal gecacht, kein Check.")
    else:
        fname = FILE_WASSERSTAND_TMPL.format(decade=current_decade)
        url   = f"{OPENGEO_BASE}/{fname}"
        try:
            r = requests.head(url, timeout=15)
            r.raise_for_status()
            remote_mtime = r.headers.get("Last-Modified", "")
            stored_mtime = checks.get(current_decade, "")

            if not stored_mtime:
                # Erster Lauf: Baseline speichern, nichts herunterladen
                print(f"  {current_decade}: Baseline gespeichert ({remote_mtime or 'kein Timestamp'})")
                checks[current_decade] = remote_mtime
            elif remote_mtime and remote_mtime == stored_mtime:
                print(f"  {current_decade}: unverändert")
            else:
                print(f"  {current_decade}: neue Version gefunden, lade herunter …")
                raw = _download(url, f"wasserstand_{current_decade}.zip")
                zip_cache.write_bytes(raw)
                checks[current_decade] = remote_mtime
                _merge_recent_into_cache(current_decade, raw, months=3)
                updated.append(current_decade)
        except Exception as e:
            print(f"  {current_decade}: Check fehlgeschlagen – {e}")

    checks["_last_check"] = now
    check_file.write_text(json.dumps(checks, indent=2))
    if updated:
        print(f"  Aktualisiert: {', '.join(updated)}")
    else:
        print("  Kein Update notwendig.")
    return updated


def _merge_recent_into_cache(decade: str, raw_zip: bytes, months: int = 3):
    """
    Liest aus einem frisch heruntergeladenen Dekaden-ZIP nur die Zeilen der
    letzten `months` Monate und pflegt sie in die per-Station-Parquets ein.
    Historische Daten (älter als cutoff) bleiben unangetastet.
    Avg-Cache wird geleert (wird beim nächsten Aufruf neu gebaut).
    """
    import shutil

    cutoff = pd.Timestamp.now() - pd.DateOffset(months=months)
    print(f"  Verarbeite neue Daten ab {cutoff.date()} …")

    # Stationsindex invalidieren (neue Stationen könnten hinzugekommen sein)
    idx = CACHE_DIR / f"station_ids_{decade}.parquet"
    if idx.exists():
        idx.unlink()

    # Durchschnitts-Cache leeren
    if _AVG_CACHE.exists():
        shutil.rmtree(_AVG_CACHE)
        _AVG_CACHE.mkdir()

    # Neue Zeilen pro Station aus dem ZIP lesen
    _MEASURE_CACHE.mkdir(exist_ok=True)
    new_by_station: dict[int, list[pd.DataFrame]] = {}

    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(csv_name) as raw_f:
            f = io.TextIOWrapper(raw_f, encoding="utf-8", errors="replace")
            done = 0
            for chunk in pd.read_csv(
                f, sep=SEP, chunksize=200_000, low_memory=False, decimal=",",
            ):
                chunk[COL_DATUM] = pd.to_datetime(chunk[COL_DATUM], errors="coerce")
                recent = chunk[chunk[COL_DATUM] >= cutoff]
                if not recent.empty:
                    ids_num = pd.to_numeric(recent[COL_ID_WS], errors="coerce")
                    for sid in ids_num.dropna().astype(int).unique():
                        mask = ids_num == sid
                        new_by_station.setdefault(sid, []).append(recent[mask])
                done += len(chunk)
                print(f"\r  Zeilen gelesen: {done:,}  ", end="", flush=True)
            print(" ✓")

    # Einpflegen: historische Rows behalten, neue überschreiben
    for sid, frames in new_by_station.items():
        new_rows = pd.concat(frames, ignore_index=True)
        cache_file = _MEASURE_CACHE / f"{sid}.parquet"
        if cache_file.exists():
            existing = pd.read_parquet(cache_file)
            existing[COL_DATUM] = pd.to_datetime(existing[COL_DATUM], errors="coerce")
            historical = existing[existing[COL_DATUM] < cutoff]
            merged = pd.concat([historical, new_rows], ignore_index=True)
        else:
            merged = new_rows
        merged.sort_values(COL_DATUM).to_parquet(cache_file, index=False)

    print(f"  {len(new_by_station)} Stationen mit neuen Daten aktualisiert.")


def _decade_size_mb(decade: str) -> int:
    sizes = {
        "1900-1969": 186, "1970-1989": 341, "1990-1999": 228,
        "2000-2009": 327, "2010-2019": 355, "2020-2029": 242,
    }
    return sizes.get(decade, 300)


# ---------------------------------------------------------------------------
# Stationsindex: welche IDs haben tatsächlich Messwerte?
# ---------------------------------------------------------------------------

def build_station_index(decade: str) -> set:
    """
    Liest nur die ID-Spalte aus dem gecachten Dekaden-ZIP und gibt alle
    vorhandenen Messstellen-IDs zurück. Ergebnis wird als kleines Parquet gecacht.
    Läuft nur wenn der ZIP bereits lokal vorliegt – kein Download.
    """
    index_file = CACHE_DIR / f"station_ids_{decade}.parquet"
    if index_file.exists():
        return set(pd.read_parquet(index_file)["messstelle_id"].tolist())

    zip_cache = CACHE_DIR / f"wasserstand_{decade}.zip"
    if not zip_cache.exists():
        return set()

    print(f"  Baue Stationsindex {decade} …", end="", flush=True)
    raw = zip_cache.read_bytes()
    ids: set = set()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(csv_name) as raw_f:
            f = io.TextIOWrapper(raw_f, encoding="utf-8", errors="replace")
            for chunk_df in pd.read_csv(
                f, sep=SEP, usecols=[COL_ID_WS], chunksize=200_000,
            ):
                numeric = pd.to_numeric(chunk_df[COL_ID_WS], errors="coerce").dropna()
                ids.update(numeric.astype(int).tolist())
    print(f" {len(ids):,} Stationen ✓")

    pd.DataFrame({"messstelle_id": sorted(ids)}).to_parquet(index_file, index=False)
    return ids


def get_station_ids_with_data() -> set:
    """Gibt alle Station-IDs zurück, die in mindestens einem gecachten ZIP vorkommen."""
    all_ids: set = set()
    for decade in DECADES:
        all_ids.update(build_station_index(decade))
    return all_ids


# ---------------------------------------------------------------------------
# Hilfe / Diagnose
# ---------------------------------------------------------------------------

def cache_info() -> dict:
    """Gibt Informationen über den lokalen Cache zurück."""
    info = {"cache_dir": str(CACHE_DIR), "stations": None, "messwerte_count": 0,
            "zips": []}
    if _STATIONS_CACHE.exists():
        df = pd.read_parquet(_STATIONS_CACHE)
        info["stations"] = len(df)
    if _MEASURE_CACHE.exists():
        info["messwerte_count"] = len(list(_MEASURE_CACHE.glob("*.parquet")))
    for decade in DECADES:
        zp = CACHE_DIR / f"wasserstand_{decade}.zip"
        if zp.exists():
            info["zips"].append(f"{decade} ({zp.stat().st_size // 1024 // 1024} MB)")
    return info


def clear_cache():
    """Löscht den lokalen Cache."""
    import shutil
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
    CACHE_DIR.mkdir(parents=True)
    print(f"Cache gelöscht: {CACHE_DIR}")
