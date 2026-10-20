// Runs Python (Pyodide) off the main thread so the page stays responsive during a search.
importScripts("vendor/pyodide/pyodide.js");

let py = null, api = null;
const post = (m) => self.postMessage(m);

const ready = (async () => {
  py = await loadPyodide({
    indexURL: new URL("vendor/pyodide/", self.location.href).href,
    stdout: (line) => post({ type: "log", line }),
    stderr: (line) => post({ type: "log", line }),
  });
  post({ type: "pyready" });
})();

function call(name, ...args) {
  const out = api[name](...args);
  return JSON.parse(out);
}

function writeFiles(files, root) {
  // files: [{path: "1208/static.csv", data: ArrayBuffer}]
  for (const f of files) {
    const parts = f.path.split("/").filter(Boolean);
    let dir = root;
    for (const p of parts.slice(0, -1)) {
      dir += "/" + p;
      try { py.FS.mkdir(dir); } catch (e) { /* exists */ }
    }
    py.FS.writeFile(dir + "/" + parts[parts.length - 1], new Uint8Array(f.data));
  }
}

self.onmessage = async (ev) => {
  const { id, cmd, args = {} } = ev.data;
  try {
    await ready;
    let result;
    switch (cmd) {
      case "load": {
        py.FS.writeFile("/tmp/bundle.zip", new Uint8Array(args.zip));
        py.runPython(`
import zipfile, sys, shutil, os
shutil.rmtree("/work", ignore_errors=True)
zipfile.ZipFile("/tmp/bundle.zip").extractall("/work")
if "/work" not in sys.path:
    sys.path.insert(0, "/work")
`);
        py.globals.set("js_progress", (stage, i, n) => post({ type: "progress", stage, i, n }));
        py.runPython(`
import importlib, propcalc, webapi
importlib.reload(propcalc); importlib.reload(webapi)
propcalc.PROGRESS = js_progress
`);
        api = py.pyimport("webapi");
        result = call("info");
        break;
      }
      case "run": result = call("run", JSON.stringify(args)); break;
      case "single": result = call("single", JSON.stringify(args)); break;
      case "save_settings": result = call("save_settings", JSON.stringify(args)); break;
      case "upsert_row": result = call("upsert_row", args.kind, JSON.stringify(args.row), args.old_name || null); break;
      case "delete_row": result = call("delete_row", args.kind, args.name); break;
      case "motor_search": result = call("motor_search", args.q); break;
      case "estimate": result = call("estimate", args.kv, args.mass_g); break;
      case "info": result = call("info"); break;
      case "entry_payload": result = call("entry_payload", args.kind, JSON.stringify(args.names)); break;
      case "apply_shared": result = call("apply_shared", JSON.stringify(args.entries)); break;
      case "remove_shared": result = call("remove_shared", args.kind, args.name); break;
      case "builtin_names": result = call("builtin_names"); break;
      case "add_prop_file": {
        py.FS.writeFile("/tmp/upload.bin", new Uint8Array(args.data));
        result = call("add_prop_file", args.filename, "/tmp/upload.bin", JSON.stringify(args.meta || {}));
        break;
      }
      case "replace_file": {
        py.FS.writeFile("/tmp/upload.bin", new Uint8Array(args.data));
        result = call("replace_file", args.name, "/tmp/upload.bin");
        break;
      }
      case "import_tunnel": {
        py.runPython(`import shutil; shutil.rmtree("/work/upload_tunnel", ignore_errors=True); import os; os.makedirs("/work/upload_tunnel")`);
        writeFiles(args.files, "/work/upload_tunnel");
        result = call("import_tunnel", "/work/upload_tunnel", args.rho, args.min_rpm, args.mass_g);
        break;
      }
      case "export_zip": {
        const r = call("export_zip");
        if (r.error) { result = r; break; }
        const bytes = py.FS.readFile(r.path);
        self.postMessage({ id, ok: true, result: { bytes: bytes.buffer } }, [bytes.buffer]);
        return;
      }
      default: throw new Error("unknown command " + cmd);
    }
    post({ id, ok: true, result });
  } catch (e) {
    post({ id, ok: false, error: String(e && e.message ? e.message : e) });
  }
};
