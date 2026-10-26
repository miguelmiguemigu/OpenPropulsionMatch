# propcalc – quick guide

Finds the best **motor + prop + ESC + battery** for SAE Aero Design Micro Class. It tries every combination from your data files, ranks them and draws graphs.

---

## 1. Setup (once)

```bash
pip3 install numpy matplotlib     # only needed for the graphs
cd propcalc
```

Always run the commands **from inside the `propcalc` folder**. The output folders (`--out`) are created where you run it.

---

## 2. The two everyday commands

**Propulsion only (no plane needed)**: "which setup pulls hardest?"
```bash
python3 propcalc.py --no-plane --metric net_thrust --max-current 80
```

**With the plane**: "which setup lets us carry the most payload / get the best score?"
```bash
python3 propcalc.py --max-current 80
```

Both print a top-10 table and save everything to `results/`.

| Option | What it does |
|---|---|
| `--metric` | Ranking without a plane: `net_thrust` (thrust minus propulsion weight, **recommended**), `avg_thrust` (0–12 m/s average), `static_thrust`, `thrust_per_watt` |
| `--max-current 80` | Caps battery current at 80 A for every combo, so motors are compared fairly. Use the value your ESC limit will be set to. |
| `--out NAME` | Save results to a different folder, so you don't overwrite earlier runs |
| `--top 20` | Show and plot more combos |
| `--no-plots` | Faster, table only |
| `--two-blade-only` | Leave the 3-blade props (APC 11x7E-3, 12x7E-3) out. Their calibration comes from 2-blade bench tests and is unverified. Set `"multi_blade_props": "exclude"` in `config.json` to make it permanent |
| `--limit-mode reject` | Only keep combos that never exceed any rating (no ESC current limit needed) |

---

## 3. Reading the results

**Console table**

| Column | Meaning |
|---|---|
| static | thrust at 0 m/s in grams-force (gf) |
| @10m/s | thrust at 10 m/s in gf |
| peak A | highest battery current |
| mass | propulsion mass in g (motor + prop + ESC + battery) |
| payload / score | (plane mode only) predicted internal payload and flight score |

Lines starting with `!` are warnings:
- `needs current limit 79 A (motor rating; would draw 95 A)` means it only works if the ESC current limit is set. The model
  keeps the motor current within the motor/ESC rating and the battery current within the pack rating.
- `motor Rm/I0 estimated` means the motor came from the rcplanes list. Check its datasheet before buying.
- `motor current up to … (rated …)` only appears with `--limit-mode ignore`: the motor would run above its rating.

**Files in `results/`**

| File | Shows |
|---|---|
| `results.csv` | every combination with all numbers; open it in Excel / Sheets and sort as you like |
| `ranking.png` | the top N and how far behind #1 each one is |
| `thrust_curves.png` | thrust and current vs airspeed for the top 5 |
| `thrust_vs_mass.png` (no plane) / `payload_vs_current.png` (plane) | every combo as a dot, to see the trade-off |
| `tds_power_curves.png` (plane) | thrust available vs drag with 1/2/3 drag articles (the graph for the TDS) |

---

## 4. Checking one specific setup

```bash
python3 propcalc.py --single "X2820 1250kV" "APC 12x8E" "Sequre SQESC 2670 70A" "Tattu R-Line 850 150C"
```
Prints RPM, thrust, current, voltage and power at each airspeed. The names must match the `name` column in the data files **exactly** (copy them from the CSVs).

---

## 5. Changing the inputs (`data_2/` folder)

All files are plain CSV/JSON. Edit them in Excel, Numbers or a text editor.

| File | Edit when… |
|---|---|
| `plane.json` | **you have a real airframe design**: wing area, CLmax, CD0, empty mass, drag articles, cruise speed. The current values are only an example, so payload numbers are rough until you replace them. |
| `motors.csv` | adding a motor: Kv, Rm (mΩ), I0 (A @ volts), mass, max current. Put `no` in `include` to skip one. |
| `props.csv` | turning props on/off (`include` = `no`), correcting a prop's mass |
| `escs.csv` | adding an ESC: continuous / burst current, mass. Check the values against the ESC you actually use. |
| `batteries.csv` | adding a pack. If you can, put the measured internal resistance (charger IR reading, whole pack, mΩ) in `r_mohm`. |
| `config.json` | rules (cells, mAh, prop size), air density, a permanent current cap (`model.max_system_current_a`) |

**Drag articles** are set in `plane.json` → `drag_articles`: `count`, `mass_kg_each` and `CdA_m2_each` (drag area). Currently 3 × Rubber Chicken (0.68 kg, 0.015 m²).

**Tip:** to compare the airframe at different settings, copy `plane.json` to e.g. `plane_2waffles.json`, change it, and run
`python3 propcalc.py --plane data_2/plane_2waffles.json --out results_2waffles`.

---

## 6. Importing new data (only when you get new data)

**New wind-tunnel / thrust-stand logs** (RCbenchmark / Tyto CSV, one sub-folder per prop, e.g. `1208` = 12×8):
```bash
python3 propcalc.py --import-tunnel "Wind tunnel measurements 2024"
```

**UIUC propeller database** (not in `data_2` at the moment; the props it adds get `cal_group = uiuc`):
```bash
python3 propcalc.py --import-uiuc ../UIUC-propDB
```

**Motor list from rcplanes.online** (already done: 116 motors added from `all_motor.txt`):
```bash
python3 propcalc.py --import-motors data_2/all_motor.txt
python3 propcalc.py --import-motors data_2/all_motor.txt --kv-min 1000 --kv-max 1300 --max-mass 180   # different shortlist
```
Re-importing replaces only the estimated motors. Your 8 datasheet motors are never touched. The import also writes the full list (8,081) to `data_2/motor_list_all.csv`.

**Adding an APC prop by hand:** put its `PER3_xxx.dat` file from apcprop.com into `data_2/propellers/APC_DAT_Files/` and add a row to `props.csv` with `format = apc` and `cal_group = apc_per3`.

---

## 7. Good to know

- **Accuracy:** expect ±5–10 % until checked on a thrust stand. Differences under ~2 % between combos are a tie.
- **Estimated motors:** motors from rcplanes have estimated winding resistance and no-load current (about ±2–3 % thrust). Before buying one, add its datasheet Rm/I0 to `motors.csv` and re-run.
- **Tunnel props:** the 2024 tunnel props show 15–23 % less thrust near standstill than APC data. Treat them as the pessimistic case.
- **Speed:** a full run takes a few minutes because there are many props and motors. Use `--no-plots` or `--prescreen 20` for quicker test runs.
- **More detail:** `README.md` explains the physics and every file column. `python3 propcalc.py --help` lists all options.

---

## Cheat sheet

```bash
python3 propcalc.py --no-plane --metric net_thrust --max-current 80     # best propulsion
python3 propcalc.py --max-current 80                                    # best for our plane
python3 propcalc.py --max-current 80 --two-blade-only                   # same, 2-blade props only
python3 propcalc.py --max-current 65 --out results_65A                  # compare at a lower cap
python3 propcalc.py --single "MOTOR" "PROP" "ESC" "BATTERY"             # one setup in detail
python3 propcalc.py --help                                              # all options
```
