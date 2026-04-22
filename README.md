# Spotify Long-term Tracker

Een Python-gebaseerd systeem om je Spotify-luistergeschiedenis vast te leggen, op te slaan in SQLite en te visualiseren via een interactief Streamlit dashboard.

## 🎯 Functies

- **Always-on Tracker**: Verzamelt automatisch je luistergeschiedenis via Spotify API elke 20 minuten.
- **SQLite Database**: Lokale opslag van alle luisterde nummers met deduplicatie.
- **JSON Import**: Eenmalige ingest van Spotify Extended Streaming History exports.
- **30-seconden Filter**: Sla alleen nummers op waarbij je langer dan 30 seconden hebt geluisterd.
- **Interactief Dashboard**: Streamlit-webinterface met:
   - Verplichte Spotify-login voordat je dashboard zichtbaar is
   - Optionele auto-sync na login (recent afgespeelde tracks direct opslaan)
   - JSON upload in dashboard (EndsSong_*.json direct importeren)
  - Totaaloverzicht (minuten + artiesten)
  - Top 10 artiesten per luistertijd
  - Zoekfunctie op tracknaam met speelcount
  - Tijdlijnen per dag/maand met trends
  - Live "Nu aan het luisteren" sectie (optioneel)

## 📋 Systeemvereisten

- **Python 3.10+** (aanbevolen 3.12)
- **Windows/Mac/Linux** (getest op Windows 10+)
- **Spotify Account** + Developer App (gratis)

## 🔧 Installatie

### 1. Kloon/Download het Project


### 2. Installeer Dependencies

```bash
pip install -r requirements.txt
```

Dit installeert:
- `spotipy` - Spotify API client
- `python-dotenv` - Environment variabelen (.env files)
- `streamlit` - Interactive dashboard
- `pandas` - Data manipulation
- `plotly` - Interactieve grafieken
- `sqlite3` - Komt standaard met Python

### 3. Vul .env in

Kopieer `.env.example` naar `.env`:

```bash
cp .env.example .env
```

Vul je Spotify credentials in:

```env
SPOTIPY_CLIENT_ID=jouw_client_id_hier
SPOTIPY_CLIENT_SECRET=jouw_client_secret_hier
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
SPOTIPY_DASHBOARD_REDIRECT_URI=http://localhost:8501
```

## 🔐 Spotify Developer Setup

