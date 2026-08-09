import { calculateRegions, formatMet, parseOrbitOffsets, regionsToCsv } from "./calculator.js";

const form = document.querySelector("#orbit-form");
const errorMessage = document.querySelector("#form-error");
const resultBody = document.querySelector("#result-body");
const resultSummary = document.querySelector("#result-summary");
const copyButton = document.querySelector("#copy-csv");

let currentRegions = [];

function readForm() {
  const data = new FormData(form);
  return {
    triggerMet: data.get("triggerMet"),
    startOffset: data.get("startOffset"),
    endOffset: data.get("endOffset"),
    orbitalPeriod: data.get("orbitalPeriod"),
    orbitOffsets: parseOrbitOffsets(data.get("orbitOffsets")),
  };
}

function addCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  row.append(cell);
}

function render(regions) {
  resultBody.replaceChildren();

  for (const region of regions) {
    const row = document.createElement("tr");
    addCell(row, region.label);
    addCell(row, formatMet(region.startMet));
    addCell(row, formatMet(region.stopMet));
    addCell(row, formatMet(region.shiftSeconds));
    resultBody.append(row);
  }

  const source = regions.find((region) => region.id === "source");
  resultSummary.textContent = `${regions.length} intervals calculated. Source duration: ${formatMet(source.stopMet - source.startMet, 3)} s.`;
  currentRegions = regions;
}

function calculate() {
  try {
    render(calculateRegions(readForm()));
    errorMessage.hidden = true;
    errorMessage.textContent = "";
  } catch (error) {
    errorMessage.textContent = error.message;
    errorMessage.hidden = false;
  }
}

async function copyCsv() {
  if (!currentRegions.length) return;
  const csv = regionsToCsv(currentRegions);

  try {
    await navigator.clipboard.writeText(csv);
    copyButton.textContent = "Copied";
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = csv;
    textarea.className = "visually-hidden";
    document.body.append(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
    copyButton.textContent = "Copied";
  }

  window.setTimeout(() => {
    copyButton.textContent = "Copy CSV";
  }, 1600);
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  calculate();
});

form.addEventListener("reset", () => {
  window.setTimeout(calculate, 0);
});

copyButton.addEventListener("click", copyCsv);
calculate();
