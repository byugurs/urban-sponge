#!/usr/bin/env python3
"""
UrbanSponge - Gercek NASA Uydu Verisi Isleme Betigi
===================================================
Landsat 8/9 (OLI) Band 4 (Red) ve Band 5 (NIR) verisinden NDVI hesaplayip,
NDVI < 0.2 olan pikselleri "Betonlasmis / Gecirimsiz Alan" olarak maskeleyerek
GeoJSON poligonlarina donusturur.

    NDVI = (NIR - Red) / (NIR + Red)

Iki calisma modu
----------------
1) --local (varsayilan)
   Indirilmis GeoTIFF dosyalarindan okur.
   Ornek:
     python process_real_nasa.py --local --red B4.TIF --nir B5.TIF
     python process_real_nasa.py --local --input-dir ./data/landsat

2) --stac
   Microsoft Planetary Computer STAC API uzerinden gercek Landsat-8/9
   sahnelerini bulur, yalnizca Batman pilot alanini (bbox) pencereli olarak
   indirir ve ayni NDVI hattini calistirir.
   Gerekli paketler: pip install pystac-client planetary-computer
   Ornek:
     python process_real_nasa.py --stac --bbox 41.095,37.855,41.175,37.920

Cikti: GeoJSON FeatureCollection (varsayilan: backend/data/impervious_areas.geojson)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD = 0.2
IMPERVIOUS_CLASS = "Betonlaşmış / Geçirimsiz Alan"
# Batman merkez pilot alani (minx, miny, maxx, maxy) - WGS84
DEFAULT_BBOX = (41.095, 37.855, 41.175, 37.920)
DEFAULT_OUT = Path(__file__).resolve().parent / "data" / "impervious_areas.geojson"
DEFAULT_INPUT_DIR = Path(__file__).resolve().parent / "data" / "landsat"

# Landsat-8/9 dosya adlarinda gecen bant kodlari
RED_TOKENS = ("_B4", "B4.", "_SR_B4", "band4")
NIR_TOKENS = ("_B5", "B5.", "_SR_B5", "band5")


# ---------------------------------------------------------------------------
# Bant okuma
# ---------------------------------------------------------------------------
def _apply_scale(array, src):
    """Radyometrik olcekleme/offset ve nodata maskeleme uygular."""
    import numpy as np

    raw = array.astype("float32")
    if src.nodata is not None:
        raw[raw == src.nodata] = np.nan

    scale = src.scales[0] if src.scales else 1.0
    offset = src.offsets[0] if src.offsets else 0.0
    return raw * scale + offset


def load_pair_local(red_path, nir_path):
    """Yerel B4 (Red) ve B5 (NIR) GeoTIFF'lerini okur ve ayni gridde hizalar."""
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    if not Path(red_path).exists():
        raise FileNotFoundError(f"Red (B4) dosyasi bulunamadi: {red_path}")
    if not Path(nir_path).exists():
        raise FileNotFoundError(f"NIR (B5) dosyasi bulunamadi: {nir_path}")

    with rasterio.open(red_path) as r:
        red = _apply_scale(r.read(1), r)
        meta = {
            "transform": r.transform,
            "crs": r.crs,
            "bounds": r.bounds,
            "width": r.width,
            "height": r.height,
        }
        red_crs = r.crs

    with rasterio.open(nir_path) as n:
        nir = _apply_scale(n.read(1), n)
        same_grid = (
            n.width == meta["width"]
            and n.height == meta["height"]
            and n.crs == red_crs
            and n.transform == meta["transform"]
        )
        if not same_grid:
            dst = np.full((meta["height"], meta["width"]), np.nan, dtype="float32")
            reproject(
                source=nir,
                destination=dst,
                src_transform=n.transform,
                src_crs=n.crs,
                dst_transform=meta["transform"],
                dst_crs=red_crs,
                resampling=Resampling.bilinear,
                dst_nodata=np.nan,
            )
            nir = dst

    return red, nir, meta


def _detect_band_files(input_dir):
    """Bir dizinde B4 (Red) ve B5 (NIR) dosyalarini otomatik bulur."""
    directory = Path(input_dir)
    tifs = [
        p for p in directory.iterdir()
        if p.suffix.lower() in (".tif", ".tiff")
    ]
    red = next((p for p in tifs if any(t in p.name.upper() for t in RED_TOKENS)), None)
    nir = next((p for p in tifs if any(t in p.name.upper() for t in NIR_TOKENS)), None)
    return red, nir


