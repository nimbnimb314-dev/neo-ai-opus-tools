from __future__ import annotations

import argparse
import bz2
import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import numpy as np
import requests
import xarray as xr
from ecmwf.opendata import Client as EcmwfClient
from scipy.interpolate import griddata
from scipy.ndimage import distance_transform_edt, gaussian_filter, label, maximum_filter, minimum_filter

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data"
STAGING_DIR = ROOT / "data-next"
CACHE_DIR = ROOT / "cache" / "gridded"
LAND_PATH = ROOT / "tools" / "ForecastMapGenerator" / "map-data" / "japan-region-land.geojson"
RUN_CACHE_PATH = CACHE_DIR / "latest-runs.json"

JST = timezone(timedelta(hours=9))
UTC = timezone.utc

REGION = {
    "lon_min": 114.0,
    "lon_max": 158.0,
    "lat_min": 19.0,
    "lat_max": 52.0,
}
# The visible map is a projected rectangle with extra padding, so the contour field
# must extend much farther west/east than the display region to avoid broken corners.
DATA_REGION = {
    "lon_min": 94.0,
    "lon_max": 178.0,
    "lat_min": 12.0,
    "lat_max": 60.0,
}
PROJECTION = {
    "lon_0": 135.0,
    "lat_0": 35.0,
    "lat_1": 30.0,
    "lat_2": 60.0,
}
REGION_CACHE_KEY = f"{int(REGION['lon_min'])}_{int(REGION['lon_max'])}_{int(REGION['lat_min'])}_{int(REGION['lat_max'])}"
DATA_REGION_CACHE_KEY = (
    f"{int(DATA_REGION['lon_min'])}_{int(DATA_REGION['lon_max'])}_"
    f"{int(DATA_REGION['lat_min'])}_{int(DATA_REGION['lat_max'])}"
)
TARGET_LONS = np.arange(DATA_REGION["lon_min"], DATA_REGION["lon_max"] + 0.001, 0.25)
TARGET_LATS = np.arange(DATA_REGION["lat_min"], DATA_REGION["lat_max"] + 0.001, 0.25)
TARGET_MESH_LON, TARGET_MESH_LAT = np.meshgrid(TARGET_LONS, TARGET_LATS)

PLOT_BOX = (96, 92, 1280 - 96 - 48, 960 - 92 - 76)

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "tenki-gridded-generator/1.0"
SESSION.trust_env = False

PREFERRED_COMMON_STEP = 144
FULL_DETAIL_HOURS = 72
EXTENDED_FORECAST_HOURS = 240
CONTOUR_SMOOTHING_SIGMA = 3.0
PLOT_PAD_X_RATIO = 0.035
PLOT_PAD_Y_RATIO = 0.045
PRESSURE_KIND_BIAS_WEIGHT = 0.35


@dataclass(frozen=True)
class Slot:
    slot_id: str
    label: str
    valid_jst: datetime


@dataclass(frozen=True)
class ModelRun:
    name: str
    key: str
    run_utc: datetime
    max_step: int


@dataclass(frozen=True)
class ResolvedModelRun:
    model: ModelRun
    source: str
    note: str | None = None


@dataclass(frozen=True)
class PressureCenter:
    kind: str
    lon: float
    lat: float
    value: float
    prominence: float


@dataclass(frozen=True)
class PersistentCenterCandidate:
    kind: str
    lon: float
    lat: float
    value: float
    prominence: float
    closed_levels: int
    max_area: int
    merged_with_stronger: bool


