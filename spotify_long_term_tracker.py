import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv


TRACKER_CACHE_PATH = ".cache-spotify-tracker"


DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    track_name TEXT NOT NULL,
    artist_name TEXT,
    album_name TEXT,
    track_uri TEXT,
    ms_played INTEGER,
    reason_end TEXT,
    skipped INTEGER,
    raw_json TEXT,
    inserted_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(ts, track_name)
);
"""

UNIQUE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_plays_ts_track_name
ON plays (ts, track_name);
"""


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_environment(env_file: str) -> None:
    env_path = Path(env_file)
    if env_path.exists() and env_path.is_file():
        load_dotenv(dotenv_path=env_path, override=True)
        logging.info(".env geladen vanaf: %s", env_path.resolve())
    else:
        logging.info("Geen .env bestand gevonden op %s, val terug op bestaande omgevingsvariabelen.", env_file)


def get_db_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(DB_SCHEMA)
    conn.execute(UNIQUE_INDEX_SQL)
    conn.commit()
    return conn


def normalize_ts(ts: str) -> str:
    # Normaliseer timestamps zodat JSON-import en API-data consistent vergelijkbaar zijn.
    if ts.endswith("Z"):
        ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts).astimezone(timezone.utc).isoformat()


def normalize_track_name(track_name: Optional[str], track_uri: Optional[str]) -> str:
    if track_name and str(track_name).strip():
        return str(track_name).strip()
    if track_uri and str(track_uri).strip():
        return f"uri:{track_uri.strip()}"
    return "unknown_track"


def insert_play(conn: sqlite3.Connection, play: Dict[str, Any]) -> bool:
    normalized_track_name = normalize_track_name(
        play.get("track_name"),
        play.get("track_uri"),
    )

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO plays (
            ts, source, track_name, artist_name, album_name,
            track_uri, ms_played, reason_end, skipped, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            play.get("ts"),
            play.get("source"),
            normalized_track_name,
            play.get("artist_name"),
            play.get("album_name"),
            play.get("track_uri"),
            play.get("ms_played"),
            play.get("reason_end"),
            play.get("skipped"),
            json.dumps(play.get("raw_json", {}), ensure_ascii=True),
        ),
    )
    conn.commit()
    return cursor.rowcount == 1


def import_endsong_folder(conn: sqlite3.Connection, folder_path: str, min_ms_played: int = 30000) -> Tuple[int, int, int]:
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"Map niet gevonden: {folder_path}")

    files = sorted(folder.glob("EndsSong_*.json"))
    if not files:
        logging.warning("Geen EndsSong_*.json bestanden gevonden in: %s", folder_path)
        return 0, 0, 0

    inserted = 0
    skipped_short = 0
    duplicates = 0

    for file_path in files:
        try:
            with file_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except json.JSONDecodeError as exc:
            logging.error("JSON parse error in %s: %s", file_path.name, exc)
            continue

        if not isinstance(payload, list):
            logging.warning("Bestand %s overgeslagen: verwachtte een lijst", file_path.name)
            continue

        for row in payload:
            ts_raw = row.get("ts")
            ms_played = int(row.get("ms_played") or 0)

            if not ts_raw:
                continue

            if ms_played < min_ms_played:
                skipped_short += 1
                continue

            play = {
                "ts": normalize_ts(ts_raw),
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
            if was_inserted:
                inserted += 1
            else:
                duplicates += 1

    logging.info(
        "Import klaar | toegevoegd=%d | korter_dan_30s=%d | duplicaten=%d",
        inserted,
        skipped_short,
        duplicates,
    )
    return inserted, skipped_short, duplicates


def build_spotify_client() -> spotipy.Spotify:
    required_env = ["SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI"]
    missing = [name for name in required_env if not os.environ.get(name)]
    if missing:
        raise EnvironmentError(
            "Ontbrekende omgevingsvariabelen: "
            + ", ".join(missing)
            + " | Zet deze in je shell of in .env voordat je het script draait."
        )

    redirect_uri = os.environ.get("SPOTIPY_REDIRECT_URI", "")
    if "code=" in redirect_uri or "error=" in redirect_uri:
        raise EnvironmentError(
            "SPOTIPY_REDIRECT_URI lijkt een callback-resultaat i.p.v. een vaste URI. "
            "Gebruik bijvoorbeeld: http://127.0.0.1:8888/callback"
        )

    scope = "user-read-recently-played"
    auth_manager = SpotifyOAuth(scope=scope, cache_path=TRACKER_CACHE_PATH)
    return spotipy.Spotify(auth_manager=auth_manager)


def estimate_recent_ms_played(items: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Optional[int]]]:
    """
    De API geeft geen direct 'ms_played' terug in recently played.
    Daarom schatten we de luistertijd met het tijdsverschil tussen plays,
    begrensd op trackduur.
    """
    parsed: List[Tuple[Dict[str, Any], datetime]] = []
    for item in items:
        played_at = item.get("played_at")
        if not played_at:
            continue
        dt = datetime.fromisoformat(played_at.replace("Z", "+00:00"))
        parsed.append((item, dt))

    parsed.sort(key=lambda x: x[1])

    out: List[Tuple[Dict[str, Any], Optional[int]]] = []
    for idx, (item, dt) in enumerate(parsed):
        track = item.get("track") or {}
        duration_ms = track.get("duration_ms")

        if idx < len(parsed) - 1:
            next_dt = parsed[idx + 1][1]
            delta_ms = int((next_dt - dt).total_seconds() * 1000)
            if isinstance(duration_ms, int):
                estimate = max(0, min(delta_ms, duration_ms))
            else:
                estimate = max(0, delta_ms)
        else:
            estimate = None

        out.append((item, estimate))

    return out


