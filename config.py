"""
Konfiguration für NRW-Grundwasserdaten via OpenHygrisC (LANUV / opengeodata.nrw.de).
"""

from pathlib import Path

# --- Datenquellen (OpenHygrisC) ---
OPENGEO_BASE = "https://www.opengeodata.nrw.de/produkte/umwelt_klima/wasser/grundwasser/hygrisc"

# Dateinamen
FILE_MESSSTELLEN = "OpenHygrisC_gw-messstelle_EPSG25832_CSV.zip"
FILE_WASSERSTAND_TMPL = "OpenHygrisC_gw-wasserstand_{decade}_EPSG25832_CSV.zip"

DECADES = ["1900-1969", "1970-1989", "1990-1999", "2000-2009", "2010-2019", "2020-2029"]

# CSV-Spaltennamen (Semikolon-getrennt)
SEP = ";"
COL_ID_MS      = "messstelle_id"    # Messstellen-ID (Stations-CSV)
COL_ID_WS      = "messstelle_id"    # Messstellen-ID (Wasserstand-CSV)
COL_NAME       = "name"
COL_EASTING    = "e32"              # UTM Zone 32N Easting
COL_NORTHING   = "n32"              # UTM Zone 32N Northing
COL_DATUM      = "datum_messung"    # Messzeitpunkt
COL_WASSERSTAND = "wasserstd_m"     # Grundwasserstand m ü. NN
COL_FLURABSTAND = "flurabstd_m"     # Flurabstand m unter GOK
COL_ABSTICH     = "abstich_m"       # Abstich m unter Oberkante Schutzrohr

# --- Lokaler Cache ---
CACHE_DIR = Path.home() / ".grundwasser"

# --- Dashboard ---
DASH_PORT       = 8051
DASH_REFRESH_MS = 14_400_000
MAPBOX_STYLE    = "open-street-map"

# --- Farben (Catppuccin Mocha) ---
BG      = "#1e1e2e"
SURFACE = "#313244"
MAUVE   = "#cba6f7"
BLUE    = "#89b4fa"
GREEN   = "#a6e3a1"
RED     = "#f38ba8"
YELLOW  = "#f9e2af"
TEXT    = "#cdd6f4"
SUBTEXT = "#a6adc8"

# --- Regionen & Städte (NRW) — Bounding Boxes: (minLon, minLat, maxLon, maxLat) WGS84 ---
REGIONS = {
    "NRW (gesamt)":    (5.86, 50.32, 9.46, 52.53),
    "Ruhrgebiet":      (6.60, 51.30, 7.90, 51.70),
    "Westfalen":       (7.50, 51.00, 9.50, 52.50),
    "Bergisches Land": (6.90, 50.80, 7.80, 51.35),
    "Rheinland":       (6.00, 50.25, 7.20, 51.55),
    "Münsterland":     (7.10, 51.50, 8.80, 52.30),
    "Sauerland":       (7.50, 50.75, 8.85, 51.50),
    "Eifel":           (6.00, 50.05, 7.15, 50.70),
}

CITIES = {
    "Aachen":       (5.95, 50.60, 6.30, 50.85),
    "Bielefeld":    (8.40, 51.93, 8.70, 52.10),
    "Bochum":       (7.10, 51.38, 7.38, 51.55),
    "Bonn":         (6.95, 50.60, 7.25, 50.80),
    "Dortmund":     (7.28, 51.38, 7.68, 51.60),
    "Duisburg":     (6.60, 51.30, 6.90, 51.55),
    "Düsseldorf":   (6.60, 51.10, 6.98, 51.42),
    "Essen":        (6.85, 51.35, 7.20, 51.56),
    "Gelsenkirchen":(7.02, 51.48, 7.18, 51.58),
    "Köln":         (6.72, 50.78, 7.10, 51.08),
    "Krefeld":      (6.50, 51.28, 6.72, 51.42),
    "Münster":      (7.50, 51.85, 7.75, 52.05),
    "Wuppertal":    (7.00, 51.15, 7.35, 51.35),
}
