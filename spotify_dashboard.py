import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
from collections import defaultdict

import plotly.express as px
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


DASHBOARD_CACHE_PATH = ".cache-spotify-dashboard"
LOGIN_SCOPE = "user-read-currently-playing user-read-recently-played"


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
        st.title("Spotify Long-term Tracker Dashboard")
        st.subheader("Log in met Spotify")
        st.write("Je moet eerst inloggen met Spotify voordat je dashboard zichtbaar wordt.")
        auth_url = auth_manager.get_authorize_url()
        st.link_button("Inloggen met Spotify", auth_url, use_container_width=True)
        st.stop()

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


def render_overview(rows: List[Dict[str, Any]]) -> None:
    total_minutes = float(sum(row["ms_played"] for row in rows)) / 60000.0
    unique_artists = len({row["artist"] for row in rows})

    c1, c2 = st.columns(2)
    c1.metric("Totaal geluisterde minuten", f"{total_minutes:,.0f}")
    c2.metric("Unieke artiesten", f"{unique_artists:,}")


def render_top_artists(rows: List[Dict[str, Any]]) -> None:
    artist_ms: Dict[str, int] = defaultdict(int)
    for row in rows:
        artist_ms[row["artist"]] += row["ms_played"]

    top_artists: List[Tuple[str, int]] = sorted(
        artist_ms.items(), key=lambda item: item[1], reverse=True
    )[:10]

    if not top_artists:
        st.info("Nog geen artiestdata beschikbaar.")
        return

    artists = [artist for artist, _ in top_artists]
    hours_played = [ms_played / 3_600_000.0 for _, ms_played in top_artists]

    fig = px.bar(
        x=artists,
        y=hours_played,
        title="Top 10 meest beluisterde artiesten (uren)",
        labels={"x": "Artiest", "y": "Luistertijd (uren)"},
    )
    fig.update_layout(xaxis_tickangle=-35)
    st.plotly_chart(fig, use_container_width=True)

