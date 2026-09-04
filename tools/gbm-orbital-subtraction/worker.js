const PYODIDE_VERSION = "0.28.1";
const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

let runtimePromise;

function sendStatus(message) {
  self.postMessage({ type: "status", message });
}

async function getRuntime() {
  if (!runtimePromise) {
    runtimePromise = (async () => {
      sendStatus("Loading Python runtime…");
      importScripts(`${PYODIDE_BASE}pyodide.js`);
      const runtime = await loadPyodide({ indexURL: PYODIDE_BASE });
      sendStatus("Loading NumPy and Astropy…");
      await runtime.loadPackage(["numpy", "astropy"]);
      const response = await fetch("./orbital_core.py", { cache: "no-cache" });
      if (!response.ok) {
        throw new Error(`Could not load the analysis core (${response.status}).`);
      }
      await runtime.runPythonAsync(await response.text());
      sendStatus("Scientific runtime ready.");
      return runtime;
    })().catch((error) => {
      runtimePromise = undefined;
      throw error;
    });
  }
  return runtimePromise;
}

function safeName(name, index) {
  const cleaned = name.replace(/[^A-Za-z0-9._-]+/gu, "-").replace(/^-+|-+$/gu, "");
  return `${String(index).padStart(3, "0")}-${cleaned || "input.fits"}`;
}

function removeQuietly(runtime, path) {
  try {
    runtime.FS.unlink(path);
  } catch {
    // A best-effort cleanup must not hide a completed analysis.
  }
}

async function analyse(message) {
  const runtime = await getRuntime();
  const runId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const runDirectory = `/tmp/osv-${runId}`;
  const outputDirectory = `${runDirectory}/output`;
  runtime.FS.mkdirTree(outputDirectory);
  const paths = [];

  try {
    sendStatus(`Copying ${message.files.length} FITS file${message.files.length === 1 ? "" : "s"}…`);
    message.files.forEach((file, index) => {
      const path = `${runDirectory}/${safeName(file.name, index)}`;
      runtime.FS.writeFile(path, new Uint8Array(file.buffer));
      paths.push({ name: file.name, path });
    });

    runtime.globals.set("_osv_params_json", JSON.stringify(message.params));
    runtime.globals.set("_osv_files_json", JSON.stringify(paths));
    runtime.globals.set("_osv_output_dir", outputDirectory);
    sendStatus("Running orbital subtraction…");
    const resultJson = await runtime.runPythonAsync(
      "run_analysis(_osv_params_json, _osv_files_json, _osv_output_dir)",
    );
    const result = JSON.parse(resultJson);
    const archive = runtime.FS.readFile(result.archive_path).slice();
    removeQuietly(runtime, result.archive_path);
    delete result.archive_path;
    self.postMessage(
      { type: "result", requestId: message.requestId, result, archive: archive.buffer },
      [archive.buffer],
    );
  } finally {
    runtime.globals.delete("_osv_params_json");
    runtime.globals.delete("_osv_files_json");
    runtime.globals.delete("_osv_output_dir");
    paths.forEach(({ path }) => removeQuietly(runtime, path));
    try {
      runtime.FS.rmdir(outputDirectory);
      runtime.FS.rmdir(runDirectory);
    } catch {
      // Pyodide will reclaim temporary files when the worker is closed.
    }
  }
}

self.addEventListener("message", async (event) => {
  if (event.data?.type !== "analyse") return;
  try {
    await analyse(event.data);
  } catch (error) {
    const rawMessage = error?.message || String(error);
    const marker = "AnalysisError:";
    const message = rawMessage.includes(marker)
      ? rawMessage.slice(rawMessage.lastIndexOf(marker) + marker.length).trim()
      : rawMessage.split("\n").filter(Boolean).at(-1) || rawMessage;
    self.postMessage({
      type: "error",
      requestId: event.data.requestId,
      message,
    });
  }
});
