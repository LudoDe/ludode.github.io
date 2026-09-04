const form = document.querySelector("#analysis-form");
const fileInput = document.querySelector("#fits-files");
const dropZone = document.querySelector("#drop-zone");
const fileList = document.querySelector("#file-list");
const runtimeStatus = document.querySelector("#runtime-status");
const runButton = document.querySelector("#run-analysis");
const errorBox = document.querySelector("#analysis-error");
const results = document.querySelector("#results");
const emptyResults = document.querySelector("#empty-results");
const resultSummary = document.querySelector("#result-summary");
const warningList = document.querySelector("#warning-list");
const detectorSelect = document.querySelector("#detector-select");
const plot = document.querySelector("#result-plot");
const plotTitle = document.querySelector("#plot-title");
const detectorMeta = document.querySelector("#detector-meta");
const downloadButton = document.querySelector("#download-results");
const intervalBody = document.querySelector("#interval-body");
const channelFirst = document.querySelector("#channel-first");
const channelLast = document.querySelector("#channel-last");
const spectrumType = document.querySelector("#spectrum-type");

const worker = new Worker("./worker.js");
let selectedFiles = [];
let activeResult = null;
let archiveUrl = null;
let requestId = 0;

const formatNumber = (value, digits = 3) => new Intl.NumberFormat("en-US", {
  maximumFractionDigits: digits,
  useGrouping: false,
}).format(value);

function setStatus(text, state = "idle") {
  runtimeStatus.textContent = text;
  runtimeStatus.dataset.state = state;
}