1. Ga naar [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Klik Create an App
3. Accepteer de voorwaarden en maak je app aan
4. In App Settings, kopieer:
   - Client ID
   - Client Secret (genereer indien nodig)
5. Voeg Redirect URI toe bij Edit Settings:
   ```
   http://127.0.0.1:8888/callback
   http://localhost:8501
   ```
6. Klik Save

Zet dezelfde waarden exact in je `.env`.

`SPOTIPY_REDIRECT_URI` wordt gebruikt door de tracker (terminal flow).
`SPOTIPY_DASHBOARD_REDIRECT_URI` wordt gebruikt door het Streamlit dashboard (in-app login flow).

> ⚠️ **Belangrijk**: Client Secret niet delen! Als je deze publiek maakt, genereer meteen een nieuwe.

## ▶️ Hoe Starten

### Terminal 1: Tracker Starten

```powershell
& "/spotify forever/.venv/Scripts/python.exe" "spotify_long_term_tracker.py" run --interval-minutes 20 --startup-backfill-pages 12
```

**Opties:**
- `--interval-minutes 20` - Sync elke 20 minuten (standaard)
- `--startup-backfill-pages 12` - Import 12 pagina's history bij start (standaard: 12)
- `--min-ms-played 30000` - Minimaal 30 seconden luistertijd (standaard)

**Eerste keer:** Je webbrowser opent. Log in op Spotify, klik Toestaan, en plak de redirect-URL in de terminal.

Daarna begint het vullen van je database. Je ziet logs als:
```
2026-04-15 19:51:47 | INFO | API sync klaar | toegevoegd=50 | korter_dan_30s=0 | duplicaten=0
2026-04-15 19:51:48 | INFO | Wachten 20 minuten tot volgende sync...
```

### Terminal 2: Dashboard Starten

Open een tweede terminal en voer uit:

```powershell
& "/spotify forever/.venv/Scripts/python.exe" -m streamlit run spotify_dashboard.py
```

Het dashboard opent automatisch op:
- **Lokaal**: http://localhost:8501
- **Netwerk**: http://<JOUW-LAPTOP-IP>:8501

Je krijgt eerst een Spotify login-knop te zien. Na succesvol inloggen wordt je dashboard geladen.

Vind je IP met: `ipconfig` → IPv4-adres opspoort.

## 📊 Dashboard Werken

### Overzichtpagina
- Totale minuten geluisterd
- Aantal unieke artiesten

### Top 10 Artiesten
- Interactief bar chart
- Sorteerbaar op luistertijd (uren)

### Zoek op Nummer
- Zoek op tracknaam
- Zie totaal minuten en aantal speelt
- Top match resultaat

### JSON Upload
- Upload 1 of meerdere `EndsSong_*.json` bestanden direct in het dashboard
- Stel minimale luistertijd in (standaard 30000 ms)
- Krijg direct aantallen: toegevoegd, duplicaten, korter dan minimum, ongeldig

### Tijdlijn
- Kies tussen Uren/Minuten
- Dagweergave met 7-daags gemiddelde
- Maandweergave met labels

### Nu aan het Luisteren (optioneel)
- Zet aan via sidebar checkbox
- Toont huidige playing track live via Spotify API

### Instellingen (Sidebar)
- **SQLite databasepad**: Waar je database opgeslagen is
- **Auto-refresh**: Hoe lang wachten voor vernieuwen (0 = uit)
- **Nu aan het luisteren ophalen**: Toggle voor live Spotify current track
- **Na login recent afgespeelde tracks opslaan**: Schrijft direct je recent played data naar SQLite

## 🗄️ Database Schema

De SQLite database `spotify_tracker.db` bevat tabel `plays`:

| Kolom | Type | Beschrijving |
|-------|------|---|
| id | INTEGER PK | Unieke ID |
| ts | TEXT NOT NULL | ISO 8601 timestamp |
| source | TEXT | 'spotify_export' of 'spotify_api_recently_played' |
| track_name | TEXT NOT NULL | Tracknaam |
| artist_name | TEXT | Artiestnaam |
| album_name | TEXT | Albumnaam |
| track_uri | TEXT | Spotify URI |
| ms_played | INTEGER | Milliseconden geluisterd |
| reason_end | TEXT | Reden stop (JSON export) |
| skipped | INTEGER | Of skip (JSON export) |
| raw_json | TEXT | Volledige JSON opgeslagen |
| inserted_at | TEXT | Moment van insert |

**Unieke constraint**: (ts, track_name) - voorkomt exacte duplicaten

## 📥 JSON Import (Spotify Export)

Zodra je [Spotify Extended Streaming History export](https://www.spotify.com/account/privacy) krijgt:

```powershell
& "C:/Users/anous/Documents/spotify forever/.venv/Scripts/python.exe" "spotify_long_term_tracker.py" import-json --folder "C:/pad/naar/je/exports"
```

Dit importeert alle `EndsSong_*.json` bestanden en voegt ze toe aan dezelfde database (dubbelen worden overgeslagen).

## 🔍 Troubleshooting

### "redirect_uri: Not matching configuration"

**Oorzaak:** Jouw Redirect URI in `.env` klopt niet met wat in Spotify Developer Dashboard staat.

**Oplossing:**
1. Open Spotify Developer Dashboard → App Settings
2. Zet exact dezelfde Redirect URI bij Redirect URIs:
   ```
   http://127.0.0.1:8888/callback
   http://localhost:8501
   ```
3. Klik Save
4. Verwijder cache en probeer opnieuw:
   ```powershell
   Remove-Item -Force .cache-spotify-tracker -ErrorAction SilentlyContinue
   Remove-Item -Force .cache-spotify-dashboard -ErrorAction SilentlyContinue
   ```

### "Database niet gevonden: spotify_tracker.db"

**Oorzaak:** Database bestaat nog niet.

**Oplossing:**
1. Start tracker voor het eerst:
   ```powershell
   & "C:/Users/anous/Documents/spotify forever/.venv/Scripts/python.exe" "spotify_long_term_tracker.py" run
   ```
2. Database wordt automatisch aangemaakt bij start.

### "Ontbrekende omgevingsvariabelen (Spotify)"

**Oorzaak:** .env ingevuld maar niet geladen.

**Oplossing:**
1. Controleer of `.env` in correct pad staat (projectmap)
2. Controleer dat minimaal deze keys aanwezig zijn:
   ```env
   SPOTIPY_CLIENT_ID=...
   SPOTIPY_CLIENT_SECRET=...
   SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
   SPOTIPY_DASHBOARD_REDIRECT_URI=http://localhost:8501
   ```
3. Zeg het script expliciet:
   ```powershell
   & "C:/Users/anous/Documents/spotify forever/.venv/Scripts/python.exe" "spotify_long_term_tracker.py" run --env-file ".env"
   ```

### "Geen tracker/dashboard processen"

**Oorzaak:** Script draait niet meer of is vastgelopen.

**Oplossing:**
1. Forceer stop:
   ```powershell
   Get-CimInstance Win32_Process |
   Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'spotify_long_term_tracker.py|spotify_dashboard.py|streamlit' } |
   ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
   ```
2. Start opnieuw.

### "Spotify server_error"

**Oorzaak:** OAuth-token invalide of verlopen.

**Oplossing:**
```powershell
Remove-Item -Force .cache-spotify-tracker -ErrorAction SilentlyContinue
```
Start tracker opnieuw en log opnieuw in via Spotify.

## 🚀 Advanced

### Aangepaste Sync-interval

Sneller verversen (bijv. elke 5 minuten):

```powershell
& "C:/Users/anous/Documents/spotify forever/.venv/Scripts/python.exe" "spotify_long_term_tracker.py" run --interval-minutes 5 --startup-backfill-pages 20
```

### Import + Run

Voer import én sync in één keer uit:

```powershell
& "C:/Users/anous/Documents/spotify forever/.venv/Scripts/python.exe" "spotify_long_term_tracker.py" import-and-run --folder "C:/path/to/exports" --interval-minutes 5
```

### Dashboard via Netwerk

Zorg dat Streamlit op alle interfaces luistert (al ingesteld in `.streamlit/config.toml`):

```toml
[server]
address = "0.0.0.0"
port = 8501
```

Vanaf ander apparaat:
```
http://192.168.1.100:8501
```

## 📝 Notes

- **API Limiet**: Spotify recent played history beperkt tot ongeveer 50 tracks. Backfill haalt maximaal wat beschikbaar is.
- **Echte ms_played**: API geeft geen echte luistertijd. Script schat dit via tijdsverschil tussen opeenvolgende tracks (begrensd op trackduur).
- **Volledige Historie**: Voor jaar(en) geschiedenis: vraag Spotify Extended Streaming History export aan (duurt weken tot maanden).
- **Autostart**: Voor persistent tracking, zet Tracker in Windows Task Scheduler of cron job.

## 📄 Licentie

Dit project is gratis en open source.

## 🤝 Support

Controleer eerst troubleshooting. Voor vragen: zet `.venv` aan en herstart met duidelijke logging.

---

**Happy listening!** 🎵
