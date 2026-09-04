"""Browser-compatible Fermi/GBM orbital background subtraction.

This module is loaded by Pyodide.  It keeps file parsing and numerical work in
Python/NumPy/Astropy while the surrounding interface lives in JavaScript.
The implementation follows the Regions, Pha_data.bin_pha, and
Pha_data.calc_background workflow in OrbitalSubtractionGBM v1.3.
"""

from __future__ import annotations

import csv
import json
import math
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from astropy.io import fits


DEFAULT_PERIOD = 5737.70910239
MAX_OUTPUT_BINS = 20_000
DETECTOR_ORDER = [
    "n0", "n1", "n2", "n3", "n4", "n5", "n6", "n7",
    "n8", "n9", "na", "nb", "b0", "b1",
]
DETECTOR_LONG_TO_SHORT = {
    **{f"NAI_{index:02d}": code for index, code in enumerate(DETECTOR_ORDER[:12])},
    "BGO_00": "b0",
    "BGO_01": "b1",
}


class AnalysisError(ValueError):
    """A validation error suitable for displaying in the browser."""


@dataclass
class SpectrumFile:
    name: str
    detector: str
    kind: str
    time: np.ndarray
    endtime: np.ndarray
    exposure: np.ndarray
    counts: np.ndarray
    e_min: np.ndarray
    e_max: np.ndarray


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisError(f"{label} must be a number.") from exc
    if not math.isfinite(number):
        raise AnalysisError(f"{label} must be finite.")
    return number


def _positive_orbits(values: Iterable[Any]) -> list[int]:
    offsets: set[int] = set()
    for value in values:
        number = _finite(value, "Each orbit offset")
        integer = int(number)
        if number != integer or integer <= 0:
            raise AnalysisError("Orbit offsets must be positive whole numbers.")
        offsets.add(integer)
    if not offsets:
        raise AnalysisError("Enter at least one orbit offset.")
    return sorted(offsets)


def _normalise_detector(value: Any, filename: str) -> str | None:
    if value is not None:
        label = str(value).strip().upper()
        if label in DETECTOR_LONG_TO_SHORT:
            return DETECTOR_LONG_TO_SHORT[label]
        short = label.lower()
        if short in DETECTOR_ORDER:
            return short

    match = re.search(r"(?:^|_)(n[0-9ab]|b[01])(?:_|\.)", filename.lower())
    return match.group(1) if match else None


def _find_table(hdul: fits.HDUList, required: set[str]):
    for hdu in hdul:
        names = set(hdu.columns.names or []) if hasattr(hdu, "columns") else set()
        if required.issubset(names):
            return hdu
    return None


def _read_spectrum(path: str, name: str) -> SpectrumFile | None:
    with fits.open(path, memmap=False, lazy_load_hdus=False) as hdul:
        spectrum = _find_table(hdul, {"TIME", "EXPOSURE", "COUNTS"})
        ebounds = _find_table(hdul, {"E_MIN", "E_MAX"})
        if spectrum is None or ebounds is None:
            return None

        names = set(spectrum.columns.names or [])
        time = np.asarray(spectrum.data["TIME"], dtype=np.float64)
        exposure = np.asarray(spectrum.data["EXPOSURE"], dtype=np.float64)
        if "ENDTIME" in names:
            endtime = np.asarray(spectrum.data["ENDTIME"], dtype=np.float64)
        else:
            endtime = time + exposure

        counts = np.asarray(spectrum.data["COUNTS"], dtype=np.float64)
        if counts.ndim == 1:
            counts = counts.reshape((-1, 1))
        if len(time) != len(counts):
            raise AnalysisError(f"{name}: TIME and COUNTS have different row counts.")

        if "QUALITY" in names:
            quality = np.asarray(spectrum.data["QUALITY"])
            mask = quality == 0
        else:
            mask = np.ones(len(time), dtype=bool)
        mask &= np.isfinite(time) & np.isfinite(endtime) & np.isfinite(exposure)
        mask &= exposure > 0
        if not np.any(mask):
            raise AnalysisError(f"{name}: no good spectral rows were found.")

        e_min = np.asarray(ebounds.data["E_MIN"], dtype=np.float64)
        e_max = np.asarray(ebounds.data["E_MAX"], dtype=np.float64)
        n_channels = counts.shape[1]
        if len(e_min) != n_channels or len(e_max) != n_channels:
            raise AnalysisError(f"{name}: EBOUNDS does not match the COUNTS channels.")

        detector = _normalise_detector(
            spectrum.header.get("DETNAM", hdul[0].header.get("DETNAM")), name
        )
        if detector is None:
            raise AnalysisError(
                f"{name}: detector could not be identified from DETNAM or the filename."
            )

        data_type = str(
            spectrum.header.get("DATATYPE", hdul[0].header.get("DATATYPE", ""))
        ).upper()
        if "CTIME" in data_type or n_channels == 8:
            kind = "CTIME"
        elif "CSPEC" in data_type or n_channels == 128:
            kind = "CSPEC"
        else:
            kind = f"{n_channels}-channel"

        return SpectrumFile(
            name=name,
            detector=detector,
            kind=kind,
            time=time[mask],
            endtime=endtime[mask],
            exposure=exposure[mask],
            counts=counts[mask],
            e_min=e_min,
            e_max=e_max,
        )


