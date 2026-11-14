"""
UrbanSponge - Hidrolojik Modelleme Modulu
=========================================
SCS Curve Number (Egri Numarasi) yontemi ile yuzeyel akis (runoff) hesabi.

Temel formuller (P yagis derinligi, mm):
    S  = 25400 / CN - 254                (maksimum potansiyel tutma, mm)
    Ia = 0.2 * S                         (baslangic kaybi, mm)
    Q  = (P - 0.2S)^2 / (P + 0.8S)       (yuzey akis derinligi, mm ; P > 0.2S)
    Q  = 0                               (P <= 0.2S)

Yuzey akis hacmi (m3) = (Q_mm / 1000) * Alan_m2
"""

from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Arazi ortusu / yuzey tipi -> Standart CN (Curve Number) degerleri
# ---------------------------------------------------------------------------
LAND_COVER: Dict[str, Dict[str, Any]] = {
    "beton_asfalt": {"label": "Concrete / Asphalt", "cn": 98.0},
    "yogun_yapilasma": {"label": "Dense Urbanization", "cn": 92.0},
    "park_cim": {"label": "Standard Park / Lawn", "cn": 74.0},
    "gecirgen_asfalt": {"label": "Permeable Asphalt", "cn": 65.0},
    "kent_ormani": {"label": "Urban Forest", "cn": 55.0},
    "yagmur_bahcesi": {"label": "Rain Garden (Bioretention)", "cn": 45.0},
}

# ---------------------------------------------------------------------------
# Kullanicinin secebilecegi "sunger" mudahale tipleri
#   cn          : mudahale sonrasi Egri Numarasi
#   cost_per_m2 : tahmini uygulama birim maliyeti (TL / m2)
# ---------------------------------------------------------------------------
INTERVENTIONS: Dict[str, Dict[str, Any]] = {
    "yagmur_bahcesi": {
        "label": "Rain Garden",
        "cn": 45.0,
        "cost_per_m2": 1250.0,
        "description": "Bioretention pit; retains and filters rainwater on site.",
    },
    "gecirgen_asfalt": {
        "label": "Permeable Asphalt",
        "cn": 65.0,
        "cost_per_m2": 950.0,
        "description": "Permeable paving; reduces surface runoff and recharges groundwater.",
    },
    "kent_ormani": {
        "label": "Green Roof / Urban Forest",
        "cn": 55.0,
        "cost_per_m2": 400.0,
        "description": "Reduces soil compaction and increases infiltration through afforestation / green roofs.",
    },
}

# Mevcut durum varsayilani: beton / asfalt zemin
DEFAULT_EXISTING_CN: float = 98.0

# 100 m3 tutulan su ~ 1 zemin kat hane korumasi
M3_PER_HOUSEHOLD: float = 100.0


def calculate_potential_retention(cn: float) -> float:
    """Maksimum potansiyel tutma (S) degerini mm cinsinden hesaplar."""
    if cn <= 0:
        raise ValueError("CN (Egri Numarasi) degeri 0'dan buyuk olmalidir.")
    return (25400.0 / cn) - 254.0


def calculate_runoff_depth(rainfall_mm: float, cn: float) -> float:
    """SCS CN yontemi ile yuzey akis derinligini (Q, mm) hesaplar."""
    if rainfall_mm <= 0:
        return 0.0
    s = calculate_potential_retention(cn)
    initial_abstraction = 0.2 * s
    if rainfall_mm <= initial_abstraction:
        return 0.0
    return ((rainfall_mm - initial_abstraction) ** 2) / (rainfall_mm + 0.8 * s)


def runoff_volume_m3(runoff_depth_mm: float, area_m2: float) -> float:
    """Akis derinligini (mm) ve alani (m2) kullanarak akis hacmini (m3) hesaplar."""
    return (runoff_depth_mm / 1000.0) * area_m2


