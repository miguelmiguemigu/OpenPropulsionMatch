# propcalc website – build, publish, update

The website is the same calculator as `propcalc.py`, running **inside the visitor's browser** (Python compiled to
WebAssembly, "Pyodide"). There is no server and no database, so it can be hosted for free as plain files.

- **Access phrase:** the calculator code and the whole `data_2/` folder are packed into one file (`bundle.bin`) and
  encrypted with your phrase (AES-256). Without the phrase, the files on the server are unreadable.
- **Team library:** motors, props (with their data files), ESCs and batteries that anyone adds or edits on the site can
  be shared. They are saved in a small free Cloudflare database (D1) and everyone sees them the next time they open the site.
  Saving needs the same access phrase. Deletions are logged and can be undone.
- **Everything else is temporary:** plane and settings changes stay in the visitor's browser tab until they reload. The "Download data folder" button saves everything, including the shared items, as a zip.
- **Speed:** the first visit downloads ~14 MB (Python), after that it's cached. A full search takes ~30–60 s.

---

## 1. Build the site (on your Mac, from the `propcalc` folder)

```bash
pip3 install cryptography          # once
python3 web/fetch_vendor.py        # once: downloads vendor/ (Pyodide, Chart.js)
python3 web/build_site.py          # asks for the access phrase twice
```

This creates the `site/` folder, which is everything you upload.

- **Phrase:** pick 3–4 random words, e.g. `crispy wings fly far`. Anyone with the phrase can see all the data in it.
- **No phrase:** `python3 web/build_site.py --open` builds an open site anyone with the link can use.
- **Rebuild** whenever you change `propcalc.py` or anything in `data_2/`.

**Test it locally before uploading:**
```bash
python3 -m http.server 8000 -d site
```
Open http://localhost:8000 and press Ctrl+C in the terminal to stop.

Opening `site/index.html` by double-clicking does **not** work; it needs to be served as above.

---

## 2. Publish on Cloudflare Pages (free)

There are two ways to publish:
- **With the team library (2a):** uses the command line. Cloudflare's drag-and-drop upload can't run the small server part that saves shared components.
- **Without it (2b):** drag and drop. The site works the same, but additions only last in each person's tab.

### 2a. With the team library (recommended)

**One-time setup**
1. Install **Node.js** (LTS) from https://nodejs.org. This gives you the `npx` command.
2. Create a free Cloudflare account at https://dash.cloudflare.com/sign-up.
3. In a terminal, from the `propcalc` folder:
   ```bash
   cd web
   npx wrangler login                          # opens the browser to log in to Cloudflare
   npx wrangler d1 create propcalc-shared      # creates the database and prints a database_id
   ```
4. Open `web/wrangler.toml` and paste that id in place of `PASTE-YOUR-DATABASE-ID-HERE`.
   - If you want a different site name than `propcalc`, change `name` too.

**Publish (and every update)**
```bash
python3 web/build_site.py             # from the propcalc folder
cd web && npx wrangler pages deploy   # creates the project the first time, then updates it
cd ..
```

Your site is at `https://propcalc.pages.dev`. The team library starts empty and survives every redeploy.

**What the free plan covers:** 100,000 database writes and 5 million reads per day, and 5 GB of storage. If a daily limit is reached, saving simply stops until the next day; nothing is charged. Normal page loads don't count toward those limits.

**Back up the team library:**
```bash
cd web && npx wrangler d1 export propcalc-shared --remote --output team-library-backup.sql
```

**Make shared items permanent** (part of the built-in library):
1. On the site, press **Download data folder**.
2. Replace `propcalc/data_2` with the folder from the zip, then rebuild and deploy.

A shared item with the same name as a built-in one overrides it. You can leave shared items in the library or delete them after merging.

### Automatic deploy from GitHub

`.github/workflows/deploy-site.yml` rebuilds and deploys the site on every push / merge to `main`
(and from the **Actions → Deploy website → Run workflow** button). It downloads `vendor/` with
`web/fetch_vendor.py`, runs `build_site.py` and `wrangler pages deploy`. The team library is kept.

**One-time setup**
1. Do steps 1-4 of the one-time setup above (the real `database_id` must be in `web/wrangler.toml` and committed).
2. Create the Pages project once, if it doesn't exist yet:
   `cd web && npx wrangler pages project create propcalc --production-branch main`
