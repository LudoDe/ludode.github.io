# GBM Orbital Subtraction browser app

This static application ports the core PHAII background-subtraction and export
workflow from [OrbitalSubtractionGBM](https://github.com/LudoDe/OrbitalSubtractionGBM)
to GitHub Pages. FITS files are processed locally in a Web Worker using Pyodide,
NumPy, and Astropy; input data is not uploaded to a server.

## Files

- `index.html` and `styles.css`: accessible, responsive interface.
- `app.js`: file handling, validation, plots, and downloads.
- `worker.js`: isolated Pyodide runtime and zero-copy result transfer.
- `orbital_core.py`: FITS parsing, orbit windows, rebinning, background model,
  and PHAII/CSV/NPZ export.

The Pyodide version is pinned in `worker.js`. The core deliberately has no
dependency on wxPython or ConfigObj.

## Validation

The numerical core can be imported and tested with ordinary CPython after
installing NumPy and Astropy. Synthetic CTIME-like FITS tests live in the
development change set that introduced this tool.

## Current scope

The browser app implements local CTIME/CSPEC PHAII analysis, optional POSHIST
orbit-period recalculation, per-detector plots, and scientific downloads.
Detector-angle and occultation diagnostics remain in the desktop application.
