import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import plotly.express as px
import spotipy
import streamlit as st
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth


DASHBOARD_CACHE_PATH = ".cache-spotify-dashboard"


def load_environment(env_file: str = ".env") -> None:
    env_path = Path(env_file)
    if env_path.exists() and env_path.is_file():
        load_dotenv(dotenv_path=env_path, override=True)


def get_connection(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path, check_same_thread=False)


def get_table_columns(conn: sqlite3.Connection, table_name: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [r[1] for r in rows]


def pick_column(columns: List[str], candidates: List[str], label: str) -> str:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    raise ValueError(f"Geen bruikbare kolom gevonden voor {label}. Gezocht: {candidates}")


def load_streams_df(conn: sqlite3.Connection) -> pd.DataFrame:
    columns = get_table_columns(conn, "plays")

    ts_col = pick_column(columns, ["ts"], "timestamp")
    artist_col = pick_column(columns, ["artist", "artist_name"], "artist")
    track_col = pick_column(columns, ["track", "track_name"], "track")
    ms_col = pick_column(columns, ["ms_played"], "ms_played")

    query = f"""
        SELECT
            {ts_col} AS ts,
            {artist_col} AS artist,
            {track_col} AS track,
            {ms_col} AS ms_played
        FROM plays
        WHERE {ms_col} IS NOT NULL AND {ms_col} > 0
    """

    df = pd.read_sql_query(query, conn)
    if df.empty:
        return df

    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts", "artist", "track", "ms_played"])
    df["ms_played"] = pd.to_numeric(df["ms_played"], errors="coerce")
    df = df.dropna(subset=["ms_played"])
    return df


def build_spotify_client() -> Optional[spotipy.Spotify]:
    required = ["SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        return None

    redirect_uri = os.environ.get("SPOTIPY_REDIRECT_URI", "")
    if "code=" in redirect_uri or "error=" in redirect_uri:
        raise ValueError(
            "SPOTIPY_REDIRECT_URI is ongeldig. Gebruik een vaste callback URI zoals http://127.0.0.1:8888/callback"
        )

    scope = "user-read-currently-playing"
    auth_manager = SpotifyOAuth(scope=scope, cache_path=DASHBOARD_CACHE_PATH)
    return spotipy.Spotify(auth_manager=auth_manager)


def get_now_playing(sp: spotipy.Spotify) -> Dict[str, str]:
    current = sp.current_user_playing_track()
    if not current or not current.get("is_playing"):
        return {"status": "Niet actief aan het afspelen"}

    item = current.get("item") or {}
    artists = item.get("artists") or []
    artist_names = ", ".join(a.get("name", "") for a in artists if a.get("name"))
    track_name = item.get("name") or "Onbekend nummer"
    album_name = (item.get("album") or {}).get("name") or "Onbekend album"

    return {
        "status": "Speelt nu",
        "track": track_name,
        "artist": artist_names or "Onbekende artiest",
        "album": album_name,
    }


def render_overview(df: pd.DataFrame) -> None:
    total_minutes = float(df["ms_played"].sum()) / 60000.0
    unique_artists = int(df["artist"].nunique())

    c1, c2 = st.columns(2)
    c1.metric("Totaal geluisterde minuten", f"{total_minutes:,.0f}")
    c2.metric("Unieke artiesten", f"{unique_artists:,}")


def render_top_artists(df: pd.DataFrame) -> None:
    agg = (
        df.groupby("artist", as_index=False)["ms_played"]
        .sum()
        .sort_values("ms_played", ascending=False)
        .head(10)
    )
    agg["hours_played"] = agg["ms_played"] / 3_600_000.0

    fig = px.bar(
        agg,
        x="artist",
        y="hours_played",
        title="Top 10 meest beluisterde artiesten (uren)",
        labels={"artist": "Artiest", "hours_played": "Luistertijd (uren)"},
        hover_data={"hours_played": ":.2f", "ms_played": True},
    )
    fig.update_layout(xaxis_tickangle=-35)
    st.plotly_chart(fig, use_container_width=True)

def render_track_search(df: pd.DataFrame) -> None:
    st.subheader("Zoek op nummer")
    search_term = st.text_input("Zoek op tracknaam", placeholder="Bijv. Blinding Lights")

    track_agg = (
        df.groupby(["track", "artist"], as_index=False)
        .agg(
            ms_played=("ms_played", "sum"),
            play_count=("ms_played", "size"),
        )
        .sort_values("ms_played", ascending=False)
    )
    track_agg["minutes_played"] = track_agg["ms_played"] / 60000.0

    if not search_term.strip():
        st.caption("Voer een tracknaam in om je luistertijd te zien.")
        st.dataframe(
            track_agg[["track", "artist", "minutes_played", "play_count"]].head(10),
            use_container_width=True,
            hide_index=True,
        )
        return

    filtered = track_agg[track_agg["track"].str.contains(search_term, case=False, na=False)].copy()
    if filtered.empty:
        st.warning("Geen nummers gevonden voor deze zoekterm.")
        return

    total_minutes_for_search = float(filtered["minutes_played"].sum())
    total_count_for_search = int(filtered["play_count"].sum())

    c1, c2 = st.columns(2)
    c1.metric("Totale luistertijd voor zoekresultaat (minuten)", f"{total_minutes_for_search:,.1f}")
    c2.metric("Totaal aantal keer afgespeeld", f"{total_count_for_search:,}")

    top_match = filtered.iloc[0]
    st.success(
        f"Top match: {top_match['track']} - {top_match['artist']} ({top_match['minutes_played']:.1f} minuten, {int(top_match['play_count'])}x afgespeeld)"
    )

    st.dataframe(
        filtered[["track", "artist", "minutes_played", "play_count"]],
        use_container_width=True,
        hide_index=True,
    )


def render_now_playing() -> None:
    st.subheader("Nu aan het luisteren")
    try:
        sp = build_spotify_client()
        if not sp:
            st.warning("Spotify variabelen ontbreken. Vul SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET en SPOTIPY_REDIRECT_URI in .env in.")
            return

        now_playing = get_now_playing(sp)
        if now_playing.get("status") != "Speelt nu":
            st.info(now_playing["status"])
            return

        st.success(now_playing["status"])
        st.write(f"**Track:** {now_playing['track']}")
        st.write(f"**Artiest:** {now_playing['artist']}")
        st.write(f"**Album:** {now_playing['album']}")
    except Exception as exc:
        st.error(f"Kon huidige track niet ophalen: {exc}")


def main() -> None:
    load_environment()

    st.set_page_config(page_title="Spotify Long-term Dashboard", layout="wide")
    st.title("Spotify Long-term Tracker Dashboard")

    st.sidebar.header("Instellingen")
    db_path = st.sidebar.text_input("SQLite databasepad", value="spotify_tracker.db")
    refresh_seconds = st.sidebar.slider("Auto-refresh (seconden)", 0, 300, 30, 5)
    enable_now_playing = st.sidebar.checkbox("Nu aan het luisteren ophalen via Spotify API", value=False)

    if refresh_seconds > 0:
        st.markdown(
            f"<meta http-equiv='refresh' content='{refresh_seconds}'>",
            unsafe_allow_html=True,
        )

    if not Path(db_path).exists():
        st.error(f"Database niet gevonden: {db_path}")
        return

    conn = get_connection(db_path)
    try:
        df = load_streams_df(conn)
    except Exception as exc:
        st.error(f"Kon data niet laden uit database: {exc}")
        return
    finally:
        conn.close()

    if df.empty:
        st.info("Nog geen streamdata beschikbaar in de database.")
        render_now_playing()
        return

    render_overview(df)
    st.divider()
    render_top_artists(df)
    st.divider()
    if enable_now_playing:
        render_now_playing()
    else:
        st.subheader("Nu aan het luisteren")
        st.info("Zet in de sidebar 'Nu aan het luisteren ophalen via Spotify API' aan als je live current track wilt tonen.")
    st.divider()
    render_track_search(df)
    st.divider()


if __name__ == "__main__":
    main()