def load_pair_stac(bbox, start, end, max_cloud, max_items):
    """Planetary Computer STAC API'den Landsat-8/9 B4+B5 penceresini indirir."""
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject, transform_bounds

    try:
        import planetary_computer
        import pystac_client
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "STAC modu icin gerekli paketler eksik. Kurmak icin:\n"
            "  pip install pystac-client planetary-computer"
        ) from exc

    print("[STAC] Planetary Computer katalogu sorgulaniyor...")
    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )
    search = catalog.search(
        collections=["landsat-c2-l2"],
        bbox=list(bbox),
        datetime=f"{start}/{end}",
        query={
            "eo:cloud_cover": {"lt": max_cloud},
            "platform": {"in": ["landsat-8", "landsat-9"]},
        },
        max_items=max_items,
    )
    items = list(search.items())
    if not items:
        raise SystemExit(
            "Belirtilen alan/tarih araliginda bulutsuz Landsat sahnesi bulunamadi."
        )

    item = sorted(items, key=lambda i: i.properties.get("eo:cloud_cover", 100.0))[0]
    print(
        f"[STAC] Sahne secildi: {item.id} "
        f"(bulut: %{item.properties.get('eo:cloud_cover')}, "
        f"tarih: {item.properties.get('datetime')})"
    )

    def read_window(href):
        with rasterio.open(href) as src:
            win_bounds = transform_bounds("EPSG:4326", src.crs, *bbox)
            window = rasterio.windows.from_bounds(*win_bounds, transform=src.transform)
            data = src.read(1, window=window, boundless=True).astype("float32")
            data = _apply_scale(data, src)
            return data, {
                "transform": src.window_transform(window),
                "crs": src.crs,
                "bounds": win_bounds,
                "width": data.shape[1],
                "height": data.shape[0],
            }

    red, meta = read_window(item.assets["red"].href)
    nir, nir_meta = read_window(item.assets["nir"].href)

    if (nir.shape != red.shape) or (nir_meta["crs"] != meta["crs"]):
        dst = np.full(red.shape, np.nan, dtype="float32")
        reproject(
            source=nir,
            destination=dst,
            src_transform=nir_meta["transform"],
            src_crs=nir_meta["crs"],
            dst_transform=meta["transform"],
            dst_crs=meta["crs"],
            resampling=Resampling.bilinear,
            dst_nodata=np.nan,
        )
        nir = dst

    meta["scene_id"] = item.id
    meta["scene_date"] = str(item.properties.get("datetime"))
    meta["cloud_cover"] = item.properties.get("eo:cloud_cover")
    return red, nir, meta


# ---------------------------------------------------------------------------
# NDVI ve maskeleme
# ---------------------------------------------------------------------------
def compute_ndvi(red, nir):
    """NDVI = (NIR - Red) / (NIR + Red). Gecersiz pikseller NaN olur."""
    import numpy as np

    with np.errstate(invalid="ignore", divide="ignore"):
        denom = nir + red
        ndvi = np.where(denom == 0, np.nan, (nir - red) / denom)
    # Fiziksel araligin disindaki degerleri kirp
    return np.clip(ndvi, -1.0, 1.0)


def mask_impervious(ndvi, threshold=DEFAULT_THRESHOLD):
    """NDVI < threshold olan gecerli pikselleri maskeleyen boolean dizi."""
    import numpy as np

    return np.isfinite(ndvi) & (ndvi < threshold)


# ---------------------------------------------------------------------------
# Raster -> GeoJSON
# ---------------------------------------------------------------------------
def _polygon_area_m2(poly):
    """Kucuk poligonlar icin WGS84 derece^2 -> m^2 yaklasik donusumu."""
    import math

    lat = poly.centroid.y
    return abs(poly.area) * (111_320.0 ** 2) * math.cos(math.radians(lat))


def mask_to_geojson(
    mask,
    meta,
    threshold=DEFAULT_THRESHOLD,
    min_area_m2=900.0,
    simplify_tol=0.0001,
):
    """Gecirimsiz maske rasterini WGS84 GeoJSON FeatureCollection'a cevirir."""
    import numpy as np
    import rasterio
    from rasterio.features import shapes
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    from shapely.geometry import mapping, shape

    src_crs = meta["crs"]
    dst_crs = "EPSG:4326"

    dst_transform, dst_width, dst_height = calculate_default_transform(
        src_crs, dst_crs, meta["width"], meta["height"], *meta["bounds"]
    )
    dst = np.zeros((dst_height, dst_width), dtype="uint8")
    reproject(
        source=mask.astype("uint8"),
        destination=dst,
        src_transform=meta["transform"],
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=Resampling.nearest,
    )

    features = []
    for geom, value in shapes(dst, mask=(dst == 1), transform=dst_transform):
        if int(value) != 1:
            continue
        poly = shape(geom)
        if simplify_tol:
            poly = poly.simplify(simplify_tol, preserve_topology=True)
        if poly.is_empty:
            continue
        area = _polygon_area_m2(poly)
        if area < min_area_m2:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "class": IMPERVIOUS_CLASS,
                    "class_id": 1,
                    "ndvi_threshold": threshold,
                    "area_m2": round(area, 1),
                },
                "geometry": mapping(poly),
            }
        )

    # Butunsel istatistikler build_feature_collection icinde hesaplanir
    return features


