"""
UrbanSponge - FastAPI Backend
=============================
Kentsel sel ve sunger alan simulasyon platformu.

Uc noktalar:
    GET  /api/risk-zones        -> Pilot bolgedeki yuksek sel riskli alanlar (GeoJSON)
    POST /api/simulate-sponge   -> Cizilen poligon icin SCS CN simulasyonu (JSON)
    GET  /api/simulate-sponge/config -> Mevcut CN ve mudahale tipleri (yardimci)

Ayrica frontend statik dosyalari (/ adresinde frontend/index.html) servis edilir.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Modul arama yoluna backend dizinini ekle (farkli calistirma sekilleri icin)
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from hydrology import (  # noqa: E402
    DEFAULT_EXISTING_CN,
    INTERVENTIONS,
    LAND_COVER,
    simulate_sponge,
)
from nasa_fetcher import PILOT_CENTER, get_risk_zones  # noqa: E402

FRONTEND_DIR = _BACKEND_DIR.parent / "frontend"

app = FastAPI(
    title="UrbanSponge API",
    description=(
        "NASA Space Apps Challenge - Kentsel sel ve sunger alan simulasyonu. "
        "SCS Curve Number yontemi ile yuzey akisi ve taskin azaltma hesabi."
    ),
    version="1.0.0",
)

# CORS - tum originlere acik (frontend farkli port/tarayicidan erisebilir)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic modelleri
# ---------------------------------------------------------------------------
class SimulationRequest(BaseModel):
    area_m2: float = Field(..., gt=0, description="Poligon alani (m2) - Turf.js ile hesaplanir")
    rainfall_mm: float = Field(..., ge=0, le=1000, description="Yagis miktari (mm)")
    sponge_type: str = Field(..., description="Sunger mudahale tipi (INTERVENTIONS anahtari)")
    existing_cn: Optional[float] = Field(
        None, gt=0, description="Mevcut zemin CN degeri (verilmezse beton/asfalt = 98)"
    )
    polygon: Optional[List[List[float]]] = Field(
        None, description="Cizilen poligon koseleri [[lat, lng], ...] (opsiyonel, geri yansitilir)"
    )


# ---------------------------------------------------------------------------
# API uc noktalari
# ---------------------------------------------------------------------------
@app.get("/api/risk-zones", tags=["risk"])
def api_risk_zones() -> Dict[str, Any]:
    """Pilot bolgedeki yuksek sel riskli mahalle/kavsaklari GeoJSON olarak dondurur.

    Her bolge icin mevcut gecirimsizlik orani (%), DEM egimi, yukseklik ve NDVI
    degerleri properties alaninda yer alir.
    """
    return get_risk_zones()


@app.get("/api/config", tags=["config"])
def api_config() -> Dict[str, Any]:
    """Frontend icin gecerli CN degerleri ve mudahale tiplerini dondurur."""
    return {
        "pilot_center": PILOT_CENTER,
        "land_cover": LAND_COVER,
        "interventions": INTERVENTIONS,
        "default_existing_cn": DEFAULT_EXISTING_CN,
    }


@app.post("/api/simulate-sponge", tags=["simulation"])
def api_simulate_sponge(req: SimulationRequest) -> Dict[str, Any]:
    """Cizilen poligon icin mudahale oncesi/sonrasi taskin simulasyonunu yapar.

    Girdi: poligon koordinatlari, alan (m2), secilen sunger tipi ve yagis (mm).
    Cikti: yeni taskin hacmi, kurtarilan hane sayisi ve tahmini maliyet.
    """
    if req.sponge_type not in INTERVENTIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Gecersiz sunger tipi: '{req.sponge_type}'. "
                f"Gecerli tipler: {list(INTERVENTIONS.keys())}"
            ),
        )

    try:
        result = simulate_sponge(
            area_m2=req.area_m2,
            rainfall_mm=req.rainfall_mm,
            sponge_type=req.sponge_type,
            existing_cn=req.existing_cn,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result["polygon"] = req.polygon
    return result


# ---------------------------------------------------------------------------
# Frontend statik dosyalari
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def serve_index() -> FileResponse:
    """Ana sayfayi (frontend/index.html) dondurur."""
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="frontend/index.html bulunamadi.")
    return FileResponse(index_path)


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
