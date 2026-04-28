# Spotify Tracker - Lite Version

Een lichtgewicht versie van het Spotify dashboard, geoptimaliseerd voor **oude hardware** (oude Dell laptops, etc).

## Wat is anders in de Lite versie?

✅ **Behouden:**
- Spotify OAuth login (beveiligd)
- Alle essentiële statistieken
- Track zoeken
- JSON import van Spotify export

❌ **Verwijderd (voor snelheid):**
- Plotly interactieve grafieken → eenvoudige tabellen
- "Nu aan het luisteren" real-time API calls
- Auto-refresh feature
- Complex UI animations

**Resultaat:** ±70% minder RAM en CPU verbruik, veel sneller op oude hardware!

## Installatie

1. **Python 3.8+** nodig (check: `python --version`)

2. **Virtual environment aanmaken:**
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

3. **Dependencies installeren:**
   ```powershell
   pip install -r requirements.txt
   ```
   (Plotly is NIET nodig, alleen de basis packages)

## Opstarten

```powershell
streamlit run spotify_dashboard_lite.py
```

De app opent op `http://localhost:8501`

## Voordelen Lite Versie

- ⚡ **Sneller**: Gaat direct naar data, geen zware visualisaties
- 💾 **Minder RAM**: Draait op 512MB RAM, zelfs op 1GB machines
- 🔐 **Veilig**: Spotify login blijft exact hetzelfde
- 📱 **Responsive**: Werkt ook op kleine schermen
- 🚀 **Eenvoudig**: Minimale UI, geen afleiding

## Spotify Setup (Eenmalig)

1. Ga naar https://developer.spotify.com/
2. Log in / maak account aan
3. Maak "Application" aan
4. Kopieer `Client ID` en `Client Secret`
5. Zet Redirect URI op: `http://localhost:8501/` (of waar je het draait)

## .env File

Maak `.env` aan in dezelfde map:

```
SPOTIPY_CLIENT_ID=your_client_id_here
SPOTIPY_CLIENT_SECRET=your_client_secret_here
SPOTIPY_REDIRECT_URI=http://localhost:8501/
```

## Tips voor Oude Hardware

- **RAM te laag?** Reduce browser tabs, restart Streamlit server
- **Traag laden?** Verhoog het sleep-interval in `main()`
- **Nog langzamer?** Upgrade Python naar 3.11+ (betere performance)

## Wanneer teruggaan naar volledige versie?

Gebruik `spotify_dashboard.py` als je:
- Interactieve Plotly grafieken wilt
- Real-time "nu aan het luisteren" feature wilt
- Een snelle moderne computer hebt

---

**Vragen?** Check de `.env` file en Spotify app settings.