def _read_positions(path: str) -> np.ndarray | None:
    with fits.open(path, memmap=False, lazy_load_hdus=False) as hdul:
        table = _find_table(hdul, {"POS_X", "POS_Y", "POS_Z"})
        if table is None:
            return None
        vectors = []
        for column in ("POS_X", "POS_Y", "POS_Z"):
            values = np.asarray(table.data[column], dtype=np.float64)
            unit = str(table.columns[column].unit or "").strip().lower()
            if unit in {"km", "kilometer", "kilometers"}:
                values = values * 1000.0
            vectors.append(values)
        positions = np.column_stack(vectors)
        return positions[np.all(np.isfinite(positions), axis=1)]


def _load_inputs(files: list[dict[str, str]], requested_kind: str):
    spectra: list[SpectrumFile] = []
    position_sets: list[np.ndarray] = []
    ignored: list[str] = []

    for item in files:
        path, name = item["path"], item["name"]
        try:
            spectrum = _read_spectrum(path, name)
            if spectrum is not None:
                if spectrum.kind not in {"CTIME", "CSPEC"}:
                    ignored.append(f"{name} ({spectrum.kind})")
                elif requested_kind != "AUTO" and spectrum.kind != requested_kind:
                    ignored.append(f"{name} ({spectrum.kind})")
                else:
                    spectra.append(spectrum)
                continue
            positions = _read_positions(path)
            if positions is not None and positions.size:
                position_sets.append(positions)
                continue
            ignored.append(name)
        except AnalysisError:
            raise
        except Exception as exc:
            raise AnalysisError(f"{name}: {exc}") from exc

    if not spectra:
        suffix = f" matching {requested_kind}" if requested_kind != "AUTO" else ""
        raise AnalysisError(f"No GBM CTIME or CSPEC PHAII files{suffix} were found.")

    kinds = {spectrum.kind for spectrum in spectra}
    if len(kinds) != 1:
        labels = ", ".join(sorted(kinds))
        raise AnalysisError(
            f"The selected files mix spectral types ({labels}). Choose one type per run."
        )

    return spectra, position_sets, ignored


def _combine_detector(files: list[SpectrumFile]) -> SpectrumFile:
    reference = files[0]
    for item in files[1:]:
        if item.counts.shape[1] != reference.counts.shape[1]:
            raise AnalysisError(f"{reference.detector}: files have different channel counts.")
        if not (
            np.allclose(item.e_min, reference.e_min, rtol=0, atol=1e-5)
            and np.allclose(item.e_max, reference.e_max, rtol=0, atol=1e-5)
        ):
            raise AnalysisError(f"{reference.detector}: files have incompatible energy bounds.")

    time = np.concatenate([item.time for item in files])
    endtime = np.concatenate([item.endtime for item in files])
    exposure = np.concatenate([item.exposure for item in files])
    counts = np.concatenate([item.counts for item in files], axis=0)
    order = np.argsort(time, kind="stable")

    # Daily files can overlap at boundaries. Keep only the first exact interval.
    pairs = np.column_stack((time[order], endtime[order]))
    _, unique_indices = np.unique(pairs, axis=0, return_index=True)
    keep = order[np.sort(unique_indices)]
    keep = keep[np.argsort(time[keep], kind="stable")]

    return SpectrumFile(
        name=", ".join(item.name for item in files),
        detector=reference.detector,
        kind=reference.kind,
        time=time[keep],
        endtime=endtime[keep],
        exposure=exposure[keep],
        counts=counts[keep],
        e_min=reference.e_min,
        e_max=reference.e_max,
    )


