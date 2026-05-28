"""
Dash-Dashboard für NRW-Grundwasserdaten (OpenHygrisC).

Layout:
  Header  – Stationsfilter · Dekade · Stadt · Region
  Zeile 1 – Karte (links) | Kombinierter Chart (rechts)
             [Einzel-Station + Stadt-Ø + Region-Ø überlagert]
"""

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, dcc, html, Input, Output, State, callback_context, no_update

from config import (
    DASH_PORT, DASH_REFRESH_MS, MAPBOX_STYLE, DECADES,
    COL_DATUM, COL_WASSERSTAND, COL_FLURABSTAND,
    BG, SURFACE, MAUVE, BLUE, GREEN, RED, YELLOW, TEXT, SUBTEXT,
    REGIONS, CITIES,
)

_CHIP = {
    "backgroundColor": SURFACE, "color": TEXT,
    "border": f"1px solid {SUBTEXT}44", "padding": "4px 10px",
    "borderRadius": "4px", "cursor": "pointer", "fontSize": "0.78rem",
}
_DATE_INPUT = {
    "backgroundColor": BG, "color": TEXT,
    "border": f"1px solid {SUBTEXT}44",
    "padding": "4px 8px", "borderRadius": "4px",
    "fontSize": "0.78rem", "colorScheme": "dark",
    "width": "130px",
}
_DD = {"fontSize": "0.85rem", "backgroundColor": BG, "color": TEXT}

# Farbreihenfolge für mehrere Overlays (Stadt + Region gemischt)
_OVERLAY_COLORS = [MAUVE, GREEN, YELLOW, RED, BLUE, SUBTEXT]