def _label_for_cn(cn: float) -> str:
    """Verilen CN degerine karsilik gelen arazi ortusu etiketini dondurur."""
    for meta in LAND_COVER.values():
        if abs(meta["cn"] - cn) < 0.5:
            return meta["label"]
    return f"Custom Surface (CN={cn:g})"


def simulate_sponge(
    area_m2: float,
    rainfall_mm: float,
    sponge_type: str,
    existing_cn: Optional[float] = None,
) -> Dict[str, Any]:
    """Belirtilen poligon icin mudahale oncesi/sonrasi tasgin simulasyonunu yapar.

    Parametreler
    ------------
    area_m2      : poligon alani (m2) - frontend'de Turf.js ile hesaplanir
    rainfall_mm  : yagis derinligi (mm)
    sponge_type  : INTERVENTIONS anahtarlarindan biri
    existing_cn  : mevcut zemin CN degeri (verilmezse beton/asfalt = 98)

    Donen deger
    -----------
    Oncesi/sonrasi akis hacimleri, tutulan su, topraga emilen su,
    taskin azalma yuzdesi, korunan hane sayisi ve tahmini maliyet.
    """
    if area_m2 <= 0:
        raise ValueError("Poligon alani (m2) 0'dan buyuk olmalidir.")
    if rainfall_mm < 0:
        raise ValueError("Yagis miktari (mm) negatif olamaz.")
    if sponge_type not in INTERVENTIONS:
        raise ValueError(
            f"Gecersiz sunger tipi: '{sponge_type}'. "
            f"Gecerli tipler: {', '.join(INTERVENTIONS)}"
        )

    existing_cn = float(existing_cn) if existing_cn else DEFAULT_EXISTING_CN
    intervention = INTERVENTIONS[sponge_type]
    intervention_cn = float(intervention["cn"])

    # --- Mudahale oncesi (mevcut zemin) ---
    s_before = calculate_potential_retention(existing_cn)
    q_before_mm = calculate_runoff_depth(rainfall_mm, existing_cn)
    v_before = runoff_volume_m3(q_before_mm, area_m2)

    # --- Mudahale sonrasi (sunger tipi) ---
    s_after = calculate_potential_retention(intervention_cn)
    q_after_mm = calculate_runoff_depth(rainfall_mm, intervention_cn)
    v_after = runoff_volume_m3(q_after_mm, area_m2)

    # --- Toplam yagis hacmi ve turetilmis metrikler ---
    total_rainfall_volume = (rainfall_mm / 1000.0) * area_m2
    retained_water = max(v_before - v_after, 0.0)          # taskindan kurtarilan su
    infiltrated_water = max(total_rainfall_volume - v_after, 0.0)  # topraga emilen su

    flood_reduction_pct = (retained_water / v_before * 100.0) if v_before > 0 else 0.0
    households_protected = retained_water / M3_PER_HOUSEHOLD
    estimated_cost_tl = intervention["cost_per_m2"] * area_m2

    return {
        "sponge_type": sponge_type,
        "sponge_label": intervention["label"],
        "sponge_description": intervention["description"],
        "area_m2": round(area_m2, 2),
        "rainfall_mm": round(rainfall_mm, 1),
        "existing": {
            "cn": existing_cn,
            "label": _label_for_cn(existing_cn),
            "potential_retention_mm": round(s_before, 2),
            "runoff_depth_mm": round(q_before_mm, 3),
            "runoff_volume_m3": round(v_before, 2),
        },
        "intervention": {
            "cn": intervention_cn,
            "label": intervention["label"],
            "potential_retention_mm": round(s_after, 2),
            "runoff_depth_mm": round(q_after_mm, 3),
            "runoff_volume_m3": round(v_after, 2),
        },
        "total_rainfall_volume_m3": round(total_rainfall_volume, 2),
        "retained_water_m3": round(retained_water, 2),
        "infiltrated_water_m3": round(infiltrated_water, 2),
        "flood_reduction_pct": round(flood_reduction_pct, 1),
        "households_protected": int(round(households_protected)),
        "estimated_cost_tl": round(estimated_cost_tl, 0),
    }