def _rebin_region(data: SpectrumFile, region: tuple[float, float], resolution: float):
    start, stop = region
    mask = (data.time >= start) & (data.endtime <= stop)
    if not np.any(mask):
        return None

    time = data.time[mask]
    endtime = data.endtime[mask]
    exposure = data.exposure[mask]
    counts = data.counts[mask]
    centers = time + (endtime - time) / 2.0
    target = np.arange(start, stop, resolution, dtype=np.float64)
    if not target.size:
        raise AnalysisError("The source interval is shorter than one output bin.")
    if target.size > MAX_OUTPUT_BINS:
        raise AnalysisError(
            f"The selected interval creates more than {MAX_OUTPUT_BINS:,} bins. "
            "Use a shorter interval."
        )

    bin_width = endtime - time
    valid = np.isfinite(bin_width) & (bin_width > 0) & (exposure > 0)
    if not np.any(valid):
        return None
    centers = centers[valid]
    exposure = exposure[valid]
    counts = counts[valid]
    bin_width = bin_width[valid]

    order = np.argsort(centers, kind="stable")
    centers = centers[order]
    exposure = exposure[order]
    counts = counts[order]
    bin_width = bin_width[order]

    output_exposure = np.interp(target, centers, exposure / bin_width) * resolution
    output_counts = np.empty((target.size, counts.shape[1]), dtype=np.float64)
    rates = counts / exposure[:, None]
    for channel in range(counts.shape[1]):
        output_counts[:, channel] = (
            np.interp(target, centers, rates[:, channel]) * output_exposure
        )

    edges = np.column_stack((target - resolution / 2.0, target + resolution / 2.0))
    errors = np.sqrt(np.clip(output_counts, 0, None))
    return edges, output_counts, output_exposure, errors


def _aligned_offsets(start: float, stop: float, resolution: float):
    # Preserve Python's truncation-toward-zero behavior from OSV_Args.check().
    aligned_start = int(start / resolution) * resolution + resolution / 2.0
    aligned_stop = int(stop / resolution) * resolution
    if aligned_start >= aligned_stop:
        raise AnalysisError("The aligned source interval contains no complete bins.")
    return aligned_start, aligned_stop


def _calculate_regions(
    trigger: float,
    source_range: tuple[float, float],
    orbits: list[int],
    period: float,
):
    start, stop = source_range
    regions = {"src": (trigger + start, trigger + stop)}
    for orbit in orbits:
        shift = orbit * period
        regions[f"pre{orbit}"] = (trigger + start - shift, trigger + stop - shift)
        regions[f"pos{orbit}"] = (trigger + start + shift, trigger + stop + shift)
    return regions


def _mean_side(items: list[tuple[str, tuple[np.ndarray, ...]]], side: str):
    selected = [(name, value) for name, value in items if name.startswith(side)]
    if not selected:
        label = "before" if side == "pre" else "after"
        raise AnalysisError(f"No usable background interval was found {label} the source.")
    counts = np.stack([value[1] for _, value in selected])
    errors = np.stack([value[3] for _, value in selected])
    exposures = np.stack([value[2] for _, value in selected])
    return (
        np.mean(counts, axis=0),
        np.sqrt(np.sum(errors**2, axis=0)) / len(selected),
        np.mean(exposures, axis=0),
        [name for name, _ in selected],
    )