def render_track_search(rows: List[Dict[str, Any]]) -> None:
    st.subheader("Zoek op nummer")
    search_term = st.text_input("Zoek op tracknaam", placeholder="Bijv. Blinding Lights")

    track_stats: Dict[Tuple[str, str], Dict[str, int]] = {}
    for row in rows:
        key = (row["track"], row["artist"])
        if key not in track_stats:
            track_stats[key] = {"ms_played": 0, "play_count": 0}
        track_stats[key]["ms_played"] += row["ms_played"]
        track_stats[key]["play_count"] += 1

    track_agg: List[Dict[str, Any]] = []
    for (track, artist), stats in track_stats.items():
        track_agg.append(
            {
                "track": track,
                "artist": artist,
                "ms_played": stats["ms_played"],
                "play_count": stats["play_count"],
                "minutes_played": stats["ms_played"] / 60000.0,
            }
        )
    track_agg.sort(key=lambda item: item["ms_played"], reverse=True)

    if not search_term.strip():
        st.caption("Voer een tracknaam in om je luistertijd te zien.")
        st.dataframe(
            [
                {
                    "track": item["track"],
                    "artist": item["artist"],
                    "minutes_played": item["minutes_played"],
                    "play_count": item["play_count"],
                }
                for item in track_agg[:10]
            ],
            use_container_width=True,
            hide_index=True,
        )
        return

    search_term_lower = search_term.lower()
    filtered = [
        item for item in track_agg if search_term_lower in str(item["track"]).lower()
    ]

    if not filtered:
        st.warning("Geen nummers gevonden voor deze zoekterm.")
        return

    total_minutes_for_search = float(sum(item["minutes_played"] for item in filtered))
    total_count_for_search = int(sum(item["play_count"] for item in filtered))

    c1, c2 = st.columns(2)
    c1.metric("Totale luistertijd voor zoekresultaat (minuten)", f"{total_minutes_for_search:,.1f}")
    c2.metric("Totaal aantal keer afgespeeld", f"{total_count_for_search:,}")

    top_match = filtered[0]
    st.success(
        f"Top match: {top_match['track']} - {top_match['artist']} ({top_match['minutes_played']:.1f} minuten, {int(top_match['play_count'])}x afgespeeld)"
    )

    st.dataframe(
        [
            {
                "track": item["track"],
                "artist": item["artist"],
                "minutes_played": item["minutes_played"],
                "play_count": item["play_count"],
            }
            for item in filtered
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_now_playing(sp: spotipy.Spotify) -> None:
    st.subheader("Nu aan het luisteren")
    try:
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


def import_uploaded_endsong_files(
    conn: sqlite3.Connection,
    uploaded_files: List[st.runtime.uploaded_file_manager.UploadedFile],
    min_ms_played: int,
) -> Dict[str, int]:
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
    st.subheader("JSON upload (Spotify export)")
    st.caption("Upload 1 of meerdere EndsSong_*.json bestanden om historische luisterdata in te laden.")

    min_ms_played = st.number_input(
        "Minimale luistertijd (ms)",
        min_value=0,
        max_value=600000,
        value=default_min_ms_played,
        step=1000,
    )

    uploaded_files = st.file_uploader(
        "Kies EndsSong JSON-bestanden",
        type=["json"],
        accept_multiple_files=True,
        help="Je kunt meerdere bestanden in een keer uploaden.",
    )

    if not uploaded_files:
        return

    st.write(f"Geselecteerde bestanden: {len(uploaded_files)}")

    if st.button("Importeer JSON naar database", use_container_width=True):
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
            "Import klaar: "
            f"+{result['inserted']} toegevoegd, "
            f"{result['duplicates']} duplicaten, "
            f"{result['skipped_short']} korter dan minimum, "
            f"{result['invalid_rows']} ongeldig"
        )


def main() -> None:
    load_environment()

    st.set_page_config(page_title="Spotify Long-term Dashboard", layout="wide")
    sp = require_spotify_login()

    st.title("Spotify Long-term Tracker Dashboard")

    st.sidebar.header("Instellingen")
    db_path = st.sidebar.text_input("SQLite databasepad", value="spotify_tracker.db")
    refresh_seconds = st.sidebar.slider("Auto-refresh (seconden)", 0, 300, 30, 5)
    enable_now_playing = st.sidebar.checkbox("Nu aan het luisteren ophalen via Spotify API", value=False)
    enable_auto_sync = st.sidebar.checkbox("Na login recent afgespeelde tracks opslaan", value=True)

    if st.sidebar.button("Uitloggen (Spotify)"):
        clear_auth_cache()
        st.query_params.clear()
        st.rerun()

    if refresh_seconds > 0:
        st.markdown(
            f"<meta http-equiv='refresh' content='{refresh_seconds}'>",
            unsafe_allow_html=True,
        )

    sync_message_shown = False
    if enable_auto_sync:
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

            st.sidebar.success(
                f"Sync klaar: +{inserted} toegevoegd, {skipped_short} kort, {duplicates} duplicaat"
            )
            sync_message_shown = True
        except Exception as exc:
            st.sidebar.error(f"Auto-sync mislukt: {exc}")

    if not Path(db_path).exists():
        st.error(f"Database niet gevonden: {db_path}")
        st.info("Na je Spotify-login wordt de database automatisch aangemaakt zodra een sync slaagt.")
        return

    conn = get_connection(db_path)
    try:
        rows = load_stream_rows(conn)
    except Exception as exc:
        st.error(f"Kon data niet laden uit database: {exc}")
        return
    finally:
        conn.close()

    if not rows:
        st.info("Nog geen streamdata beschikbaar in de database.")
        render_json_upload_section(db_path)
        if enable_now_playing:
            render_now_playing(sp)
        elif sync_message_shown:
            st.info("Eerste sync is uitgevoerd. Zodra er genoeg luisterdata is, verschijnt die hier.")
        return

    render_overview(rows)
    st.divider()
    render_top_artists(rows)
    st.divider()
    if enable_now_playing:
        render_now_playing(sp)
    else:
        st.subheader("Nu aan het luisteren")
        st.info("Zet in de sidebar 'Nu aan het luisteren ophalen via Spotify API' aan als je live current track wilt tonen.")
    st.divider()
    render_track_search(rows)
    st.divider()
    render_json_upload_section(db_path)
    st.divider()


if __name__ == "__main__":
    main()