function setError(message = "") {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

function fileKey(file) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function addFiles(files) {
  const known = new Set(selectedFiles.map(fileKey));
  for (const file of files) {
    if (!known.has(fileKey(file))) {
      selectedFiles.push(file);
      known.add(fileKey(file));
    }
  }
  selectedFiles.sort((a, b) => a.name.localeCompare(b.name));
  renderFiles();
}

function renderFiles() {
  fileList.replaceChildren();
  if (!selectedFiles.length) {
    const empty = document.createElement("li");
    empty.className = "file-empty";
    empty.textContent = "No FITS files selected yet.";
    fileList.append(empty);
    return;
  }

  selectedFiles.forEach((file, index) => {
    const item = document.createElement("li");
    const details = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = file.name;
    const size = document.createElement("small");
    size.textContent = `${(file.size / 1024 / 1024).toFixed(1)} MB`;
    details.append(name, size);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "file-remove";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.addEventListener("click", () => {
      selectedFiles.splice(index, 1);
      renderFiles();
    });
    item.append(details, remove);
    fileList.append(item);
  });
}

function parseOrbits(value) {
  const values = String(value).split(",").map(part => Number(part.trim()));
  if (!values.length || values.some(value => !Number.isInteger(value) || value <= 0)) {
    throw new Error("Orbit offsets must be positive whole numbers separated by commas.");
  }
  return [...new Set(values)].sort((a, b) => a - b);
}

function readParams() {
  const data = new FormData(form);
  return {
    event_name: String(data.get("eventName") || "gbm-event"),
    trigger_met: Number(data.get("triggerMet")),
    start_offset: Number(data.get("startOffset")),
    stop_offset: Number(data.get("stopOffset")),
    orbit_period: Number(data.get("orbitPeriod")),
    orbit_offsets: parseOrbits(data.get("orbitOffsets")),
    spectrum_type: String(data.get("spectrumType") || "AUTO"),
    channel_first: Number(data.get("channelFirst")),
    channel_last: Number(data.get("channelLast")),
    recalculate_period: data.get("recalculatePeriod") === "on",
  };
}

function finiteInputs(params) {
  const fields = [
    [params.trigger_met, "Trigger MET"],
    [params.start_offset, "Start offset"],
    [params.stop_offset, "Stop offset"],
    [params.orbit_period, "Orbit period"],
    [params.channel_first, "First channel"],
    [params.channel_last, "Last channel"],
  ];
  for (const [value, label] of fields) {
    if (!Number.isFinite(value)) throw new Error(`${label} must be a number.`);
  }
  if (params.start_offset >= params.stop_offset) {
    throw new Error("Start offset must be smaller than stop offset.");
  }
  if (params.orbit_period <= 0) throw new Error("Orbit period must be greater than zero.");
  if (!Number.isInteger(params.channel_first) || !Number.isInteger(params.channel_last)) {
    throw new Error("Channel numbers must be whole numbers.");
  }
  if (params.channel_first < 0 || params.channel_last < params.channel_first) {
    throw new Error("Enter a valid inclusive channel range.");
  }
}

function updateChannelDefaults() {
  if (spectrumType.value === "CSPEC") {
    channelFirst.value = "10";
    channelLast.value = "119";
  } else if (spectrumType.value === "CTIME") {
    channelFirst.value = "1";
    channelLast.value = "6";
  }
}

function makeSvg(name, attrs = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
  return element;
}

function finiteSeries(series) {
  return series.filter(Number.isFinite);
}

function sampleIndices(length, maximum = 1200) {
  if (length <= maximum) return Array.from({ length }, (_, index) => index);
  const step = (length - 1) / (maximum - 1);
  return Array.from({ length: maximum }, (_, index) => Math.round(index * step));
}

function pathFor(x, y, xScale, yScale, indices) {
  let path = "";
  let started = false;
  for (const index of indices) {
    if (!Number.isFinite(x[index]) || !Number.isFinite(y[index])) {
      started = false;
      continue;
    }
    path += `${started ? "L" : "M"}${xScale(x[index]).toFixed(2)},${yScale(y[index]).toFixed(2)}`;
    started = true;
  }
  return path;
}

function renderPlot(detector) {
  const entry = activeResult.detector_results[detector];
  const data = entry.plot;
  plot.replaceChildren();
  plot.setAttribute("viewBox", "0 0 900 430");
  const width = 900;
  const height = 430;
  const margin = { top: 24, right: 24, bottom: 58, left: 74 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const xValues = finiteSeries(data.time);
  const yValues = finiteSeries([...data.source, ...data.background, ...data.residual]);
  const xMin = Math.min(...xValues);
  const xMax = Math.max(...xValues);
  let yMin = Math.min(...yValues);
  let yMax = Math.max(...yValues);
  if (yMin === yMax) {
    yMin -= 1;
    yMax += 1;
  }
  const yPad = (yMax - yMin) * 0.08;
  yMin -= yPad;
  yMax += yPad;
  const xScale = value => margin.left + ((value - xMin) / (xMax - xMin || 1)) * innerWidth;
  const yScale = value => margin.top + (1 - (value - yMin) / (yMax - yMin)) * innerHeight;

  const title = makeSvg("title");
  title.textContent = `Detector ${detector}: source, estimated background, and residual count rates`;
  plot.append(title);
  const grid = makeSvg("g", { class: "plot-grid" });
  for (let index = 0; index <= 4; index += 1) {
    const fraction = index / 4;
    const x = margin.left + fraction * innerWidth;
    const y = margin.top + fraction * innerHeight;
    grid.append(makeSvg("line", { x1: x, y1: margin.top, x2: x, y2: margin.top + innerHeight }));
    grid.append(makeSvg("line", { x1: margin.left, y1: y, x2: margin.left + innerWidth, y2: y }));
    const xLabel = makeSvg("text", { x, y: height - 28, "text-anchor": "middle" });
    xLabel.textContent = formatNumber(xMin + fraction * (xMax - xMin), 1);
    const yLabel = makeSvg("text", { x: margin.left - 12, y: y + 4, "text-anchor": "end" });
    yLabel.textContent = formatNumber(yMax - fraction * (yMax - yMin), 2);
    grid.append(xLabel, yLabel);
  }
  plot.append(grid);

  if (yMin < 0 && yMax > 0) {
    plot.append(makeSvg("line", {
      class: "plot-zero",
      x1: margin.left,
      x2: margin.left + innerWidth,
      y1: yScale(0),
      y2: yScale(0),
    }));
  }

  const indices = sampleIndices(data.time.length);
  [
    ["source", "plot-source"],
    ["background", "plot-background"],
    ["residual", "plot-residual"],
  ].forEach(([key, className]) => {
    plot.append(makeSvg("path", {
      class: `plot-line ${className}`,
      d: pathFor(data.time, data[key], xScale, yScale, indices),
    }));
  });

  const xAxis = makeSvg("text", { class: "plot-label", x: margin.left + innerWidth / 2, y: height - 4, "text-anchor": "middle" });
  xAxis.textContent = "Time since trigger (s)";
  const yAxis = makeSvg("text", {
    class: "plot-label",
    x: 18,
    y: margin.top + innerHeight / 2,
    transform: `rotate(-90 18 ${margin.top + innerHeight / 2})`,
    "text-anchor": "middle",
  });
  yAxis.textContent = "Count rate (counts s⁻¹)";
  plot.append(xAxis, yAxis);

  plotTitle.textContent = `Detector ${detector}`;
  const used = entry.used_regions.map(region => region.replace("pre", "−").replace("pos", "+")).join(", ");
  detectorMeta.textContent = `${entry.bins.toLocaleString()} bins · ${entry.channels} channels · ${entry.quality_flagged.toLocaleString()} flagged bins · regions ${used}`;
}

function renderIntervals(regionData) {
  intervalBody.replaceChildren();
  regionData.forEach(region => {
    const row = document.createElement("tr");
    const label = region.name === "src"
      ? "Source"
      : region.name.replace("pre", "−").replace("pos", "+") + " orbits";
    [label, formatNumber(region.start, 6), formatNumber(region.stop, 6)].forEach(value => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    intervalBody.append(row);
  });
}

function renderResult(result, archive) {
  activeResult = result;
  detectorSelect.replaceChildren();
  Object.keys(result.detector_results).forEach(detector => {
    const option = document.createElement("option");
    option.value = detector;
    option.textContent = detector;
    detectorSelect.append(option);
  });
  const detectorCount = Object.keys(result.detector_results).length;
  const periodNote = result.period_recalculated ? "recalculated from POSHIST" : "configured";
  resultSummary.textContent = `${detectorCount} detector${detectorCount === 1 ? "" : "s"} analysed as ${result.spectrum_type} at ${formatNumber(result.resolution_seconds)} s resolution. Orbit period: ${formatNumber(result.orbit_period_seconds, 6)} s (${periodNote}).`;

  warningList.replaceChildren();
  const notices = [...result.warnings];
  if (result.ignored_files.length) notices.push(`Ignored files: ${result.ignored_files.join(", ")}`);
  warningList.hidden = !notices.length;
  notices.forEach(notice => {
    const item = document.createElement("li");
    item.textContent = notice;
    warningList.append(item);
  });
  renderIntervals(result.regions);
  renderPlot(detectorSelect.value);

  if (archiveUrl) URL.revokeObjectURL(archiveUrl);
  archiveUrl = URL.createObjectURL(new Blob([archive], { type: "application/zip" }));
  downloadButton.download = result.archive_name;
  downloadButton.href = archiveUrl;
  emptyResults.hidden = true;
  results.hidden = false;
  results.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function runAnalysis() {
  setError();
  if (!selectedFiles.length) {
    setError("Choose the CTIME or CSPEC FITS files needed for the source and background windows.");
    fileInput.focus();
    return;
  }

  let params;
  try {
    params = readParams();
    finiteInputs(params);
  } catch (error) {
    setError(error.message);
    return;
  }

  const totalBytes = selectedFiles.reduce((sum, file) => sum + file.size, 0);
  if (totalBytes > 750 * 1024 * 1024) {
    setError("The selected files exceed 750 MB. Run fewer detectors at a time.");
    return;
  }

  runButton.disabled = true;
  form.setAttribute("aria-busy", "true");
  setStatus("Reading local files…", "busy");
  requestId += 1;
  const files = await Promise.all(selectedFiles.map(async file => ({
    name: file.name,
    buffer: await file.arrayBuffer(),
  })));
  worker.postMessage(
    { type: "analyse", requestId, params, files },
    files.map(file => file.buffer),
  );
}

worker.addEventListener("message", event => {
  const message = event.data;
  if (message.type === "status") {
    setStatus(message.message, "busy");
    return;
  }
  if (message.requestId !== requestId) return;
  runButton.disabled = false;
  form.removeAttribute("aria-busy");
  if (message.type === "error") {
    setStatus("Ready to try again", "error");
    setError(message.message.replace(/^AnalysisError:\s*/u, ""));
    return;
  }
  if (message.type === "result") {
    setStatus("Analysis complete", "ready");
    renderResult(message.result, message.archive);
  }
});

worker.addEventListener("error", event => {
  runButton.disabled = false;
  form.removeAttribute("aria-busy");
  setStatus("Runtime error", "error");
  setError(event.message || "The scientific runtime could not start.");
});

form.addEventListener("submit", event => {
  event.preventDefault();
  runAnalysis();
});
fileInput.addEventListener("change", () => {
  addFiles(fileInput.files);
  fileInput.value = "";
});
spectrumType.addEventListener("change", updateChannelDefaults);
detectorSelect.addEventListener("change", () => renderPlot(detectorSelect.value));

["dragenter", "dragover"].forEach(type => dropZone.addEventListener(type, event => {
  event.preventDefault();
  dropZone.classList.add("is-dragging");
}));
["dragleave", "drop"].forEach(type => dropZone.addEventListener(type, event => {
  event.preventDefault();
  dropZone.classList.remove("is-dragging");
}));
dropZone.addEventListener("drop", event => addFiles(event.dataTransfer.files));

window.addEventListener("beforeunload", () => {
  if (archiveUrl) URL.revokeObjectURL(archiveUrl);
  worker.terminate();
});

renderFiles();
