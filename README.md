# UrbanSponge — NASA Space Apps Challenge

Urban flood and sponge-area simulation for Batman, Türkiye. UrbanSponge combines
NASA satellite data with the SCS Curve Number hydrological model to visualize
flood risk and to measure the impact of "sponge city" interventions (rain gardens,
permeable asphalt, green roofs) drawn on an interactive map.

## Tech stack

- **Backend:** Python, FastAPI, NumPy, Shapely (Rasterio for optional GeoTIFF / NDVI)
- **Frontend:** Vanilla HTML/CSS/JS, Leaflet, Leaflet.draw, Turf.js

## Data sources

- **NASA NASADEM** — elevation and slope
- **Landsat 8/9 OLI** — NDVI / impervious surface detection
- **GPM IMERG** — rainfall intensity

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
cd backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000

## Deploy

Deployed as a single Vercel Function. `app.py` is the entrypoint and the root
`requirements.txt` holds the deployment dependencies. Rasterio is omitted because
it requires GDAL and cannot build on Vercel; the app imports it lazily and falls
back to calibrated local risk data.
