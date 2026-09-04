import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
from astropy.io import fits

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "gbm-orbital-subtraction"))
from orbital_core import _aligned_offsets, _calculate_regions, run_analysis  # noqa: E402


def make_phaii(path: Path, detector="NAI_00", channels=8):
    period = 5737.70910239
    trigger = 700_000_000.0
    start = trigger - 17 * period - 500
    stop = trigger + 17 * period + 500
    resolution = 1.024 if channels == 8 else 4.096
    time = np.arange(start, stop, resolution)
    exposure = np.full(len(time), resolution * 0.95, dtype=np.float32)
    phase = (time - trigger) / 500
    base_rate = 18 + 0.5 * np.sin(phase)
    counts = np.column_stack(
        [(base_rate + channel) * exposure for channel in range(channels)]
    ).astype(np.float32)
    # Add a source-only excess in the requested source region.
    source = (time >= trigger - 100) & (time <= trigger + 200)
    counts[source, 2:5] += 12 * exposure[source, None]

    primary = fits.PrimaryHDU()
    primary.header["DETNAM"] = detector
    primary.header["DATATYPE"] = "CTIME" if channels == 8 else "CSPEC"
    ebounds = fits.BinTableHDU.from_columns(
        [
            fits.Column(name="E_MIN", format="1E", array=np.arange(channels, dtype=float)),
            fits.Column(name="E_MAX", format="1E", array=np.arange(1, channels + 1, dtype=float)),
        ],
        name="EBOUNDS",
    )
    spectrum = fits.BinTableHDU.from_columns(
        [
            fits.Column(name="TIME", format="1D", array=time),
            fits.Column(name="ENDTIME", format="1D", array=time + resolution),
            fits.Column(name="EXPOSURE", format="1E", array=exposure),
            fits.Column(name="QUALITY", format="1I", array=np.zeros(len(time), dtype=np.int16)),
            fits.Column(name="COUNTS", format=f"{channels}E", array=counts),
        ],
        name="SPECTRUM",
    )
    fits.HDUList([primary, ebounds, spectrum]).writeto(path)


class OrbitalCoreTests(unittest.TestCase):
    def test_regions_match_desktop_definition(self):
        regions = _calculate_regions(1000.0, (-10.0, 20.0), [2], 100.0)
        self.assertEqual(regions["src"], (990.0, 1020.0))
        self.assertEqual(regions["pre2"], (790.0, 820.0))
        self.assertEqual(regions["pos2"], (1190.0, 1220.0))

    def test_alignment_preserves_osv_truncation(self):
        start, stop = _aligned_offsets(-250.0, 750.0, 1.024)
        self.assertAlmostEqual(start, -249.344)
        self.assertAlmostEqual(stop, 749.568)

    def test_end_to_end_creates_scientific_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "glg_ctime_n0_test.pha"
            output_path = root / "output"
            make_phaii(input_path)
            params = {
                "event_name": "synthetic",
                "trigger_met": 700_000_000,
                "start_offset": -100,
                "stop_offset": 200,
                "orbit_period": 5737.70910239,
                "orbit_offsets": [14, 16],
                "spectrum_type": "CTIME",
                "channel_first": 1,
                "channel_last": 6,
                "recalculate_period": False,
            }
            result = json.loads(
                run_analysis(
                    json.dumps(params),
                    json.dumps([{"name": input_path.name, "path": str(input_path)}]),
                    str(output_path),
                )
            )
            self.assertIn("n0", result["detector_results"])
            self.assertGreater(result["detector_results"]["n0"]["bins"], 200)
            self.assertTrue(Path(result["archive_path"]).exists())
            self.assertEqual(result["warnings"], [])
            with zipfile.ZipFile(result["archive_path"]) as archive:
                names = set(archive.namelist())
                self.assertIn("analysis.json", names)
                self.assertIn("glg_osv_synthetic_n0.pha", names)
                self.assertIn("glg_osv_synthetic_n0.bak", names)
                with fits.open(io.BytesIO(archive.read("glg_osv_synthetic_n0.bak"))) as hdul:
                    self.assertEqual(hdul[0].header["DETNAM"], "NAI_00")
                    self.assertEqual(hdul["SPECTRUM"].header["HDUCLAS2"], "BKG")
                    self.assertIn("STAT_ERR", hdul["SPECTRUM"].columns.names)


if __name__ == "__main__":
    unittest.main()
