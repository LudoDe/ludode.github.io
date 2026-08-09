import test from "node:test";
import assert from "node:assert/strict";
import { calculateRegions, formatMet, parseOrbitOffsets, regionsToCsv } from "./calculator.js";

test("calculates source and ±14/±16 orbit intervals", () => {
  const regions = calculateRegions({
    triggerMet: 700000000,
    startOffset: -100,
    endOffset: 300,
    orbitalPeriod: 5737.70910239,
    orbitOffsets: [14, 16],
  });

  assert.equal(regions.length, 5);
  assert.deepEqual(regions.map((region) => region.label), [
    "−16 orbits",
    "−14 orbits",
    "Source",
    "+14 orbits",
    "+16 orbits",
  ]);

  const source = regions.find((region) => region.id === "source");
  assert.equal(source.startMet, 699999900);
  assert.equal(source.stopMet, 700000300);

  const pre14 = regions.find((region) => region.id === "pre-14");
  assert.ok(Math.abs(pre14.startMet - 699919572.0725665) < 1e-6);
  assert.ok(Math.abs(pre14.stopMet - 699919972.0725665) < 1e-6);
});

test("normalises comma-separated orbit values", () => {
  assert.deepEqual(parseOrbitOffsets("30, 14, 16, 14"), [14, 16, 30]);
});

test("rejects invalid ranges and orbit values", () => {
  assert.throws(() => calculateRegions({ triggerMet: 1, startOffset: 5, endOffset: 5 }), /smaller/);
  assert.throws(() => parseOrbitOffsets("14, zero"), /positive whole numbers/);
  assert.throws(() => parseOrbitOffsets("0, 14"), /positive whole numbers/);
});

test("formats values and exports stable CSV", () => {
  assert.equal(formatMet(10.5), "10.5");
  const csv = regionsToCsv([{ label: "Source", startMet: 10, stopMet: 20.25, shiftSeconds: 0 }]);
  assert.equal(csv, "region,start_met,stop_met,shift_seconds\nSource,10,20.25,0");
});