def build_feature_collection(features, ndvi, mask, source_info, threshold):
    """GeoJSON FeatureCollection + bilimsel ozet meta verisi uretir."""
    import numpy as np

    valid = np.isfinite(ndvi)
    total_valid = int(valid.sum())
    impervious_px = int(mask.sum())
    ratio = (impervious_px / total_valid * 100.0) if total_valid else 0.0

    return {
        "type": "FeatureCollection",
        "source": source_info,
        "method": {
            "index": "NDVI = (NIR - Red) / (NIR + Red)",
            "impervious_rule": f"NDVI < {threshold}",
            "class": IMPERVIOUS_CLASS,
            "bands": "Landsat 8/9 OLI B4 (Red) & B5 (NIR), 30 m",
        },
        "statistics": {
            "valid_pixels": total_valid,
            "impervious_pixels": impervious_px,
            "impervious_ratio_pct": round(ratio, 2),
            "ndvi_min": round(float(np.nanmin(ndvi)), 4) if total_valid else None,
            "ndvi_max": round(float(np.nanmax(ndvi)), 4) if total_valid else None,
            "ndvi_mean": round(float(np.nanmean(ndvi)), 4) if total_valid else None,
            "polygon_count": len(features),
        },
        "features": features,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_bbox(text):
    parts = [float(v) for v in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox 'minx,miny,maxx,maxy' olmalidir.")
    minx, miny, maxx, maxy = parts
    if minx >= maxx or miny >= maxy:
        raise argparse.ArgumentTypeError("bbox degerleri gecersiz (min < max olmali).")
    return tuple(parts)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Landsat B4/B5 -> NDVI -> Gecirimsiz alan GeoJSON (UrbanSponge)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--local", action="store_true", help="Yerel GeoTIFF dosyalarini kullan (varsayilan)")
    mode.add_argument("--stac", action="store_true", help="Planetary Computer STAC API'den indir")

    parser.add_argument("--red", help="B4 (Red) GeoTIFF yolu")
    parser.add_argument("--nir", help="B5 (NIR) GeoTIFF yolu")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR), help="B4/B5 dosyalarini otomatik bul")
    parser.add_argument("--bbox", type=_parse_bbox, default=DEFAULT_BBOX, help="minx,miny,maxx,maxy (WGS84)")
    parser.add_argument("--start", default=str(date.today() - timedelta(days=365)), help="STAC baslangic tarihi (YYYY-MM-DD)")
    parser.add_argument("--end", default=str(date.today()), help="STAC bitis tarihi (YYYY-MM-DD)")
    parser.add_argument("--max-cloud", type=float, default=20.0, help="STAC maksimum bulutluluk (%)")
    parser.add_argument("--max-items", type=int, default=50, help="STAC maksimum sahne sayisi")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="NDVI gecirimsizlik esigi")
    parser.add_argument("--min-area", type=float, default=900.0, help="Minimum poligon alani (m2)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Cikti GeoJSON yolu")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    source_info = {}
    if args.stac:
        red, nir, meta = load_pair_stac(
            args.bbox, args.start, args.end, args.max_cloud, args.max_items
        )
        source_info = {
            "provider": "Microsoft Planetary Computer STAC API",
            "collection": "landsat-c2-l2",
            "scene_id": meta.get("scene_id"),
            "scene_date": meta.get("scene_date"),
            "cloud_cover": meta.get("cloud_cover"),
            "bbox": list(args.bbox),
        }
    else:
        red_path, nir_path = args.red, args.nir
        if not (red_path and nir_path):
            auto_red, auto_nir = _detect_band_files(args.input_dir)
            red_path = red_path or auto_red
            nir_path = nir_path or auto_nir
            if not (red_path and nir_path):
                raise SystemExit(
                    "B4 (Red) ve B5 (NIR) dosyalari bulunamadi.\n"
                    f"  Aranan dizin: {args.input_dir}\n"
                    "  --red ve --nir ile dosya yollarini verin ya da "
                    "--stac ile Planetary Computer'dan indirin."
                )
        print(f"[LOCAL] Red (B4): {red_path}")
        print(f"[LOCAL] NIR (B5): {nir_path}")
        red, nir, meta = load_pair_local(red_path, nir_path)
        source_info = {
            "provider": "Yerel GeoTIFF (Landsat 8/9 OLI)",
            "red_band": str(red_path),
            "nir_band": str(nir_path),
            "crs": str(meta["crs"]),
        }

    print("[NDVI] NDVI hesaplaniyor...")
    ndvi = compute_ndvi(red, nir)
    mask = mask_impervious(ndvi, args.threshold)

    print(f"[MASK] NDVI < {args.threshold} -> '{IMPERVIOUS_CLASS}' maskeleniyor...")
    features = mask_to_geojson(
        mask, meta, threshold=args.threshold, min_area_m2=args.min_area
    )

    collection = build_feature_collection(features, ndvi, mask, source_info, args.threshold)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(collection, fh, ensure_ascii=False, indent=2)

    stats = collection["statistics"]
    print("-" * 58)
    print(f"Gecerli piksel      : {stats['valid_pixels']:,}")
    print(f"Gecirimsiz piksel   : {stats['impervious_pixels']:,} "
          f"(%{stats['impervious_ratio_pct']})")
    print(f"NDVI (min/ort/max)  : {stats['ndvi_min']} / {stats['ndvi_mean']} / {stats['ndvi_max']}")
    print(f"Uretilen poligon    : {stats['polygon_count']}")
    print(f"Cikti               : {out_path}")
    return collection


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