def main() -> None:
    args = parse_args()
    disable_proxy_env()
    started_at = datetime.now(JST)
    resolved_runs, warnings = resolve_runs(strict_models=args.strict_models)
    runs = {key: resolved.model for key, resolved in resolved_runs.items()}

    slots = build_slots(datetime.now(JST))
    if args.limit_slots is not None:
        slots = slots[: args.limit_slots]

    land_features = load_land_features()
    recreate_dir(STAGING_DIR)
    image_root = STAGING_DIR / "images"
    image_root.mkdir(parents=True, exist_ok=True)

    rendered_slot_counts = {key: 0 for key in runs}
    skipped_slot_counts = {key: 0 for key in runs}
    for resolved in resolved_runs.values():
        model = resolved.model
        print(
            f"Using {model.name} run {model.run_utc:%Y-%m-%d %H:%M} UTC "
            f"(max_step={model.max_step}, source={resolved.source})"
        )
        if resolved.note:
            print(f"Warning: {resolved.note}")

    manifest_slots = []
    for slot in slots:
        slot_models = []
        for resolved in resolved_runs.values():
            model = resolved.model
            if step_for(slot.valid_jst, model) is None:
                continue
            try:
                grid = load_grid(model, slot.valid_jst)
                model_dir = image_root / model.key
                model_dir.mkdir(parents=True, exist_ok=True)
                image_path = model_dir / f"{slot.slot_id}.jpg"
                render_map(image_path, model.name, slot.valid_jst, model.run_utc, grid, land_features)
            except Exception as exc:
                skipped_slot_counts[model.key] += 1
                message = f"{model.key} {slot.slot_id}: skipped render ({describe_exception(exc)})"
                warnings.append(message)
                print(f"Warning: {message}")
                if args.strict_models:
                    raise RuntimeError(message) from exc
                continue
            rendered_slot_counts[model.key] += 1
            slot_models.append(
                {
                    "key": model.key,
                    "name": model.name,
                    "imagePath": f"./data/images/{model.key}/{slot.slot_id}.jpg",
                    "forecastTime": slot.valid_jst.isoformat(),
                    "modelRunTime": model.run_utc.astimezone(JST).isoformat(),
                }
            )

        if not slot_models:
            continue
        print(f"Rendering {slot.slot_id} ({', '.join(model['key'] for model in slot_models)})")
        manifest_slots.append(
            {
                "id": slot.slot_id,
                "label": slot.label,
                "forecastTime": slot.valid_jst.isoformat(),
                "models": slot_models,
            }
        )

    if not manifest_slots:
        raise RuntimeError("No forecast slots available from any configured model.")

    generated_at = datetime.now(JST)
    manifest = {
        "title": "Japan Surrounding Forecast Viewer",
        "generatedAt": generated_at.isoformat(),
        "timezone": "Asia/Tokyo",
        "dataSource": "ECMWF Open Data / NOAA NOMADS GFS / DWD ICON Open Data",
        "note": "Rendered from cached gridded pressure data. Map boundary data: Natural Earth.",
        "slots": manifest_slots,
    }

    write_text(STAGING_DIR / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    write_text(STAGING_DIR / "manifest.js", "window.TENKI_MANIFEST = " + json.dumps(manifest, ensure_ascii=False, indent=2) + ";")
    summary = build_run_summary(
        started_at,
        generated_at,
        slots,
        manifest_slots,
        resolved_runs,
        rendered_slot_counts,
        skipped_slot_counts,
        warnings,
    )
    write_text(STAGING_DIR / "run-summary.json", json.dumps(summary, ensure_ascii=False, indent=2))

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    STAGING_DIR.rename(OUTPUT_DIR)
    model_summary = ", ".join(
        f"{resolved_runs[key].model.name}={rendered_slot_counts[key]}"
        for key in model_order(resolved_runs)
    )
    print(f"Generated {len(manifest_slots)} slots into {OUTPUT_DIR}")
    print(f"Rendered slot counts: {model_summary}")
    if warnings:
        print(f"Completed with {len(warnings)} warning(s). See {OUTPUT_DIR / 'run-summary.json'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate pressure maps from gridded forecast data.")
    parser.add_argument("--limit-slots", type=int, default=None, help="Generate only the first N forecast slots.")
    parser.add_argument(
        "--strict-models",
        action="store_true",
        help="Abort when any model run lookup or slot render fails instead of continuing with available models.",
    )
    return parser.parse_args()


def disable_proxy_env() -> None:
    for key in [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "GIT_HTTP_PROXY",
        "GIT_HTTPS_PROXY",
    ]:
        os.environ.pop(key, None)


def resolve_runs(strict_models: bool = False) -> tuple[dict[str, ResolvedModelRun], list[str]]:
    cached = load_cached_runs()
    resolvers = {
        "ecmwf": resolve_ecmwf_run,
        "gfs": resolve_gfs_run,
        "icon": resolve_icon_run,
    }

    resolved: dict[str, ResolvedModelRun] = {}
    warnings: list[str] = []
    for key, resolver in resolvers.items():
        try:
            model_run = resolver()
            resolved[key] = ResolvedModelRun(model_run, "live")
        except Exception as exc:
            cached_run = cached.get(key)
            detail = describe_exception(exc)
            if cached_run is not None:
                warnings.append(
                    f"{key}: live run lookup failed, using cached run "
                    f"{cached_run.run_utc.isoformat()} ({detail})"
                )
                resolved[key] = ResolvedModelRun(cached_run, "cache", warnings[-1])
                continue
            if strict_models:
                raise RuntimeError(f"{key}: run lookup failed and no cached run is available ({detail})") from exc
            warnings.append(f"{key}: skipped because run lookup failed and no cached run is available ({detail})")

    if not resolved:
        raise RuntimeError("No model runs could be resolved from live sources or cache.")

    save_cached_runs({key: outcome.model for key, outcome in resolved.items()})
    return resolved, warnings


def resolve_ecmwf_run() -> ModelRun:
    run_utc = preferred_ecmwf_run(latest_ecmwf_run())
    _, max_step = ecmwf_run_profile(run_utc)
    return ModelRun("ECMWF", "ecmwf", run_utc, max_step)


def resolve_gfs_run() -> ModelRun:
    run_utc, max_step = latest_gfs_run()
    return ModelRun("GFS", "gfs", run_utc, max_step)


def resolve_icon_run() -> ModelRun:
    run_utc, max_step = latest_icon_run()
    return ModelRun("ICON", "icon", run_utc, max_step)


def load_cached_runs() -> dict[str, ModelRun]:
    if not RUN_CACHE_PATH.exists():
        return {}
    payload = json.loads(RUN_CACHE_PATH.read_text(encoding="utf-8"))
    runs: dict[str, ModelRun] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            legacy_names = {"ecmwf": "ECMWF", "gfs": "GFS", "icon": "ICON"}
            legacy_max_steps = {"ecmwf": 144, "gfs": 109, "icon": 180}
            runs[key] = ModelRun(legacy_names[key], key, datetime.fromisoformat(value), legacy_max_steps[key])
        else:
            runs[key] = ModelRun(value["name"], key, datetime.fromisoformat(value["runUtc"]), int(value["maxStep"]))
    return runs


def save_cached_runs(runs: dict[str, ModelRun]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        key: {
            "name": model.name,
            "runUtc": model.run_utc.isoformat(),
            "maxStep": model.max_step,
        }
        for key, model in runs.items()
    }
    RUN_CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_slots(now_jst: datetime) -> list[Slot]:
    now_jst = round_up_three_hours(now_jst)
    result: list[Slot] = []
    for hours in range(0, EXTENDED_FORECAST_HOURS + 1, 3):
        valid = now_jst + timedelta(hours=hours)
        if hours >= FULL_DETAIL_HOURS and valid.hour not in {9, 21}:
            continue
        result.append(Slot(valid.strftime("%Y%m%dT%H%M"), valid.strftime("%m/%d %H:%M JST"), valid))
    seen = set()
    unique = []
    for slot in result:
        if slot.slot_id not in seen:
            seen.add(slot.slot_id)
            unique.append(slot)
    return unique


def round_up_hour(dt: datetime) -> datetime:
    if dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt
    return (dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)


def round_up_three_hours(dt: datetime) -> datetime:
    dt = round_up_hour(dt)
    offset = dt.hour % 3
    if offset == 0:
        return dt
    return dt + timedelta(hours=3 - offset)


def model_order(models: dict[str, object]) -> list[str]:
    preferred = ["ecmwf", "gfs", "icon"]
    return [key for key in preferred if key in models] + sorted(key for key in models if key not in preferred)


def describe_exception(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return f"{exc.__class__.__name__}: {message}"
    return exc.__class__.__name__


def build_run_summary(
    started_at: datetime,
    generated_at: datetime,
    requested_slots: list[Slot],
    manifest_slots: list[dict[str, object]],
    resolved_runs: dict[str, ResolvedModelRun],
    rendered_slot_counts: dict[str, int],
    skipped_slot_counts: dict[str, int],
    warnings: list[str],
) -> dict[str, object]:
    generated_slot_ids = [slot["id"] for slot in manifest_slots]
    models = []
    for key in model_order(resolved_runs):
        resolved = resolved_runs[key]
        models.append(
            {
                "key": key,
                "name": resolved.model.name,
                "runUtc": resolved.model.run_utc.isoformat(),
                "runJst": resolved.model.run_utc.astimezone(JST).isoformat(),
                "maxStep": resolved.model.max_step,
                "resolutionSource": resolved.source,
                "resolutionNote": resolved.note,
                "renderedSlotCount": rendered_slot_counts.get(key, 0),
                "skippedSlotCount": skipped_slot_counts.get(key, 0),
            }
        )
    return {
        "status": "success",
        "startedAt": started_at.isoformat(),
        "generatedAt": generated_at.isoformat(),
        "requestedSlotCount": len(requested_slots),
        "generatedSlotCount": len(manifest_slots),
        "emptySlotCount": len(requested_slots) - len(manifest_slots),
        "firstGeneratedSlotId": generated_slot_ids[0],
        "lastGeneratedSlotId": generated_slot_ids[-1],
        "models": models,
        "warningCount": len(warnings),
        "warnings": warnings,
    }


def step_for(valid_jst: datetime, model: ModelRun) -> int | None:
    hours = int((valid_jst.astimezone(UTC) - model.run_utc).total_seconds() // 3600)
    if hours < 0 or hours > model.max_step:
        return None
    if model.key == "ecmwf":
        return ecmwf_step_for(hours, model.run_utc)
    if hours % 3 != 0:
        return None
    return hours


def latest_ecmwf_run() -> datetime:
    return EcmwfClient().latest().replace(tzinfo=UTC)


def preferred_ecmwf_run(latest_run_utc: datetime) -> datetime:
    if latest_run_utc.hour in {0, 12}:
        return latest_run_utc
    return latest_run_utc - timedelta(hours=6)


def ecmwf_run_profile(run_utc: datetime) -> tuple[str, int]:
    if run_utc.hour in {0, 12}:
        return "oper", 240
    return "scda", 90


def ecmwf_step_for(hours: int, run_utc: datetime) -> int | None:
    _, max_step = ecmwf_run_profile(run_utc)
    if hours < 0 or hours > max_step:
        return None
    if run_utc.hour in {0, 12}:
        if hours <= 144 and hours % 3 == 0:
            return hours
        if 150 <= hours <= 240 and hours % 6 == 0:
            return hours
        return None
    if hours % 3 != 0:
        return None
    return hours


def latest_gfs_run() -> tuple[datetime, int]:
    html = SESSION.get("https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/", timeout=120).text
    dates = sorted(set(re.findall(r"gfs\.(\d{8})/", html)), reverse=True)
    if not dates:
        raise RuntimeError("No GFS run directories found.")
    fallback: tuple[datetime, int] | None = None
    for run_date in dates:
        day_html = SESSION.get(f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.{run_date}/", timeout=120).text
        hours = sorted(set(re.findall(r'href="(\d{2})/"', day_html)), reverse=True)
        for hour in hours:
            atmos_html = SESSION.get(
                f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.{run_date}/{hour}/atmos/",
                timeout=120,
            ).text
            steps = sorted({int(value) for value in re.findall(fr"gfs\.t{hour}z\.pgrb2\.0p25\.f(\d{{3}})", atmos_html)})
            if steps:
                candidate = (datetime.strptime(run_date + hour, "%Y%m%d%H").replace(tzinfo=UTC), steps[-1])
                if fallback is None:
                    fallback = candidate
                if steps[-1] >= PREFERRED_COMMON_STEP:
                    return candidate
    if fallback is not None:
        return fallback
    raise RuntimeError("No usable GFS runs found.")


def latest_icon_run() -> tuple[datetime, int]:
    hours_html = SESSION.get("https://opendata.dwd.de/weather/nwp/icon/grib/", timeout=120).text
    hours = sorted(set(re.findall(r'href="(\d{2})/"', hours_html)), reverse=True)
    runs: dict[datetime, set[int]] = {}
    for hour in hours:
        html = SESSION.get(f"https://opendata.dwd.de/weather/nwp/icon/grib/{hour}/pmsl/", timeout=120).text
        matches = re.findall(r"single-level_(\d{10})_(\d{3})_PMSL", html)
        for run_text, step_text in matches:
            run_utc = datetime.strptime(run_text, "%Y%m%d%H").replace(tzinfo=UTC)
            runs.setdefault(run_utc, set()).add(int(step_text))
    if not runs:
        raise RuntimeError("No ICON run files found.")
    candidates = sorted(((run_utc, max(steps)) for run_utc, steps in runs.items()), reverse=True)
    for run_utc, max_step in candidates:
        if max_step >= PREFERRED_COMMON_STEP:
            return run_utc, max_step
    return candidates[0]


def load_grid(model: ModelRun, valid_jst: datetime) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    step = step_for(valid_jst, model)
    if step is None:
        raise RuntimeError(f"{model.name} does not cover {valid_jst.isoformat()}")
    if model.key == "ecmwf":
        return load_ecmwf_grid(model.run_utc, step)
    if model.key == "gfs":
        return load_gfs_grid(model.run_utc, step)
    return load_icon_grid(model.run_utc, step)


def load_ecmwf_grid(run_utc: datetime, step: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cache_path = CACHE_DIR / "ecmwf" / run_utc.strftime("%Y%m%d%H") / f"f{step:03d}.grib2"
    if not cache_path.exists():
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        stream, _ = ecmwf_run_profile(run_utc)
        EcmwfClient().retrieve(
            date=int(run_utc.strftime("%Y%m%d")),
            time=run_utc.hour,
            step=step,
            stream=stream,
            type="fc",
            param="msl",
            target=str(cache_path),
        )
    return load_regular_grid_from_grib(cache_path)


def load_gfs_grid(run_utc: datetime, step: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cache_path = CACHE_DIR / "gfs" / run_utc.strftime("%Y%m%d%H") / DATA_REGION_CACHE_KEY / f"f{step:03d}.grib2"
    if not cache_path.exists():
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        url = (
            "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
            f"?dir=%2Fgfs.{run_utc:%Y%m%d}%2F{run_utc:%H}%2Fatmos"
            f"&file=gfs.t{run_utc:%H}z.pgrb2.0p25.f{step:03d}"
            "&var_PRMSL=on"
            f"&leftlon={DATA_REGION['lon_min']}&rightlon={DATA_REGION['lon_max']}"
            f"&toplat={DATA_REGION['lat_max']}&bottomlat={DATA_REGION['lat_min']}&subregion="
        )
        data = SESSION.get(url, timeout=180)
        data.raise_for_status()
        cache_path.write_bytes(data.content)
    return load_regular_grid_from_grib(cache_path)


def load_icon_grid(run_utc: datetime, step: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cache_dir = CACHE_DIR / "icon" / run_utc.strftime("%Y%m%d%H")
    interp_path = cache_dir / f"f{step:03d}_{DATA_REGION_CACHE_KEY}.npz"
    if interp_path.exists():
        data = np.load(interp_path)
        return data["lons"], data["lats"], data["values"]

    cache_dir.mkdir(parents=True, exist_ok=True)
    lon_points, lat_points = load_icon_coordinates(run_utc)
    field_path = cache_dir / f"pmsl_f{step:03d}.grib2"
    if not field_path.exists():
        url = (
            f"https://opendata.dwd.de/weather/nwp/icon/grib/{run_utc:%H}/pmsl/"
            f"icon_global_icosahedral_single-level_{run_utc:%Y%m%d%H}_{step:03d}_PMSL.grib2.bz2"
        )
        payload = SESSION.get(url, timeout=300)
        payload.raise_for_status()
        field_path.write_bytes(bz2.decompress(payload.content))

    values = read_first_data_var(field_path) / 100.0
    mask = (
        (lon_points >= DATA_REGION["lon_min"] - 4)
        & (lon_points <= DATA_REGION["lon_max"] + 4)
        & (lat_points >= DATA_REGION["lat_min"] - 4)
        & (lat_points <= DATA_REGION["lat_max"] + 4)
    )
    interp = griddata(
        np.column_stack((lon_points[mask], lat_points[mask])),
        values[mask],
        (TARGET_MESH_LON, TARGET_MESH_LAT),
        method="linear",
    )
    if np.isnan(interp).any():
        nearest = griddata(
            np.column_stack((lon_points[mask], lat_points[mask])),
            values[mask],
            (TARGET_MESH_LON, TARGET_MESH_LAT),
            method="nearest",
        )
        interp = np.where(np.isnan(interp), nearest, interp)
    np.savez_compressed(interp_path, lons=TARGET_LONS, lats=TARGET_LATS, values=interp)
    return TARGET_LONS, TARGET_LATS, interp


def load_icon_coordinates(run_utc: datetime) -> tuple[np.ndarray, np.ndarray]:
    cache_dir = CACHE_DIR / "icon" / run_utc.strftime("%Y%m%d%H")
    lon_path = cache_dir / "clon.npy"
    lat_path = cache_dir / "clat.npy"
    if lon_path.exists() and lat_path.exists():
        return np.load(lon_path), np.load(lat_path)

    cache_dir.mkdir(parents=True, exist_ok=True)
    for name, out_path in [("CLON", lon_path), ("CLAT", lat_path)]:
        grib_path = cache_dir / f"{name.lower()}.grib2"
        if not grib_path.exists():
            url = (
                f"https://opendata.dwd.de/weather/nwp/icon/grib/{run_utc:%H}/{name.lower()}/"
                f"icon_global_icosahedral_time-invariant_{run_utc:%Y%m%d%H}_{name}.grib2.bz2"
            )
            payload = SESSION.get(url, timeout=300)
            payload.raise_for_status()
            grib_path.write_bytes(bz2.decompress(payload.content))
        np.save(out_path, read_first_data_var(grib_path))

    return np.load(lon_path), np.load(lat_path)


def load_regular_grid_from_grib(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with xr.open_dataset(path, engine="cfgrib") as ds:
        data_var = next(iter(ds.data_vars.values()))
        lons = ds["longitude"].values
        lats = ds["latitude"].values
        values = data_var.values / 100.0
    return crop_regular_grid(lons, lats, values)


def read_first_data_var(path: Path) -> np.ndarray:
    with xr.open_dataset(path, engine="cfgrib") as ds:
        data_var = next(iter(ds.data_vars.values()))
        return np.asarray(data_var.values)


def crop_regular_grid(lons: np.ndarray, lats: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lons = np.asarray(lons).astype(float)
    lats = np.asarray(lats).astype(float)
    data = np.asarray(values).astype(float)

    if lons.ndim != 1 or lats.ndim != 1:
        raise RuntimeError("Expected 1D longitude/latitude arrays for regular grid.")

    lons = np.where(lons < 0, lons + 360.0, lons)
    lon_order = np.argsort(lons)
    lons = lons[lon_order]
    data = data[:, lon_order]

    if lats[0] > lats[-1]:
        lats = lats[::-1]
        data = data[::-1, :]

    lon_mask = (lons >= DATA_REGION["lon_min"]) & (lons <= DATA_REGION["lon_max"])
    lat_mask = (lats >= DATA_REGION["lat_min"]) & (lats <= DATA_REGION["lat_max"])
    return lons[lon_mask], lats[lat_mask], data[np.ix_(lat_mask, lon_mask)]


def is_within_plot_region(lon: float, lat: float, margin_degrees: float = 0.0) -> bool:
    return (
        REGION["lon_min"] - margin_degrees <= lon <= REGION["lon_max"] + margin_degrees
        and REGION["lat_min"] - margin_degrees <= lat <= REGION["lat_max"] + margin_degrees
    )


def load_land_features() -> list[list[list[tuple[float, float]]]]:
    data = json.loads(LAND_PATH.read_text(encoding="utf-8"))
    features = []
    for feature in data["features"]:
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        feature_polygons = []
        for polygon in polygons:
            rings = []
            for ring in polygon:
                rings.append([(float(lon), float(lat)) for lon, lat in ring])
            feature_polygons.append(rings)
        features.append(feature_polygons)
    return features


def detect_pressure_centers(lons: np.ndarray, lats: np.ndarray, values: np.ndarray) -> list[PressureCenter]:
    values = np.asarray(values, dtype=float)
    smoothed = gaussian_filter(values, sigma=1.1)
    field_reference = pressure_field_reference(values)
    candidates = find_persistent_centers(lons, lats, values, smoothed)
    candidates.sort(key=lambda item: candidate_rank_score(item, field_reference), reverse=True)
    result: list[PressureCenter] = []
    per_kind = {"high": 0, "low": 0}
    for candidate in candidates:
        center = PressureCenter(candidate.kind, candidate.lon, candidate.lat, candidate.value, candidate.prominence)
        if not should_keep_candidate(candidate, field_reference):
            continue
        if not is_within_plot_region(center.lon, center.lat, 0.5):
            continue
        if any(center.kind == other.kind and is_nearby_center(center, other) for other in result):
            continue
        if any(center.kind != other.kind and is_cross_kind_conflict(center, other, field_reference) for other in result):
            continue
        if per_kind[center.kind] >= 4:
            continue
        result.append(center)
        per_kind[center.kind] += 1
    return result


def find_persistent_centers(
    lons: np.ndarray,
    lats: np.ndarray,
    raw_values: np.ndarray,
    smoothed_values: np.ndarray,
) -> list[PersistentCenterCandidate]:
    candidates: list[PersistentCenterCandidate] = []
    for kind in ("high", "low"):
        for grid_y, grid_x in find_local_extrema(smoothed_values, kind):
            candidate = analyze_extremum(lons, lats, raw_values, smoothed_values, grid_y, grid_x, kind)
            if candidate is None:
                continue
            candidates.append(candidate)
    return candidates


def find_local_extrema(values: np.ndarray, kind: str) -> list[tuple[int, int]]:
    filtered = maximum_filter(values, size=5, mode="nearest") if kind == "high" else minimum_filter(values, size=5, mode="nearest")
    ys, xs = np.where(values == filtered)
    points = sorted(
        zip(ys.tolist(), xs.tolist()),
        key=lambda point: float(values[point[0], point[1]]),
        reverse=kind == "high",
    )
    selected: list[tuple[int, int]] = []
    for grid_y, grid_x in points:
        if grid_y < 2 or grid_y >= values.shape[0] - 2 or grid_x < 2 or grid_x >= values.shape[1] - 2:
            continue
        if any(((grid_x - other_x) / 6.0) ** 2 + ((grid_y - other_y) / 5.0) ** 2 < 1.0 for other_y, other_x in selected):
            continue
        selected.append((grid_y, grid_x))
    return selected


def analyze_extremum(
    lons: np.ndarray,
    lats: np.ndarray,
    raw_values: np.ndarray,
    smoothed_values: np.ndarray,
    grid_y: int,
    grid_x: int,
    kind: str,
) -> PersistentCenterCandidate | None:
    center_smoothed = float(smoothed_values[grid_y, grid_x])
    center_raw = float(raw_values[grid_y, grid_x])
    threshold = math.floor(center_smoothed / 2.0) * 2.0 if kind == "high" else math.ceil(center_smoothed / 2.0) * 2.0
    stronger_mask = smoothed_values > center_smoothed if kind == "high" else smoothed_values < center_smoothed

    closed_masks: list[np.ndarray] = []
    merged_with_stronger = False
    merge_level = threshold
    for _ in range(24):
        active = smoothed_values >= threshold if kind == "high" else smoothed_values <= threshold
        labels, _ = label(active)
        component_id = int(labels[grid_y, grid_x])
        if component_id == 0:
            break
        component_mask = labels == component_id
        comp_y, comp_x = np.where(component_mask)
        touches_boundary = (
            comp_y.min() == 0
            or comp_y.max() == smoothed_values.shape[0] - 1
            or comp_x.min() == 0
            or comp_x.max() == smoothed_values.shape[1] - 1
        )
        merged_with_stronger = bool(np.any(component_mask & stronger_mask))
        merge_level = threshold
        if touches_boundary or merged_with_stronger:
            break
        closed_masks.append(component_mask)
        threshold = threshold - 2.0 if kind == "high" else threshold + 2.0

    if not closed_masks:
        return None

    best_mask = max(closed_masks, key=lambda mask: int(mask.sum()))
    distance = distance_transform_edt(best_mask)
    if float(distance[grid_y, grid_x]) >= 1.0:
        label_y, label_x = grid_y, grid_x
    else:
        label_y, label_x = np.unravel_index(np.argmax(distance), distance.shape)
    if float(distance[label_y, label_x]) < 0.75:
        return None

    prominence = center_smoothed - merge_level if kind == "high" else merge_level - center_smoothed
    return PersistentCenterCandidate(
        kind=kind,
        lon=float(lons[label_x]),
        lat=float(lats[label_y]),
        value=center_raw,
        prominence=float(prominence),
        closed_levels=len(closed_masks),
        max_area=max(int(mask.sum()) for mask in closed_masks),
        merged_with_stronger=merged_with_stronger,
    )


def pressure_field_reference(values: np.ndarray) -> float:
    return float(np.nanmedian(np.asarray(values, dtype=float)))


def candidate_pressure_bias(candidate: PersistentCenterCandidate | PressureCenter, field_reference: float) -> float:
    if candidate.kind == "high":
        return float(candidate.value) - field_reference
    return field_reference - float(candidate.value)


def candidate_rank_score(candidate: PersistentCenterCandidate | PressureCenter, field_reference: float) -> float:
    if candidate.kind == "high":
        return candidate.prominence + PRESSURE_KIND_BIAS_WEIGHT * max(float(candidate.value) - field_reference, 0.0)
    return candidate.prominence - PRESSURE_KIND_BIAS_WEIGHT * max(float(candidate.value) - field_reference, 0.0)


def should_keep_candidate(candidate: PersistentCenterCandidate, field_reference: float) -> bool:
    if candidate.kind == "low":
        above_reference = max(float(candidate.value) - field_reference, 0.0)
        effective_prominence = candidate.prominence - PRESSURE_KIND_BIAS_WEIGHT * above_reference
        if candidate.merged_with_stronger and float(candidate.value) > field_reference + 1.5:
            return False
        return (
            (effective_prominence >= 3.0 and candidate.max_area >= 80)
            or (not candidate.merged_with_stronger and effective_prominence >= 2.8 and candidate.max_area >= 24)
            or (candidate.value <= min(1002.0, field_reference - 4.0) and candidate.max_area >= 10)
        )

    effective_prominence = candidate.prominence + PRESSURE_KIND_BIAS_WEIGHT * max(float(candidate.value) - field_reference, 0.0)
    return (
        (not candidate.merged_with_stronger and effective_prominence >= 2.4 and candidate.max_area >= 10)
        or (
            not candidate.merged_with_stronger
            and candidate.value >= max(1024.0, field_reference + 4.0)
            and effective_prominence >= 2.2
            and candidate.max_area >= 20
        )
    )


def is_nearby_center(left: PressureCenter, right: PressureCenter) -> bool:
    return ((left.lon - right.lon) / 6.0) ** 2 + ((left.lat - right.lat) / 5.0) ** 2 < 1.0


def is_cross_kind_conflict(left: PressureCenter, right: PressureCenter, field_reference: float) -> bool:
    if not is_nearby_center(left, right):
        return False
    return candidate_rank_score(left, field_reference) <= candidate_rank_score(right, field_reference)


def center_label_style(center: PressureCenter) -> tuple[str, int, float]:
    if center.kind == "low":
        if center.prominence >= 10.0:
            return "\u4f4e", 40, 5.5
        if center.prominence >= 6.0:
            return "\u4f4e", 36, 5.0
        if center.prominence >= 3.0:
            return "\u4f4e", 31, 4.5
        return "\u4f4e", 26, 4.0

    if center.prominence >= 5.0:
        return "\u9ad8", 34, 5.0
    if center.prominence >= 3.0:
        return "\u9ad8", 31, 4.5
    return "\u9ad8", 27, 4.0


def center_value_style(font_size: int, stroke_width: float) -> tuple[int, float]:
    return max(13, int(round(font_size * 0.48))), max(2.4, stroke_width - 1.5)


def smooth_contour_values(values: np.ndarray) -> np.ndarray:
    return gaussian_filter(np.asarray(values, dtype=float), sigma=CONTOUR_SMOOTHING_SIGMA)


PROJ_LON0_RAD = math.radians(PROJECTION["lon_0"])
PROJ_LAT0_RAD = math.radians(PROJECTION["lat_0"])
PROJ_LAT1_RAD = math.radians(PROJECTION["lat_1"])
PROJ_LAT2_RAD = math.radians(PROJECTION["lat_2"])
PROJ_N = math.log(math.cos(PROJ_LAT1_RAD) / math.cos(PROJ_LAT2_RAD)) / math.log(
    math.tan(math.pi / 4.0 + PROJ_LAT2_RAD / 2.0) / math.tan(math.pi / 4.0 + PROJ_LAT1_RAD / 2.0)
)
PROJ_F = math.cos(PROJ_LAT1_RAD) * math.tan(math.pi / 4.0 + PROJ_LAT1_RAD / 2.0) ** PROJ_N / PROJ_N
PROJ_RHO0 = PROJ_F / math.tan(math.pi / 4.0 + PROJ_LAT0_RAD / 2.0) ** PROJ_N


def project_coords(
    longitudes: np.ndarray | list[float] | float,
    latitudes: np.ndarray | list[float] | float,
) -> tuple[np.ndarray, np.ndarray] | tuple[float, float]:
    lon_array, lat_array = np.broadcast_arrays(np.asarray(longitudes, dtype=float), np.asarray(latitudes, dtype=float))
    lon_radians = np.deg2rad(lon_array)
    lat_radians = np.deg2rad(lat_array)
    rho = PROJ_F / np.power(np.tan(np.pi / 4.0 + lat_radians / 2.0), PROJ_N)
    theta = PROJ_N * (lon_radians - PROJ_LON0_RAD)
    projected_x = rho * np.sin(theta)
    projected_y = PROJ_RHO0 - rho * np.cos(theta)
    if np.isscalar(longitudes) and np.isscalar(latitudes):
        return float(projected_x), float(projected_y)
    return projected_x, projected_y


def inverse_project_coords(
    projected_x: np.ndarray | list[float] | float,
    projected_y: np.ndarray | list[float] | float,
) -> tuple[np.ndarray, np.ndarray] | tuple[float, float]:
    x_array, y_array = np.broadcast_arrays(np.asarray(projected_x, dtype=float), np.asarray(projected_y, dtype=float))
    rho = np.hypot(x_array, PROJ_RHO0 - y_array)
    theta = np.arctan2(x_array, PROJ_RHO0 - y_array)
    lat_radians = 2.0 * np.arctan(np.power(PROJ_F / rho, 1.0 / PROJ_N)) - np.pi / 2.0
    lon_radians = PROJ_LON0_RAD + theta / PROJ_N
    longitudes = np.rad2deg(lon_radians) % 360.0
    latitudes = np.rad2deg(lat_radians)
    if np.isscalar(projected_x) and np.isscalar(projected_y):
        return float(longitudes), float(latitudes)
    return longitudes, latitudes


def projected_region_bounds() -> tuple[float, float, float, float]:
    sample_lons = np.linspace(REGION["lon_min"], REGION["lon_max"], 256)
    sample_lats = np.linspace(REGION["lat_min"], REGION["lat_max"], 256)
    boundary_xs = []
    boundary_ys = []
    for lon in (REGION["lon_min"], REGION["lon_max"]):
        xs, ys = project_coords(np.full_like(sample_lats, lon), sample_lats)
        boundary_xs.append(xs)
        boundary_ys.append(ys)
    for lat in (REGION["lat_min"], REGION["lat_max"]):
        xs, ys = project_coords(sample_lons, np.full_like(sample_lons, lat))
        boundary_xs.append(xs)
        boundary_ys.append(ys)
    all_x = np.concatenate(boundary_xs)
    all_y = np.concatenate(boundary_ys)
    return float(all_x.min()), float(all_x.max()), float(all_y.min()), float(all_y.max())


def draw_projected_grid(ax: plt.Axes, x_min: float, x_max: float, y_min: float, y_max: float) -> None:
    grid_color = "#94a3ad"
    lon_samples = np.linspace(REGION["lon_min"], REGION["lon_max"], 256)
    lat_samples = np.linspace(REGION["lat_min"], REGION["lat_max"], 256)
    label_dx = (x_max - x_min) * 0.018
    label_dy = (y_max - y_min) * 0.02

    for lon in range(120, 156, 5):
        xs, ys = project_coords(np.full_like(lat_samples, float(lon)), lat_samples)
        ax.plot(xs, ys, color=grid_color, alpha=0.25, linewidth=0.8, zorder=0)
        label_x, label_y = project_coords(float(lon), REGION["lat_min"])
        ax.text(label_x, label_y - label_dy, f"{lon}", fontsize=9, color="#64727a", ha="center", va="top")

    for lat in range(20, 51, 5):
        xs, ys = project_coords(lon_samples, np.full_like(lon_samples, float(lat)))
        ax.plot(xs, ys, color=grid_color, alpha=0.25, linewidth=0.8, zorder=0)
        label_x, label_y = project_coords(REGION["lon_min"], float(lat))
        ax.text(label_x - label_dx, label_y, f"{lat}", fontsize=9, color="#64727a", ha="right", va="center")


def render_map(path: Path, model_name: str, valid_jst: datetime, run_utc: datetime, grid: tuple[np.ndarray, np.ndarray, np.ndarray], land_features) -> None:
    lons, lats, values = grid
    contour_values = smooth_contour_values(values)
    mesh_lons, mesh_lats = np.meshgrid(lons, lats)
    projected_x, projected_y = project_coords(mesh_lons, mesh_lats)
    x_min, x_max, y_min, y_max = projected_region_bounds()
    pad_x = (x_max - x_min) * PLOT_PAD_X_RATIO
    pad_y = (y_max - y_min) * PLOT_PAD_Y_RATIO
    levels = np.arange(
        math.floor(np.nanmin(contour_values) / 4.0) * 4.0,
        math.ceil(np.nanmax(contour_values) / 4.0) * 4.0 + 4.0,
        4.0,
    )
    strong = [level for level in levels if level % 8 == 0]
    weak = [level for level in levels if level % 8 != 0]
    centers = [center for center in detect_pressure_centers(lons, lats, values) if is_within_plot_region(center.lon, center.lat, 0.5)]

    fig = plt.figure(figsize=(12.8, 9.6), dpi=100, facecolor="#eef2ea")
    fig.subplots_adjust(left=0.075, right=0.97, top=0.90, bottom=0.08)
    ax = fig.add_subplot(111)
    ax.set_facecolor("#d6e4ed")
    ax.set_xlim(x_min - pad_x, x_max + pad_x)
    ax.set_ylim(y_min - pad_y, y_max + pad_y)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([])
    ax.set_yticks([])
    draw_projected_grid(ax, x_min, x_max, y_min, y_max)
    for spine in ax.spines.values():
        spine.set_color("#727d85")
        spine.set_linewidth(1.0)

    for feature in land_features:
        for polygon in feature:
            for ring in polygon:
                ring_lons = [point[0] for point in ring]
                ring_lats = [point[1] for point in ring]
                xs, ys = project_coords(ring_lons, ring_lats)
                ax.fill(xs, ys, facecolor="#e9e5d4", edgecolor="#727d85", linewidth=1.1, zorder=1)

    if weak:
        ax.contour(projected_x, projected_y, contour_values, levels=weak, colors="#7c858c", linewidths=1.0, zorder=2)
    cs = ax.contour(projected_x, projected_y, contour_values, levels=strong, colors="#2b3237", linewidths=1.7, zorder=3)
    contour_labels = ax.clabel(cs, inline=False, fmt="%d", fontsize=11)
    for contour_label in contour_labels:
        contour_label.set_bbox({"facecolor": (0.93, 0.95, 0.92, 0.85), "edgecolor": "none", "pad": 0.15})
        contour_label.set_path_effects([pe.Stroke(linewidth=1.5, foreground="#eef2ea"), pe.Normal()])

    for center in centers:
        color = "#1c5aa6" if center.kind == "high" else "#b13b36"
        label, font_size, stroke_width = center_label_style(center)
        value_font_size, value_stroke_width = center_value_style(font_size, stroke_width)
        center_x, center_y = project_coords(center.lon, center.lat)
        text = ax.text(
            center_x,
            center_y,
            label,
            fontsize=font_size,
            weight="bold",
            color=color,
            ha="center",
            va="center",
            zorder=4,
            fontfamily="Yu Gothic",
        )
        text.set_path_effects([pe.Stroke(linewidth=stroke_width, foreground="white"), pe.Normal()])
        value_text = ax.annotate(
            f"{int(round(center.value))}",
            (center_x, center_y),
            xytext=(0, -font_size * 0.82),
            textcoords="offset points",
            fontsize=value_font_size,
            weight="bold",
            color=color,
            ha="center",
            va="top",
            zorder=4,
            fontfamily="Yu Gothic",
        )
        value_text.set_path_effects([pe.Stroke(linewidth=value_stroke_width, foreground="white"), pe.Normal()])

    fig.text(0.03, 0.965, f"{model_name}  Surface Pressure", fontsize=22, weight="bold", color="#232c31", family="serif")
    fig.text(
        0.03,
        0.938,
        f"Forecast: {valid_jst:%Y-%m-%d %H:%M} JST    Model run: {run_utc:%Y-%m-%d %H:%M} UTC    Created: {datetime.now(JST):%Y-%m-%d %H:%M} JST",
        fontsize=10,
        color="#4a5961",
    )
    fig.text(
        0.97,
        0.965,
        "Surface Pressure\nContour interval: 4 hPa\nBold line: every 8 hPa",
        fontsize=10,
        color="#334047",
        ha="right",
        va="top",
        bbox={"facecolor": (1, 1, 1, 0.6), "edgecolor": "#8c999f", "boxstyle": "round,pad=0.45"},
    )
    fig.savefig(path, format="jpg", dpi=100, pil_kwargs={"quality": 92})
    plt.close(fig)


def recreate_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