def _analyse_detector(
    data: SpectrumFile,
    regions: dict[str, tuple[float, float]],
    resolution: float,
    trigger: float,
    channel_first: int,
    channel_last: int,
):
    rebinned: dict[str, tuple[np.ndarray, ...] | None] = {
        name: _rebin_region(data, region, resolution) for name, region in regions.items()
    }
    source = rebinned["src"]
    if source is None:
        raise AnalysisError("no source data falls inside the selected interval")

    available = [(name, value) for name, value in rebinned.items() if name != "src" and value]
    pre, pre_error, pre_exposure, pre_names = _mean_side(available, "pre")
    post, post_error, post_exposure, post_names = _mean_side(available, "pos")
    background = (pre + post) / 2.0
    background_error = 0.5 * np.sqrt(pre_error**2 + post_error**2)
    background_exposure = (pre_exposure + post_exposure) / 2.0

    _, source_counts, source_exposure, source_error = source
    zero_mask = np.mean(source_counts, axis=1) == 0
    for _, value in available:
        zero_mask |= np.mean(value[1], axis=1) == 0

    quality = np.zeros(len(zero_mask), dtype=np.int16)
    quality[zero_mask] = 1
    for index in np.flatnonzero(zero_mask):
        quality[max(0, index - 10):index] = 1

    background[zero_mask] = 0
    background_error[zero_mask] = 0
    pre[zero_mask] = 0
    post[zero_mask] = 0
    source_counts = source_counts.copy()
    source_error = source_error.copy()
    source_counts[zero_mask] = 0
    source_error[zero_mask] = 0

    n_channels = source_counts.shape[1]
    if channel_first < 0 or channel_last < channel_first or channel_last >= n_channels:
        raise AnalysisError(
            f"channel range {channel_first}–{channel_last} is outside 0–{n_channels - 1}"
        )
    selection = slice(channel_first, channel_last + 1)
    edges = source[0]
    midpoint = np.mean(edges, axis=1)

    source_rate = np.sum(source_counts[:, selection], axis=1) / source_exposure
    background_rate = np.sum(background[:, selection], axis=1) / background_exposure
    pre_rate = np.sum(pre[:, selection], axis=1) / pre_exposure
    post_rate = np.sum(post[:, selection], axis=1) / post_exposure
    source_rate_error = (
        np.sqrt(np.sum(source_error[:, selection] ** 2, axis=1)) / source_exposure
    )
    background_rate_error = (
        np.sqrt(np.sum(background_error[:, selection] ** 2, axis=1))
        / background_exposure
    )
    residual_rate = source_rate - background_rate
    residual_error = np.sqrt(source_rate_error**2 + background_rate_error**2)

    return {
        "edges": edges,
        "time_relative": midpoint - trigger,
        "source_counts": source_counts,
        "source_error": source_error,
        "source_exposure": source_exposure,
        "background_counts": background,
        "background_error": background_error,
        "background_exposure": background_exposure,
        "pre_counts": pre,
        "post_counts": post,
        "quality": quality,
        "source_rate": source_rate,
        "background_rate": background_rate,
        "pre_rate": pre_rate,
        "post_rate": post_rate,
        "residual_rate": residual_rate,
        "source_rate_error": source_rate_error,
        "background_rate_error": background_rate_error,
        "residual_error": residual_error,
        "used_regions": pre_names + post_names,
        "missing_regions": [
            name for name, value in rebinned.items() if name != "src" and value is None
        ],
    }


def _clean_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return cleaned or "gbm-event"


def _write_csv(path: Path, result: dict[str, Any]):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "time_start_met", "time_stop_met", "time_since_trigger_s",
                "source_rate", "background_rate", "residual_rate",
                "source_rate_error", "background_rate_error", "residual_rate_error",
                "quality",
            ]
        )
        for row in zip(
            result["edges"][:, 0],
            result["edges"][:, 1],
            result["time_relative"],
            result["source_rate"],
            result["background_rate"],
            result["residual_rate"],
            result["source_rate_error"],
            result["background_rate_error"],
            result["residual_error"],
            result["quality"],
        ):
            writer.writerow(row)


def _base_header(detector: str, kind: str, trigger: float, start: float, stop: float):
    header = fits.Header()
    header["CREATOR"] = ("OSV browser 1.0", "Browser port of OrbitalSubtractionGBM")
    header["FILETYPE"] = "PHAII"
    header["TELESCOP"] = "GLAST"
    header["INSTRUME"] = "GBM"
    header["DETNAM"] = next(
        key for key, short in DETECTOR_LONG_TO_SHORT.items() if short == detector
    )
    header["DATATYPE"] = kind
    header["TIMESYS"] = "TT"
    header["TIMEUNIT"] = "s"
    header["MJDREFI"] = 51910
    header["MJDREFF"] = 7.428703703703703e-4
    header["TSTART"] = float(start)
    header["TSTOP"] = float(stop)
    header["TRIGTIME"] = float(trigger)
    return header


