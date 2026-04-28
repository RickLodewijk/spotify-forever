"""
Lightweight Spotify Dashboard - optimized for older hardware
Minimalistic interface with essential features only
"""
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
from collections import defaultdict

import spotipy
import streamlit as st
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

from spotify_long_term_tracker import (
    fetch_and_store_recent,
    get_db_connection,
    insert_play,
    normalize_ts,
)


DASHBOARD_CACHE_PATH = ".cache-spotify-dashboard-lite"
LOGIN_SCOPE = "user-read-recently-played"


def get_dashboard_redirect_uri() -> Optional[str]:
    return os.environ.get("SPOTIPY_DASHBOARD_REDIRECT_URI") or os.environ.get("SPOTIPY_REDIRECT_URI")


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


def load_stream_rows(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
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

    raw_rows = conn.execute(query).fetchall()
    cleaned_rows: List[Dict[str, Any]] = []

    for ts, artist, track, ms_played in raw_rows:
        if ts is None or artist is None or track is None or ms_played is None:
            continue

        try:
            ms_played_int = int(ms_played)
        except (TypeError, ValueError):
            continue

        if ms_played_int <= 0:
            continue

        cleaned_rows.append(
            {
                "ts": str(ts),
                "artist": str(artist),
                "track": str(track),
                "ms_played": ms_played_int,
            }
        )

    return cleaned_rows


def clear_auth_cache() -> None:
    cache_path = Path(DASHBOARD_CACHE_PATH)
    if cache_path.exists() and cache_path.is_file():
        cache_path.unlink()


def require_spotify_login() -> spotipy.Spotify:
    required = ["SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET"]
    missing = [k for k in required if not os.environ.get(k)]
    redirect_uri = get_dashboard_redirect_uri()

    if missing:
        st.error(
            "Spotify variabelen ontbreken. Vul SPOTIPY_CLIENT_ID en SPOTIPY_CLIENT_SECRET in .env in."
        )
        st.stop()

    if not redirect_uri:
        st.error(
            "Redirect URI ontbreekt. Vul SPOTIPY_DASHBOARD_REDIRECT_URI (of SPOTIPY_REDIRECT_URI) in .env in."
        )
        st.stop()

    if "code=" in redirect_uri or "error=" in redirect_uri:
        st.error(
            "De redirect URI is ongeldig. Gebruik een vaste URI zoals http://localhost:8501."
        )
        st.stop()

    auth_manager = SpotifyOAuth(
        redirect_uri=redirect_uri,
        scope=LOGIN_SCOPE,
        cache_path=DASHBOARD_CACHE_PATH,
        show_dialog=False,
    )

    query_params = st.query_params
    auth_error = query_params.get("error")
    if auth_error:
        st.error(f"Spotify login mislukt: {auth_error}")
        if st.button("Opnieuw proberen"):
            st.query_params.clear()
            st.rerun()
        st.stop()

    token_info = auth_manager.get_cached_token()
    auth_code = query_params.get("code")

    if not token_info and auth_code:
        try:
            auth_manager.get_access_token(auth_code, as_dict=True)
            st.query_params.clear()
            st.rerun()
        except Exception as exc:
            st.error(f"Kon Spotify callback niet verwerken: {exc}")
            st.stop()

    token_info = auth_manager.get_cached_token()
    if not token_info:
        st.title("Spotify Long-term Tracker")
        st.subheader("Log in met Spotify")
        st.write("Je moet eerst inloggen met Spotify.")
        auth_url = auth_manager.get_authorize_url()
        st.link_button("Inloggen met Spotify", auth_url, use_container_width=True)
        st.stop()

    return spotipy.Spotify(auth_manager=auth_manager)


def render_overview(rows: List[Dict[str, Any]]) -> None:
    """Display basic statistics"""
    total_minutes = float(sum(row["ms_played"] for row in rows)) / 60000.0
    unique_artists = len({row["artist"] for row in rows})
    total_tracks = len(rows)

    st.markdown("### Statistieken")
    col1, col2, col3 = st.columns(3)
    col1.metric("Luisterminuten", f"{total_minutes:,.0f}")
    col2.metric("Unieke artiesten", f"{unique_artists:,}")
    col3.metric("Nummers afgespeeld", f"{total_tracks:,}")


def render_top_artists(rows: List[Dict[str, Any]]) -> None:
    """Display top artists as a simple table"""
    artist_ms: Dict[str, int] = defaultdict(int)
    for row in rows:
        artist_ms[row["artist"]] += row["ms_played"]

    top_artists = sorted(artist_ms.items(), key=lambda item: item[1], reverse=True)[:15]

    if not top_artists:
        st.info("Nog geen artiestdata beschikbaar.")
        return

    st.markdown("### Top 15 Meest Beluisterde Artiesten")
    
    data = []
    for rank, (artist, ms_played) in enumerate(top_artists, 1):
        hours = ms_played / 3_600_000.0
        data.append({
            "#": rank,
            "Artiest": artist,
            "Uren": f"{hours:.1f}"
        })
    
    st.dataframe(data, use_container_width=True, hide_index=True)


def render_track_search(rows: List[Dict[str, Any]]) -> None:
    """Simple track search"""
    st.markdown("### Nummers Zoeken")
    search_term = st.text_input("Zoek op tracknaam")

    track_stats: Dict[tuple, Dict[str, int]] = {}
    for row in rows:
        key = (row["track"], row["artist"])
        if key not in track_stats:
            track_stats[key] = {"ms_played": 0, "play_count": 0}
        track_stats[key]["ms_played"] += row["ms_played"]
        track_stats[key]["play_count"] += 1

    track_agg = []
    for (track, artist), stats in track_stats.items():
        track_agg.append({
            "track": track,
            "artist": artist,
            "ms_played": stats["ms_played"],
            "play_count": stats["play_count"],
            "minutes": stats["ms_played"] / 60000.0,
        })
    track_agg.sort(key=lambda x: x["ms_played"], reverse=True)

    if not search_term.strip():
        st.caption("Top 20 nummers:")
        display_data = [
            {
                "Track": item["track"][:50],
                "Artiest": item["artist"][:40],
                "Minuten": f"{item['minutes']:.0f}",
                "Keer afgespeeld": item["play_count"],
            }
            for item in track_agg[:20]
        ]
        st.dataframe(display_data, use_container_width=True, hide_index=True)
        return

    search_lower = search_term.lower()
    filtered = [i for i in track_agg if search_lower in i["track"].lower()]

    if not filtered:
        st.warning("Geen nummers gevonden.")
        return

    total_minutes = sum(i["minutes"] for i in filtered)
    total_plays = sum(i["play_count"] for i in filtered)

    col1, col2 = st.columns(2)
    col1.metric("Totale minuten", f"{total_minutes:.0f}")
    col2.metric("Totaal afgespeeld", total_plays)

    display_data = [
        {
            "Track": item["track"][:50],
            "Artiest": item["artist"][:40],
            "Minuten": f"{item['minutes']:.0f}",
            "Keer": item["play_count"],
        }
        for item in filtered[:50]
    ]
    st.dataframe(display_data, use_container_width=True, hide_index=True)


def import_uploaded_endsong_files(
    conn: sqlite3.Connection,
    uploaded_files: List,
    min_ms_played: int,
) -> Dict[str, int]:
    """Import JSON files from Spotify export"""
    inserted = 0
    skipped_short = 0
    duplicates = 0
    invalid_rows = 0

    for uploaded_file in uploaded_files:
        try:
            payload = json.loads(uploaded_file.getvalue().decode("utf-8"))
        except Exception:
            invalid_rows += 1
            continue

        if not isinstance(payload, list):
            invalid_rows += 1
            continue

        for row in payload:
            if not isinstance(row, dict):
                invalid_rows += 1
                continue

            ts_raw = row.get("ts")
            if not ts_raw:
                invalid_rows += 1
                continue

            try:
                ms_played = int(row.get("ms_played") or 0)
            except (TypeError, ValueError):
                invalid_rows += 1
                continue

            if ms_played < min_ms_played:
                skipped_short += 1
                continue

            try:
                play = {
                    "ts": normalize_ts(str(ts_raw)),
                    "source": "spotify_export",
                    "track_name": row.get("master_metadata_track_name"),
                    "artist_name": row.get("master_metadata_album_artist_name"),
                    "album_name": row.get("master_metadata_album_album_name"),
                    "track_uri": row.get("spotify_track_uri"),
                    "ms_played": ms_played,
                    "reason_end": row.get("reason_end"),
                    "skipped": 1 if row.get("skipped") else 0,
                    "raw_json": row,
                }
                was_inserted = insert_play(conn, play)
            except Exception:
                invalid_rows += 1
                continue

            if was_inserted:
                inserted += 1
            else:
                duplicates += 1

    return {
        "inserted": inserted,
        "skipped_short": skipped_short,
        "duplicates": duplicates,
        "invalid_rows": invalid_rows,
    }


def render_json_upload_section(db_path: str, default_min_ms_played: int = 30000) -> None:
    """Upload JSON files from Spotify export"""
    st.markdown("### JSON Importeren")
    st.caption("Upload EndsSong_*.json bestanden uit je Spotify export.")

    min_ms_played = st.number_input(
        "Minimale luistertijd (ms)",
        min_value=0,
        max_value=600000,
        value=default_min_ms_played,
        step=1000,
    )

    uploaded_files = st.file_uploader(
        "Kies bestanden",
        type=["json"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        return

    if st.button("Importeer JSON", use_container_width=True):
        conn = get_db_connection(db_path)
        try:
            result = import_uploaded_endsong_files(
                conn,
                uploaded_files,
                min_ms_played=int(min_ms_played),
            )
        finally:
            conn.close()

        st.success(
            f"+{result['inserted']} toegevoegd, "
            f"{result['duplicates']} duplicaten, "
            f"{result['skipped_short']} korter dan minimum"
        )


def main() -> None:
    load_environment()

    # Minimal page config for older hardware
    st.set_page_config(
        page_title="Spotify Tracker",
        layout="wide",
        initial_sidebar_state="collapsed"  # Start with sidebar collapsed
    )

    sp = require_spotify_login()

    st.title("Spotify Long-term Tracker")

    # Simple sidebar with minimal options
    st.sidebar.header("Menu")
    db_path = st.sidebar.text_input("Database pad", value="spotify_tracker.db")
    
    if st.sidebar.button("Uitloggen", use_container_width=True):
        clear_auth_cache()
        st.query_params.clear()
        st.rerun()

    # Auto-sync on load
    try:
        sync_conn = get_db_connection(db_path)
        try:
            inserted, skipped_short, duplicates = fetch_and_store_recent(
                sync_conn,
                sp,
                min_ms_played=30000,
                limit=50,
            )
        finally:
            sync_conn.close()

        if inserted > 0 or duplicates > 0:
            st.sidebar.info(f"Sync: +{inserted}, {duplicates} dup.")
    except Exception as exc:
        st.sidebar.warning(f"Sync fout: {exc}")

    # Load data
    if not Path(db_path).exists():
        st.error(f"Database niet gevonden: {db_path}")
        st.info("Na login verschijnt de database hier.")
        return

    try:
        conn = get_connection(db_path)
        rows = load_stream_rows(conn)
        conn.close()
    except Exception as exc:
        st.error(f"Kan data niet laden: {exc}")
        return

    if not rows:
        st.info("Nog geen luisterdata. Upload JSON bestanden hieronder.")
        render_json_upload_section(db_path)
        return

    # Main content
    render_overview(rows)
    st.divider()
    render_top_artists(rows)
    st.divider()
    render_track_search(rows)
    st.divider()
    render_json_upload_section(db_path)


if __name__ == "__main__":
    main()