def fetch_and_store_recent(
    conn: sqlite3.Connection,
    sp: spotipy.Spotify,
    min_ms_played: int = 30000,
    limit: int = 50,
    before: Optional[int] = None,
) -> Tuple[int, int, int]:
    kwargs: Dict[str, Any] = {"limit": limit}
    if before is not None:
        kwargs["before"] = before

    response = sp.current_user_recently_played(**kwargs)
    items = response.get("items", [])

    estimated_items = estimate_recent_ms_played(items)

    inserted = 0
    skipped_short = 0
    duplicates = 0

    for item, estimated_ms in estimated_items:
        track = item.get("track") or {}
        played_at = item.get("played_at")
        if not played_at:
            continue

        if estimated_ms is None:
            # Voor het nieuwste item ontbreekt een volgend tijdspunt; gebruik trackduur als best-effort.
            duration_ms = track.get("duration_ms")
            estimated_ms = int(duration_ms) if isinstance(duration_ms, int) else 0

        if estimated_ms < min_ms_played:
            skipped_short += 1
            continue

        artists = track.get("artists") or []
        primary_artist = artists[0].get("name") if artists else None
        album = track.get("album") or {}

        play = {
            "ts": normalize_ts(played_at),
            "source": "spotify_api_recently_played",
            "track_name": track.get("name"),
            "artist_name": primary_artist,
            "album_name": album.get("name"),
            "track_uri": track.get("uri"),
            "ms_played": estimated_ms,
            "reason_end": None,
            "skipped": None,
            "raw_json": item,
        }

        was_inserted = insert_play(conn, play)
        if was_inserted:
            inserted += 1
        else:
            duplicates += 1

    logging.info(
        "API sync klaar | toegevoegd=%d | korter_dan_30s=%d | duplicaten=%d",
        inserted,
        skipped_short,
        duplicates,
    )
    return inserted, skipped_short, duplicates


def backfill_recent_history(
    conn: sqlite3.Connection,
    sp: spotipy.Spotify,
    min_ms_played: int = 30000,
    page_limit: int = 50,
    max_pages: int = 12,
) -> Tuple[int, int, int]:
    total_inserted = 0
    total_skipped_short = 0
    total_duplicates = 0

    before: Optional[int] = None
    for page in range(1, max_pages + 1):
        kwargs: Dict[str, Any] = {"limit": page_limit}
        if before is not None:
            kwargs["before"] = before

        response = sp.current_user_recently_played(**kwargs)
        items = response.get("items", [])
        if not items:
            logging.info("Backfill gestopt: geen extra items meer (pagina %d).", page)
            break

        inserted, skipped_short, duplicates = fetch_and_store_recent(
            conn,
            sp,
            min_ms_played=min_ms_played,
            limit=page_limit,
            before=before,
        )
        total_inserted += inserted
        total_skipped_short += skipped_short
        total_duplicates += duplicates

        oldest_played_at = items[-1].get("played_at")
        if not oldest_played_at:
            logging.info("Backfill gestopt: oudste item zonder played_at (pagina %d).", page)
            break

        oldest_dt = datetime.fromisoformat(oldest_played_at.replace("Z", "+00:00"))
        before = int(oldest_dt.timestamp() * 1000) - 1

        logging.info(
            "Backfill pagina %d/%d verwerkt | toegevoegd=%d | korter_dan_30s=%d | duplicaten=%d",
            page,
            max_pages,
            inserted,
            skipped_short,
            duplicates,
        )

    logging.info(
        "Backfill totaal klaar | toegevoegd=%d | korter_dan_30s=%d | duplicaten=%d",
        total_inserted,
        total_skipped_short,
        total_duplicates,
    )
    return total_inserted, total_skipped_short, total_duplicates


