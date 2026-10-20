#!/usr/bin/env python3
"""
Download the browser libraries into web/vendor/ (not kept in git - 13 MB).

    python3 web/fetch_vendor.py

Pyodide 0.26.4 (MPL-2.0) and Chart.js 4.4.1 (MIT) from jsDelivr. Every file is checked against its SHA-256,
so the site always ships exactly the versions it was tested with. Files already present and correct are skipped.
"""
import hashlib
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/"
FILES = {  # path inside vendor/: (url, sha256)
    "chart.umd.js": ("https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.js",
                     "74401d738dd3e03ee5dfb3b6841210fe2c4ead8a960c4011ca4ba0b78a9fd8f3"),
    "pyodide/pyodide.js": (PYODIDE + "pyodide.js",
                           "c0069107621d5b942a659e737a12e774cc0451feaa2256f475d72e071d844ec7"),
    "pyodide/pyodide.asm.js": (PYODIDE + "pyodide.asm.js",
                               "919560652ed3dad3707cb3a394785da1e046fb13dc0defa162058ff230cb7eed"),
    "pyodide/pyodide.asm.wasm": (PYODIDE + "pyodide.asm.wasm",
                                 "b7e66a19427a55010ac3367c1b6c64b893f9826f783412945fdf0c3337f3bc94"),
    "pyodide/python_stdlib.zip": (PYODIDE + "python_stdlib.zip",
                                  "72894522b791858b9d613ac786b951d8b5094035dcf376313ea24a466810f336"),
    "pyodide/pyodide-lock.json": (PYODIDE + "pyodide-lock.json",
                                  "cd50b49de944c579045e122fe8628b31f9ce446379f032f36c05e273d38766e0"),
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    for rel, (url, digest) in FILES.items():
        path = os.path.join(HERE, "vendor", rel)
        if os.path.exists(path) and sha256(open(path, "rb").read()) == digest:
            print(f"  ok          {rel}")
            continue
        with urllib.request.urlopen(url, timeout=60) as r:
            data = r.read()
        if sha256(data) != digest:
            sys.exit(f"SHA-256 mismatch for {url} - not saved")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        print(f"  downloaded  {rel} ({len(data) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