3. In Cloudflare: **My Profile → API Tokens → Create Token → Custom token** with the permissions
   **Account → Cloudflare Pages → Edit** and **Account → D1 → Edit**. Copy the token.
4. Your **Account ID** is on the Cloudflare dashboard under **Workers & Pages** (right-hand side).
5. On GitHub: repo → **Settings → Secrets and variables → Actions → New repository secret**, add:
   - `PROPCALC_PASSPHRASE` - the site's access phrase
   - `CLOUDFLARE_API_TOKEN` - the token from step 3
   - `CLOUDFLARE_ACCOUNT_ID` - the id from step 4

Changing the phrase: update the `PROPCALC_PASSPHRASE` secret and re-run the workflow.

### 2b. Without the team library (drag and drop)

Build with `python3 web/build_site.py --no-sharing`, then:
1. Create a free account at https://dash.cloudflare.com/sign-up.
2. Go to **Workers & Pages** → **Create application** → **Get started** → **Drag and drop your files**.
3. Enter a project name, e.g. `propcalc`. It becomes `https://propcalc.pages.dev`.
4. Drag the **`site` folder** in → **Deploy site**.
5. Open the `.pages.dev` link and check that the phrase works.

**Updating later**
1. Rebuild with `python3 web/build_site.py --no-sharing`.
2. In Cloudflare: open the project → **Create a new deployment** → **Production**.
3. Drag the `site` folder in again.

Old deployments stay listed there, so you can roll back.

**Updating from the terminal instead** (needs Node.js):
```bash
npx wrangler pages deploy site --project-name propcalc
```

The site has about 15 files and the largest is ~10 MB, well inside the free limits (1,000 files and 25 MB per file for drag-and-drop).

---

## 3. Use your own domain

A **subdomain** like `calc.yourdomain.com` is easiest and works wherever your domain is registered:

1. In Cloudflare: your Pages project → **Custom domains** → **Set up a domain**.
2. Enter `calc.yourdomain.com` → **Continue**. Do this step first, or the domain shows an error.
3. At the company where your domain's DNS is managed, add a record:
   - Type: `CNAME`
   - Name: `calc`
   - Target: `propcalc.pages.dev`
4. Wait a few minutes to an hour. HTTPS is set up automatically.

The **bare domain** (`yourdomain.com` without a prefix) only works if the domain's nameservers point to Cloudflare. That is free: add the domain to Cloudflare and change the nameservers at your registrar. Cloudflare then creates the record itself.

If your domain is already on Cloudflare, step 3 happens automatically.

---

## Other free hosts

The `site` folder (built with `--no-sharing`) works on any static host that uses https. These hosts have no team library:
- **Netlify:** drag the folder onto https://app.netlify.com/drop.
- **GitHub Pages:** on a free account the repository must be public. Only put the built `site` folder in it. `bundle.bin` is encrypted, but the raw `data/` folder is not.

The `_headers` file in `site/` sets caching on Cloudflare and Netlify. Other hosts ignore it.

---

## Security notes

- The protection is only as strong as the phrase. A long phrase of random words is much better than a short password.
- **Changing the phrase:** rebuild with a new phrase and upload again. Old copies people downloaded still open with the old phrase.
- **"Remember on this device"** saves the phrase in that browser's storage. Only tick it on your own devices.
- The page tells search engines not to index it (`robots.txt`, `noindex`).
- **Team library:** the site only accepts saves from browsers that know the phrase. The page sends a key derived from the phrase, and the server stores only a hash of that key. Each shared item records the name the person typed; there are no individual accounts.
- **Changing the phrase:** rebuild and deploy. The team library stays as it is.

## What's in `web/`

| file | what |
|---|---|
| `build_site.py` | builds `site/` (encrypts the bundle) |
| `_worker.js` | the server part of the team library (Cloudflare), copied into `site/` with the phrase check |
| `wrangler.toml` | Cloudflare settings: site name and the D1 database id |
| `index.html`, `style.css`, `app.js` | the page |
| `worker.js` | runs Python in the background so the page stays responsive |
| `webapi.py` | connects the page to `propcalc.py` |
| `fetch_vendor.py` | downloads `vendor/` (checked by SHA-256) |
| `vendor/` | not in git - Pyodide 0.26.4 (Python for the browser, MPL-2.0) and Chart.js 4.4.1 (MIT), served from your own site |