def _write_phaii(
    path: Path,
    data: SpectrumFile,
    result: dict[str, Any],
    trigger: float,
    background: bool,
):
    edges = result["edges"]
    counts = result["background_counts"] if background else result["source_counts"]
    exposure = (
        result["background_exposure"] if background else result["source_exposure"]
    )
    n_channels = counts.shape[1]
    header = _base_header(
        data.detector, data.kind, trigger, float(edges[0, 0]), float(edges[-1, 1])
    )
    primary = fits.PrimaryHDU(header=header)

    channels = np.arange(n_channels, dtype=np.int16)
    ebounds = fits.BinTableHDU.from_columns(
        [
            fits.Column(name="CHANNEL", format="1I", array=channels),
            fits.Column(name="E_MIN", format="1E", unit="keV", array=data.e_min),
            fits.Column(name="E_MAX", format="1E", unit="keV", array=data.e_max),
        ],
        name="EBOUNDS",
    )
    ebounds.header["DETCHANS"] = n_channels
    ebounds.header["CHANTYPE"] = "PHA"

    columns = [
        fits.Column(name="COUNTS", format=f"{n_channels}E", unit="count", array=counts),
    ]
    if background:
        columns.append(
            fits.Column(
                name="STAT_ERR",
                format=f"{n_channels}E",
                unit="count",
                array=result["background_error"],
            )
        )
    columns.extend(
        [
            fits.Column(name="EXPOSURE", format="1E", unit="s", array=exposure),
            fits.Column(name="QUALITY", format="1I", array=result["quality"]),
            fits.Column(name="TIME", format="1D", unit="s", array=edges[:, 0]),
            fits.Column(name="ENDTIME", format="1D", unit="s", array=edges[:, 1]),
        ]
    )
    spectrum = fits.BinTableHDU.from_columns(columns, name="SPECTRUM")
    spectrum.header["HDUCLASS"] = "OGIP"
    spectrum.header["HDUCLAS1"] = "SPECTRUM"
    spectrum.header["HDUCLAS2"] = "BKG" if background else "TOTAL"
    spectrum.header["HDUCLAS3"] = "COUNT"
    spectrum.header["HDUCLAS4"] = "TYPEII"
    spectrum.header["HDUVERS"] = "1.2.1"
    spectrum.header["CHANTYPE"] = "PHA"
    spectrum.header["DETCHANS"] = n_channels
    spectrum.header["POISSERR"] = not background
    spectrum.header["TRIGTIME"] = float(trigger)

    gti = fits.BinTableHDU.from_columns(
        [
            fits.Column(name="START", format="1D", unit="s", array=[edges[0, 0]]),
            fits.Column(name="STOP", format="1D", unit="s", array=[edges[-1, 1]]),
        ],
        name="GTI",
    )
    gti.header["HDUCLASS"] = "OGIP"
    gti.header["HDUCLAS1"] = "GTI"
    fits.HDUList([primary, ebounds, spectrum, gti]).writeto(path, overwrite=True)


def _serialise_plot(result: dict[str, Any]):
    def values(key: str):
        array = np.asarray(result[key], dtype=np.float64)
        return [None if not np.isfinite(value) else float(value) for value in array]

    return {
        "time": values("time_relative"),
        "source": values("source_rate"),
        "background": values("background_rate"),
        "pre": values("pre_rate"),
        "post": values("post_rate"),
        "residual": values("residual_rate"),
        "quality": [int(value) for value in result["quality"]],
    }


