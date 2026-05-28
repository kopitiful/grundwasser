#!/usr/bin/env python3
"""
grundwasser.py – CLI für NRW-Grundwasserdaten (OpenHygrisC / opengeodata.nrw.de)

Befehle:
  stations              Messstellen auflisten / filtern
  data <ID>             Zeitreihe einer Messstelle abrufen
  cache                 Cache-Status anzeigen
  refresh               Stations-Cache neu aufbauen
  dashboard             Dash-Dashboard starten (localhost:8051)
"""

import argparse
import sys
from datetime import datetime


def cmd_stations(args):
    from fetcher import get_stations
    from tabulate import tabulate

    bbox = None
    if args.bbox:
        try:
            bbox = tuple(float(x) for x in args.bbox.split(","))
            assert len(bbox) == 4
        except Exception:
            print("--bbox: minLon,minLat,maxLon,maxLat erwartet", file=sys.stderr)
            sys.exit(1)

    df = get_stations(name_filter=args.name, bbox_wgs84=bbox)

    if df.empty:
        print("Keine Messstellen gefunden.")
        return

    show_cols = [c for c in ["messstelle_id", "name", "gemeinde_name", "lat", "lon",
                              "wasserstandsmessstelle", "guetemessstelle"] if c in df.columns]
    rows = df[show_cols].head(args.max)
    print(f"\n{len(df)} Messstellen (Auswahl: {len(rows)}):\n")
    print(tabulate(rows, headers="keys", tablefmt="rounded_outline",
                   showindex=False, floatfmt=".4f"))


def cmd_data(args):
    from fetcher import get_measurements
    from tabulate import tabulate

    start = datetime.fromisoformat(args.start) if args.start else None
    end   = datetime.fromisoformat(args.end)   if args.end   else None
    dec   = args.decades.split(",") if args.decades else None

    print(f"Station: {args.station_id}")
    df = get_measurements(args.station_id, start=start, end=end, decades=dec)

    if df.empty:
        print("Keine Messwerte gefunden.")
        print("Tipp: andere --decades angeben (z.B. --decades 2010-2019,2020-2029)")
        return

    print(f"\n{len(df)} Messwerte  {df['datum_messung'].min()}  –  {df['datum_messung'].max()}\n")

    show_cols = [c for c in ["datum_messung", "wasserstd_m", "flurabstd_m", "abstich_m"] if c in df.columns]
    preview = df[show_cols].tail(40) if len(df) > 40 else df[show_cols]
    print(tabulate(preview, headers="keys", tablefmt="rounded_outline",
                   showindex=False, floatfmt=".3f"))

    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"\nGespeichert: {args.csv}")


def cmd_cache(args):
    from fetcher import cache_info
    info = cache_info()
    print(f"Cache-Verzeichnis: {info['cache_dir']}")
    print(f"Messstellen:       {info['stations'] or 'nicht gecacht (→ python3 grundwasser.py refresh)'}")
    print(f"Zeitreihen cached: {info['messwerte_count']} Stationen")
    if info["zips"]:
        print(f"Dekaden-ZIPs:      {', '.join(info['zips'])}")
    else:
        print("Dekaden-ZIPs:      keine (werden beim ersten data-Aufruf geladen)")


def cmd_refresh(args):
    from fetcher import get_stations
    get_stations(force_refresh=True)


def cmd_avg(args):
    from fetcher import get_stations, get_average_timeseries
    from tabulate import tabulate

    bbox = None
    if args.bbox:
        try:
            bbox = tuple(float(x) for x in args.bbox.split(","))
            assert len(bbox) == 4
        except Exception:
            print("--bbox: minLon,minLat,maxLon,maxLat erwartet", file=sys.stderr)
            sys.exit(1)

    print("Lade Stationsliste …")
    df_s = get_stations(name_filter=args.name, bbox_wgs84=bbox)
    if df_s.empty:
        print("Keine Stationen gefunden.")
        return

    station_ids = df_s["messstelle_id"].tolist()
    print(f"{len(station_ids)} Stationen ausgewählt.")

    decade = args.decade or "2020-2029"
    # Dekade in Zeitraum umrechnen
    try:
        y0, y1 = [int(x) for x in decade.split("-")]
    except Exception:
        y0, y1 = 2020, 2029
    from datetime import datetime as _dt
    agg = get_average_timeseries(
        station_ids,
        start=_dt(y0, 1, 1),
        end=_dt(y1, 12, 31),
    )

    if agg.empty:
        print("Keine Messwerte für diese Stationen in der gewählten Dekade.")
        return

    print(f"\nRegionaler Durchschnitt  ({decade}):\n")
    show = agg[["datum", "mittel_m", "min_m", "max_m", "std_m", "n_stationen"]]
    print(tabulate(show, headers=["Datum", "Mittel (m)", "Min (m)", "Max (m)",
                                  "Std (m)", "N Stationen"],
                   tablefmt="rounded_outline", showindex=False, floatfmt=".3f"))

    if args.csv:
        agg.to_csv(args.csv, index=False)
        print(f"\nGespeichert: {args.csv}")


def cmd_dashboard(args):
    from dashboard import create_app
    from config import DASH_PORT
    app = create_app()
    print(f"Dashboard: http://localhost:{DASH_PORT}/")
    app.run(debug=False, host="0.0.0.0", port=DASH_PORT)


# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="grundwasser",
        description="NRW-Grundwasserdaten (OpenHygrisC)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("stations", help="Messstellen auflisten")
    s.add_argument("--name",  metavar="TEILSTRING", help="Namensfilter")
    s.add_argument("--bbox",  metavar="minLon,minLat,maxLon,maxLat",
                   help="z.B. 6.5,51.2,7.2,51.6 (Ruhrgebiet)")
    s.add_argument("--max",   type=int, default=50, help="Max. Zeilen (Standard: 50)")

    d = sub.add_parser("data", help="Zeitreihe einer Messstelle")
    d.add_argument("station_id", metavar="MESSSTELLEN_ID")
    d.add_argument("--start",   metavar="YYYY-MM-DD")
    d.add_argument("--end",     metavar="YYYY-MM-DD")
    d.add_argument("--decades", metavar="DEKADE[,DEKADE]",
                   help="z.B. '2020-2029' oder '2010-2019,2020-2029'")
    d.add_argument("--csv",     metavar="DATEI")

    a = sub.add_parser("avg", help="Regionaler Durchschnitt über mehrere Stationen")
    a.add_argument("--name",   metavar="TEILSTRING", help="Namensfilter")
    a.add_argument("--bbox",   metavar="minLon,minLat,maxLon,maxLat",
                   help="z.B. 6.8,51.4,7.2,51.6 (Ruhrgebiet)")
    a.add_argument("--decade", metavar="DEKADE", default="2020-2029",
                   help="Dekade (Standard: 2020-2029)")
    a.add_argument("--csv",    metavar="DATEI")

    sub.add_parser("cache",   help="Cache-Status anzeigen")
    sub.add_parser("refresh", help="Stations-Cache neu laden")
    sub.add_parser("dashboard", help="Dash-Dashboard starten")

    return p


def main():
    p = build_parser()
    args = p.parse_args()
    {
        "stations":  cmd_stations,
        "data":      cmd_data,
        "avg":       cmd_avg,
        "cache":     cmd_cache,
        "refresh":   cmd_refresh,
        "dashboard": cmd_dashboard,
    }[args.command](args)


if __name__ == "__main__":
    main()