def run_always_on_loop(
    conn: sqlite3.Connection,
    interval_minutes: int = 20,
    min_ms_played: int = 30000,
    startup_backfill_pages: int = 12,
) -> None:
    sp = build_spotify_client()
    interval_seconds = interval_minutes * 60

    logging.info(
        "Always-on loop gestart | interval=%d min | min_ms_played=%d",
        interval_minutes,
        min_ms_played,
    )
    logging.info(
        "Let op: Spotify recently played endpoint bevat geen echte ms_played; script gebruikt een tijdsverschil-schatting."
    )

    if startup_backfill_pages > 0:
        try:
            logging.info("Startup backfill gestart (max %d pagina's).", startup_backfill_pages)
            backfill_recent_history(
                conn,
                sp,
                min_ms_played=min_ms_played,
                max_pages=startup_backfill_pages,
            )
        except Exception as exc:
            logging.exception("Fout tijdens startup backfill: %s", exc)

    while True:
        try:
            fetch_and_store_recent(conn, sp, min_ms_played=min_ms_played)
        except Exception as exc:
            logging.exception("Fout tijdens API sync: %s", exc)

        logging.info("Wachten %d minuten tot volgende sync...", interval_minutes)
        time.sleep(interval_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Spotify Long-term Tracker (sqlite3 + spotipy)")
    parser.add_argument("--db-path", default="spotify_tracker.db", help="Pad naar SQLite database")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Pad naar .env bestand met SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET en SPOTIPY_REDIRECT_URI",
    )
    parser.add_argument(
        "--min-ms-played",
        type=int,
        default=30000,
        help="Sla alleen tracks op met minimaal deze luistertijd in milliseconden",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_import = subparsers.add_parser("import-json", help="Eenmalig EndsSong_*.json importeren")
    p_import.add_argument("--folder", required=True, help="Map met EndsSong_*.json bestanden")

    p_run = subparsers.add_parser("run", help="Start always-on API sync loop")
    p_run.add_argument("--interval-minutes", type=int, default=20, help="Sync-interval in minuten")
    p_run.add_argument(
        "--startup-backfill-pages",
        type=int,
        default=12,
        help="Aantal pagina's recently played om bij opstarten meteen terug te vullen (50 items per pagina)",
    )

    p_both = subparsers.add_parser("import-and-run", help="Voer eerst import uit, start daarna loop")
    p_both.add_argument("--folder", required=True, help="Map met EndsSong_*.json bestanden")
    p_both.add_argument("--interval-minutes", type=int, default=20, help="Sync-interval in minuten")
    p_both.add_argument(
        "--startup-backfill-pages",
        type=int,
        default=12,
        help="Aantal pagina's recently played om bij opstarten meteen terug te vullen (50 items per pagina)",
    )

    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    load_environment(args.env_file)

    conn = get_db_connection(args.db_path)

    try:
        if args.command == "import-json":
            import_endsong_folder(conn, args.folder, min_ms_played=args.min_ms_played)
        elif args.command == "run":
            run_always_on_loop(
                conn,
                interval_minutes=args.interval_minutes,
                min_ms_played=args.min_ms_played,
                startup_backfill_pages=args.startup_backfill_pages,
            )
        elif args.command == "import-and-run":
            import_endsong_folder(conn, args.folder, min_ms_played=args.min_ms_played)
            run_always_on_loop(
                conn,
                interval_minutes=args.interval_minutes,
                min_ms_played=args.min_ms_played,
                startup_backfill_pages=args.startup_backfill_pages,
            )
        else:
            raise ValueError(f"Onbekend command: {args.command}")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Gestopt door gebruiker.")
        sys.exit(0)