def run_analysis(params_json: str, files_json: str, output_dir: str) -> str:
    """Run the analysis and return JSON metadata plus a path to a ZIP archive."""
    params = json.loads(params_json)
    files = json.loads(files_json)
    trigger = _finite(params.get("trigger_met"), "Trigger MET")
    start = _finite(params.get("start_offset"), "Start offset")
    stop = _finite(params.get("stop_offset"), "Stop offset")
    period = _finite(params.get("orbit_period", DEFAULT_PERIOD), "Orbit period")
    if start >= stop:
        raise AnalysisError("Start offset must be smaller than stop offset.")
    if period <= 0:
        raise AnalysisError("Orbit period must be greater than zero.")

    orbits = _positive_orbits(params.get("orbit_offsets", []))
    requested_kind = str(params.get("spectrum_type", "AUTO")).upper()
    if requested_kind not in {"AUTO", "CTIME", "CSPEC"}:
        raise AnalysisError("Spectrum type must be Auto, CTIME, or CSPEC.")
    channel_first_value = _finite(params.get("channel_first", 0), "First channel")
    channel_last_value = _finite(params.get("channel_last", 7), "Last channel")
    if not channel_first_value.is_integer() or not channel_last_value.is_integer():
        raise AnalysisError("Channel numbers must be whole numbers.")
    channel_first = int(channel_first_value)
    channel_last = int(channel_last_value)

    spectra, positions, ignored = _load_inputs(files, requested_kind)
    kind = spectra[0].kind
    resolution = 1.024 if spectra[0].counts.shape[1] == 8 else 4.096
    source_range = _aligned_offsets(start, stop, resolution)

    recalculated = False
    initial_warnings: list[str] = []
    if params.get("recalculate_period") and positions:
        position = np.concatenate(positions, axis=0)
        radius = np.sqrt(np.sum(position**2, axis=1))
        if radius.size:
            period = 2.0 * np.pi * np.sqrt(float(np.mean(radius)) ** 3 / (6.67428e-11 * 5.9722e24))
            recalculated = True
    elif params.get("recalculate_period"):
        initial_warnings.append(
            "Orbit-period recalculation was requested, but no readable POSHIST file was supplied."
        )

    regions = _calculate_regions(trigger, source_range, orbits, period)
    grouped: dict[str, list[SpectrumFile]] = {}
    for item in spectra:
        grouped.setdefault(item.detector, []).append(item)

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    event_name = _clean_name(str(params.get("event_name", "gbm-event")))
    detector_results: dict[str, dict[str, Any]] = {}
    warnings_out: list[str] = initial_warnings
    generated: list[Path] = []

    for detector in sorted(grouped, key=DETECTOR_ORDER.index):
        data = _combine_detector(grouped[detector])
        try:
            result = _analyse_detector(
                data,
                regions,
                resolution,
                trigger,
                channel_first,
                channel_last,
            )
        except AnalysisError as exc:
            warnings_out.append(f"{detector}: {exc}")
            continue

        stem = f"glg_osv_{event_name}_{detector}"
        source_path = output / f"{stem}.pha"
        background_path = output / f"{stem}.bak"
        csv_path = output / f"{stem}.csv"
        npz_path = output / f"{stem}.npz"
        _write_phaii(source_path, data, result, trigger, background=False)
        _write_phaii(background_path, data, result, trigger, background=True)
        _write_csv(csv_path, result)
        np.savez_compressed(
            npz_path,
            time_start=result["edges"][:, 0],
            time_stop=result["edges"][:, 1],
            source_counts=result["source_counts"],
            source_error=result["source_error"],
            source_exposure=result["source_exposure"],
            background_counts=result["background_counts"],
            background_error=result["background_error"],
            background_exposure=result["background_exposure"],
            quality=result["quality"],
            e_min=data.e_min,
            e_max=data.e_max,
        )
        generated.extend([source_path, background_path, csv_path, npz_path])
        detector_results[detector] = {
            "detector": detector,
            "bins": int(len(result["time_relative"])),
            "channels": int(data.counts.shape[1]),
            "quality_flagged": int(np.count_nonzero(result["quality"])),
            "used_regions": result["used_regions"],
            "missing_regions": result["missing_regions"],
            "plot": _serialise_plot(result),
        }
        if result["missing_regions"]:
            missing = ", ".join(result["missing_regions"])
            warnings_out.append(f"{detector}: missing {missing}; available offsets were averaged.")

    if not detector_results:
        details = " ".join(warnings_out)
        raise AnalysisError(f"No detector could be analysed. {details}".strip())

    region_summary = [
        {"name": name, "start": float(bounds[0]), "stop": float(bounds[1])}
        for name, bounds in regions.items()
    ]
    metadata = {
        "software": "OSV browser 1.0",
        "source": "OrbitalSubtractionGBM v1.3",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "event_name": event_name,
        "trigger_met": trigger,
        "spectrum_type": kind,
        "resolution_seconds": resolution,
        "source_offsets_seconds": list(source_range),
        "orbit_offsets": orbits,
        "orbit_period_seconds": period,
        "period_recalculated": recalculated,
        "channel_first": channel_first,
        "channel_last": channel_last,
        "regions": region_summary,
        "detectors": list(detector_results),
        "ignored_files": ignored,
        "warnings": warnings_out,
    }
    metadata_path = output / "analysis.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    generated.append(metadata_path)

    archive_path = output / f"{event_name}-orbital-subtraction.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in generated:
            archive.write(path, arcname=path.name)
    for path in generated:
        path.unlink(missing_ok=True)

    response = {
        **metadata,
        "archive_path": str(archive_path),
        "archive_name": archive_path.name,
        "detector_results": detector_results,
    }
    return json.dumps(response, separators=(",", ":"), allow_nan=False)
