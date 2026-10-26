#!/usr/bin/env python3
"""
Build the propcalc website into ../site (a folder of static files you can upload to any static host).

    python3 web/build_site.py                       # asks for the access phrase
    python3 web/build_site.py --passphrase "..."    # phrase on the command line (or env PROPCALC_PASSPHRASE)
    python3 web/build_site.py --open                # no phrase: anyone with the link can use it

The calculator code and the whole data folder are packed into site/bundle.bin and encrypted with the phrase
(AES-256-GCM, key from PBKDF2-SHA256). Without the phrase the bundle cannot be read, even by downloading it.
Needs: pip3 install cryptography   (not needed with --open)

Test locally:   python3 -m http.server 8000 -d site     then open http://localhost:8000
Re-run this script whenever you change propcalc.py or anything in data/, then upload the site folder again.
"""
import argparse
import getpass
import hashlib
import io
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
STATIC = ["index.html", "style.css", "app.js", "worker.js", "favicon.svg"]
STAMPED = {"index.html", "app.js"}  # files whose __BUILD__ placeholder becomes the build id
SKIP_DATA = {"all_motor.txt", ".DS_Store"}
ITERATIONS = 310000
SHARE_SALT, SHARE_ITER = b"propcalc-share-v1", 100000  # must match app.js


def share_token_hash(phrase):
    """The page derives a token from the phrase and sends it with every shared-library request;
    the server only knows SHA-256(token)."""
    if not phrase:
        return ""
    tok = hashlib.pbkdf2_hmac("sha256", phrase.encode(), SHARE_SALT, SHARE_ITER, 32).hex()
    return hashlib.sha256(tok.encode()).hexdigest()


def bundle_zip(data_dir):
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(os.path.join(BASE, "propcalc.py"), "propcalc.py")
        z.write(os.path.join(HERE, "webapi.py"), "webapi.py")
        for dp, dirs, files in os.walk(data_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in sorted(files):
                if f in SKIP_DATA or f.startswith("."):
                    continue
                full = os.path.join(dp, f)
                z.write(full, os.path.join("data", os.path.relpath(full, data_dir)))
                n += 1
    return buf.getvalue(), n


def encrypt(data, phrase):
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError:
        sys.exit("The 'cryptography' package is needed to encrypt the site:  pip3 install cryptography\n"
                 "(or build without a phrase using --open)")
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(phrase.encode())
    return b"PCB1" + ITERATIONS.to_bytes(4, "big") + salt + iv + AESGCM(key).encrypt(iv, data, None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--passphrase", help="access phrase (default: asked, or env PROPCALC_PASSPHRASE)")
    ap.add_argument("--open", action="store_true", help="no access phrase")
    ap.add_argument("--data", default=os.path.join(BASE, "data_2"), help="data folder to publish")  # same default as propcalc.py
    ap.add_argument("--out", default=os.path.join(BASE, "site"), help="output folder")
    ap.add_argument("--no-sharing", action="store_true",
                    help="leave out the shared-library server part (_worker.js) - for drag-and-drop uploads")
    a = ap.parse_args()

    phrase = None
    if not a.open:
        phrase = a.passphrase or os.environ.get("PROPCALC_PASSPHRASE")
        if not phrase:
            phrase = getpass.getpass("Access phrase for the site: ")
            if phrase != getpass.getpass("Repeat it: "):
                sys.exit("The phrases do not match.")
        if len(phrase) < 6:
            sys.exit("Use at least 6 characters (a few words is best, e.g. 'crispy wings fly far').")

    data, n_files = bundle_zip(a.data)
    blob = encrypt(data, phrase) if phrase else b"PCB0" + data

    # build id for the asset URLs (app.js?v=..., worker.js?v=..., bundle.bin?v=...): a new deploy gets new URLs, so no
    # browser or CDN cache can pair the new page with an old script or bundle
    h = hashlib.sha256(blob)
    for f in STATIC:
        h.update(open(os.path.join(HERE, f), "rb").read())
    build = h.hexdigest()[:12]

    if os.path.isdir(a.out):
        shutil.rmtree(a.out)
    os.makedirs(a.out)
    for f in STATIC:
        if f in STAMPED:
            txt = open(os.path.join(HERE, f), encoding="utf-8").read()
            with open(os.path.join(a.out, f), "w", encoding="utf-8") as fo:
                fo.write(txt.replace("__BUILD__", build))
        else:
            shutil.copy2(os.path.join(HERE, f), os.path.join(a.out, f))
    shutil.copytree(os.path.join(HERE, "vendor"), os.path.join(a.out, "vendor"))
    with open(os.path.join(a.out, "bundle.bin"), "wb") as f:
        f.write(blob)
    if not a.no_sharing:
        w = open(os.path.join(HERE, "_worker.js"), encoding="utf-8").read()
        with open(os.path.join(a.out, "_worker.js"), "w", encoding="utf-8") as f:
            f.write(w.replace("__TOKEN_SHA256__", share_token_hash(phrase)))
        with open(os.path.join(a.out, "_routes.json"), "w") as f:  # only /api/* runs server code; files are served directly
            f.write('{"version": 1, "include": ["/api/*"], "exclude": []}\n')
    with open(os.path.join(a.out, "_headers"), "w") as f:  # Cloudflare Pages / Netlify caching rules
        f.write("/bundle.bin\n  Cache-Control: no-cache\n/app.js\n  Cache-Control: no-cache\n"
                "/worker.js\n  Cache-Control: no-cache\n/index.html\n  Cache-Control: no-cache\n"
                "/vendor/*\n  Cache-Control: public, max-age=604800\n/*\n  X-Robots-Tag: noindex\n")
    with open(os.path.join(a.out, "robots.txt"), "w") as f:
        f.write("User-agent: *\nDisallow: /\n")

    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(a.out) for f in fs)
    print(f"Built {a.out}  (build {build})")
    print(f"  bundle: propcalc.py + {n_files} data files, {len(blob) / 1024:.0f} kB "
          f"({'encrypted with your phrase' if phrase else 'NOT encrypted - open to anyone with the link'})")
    print(f"  total site size {size / 1e6:.1f} MB")
    print("\nTest it:   python3 -m http.server 8000 -d " + os.path.relpath(a.out) + "   ->  http://localhost:8000")
    if a.no_sharing:
        print("Publish:   drag the site folder into Cloudflare Pages (no shared library) - see web/WEB.md")
    else:
        print("Publish:   cd web && npx wrangler pages deploy      (shared library included - see web/WEB.md)")


if __name__ == "__main__":
    main()