def _rgba(hex6: str, alpha: float) -> str:
    r, g, b = int(hex6[1:3], 16), int(hex6[3:5], 16), int(hex6[5:7], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _start_update_check():
    import threading
    from fetcher import check_for_updates
    t = threading.Thread(target=check_for_updates, daemon=True, name="update-check")
    t.start()


def create_app() -> Dash:
    _start_update_check()
    app = Dash(__name__, title="Grundwasser NRW")

    app.layout = html.Div(
        style={"backgroundColor": BG, "minHeight": "100vh",
               "fontFamily": "'JetBrains Mono', monospace", "color": TEXT},
        children=[
            # --- Header ---
            html.Div(
                style={"padding": "10px 20px", "backgroundColor": SURFACE,
                       "borderBottom": f"1px solid {MAUVE}33",
                       "display": "flex", "alignItems": "center",
                       "flexWrap": "wrap", "gap": "10px"},
                children=[
                    html.H1("Grundwasser NRW",
                            style={"margin": 0, "fontSize": "1.1rem", "color": MAUVE,
                                   "whiteSpace": "nowrap"}),
                    dcc.Input(
                        id="search-input", type="text",
                        placeholder="Station filtern …", debounce=True,
                        style={"backgroundColor": BG, "color": TEXT,
                               "border": f"1px solid {SUBTEXT}44",
                               "padding": "5px 10px", "borderRadius": "6px",
                               "width": "180px", "fontSize": "0.82rem"},
                    ),
                    _label("Dekade"),
                    dcc.Dropdown(
                        id="decade-select",
                        options=[{"label": d, "value": d} for d in DECADES],
                        value=DECADES[-1], clearable=False,
                        style={**_DD, "width": "138px"},
                    ),
                    _label("Stadt"),
                    dcc.Dropdown(
                        id="city-select",
                        options=[{"label": k, "value": k} for k in CITIES],
                        value=None, clearable=True, multi=True,
                        placeholder="– keine –",
                        style={**_DD, "width": "200px", "minWidth": "155px"},
                    ),
                    _label("Region"),
                    dcc.Dropdown(
                        id="region-select",
                        options=[{"label": k, "value": k} for k in REGIONS],
                        value=["NRW (gesamt)"], clearable=True, multi=True,
                        placeholder="– keine –",
                        style={**_DD, "width": "220px", "minWidth": "165px"},
                    ),
                    html.Span(id="station-count",
                              style={"color": SUBTEXT, "fontSize": "0.78rem",
                                     "marginLeft": "auto", "whiteSpace": "nowrap"}),
                ],
            ),

            # --- Karte + Kombinierter Chart ---
            html.Div(
                style={"display": "flex", "height": "calc(100vh - 62px)"},
                children=[
                    # Karte
                    html.Div(style={"flex": "1", "padding": "8px"},
                             children=[dcc.Graph(id="station-map",
                                                 style={"height": "100%"},
                                                 config={"scrollZoom": True})]),

                    # Chart-Spalte
                    html.Div(
                        style={"flex": "1", "padding": "8px", "display": "flex",
                               "flexDirection": "column", "gap": "6px"},
                        children=[
                            html.Div(
                                id="station-info",
                                style={"color": SUBTEXT, "fontSize": "0.78rem",
                                       "padding": "5px 10px",
                                       "backgroundColor": SURFACE,
                                       "borderRadius": "6px",
                                       "whiteSpace": "nowrap",
                                       "overflow": "hidden",
                                       "textOverflow": "ellipsis"},
                                children="← Station anklicken oder Stadt / Region wählen",
                            ),
                            dcc.Loading(
                                type="circle", color=BLUE,
                                children=dcc.Graph(
                                    id="timeseries-chart",
                                    style={"flex": "1", "minHeight": "0"},
                                    config={"scrollZoom": True,
                                            "modeBarButtonsToRemove": ["toImage", "lasso2d", "select2d"],
                                            "displaylogo": False},
                                ),
                            ),
                            # Zeitauswahl
                            html.Div(
                                style={"display": "flex", "gap": "6px",
                                       "alignItems": "center", "flexWrap": "wrap"},
                                children=[
                                    html.Button("6 Mo",  id="btn-180",  n_clicks=0, style=_CHIP),
                                    html.Button("1 J",   id="btn-365",  n_clicks=0, style=_CHIP),
                                    html.Button("5 J",   id="btn-1825", n_clicks=0, style=_CHIP),
                                    html.Button("Alles", id="btn-all",  n_clicks=0, style=_CHIP),
                                    html.Span("Von", style={"color": SUBTEXT,
                                                            "fontSize": "0.75rem",
                                                            "marginLeft": "6px"}),
                                    dcc.Input(id="start-date-input", type="date",
                                              style=_DATE_INPUT),
                                    html.Span("Bis", style={"color": SUBTEXT,
                                                            "fontSize": "0.75rem"}),
                                    dcc.Input(id="end-date-input", type="date",
                                              style=_DATE_INPUT),
                                ],
                            ),
                        ],
                    ),
                ],
            ),

            # --- Stores ---
            dcc.Store(id="stations-store"),
            dcc.Store(id="selected-station"),
            dcc.Interval(id="auto-refresh", interval=DASH_REFRESH_MS, n_intervals=0),
        ],
    )
    _register_callbacks(app)
    return app


def _label(text: str):
    return html.Span(text, style={"color": SUBTEXT, "fontSize": "0.78rem",
                                  "whiteSpace": "nowrap"})


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

def _register_callbacks(app: Dash):

    @app.callback(
        Output("stations-store", "data"),
        Output("station-count", "children"),
        Input("auto-refresh", "n_intervals"),
        Input("search-input", "value"),
    )
    def load_stations(_, name_filter):
        from fetcher import get_stations
        try:
            df = get_stations(name_filter=name_filter or None, only_with_data=True)
            df_map = df.dropna(subset=["lat", "lon"])
            return df_map.to_dict("records"), f"{len(df_map):,} Messstellen"
        except Exception as e:
            return [], f"Fehler: {str(e)[:80]}"

    @app.callback(
        Output("station-map", "figure"),
        Input("stations-store", "data"),
        Input("selected-station", "data"),
    )
    def render_map(stations, selected_id):
        df = pd.DataFrame(stations or [])
        fig = go.Figure()
        fig.update_layout(**_map_layout())
        if df.empty or "lat" not in df.columns:
            return fig

        id_col   = "messstelle_id" if "messstelle_id" in df.columns else df.columns[0]
        name_col = "name" if "name" in df.columns else id_col

        colors = [MAUVE if str(r.get(id_col, "")) == str(selected_id or "")
                  else BLUE for _, r in df.iterrows()]
        hover = df.apply(
            lambda r: (f"<b>{r.get(name_col, '')}</b><br>"
                       f"ID: {r.get(id_col, '')}<br>"
                       f"{r.get('gemeinde_name', '')}"), axis=1,
        )
        fig.add_trace(go.Scattermapbox(
            lat=df["lat"].astype(float), lon=df["lon"].astype(float),
            mode="markers",
            marker=dict(size=8, color=colors, opacity=0.85),
            text=hover, hoverinfo="text",
            customdata=df[id_col].astype(str),
        ))
        fig.update_layout(**_map_layout(df))
        return fig

    @app.callback(
        Output("selected-station", "data"),
        Input("station-map", "clickData"),
    )
    def select_station(click):
        if not click:
            return no_update
        pts = click.get("points", [])
        return pts[0].get("customdata") if pts else no_update

    @app.callback(
        Output("start-date-input", "value"),
        Output("end-date-input",   "value"),
        Input("btn-180",  "n_clicks"),
        Input("btn-365",  "n_clicks"),
        Input("btn-1825", "n_clicks"),
        Input("btn-all",  "n_clicks"),
    )
    def set_date_range(*_):
        today = datetime.now()
        end_s = today.strftime("%Y-%m-%d")
        ctx   = callback_context
        if not ctx.triggered:
            return (today - timedelta(days=1825)).strftime("%Y-%m-%d"), end_s
        btn   = ctx.triggered[0]["prop_id"].split(".")[0]
        days  = {"btn-180": 180, "btn-365": 365, "btn-1825": 1825,
                 "btn-all": None}.get(btn, 1825)
        start_s = (today - timedelta(days=days)).strftime("%Y-%m-%d") if days else ""
        return start_s, end_s

    @app.callback(
        Output("timeseries-chart", "figure"),
        Output("station-info",     "children"),
        Input("selected-station",  "data"),
        Input("city-select",       "value"),
        Input("region-select",     "value"),
        Input("start-date-input",  "value"),
        Input("end-date-input",    "value"),
        Input("decade-select",     "value"),
        Input("btn-180",           "n_clicks"),
        Input("btn-365",           "n_clicks"),
        Input("btn-1825",          "n_clicks"),
        Input("btn-all",           "n_clicks"),
        State("stations-store",    "data"),
    )
    def render_timeseries(station_id, city, region,
                          start_val, end_val,
                          decade,
                          _b180, _b365, _b1825, _ball,
                          stations_data):
        if not station_id and not city and not region:
            empty = _empty_fig("← Station anklicken oder Stadt / Region wählen")
            return empty, "← Station anklicken oder Stadt / Region wählen"

        from fetcher import get_measurements, get_stations, get_average_timeseries
        from config import CACHE_DIR

        today = datetime.now()
        ctx   = callback_context
        trig  = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else ""
        _btn_days = {"btn-180": 180, "btn-365": 365, "btn-1825": 1825, "btn-all": None}
        if trig in _btn_days:
            days     = _btn_days[trig]
            end_dt   = today
            start_dt = (today - timedelta(days=days)) if days is not None else None
        else:
            end_dt   = datetime.fromisoformat(end_val)   if end_val   else today
            start_dt = datetime.fromisoformat(start_val) if start_val else None

        fig        = go.Figure()
        info_parts = []

        # ── Einzelstation ──────────────────────────────────────────────────
        if station_id:
            df_s = pd.DataFrame(stations_data or [])
            station_name = str(station_id)
            if not df_s.empty and "messstelle_id" in df_s.columns:
                row = df_s[df_s["messstelle_id"].astype(str) == str(station_id)]
                if not row.empty:
                    station_name = str(row.iloc[0].get("name", station_id)).strip()

            cache_file = CACHE_DIR / "messwerte" / f"{station_id}.parquet"

            if cache_file.exists():
                # Parquet vorhanden → direkt laden, kein ZIP-Scan
                try:
                    df = get_measurements(station_id, start=start_dt, end=end_dt)
                except Exception as e:
                    return _empty_fig(str(e)), f"Fehler: {e}"
            else:
                # Parquet fehlt → nur ausgewählte Dekade laden wenn lokal gecacht
                zip_path = CACHE_DIR / f"wasserstand_{decade}.zip"
                if not zip_path.exists():
                    df = pd.DataFrame()
                else:
                    try:
                        df = get_measurements(station_id,
                                              start=start_dt, end=end_dt,
                                              decades=[decade])
                    except Exception as e:
                        return _empty_fig(str(e)), f"Fehler: {e}"

            # Zeitfilter hat alles abgeschnitten → alle verfügbaren Daten zeigen
            clipped = False
            if df.empty and start_dt is not None and cache_file.exists():
                try:
                    df = get_measurements(station_id)
                    clipped = not df.empty
                except Exception:
                    pass

            if not df.empty:
                last      = df[COL_WASSERSTAND].iloc[-1]
                last_date = df[COL_DATUM].iloc[-1]
                last_str  = (last_date.strftime("%Y-%m-%d")
                             if hasattr(last_date, "strftime") else str(last_date)[:10])
                vmin  = df[COL_WASSERSTAND].min()
                vmax  = df[COL_WASSERSTAND].max()
                vmean = df[COL_WASSERSTAND].mean()

                fig.add_trace(go.Scatter(
                    x=df[COL_DATUM], y=df[COL_WASSERSTAND],
                    mode="lines", name=station_name,
                    line=dict(color=BLUE, width=1.8),
                ))
                if COL_FLURABSTAND in df.columns:
                    fig.add_trace(go.Scatter(
                        x=df[COL_DATUM], y=df[COL_FLURABSTAND],
                        mode="lines", name="Flurabstand",
                        line=dict(color=YELLOW, width=1.2, dash="dot"),
                        yaxis="y2", opacity=0.6,
                    ))
                for idx, color, label in [
                    (df[COL_WASSERSTAND].idxmin(), GREEN, f"Min {vmin:.2f} m"),
                    (df[COL_WASSERSTAND].idxmax(), RED,   f"Max {vmax:.2f} m"),
                ]:
                    r2 = df.loc[idx]
                    fig.add_annotation(
                        x=r2[COL_DATUM], y=r2[COL_WASSERSTAND],
                        text=label, showarrow=True, arrowhead=2,
                        arrowcolor=color, font=dict(color=color, size=10),
                        bgcolor=_rgba(SURFACE, 0.87),
                        bordercolor=color, borderwidth=1, borderpad=3,
                    )
                if clipped:
                    fig.add_annotation(
                        text=f"⚠ Kein aktueller Messwert – letzter: {last_str}",
                        xref="paper", yref="paper", x=0.5, y=0.97,
                        showarrow=False, font=dict(size=10, color=YELLOW),
                        bgcolor=_rgba(SURFACE, 0.9),
                        bordercolor=YELLOW, borderwidth=1, borderpad=4,
                    )
                    info_parts.append(
                        f"{station_name}  ⚠ letzter: {last_str}"
                        f"  ·  Ø {vmean:.2f} m  ·  ↓{vmin:.2f}  ↑{vmax:.2f}"
                        f"  ·  {len(df):,} Mess."
                    )
                else:
                    info_parts.append(
                        f"{station_name}  Aktuell: {last:.2f} m ({last_str})"
                        f"  ·  Ø {vmean:.2f} m  ·  ↓{vmin:.2f}  ↑{vmax:.2f}"
                        f"  ·  {len(df):,} Mess."
                    )

        # ── Durchschnitt Stadt / Region ────────────────────────────────────
        def _add_avg(label: str, bbox: tuple, color: str):
            try:
                df_area = get_stations(bbox_wgs84=bbox, only_with_data=True)
                if df_area.empty:
                    info_parts.append(f"Ø {label}: keine Stationen")
                    return
                ids = df_area["messstelle_id"].tolist()
                agg = get_average_timeseries(ids, start=start_dt, end=end_dt)
                if agg.empty:
                    info_parts.append(f"Ø {label}: keine Daten im Zeitraum")
                    return
                n = int(agg["n_stationen"].max())
                # Mittellinie
                fig.add_trace(go.Scatter(
                    x=agg["datum"], y=agg["mittel_m"],
                    mode="lines", name=f"Ø {label}  (n={n})",
                    line=dict(color=color, width=2.2),
                    hovertemplate=(
                        f"<b>Ø {label}</b>  %{{x|%Y-%m}}  %{{y:.3f}} m"
                        "<extra></extra>"
                    ),
                ))
                # Hinweis wenn letzter Datenpunkt im laufenden Monat liegt
                last_datum = agg["datum"].iloc[-1]
                now        = pd.Timestamp.now()
                if last_datum.year == now.year and last_datum.month == now.month:
                    fig.add_annotation(
                        text="(akt. Monat unvollständig – Meldungen gehen noch ein)",
                        xref="paper", yref="paper",
                        x=1.0, y=0.0,
                        xanchor="right", yanchor="bottom",
                        showarrow=False,
                        font=dict(size=9, color=SUBTEXT),
                    )
                info_parts.append(
                    f"Ø {label}: {agg['mittel_m'].mean():.2f} m  ·  Ø aus {n} Stationen"
                )
            except Exception as e:
                info_parts.append(f"Ø {label}: Fehler – {str(e)[:50]}")

        color_idx = 0
        for c in (city or []):
            if c in CITIES:
                _add_avg(c, CITIES[c], _OVERLAY_COLORS[color_idx % len(_OVERLAY_COLORS)])
                color_idx += 1
        for r in (region or []):
            if r in REGIONS:
                _add_avg(r, REGIONS[r], _OVERLAY_COLORS[color_idx % len(_OVERLAY_COLORS)])
                color_idx += 1

        if not fig.data:
            return _empty_fig("Keine Daten im gewählten Zeitfenster"), "Keine Daten"

        y_range = [40, 60]

        # X-Achse auf den gewählten Zeitraum begrenzen; None → Plotly auto-fit
        x_end   = end_dt or datetime.now()
        x_range = [start_dt.strftime("%Y-%m-%d"), x_end.strftime("%Y-%m-%d")] if start_dt else None

        fig.update_layout(
            template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=SURFACE,
            font=dict(color=TEXT, family="monospace", size=11),
            xaxis=dict(gridcolor=_rgba(SUBTEXT, 0.13), title="",
                       range=x_range),
            yaxis=dict(gridcolor=_rgba(SUBTEXT, 0.13), title="GW-Stand (m ü. NN)",
                       range=y_range),
            yaxis2=dict(title="Flurabstand (m)", overlaying="y", side="right",
                        gridcolor="rgba(0,0,0,0)", showgrid=False),
            legend=dict(
                orientation="v", x=0.01, y=0.99,
                xanchor="left", yanchor="top",
                font=dict(size=10),
                bgcolor=_rgba(SURFACE, 0.82),
                bordercolor=_rgba(SUBTEXT, 0.2), borderwidth=1,
            ),
            margin=dict(l=55, r=50, t=20, b=40),
        )
        return fig, "  |  ".join(info_parts)


# ---------------------------------------------------------------------------
# Hilfsfunktionen Plotly
# ---------------------------------------------------------------------------

def _map_layout(df: pd.DataFrame = None) -> dict:
    center, zoom = {"lat": 51.5, "lon": 7.5}, 7
    if df is not None and not df.empty and "lat" in df.columns:
        lats = df["lat"].dropna().astype(float)
        lons = df["lon"].dropna().astype(float)
        if not lats.empty:
            center = {"lat": float(lats.mean()), "lon": float(lons.mean())}
            zoom   = 8
    return dict(
        mapbox=dict(style=MAPBOX_STYLE, center=center, zoom=zoom),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=0, b=0),
        font=dict(color=TEXT),
    )


def _empty_fig(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, xref="paper", yref="paper",
                       x=0.5, y=0.5, showarrow=False,
                       font=dict(size=12, color=SUBTEXT))
    fig.update_layout(template="plotly_dark", paper_bgcolor=BG,
                      plot_bgcolor=SURFACE, margin=dict(l=0, r=0, t=0, b=0))
    return fig


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=DASH_PORT)
