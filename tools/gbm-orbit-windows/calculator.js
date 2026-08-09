const assertFinite = (value, label) => {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    throw new TypeError(`${label} must be a finite number.`);
  }
  return number;
};

export function parseOrbitOffsets(value) {
  const parts = Array.isArray(value) ? value : String(value).split(",");
  const offsets = parts.map((part) => Number(String(part).trim()));

  if (!offsets.length || offsets.some((offset) => !Number.isInteger(offset) || offset <= 0)) {
    throw new TypeError("Background orbits must be positive whole numbers separated by commas.");
  }

  return [...new Set(offsets)].sort((a, b) => a - b);
}

export function calculateRegions({
  triggerMet,
  startOffset,
  endOffset,
  orbitalPeriod = 5737.70910239,
  orbitOffsets = [14, 16],
}) {
  const trigger = assertFinite(triggerMet, "Trigger MET");
  const start = assertFinite(startOffset, "Start offset");
  const stop = assertFinite(endOffset, "End offset");
  const period = assertFinite(orbitalPeriod, "Orbit period");
  const orbits = parseOrbitOffsets(orbitOffsets);

  if (start >= stop) {
    throw new RangeError("Start offset must be smaller than end offset.");
  }
  if (period <= 0) {
    throw new RangeError("Orbit period must be greater than zero.");
  }

  const regions = [{
    id: "source",
    label: "Source",
    startMet: trigger + start,
    stopMet: trigger + stop,
    shiftSeconds: 0,
  }];

  for (const orbit of orbits) {
    const shift = orbit * period;
    regions.push(
      {
        id: `pre-${orbit}`,
        label: `−${orbit} orbits`,
        startMet: trigger + start - shift,
        stopMet: trigger + stop - shift,
        shiftSeconds: -shift,
      },
      {
        id: `post-${orbit}`,
        label: `+${orbit} orbits`,
        startMet: trigger + start + shift,
        stopMet: trigger + stop + shift,
        shiftSeconds: shift,
      },
    );
  }

  return regions.sort((a, b) => a.startMet - b.startMet);
}

export function formatMet(value, precision = 6) {
  const number = assertFinite(value, "MET value");
  return number.toFixed(precision).replace(/\.?0+$/u, "");
}

export function regionsToCsv(regions) {
  const rows = ["region,start_met,stop_met,shift_seconds"];
  for (const region of regions) {
    rows.push([
      region.label,
      formatMet(region.startMet),
      formatMet(region.stopMet),
      formatMet(region.shiftSeconds),
    ].join(","));
  }
  return rows.join("\n");
}
