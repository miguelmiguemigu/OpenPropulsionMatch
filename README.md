# propcalc – propulsion combination optimiser (SAE Aero Design Micro Class)

https://202294.xyz/
Auto deployment is setup through cloudflare so after every merge the site should be updated automatically

** DATA IS FROM APC & rcplanes.online **


Tries every legal **motor × prop × ESC × battery** from your data files, ranks them and draws graphs.
New here? Start with **[GUIDE.md](GUIDE.md)** (step-by-step usage). This README is the reference: files, columns, model.
There is also a **website version** (same calculator, runs in the browser), deployed to Cloudflare Pages on every push to
`main` - see [web/WEB.md](web/WEB.md).

Current library (`data_2/`): 124 motors (8 datasheet + 116 from rcplanes.online with estimated Rm/I0), 20 APC PER3 props
(19 switched on; 203 APC tables in `data_2/propellers/APC_DAT_Files/`), 5 ESCs, 14 batteries.

```
python propcalc.py                          # rank by estimated flight score (uses data_2/plane.json)
python propcalc.py --no-plane               # ignore the plane: rank on thrust minus propulsion weight
python propcalc.py --no-plane --metric avg_thrust      # net_thrust (default) | avg_thrust | static_thrust | thrust_per_watt
python propcalc.py --max-current 80         # compare everything at the same battery-current cap (recommended)
python propcalc.py --limit-mode reject      # only combos that never exceed a rating (no current limiting)
python propcalc.py --single "HW 2820SL 1250kV" "APC 12x8E" "Skystars Jupiter Single 80A ESC" "CNHL MiniStar 1000 100C"
python propcalc.py --data otherdata --plane otherplane.json --out myresults --top 15
python propcalc.py --prescreen 20 --no-plots          # quicker test run
```
Needs only Python 3 for the calculation; the graphs need numpy + matplotlib (`pip install -r requirements.txt`).
Run from inside the `propcalc` folder.

### All options
| option | what |
|---|---|
| `--data FOLDER` | data folder (default `data_2/`) |
| `--plane FILE` | plane JSON (default `data_2/plane.json`) |
| `--no-plane` | ignore the plane; rank on propulsion only |
| `--metric M` | ranking without a plane: `avg_thrust`, `static_thrust`, `net_thrust`, `thrust_per_watt` |
| `--limit-mode M` | `cap` (default), `reject`, `ignore` - see *Current limit modes* |
| `--max-current A` | extra battery-current cap for every combination (= `model.max_system_current_a`) |
| `--two-blade-only` | leave 3/4-blade props out (their calibration from 2-blade bench tests is unverified) |
| `--prescreen N` | keep the best N props and N motors after the quick pre-screen (0 = everything) |
| `--out FOLDER` | output folder (default `results/`) |
| `--top N` | how many to print / plot (default 10) |
| `--no-plots` | skip the graphs |
| `--single MOTOR PROP ESC BATTERY` | thrust / RPM / current vs airspeed for one combination (names exactly as in the CSVs) |
| `--import-tunnel FOLDER` (+ `--tunnel-rho`, `--tunnel-min-rpm`, `--tunnel-prop-mass`) | import thrust-stand / tunnel logs |
| `--import-uiuc FOLDER` | import the UIUC propeller database |
| `--import-xflr5 PLANE_XML POLAR` (+ `--alpha-ground`, `--extra-cda`, `--fit-cl-min`) | write the aero keys of plane.json from XFLR5 (see below) |
| `--import-motors FILE` (+ `--kv-min`, `--kv-max`, `--min-current`, `--min-mass`, `--max-mass`, `--keep-discontinued`) | import an rcplanes.online motor list |

## Outputs (`results/`)
| file | what |
|---|---|
| `results.csv` | every combination with all numbers, ranked |
| `ranking.png` | top N by the ranking metric, with % gap to #1 |
| `thrust_curves.png` | thrust and battery current vs airspeed for the top 5 |
| `payload_vs_current.png` (plane) / `thrust_vs_mass.png` (no plane) | trade-off over every combination |
| `tds_power_curves.png` (plane) | thrust available vs drag with 1/2/3 drag articles – the Micro TDS graph |

## Data files (`data_2/`)
| file | columns |
|---|---|
| `motors.csv` | name, brand, kv, rm_mohm, i0_a, i0_voltage, mass_g, max_current_a, rating_note, source, include (`no` = skip). `source` containing "estimated" marks Rm/I0 as estimated |
| `motor_list_all.csv` | full rcplanes list (8,081 motors) with estimated Rm/I0 - reference only, not searched; written by `--import-motors` |
| `props.csv` | name, diameter_in, pitch_in, mass_g, format, file, cal_group, max_rpm, include (`no` = skip), notes |
| `props/*.csv` | `J,Ct,Cp` table (static row J=0 first). `format=apc` reads APC `PER3_*.dat` directly (all RPM tables); `format=uiuc` reads `static.txt;tunnel.txt` |
| `escs.csv` | name, cont_a, burst_a, mass_g, r_mohm, notes |
| `batteries.csv` | name, cells, mah, mass_g, r_mohm, c_cont (optional c_burst), notes |
| `calibration.csv` | bench points (group, motor, prop, current_a, rpm, thrust_gf, use=both/thrust/torque, source) |
| `config.json` | rules (cells, max mAh, max prop diameter) and model settings (see below) |
| `plane.json` | airframe + mission (optional, see below) |
| `all_motor.txt` | raw rcplanes.online export (input for `--import-motors`) |

### `config.json` → `model` settings
| key | default | what |
|---|---|---|
| `air_density_kg_m3` | 1.225 | air density |
| `voc_per_cell_fresh` / `voc_per_cell_cruise` | 4.0 / 3.8 | pack open-circuit voltage at takeoff / later in the flight |
| `wiring_and_plug_mohm` | 5 | extra resistance of leads, plugs, arming plug |
| `usable_capacity_fraction` | 0.8 | share of the pack's mAh the mission may use |
| `speeds_mps` | 0-20 | airspeeds for curves |
| `current_limit_mode` | cap | `cap` / `reject` / `ignore` |
| `battery_rating` | continuous | pack current limit from `c_cont` (`continuous`) or `c_burst` (`burst`) |
| `multi_blade_props` | include | `exclude` = 2-blade props only, same as `--two-blade-only` (blade count from the name, e.g. `12x7E-3`, or a `blades` column in `props.csv`) |
| `thrust_cal_extrapolation` | hold | thrust factor beyond the fastest bench point: `hold`, `linear`, `flat` (also `--thrust-cal`) |
| `max_system_current_a` | - | optional cap for every combination (same as `--max-current`) |
| `prescreen_props` / `prescreen_motors` | 40 / 15 | how many props / motors survive the pre-screen |
| `plane_evaluate_top` | 400 | combinations that get the full mission simulation (0 = all) |

### `plane.json` keys
Wing: `wing_area_m2`, `span_m`, `CLmax`, `CL_ground_roll`, `CD0`, `oswald_e`, `extra_CdA_m2` (drag area of gear, motor,
payload bay… added to the drag at every speed). Takeoff: `rolling_friction`, `liftoff_speed_factor`
(× stall speed), `takeoff_distance_limit_m` (15.24 = 50 ft), `takeoff_margin`. Masses: `empty_mass_kg` (airframe without payload
and propulsion), `extra_propulsion_mass_g` (mount, wiring, spinner). `drag_articles`: `count`, `mass_kg_each`, `CdA_m2_each`.
`mission`: `climb_altitude_m`, `climb_speed_factor`, `min_climb_rate_mps`, `cruise_speed_mps` (must be ≥ `min_cruise_speed_factor`
× stall speed), `cruise_time_s`, `approach_time_s`, `approach_current_a`. `score_factor` = 1.25 (rules).

**Aero from XFLR5:** export the plane (Plane → Export to XML) and its polar (the polar's text or CSV export), then
```
python3 propcalc.py --import-xflr5 "data_2/xflr5/vutaz uzky.xml" "data_2/xflr5/vutaz uzky_T1-10_0 m_s-VLM2.txt" --alpha-ground 5 --extra-cda 0.009
```
It takes `wing_area_m2` and `span_m` from the main wing only, fits `CD = CD0 + CL²/(π·AR·e)` to the polar above CL 0.15 (`e` also
absorbs the profile drag that grows with lift, so e > 1 is normal), reads `CL_ground_roll` off the polar at `--alpha-ground`
(the wing's angle on its gear) and sets `extra_CdA_m2` from `--extra-cda`. Masses, mission and the other keys are not touched;
a hidden `_aero_source` records the files, speed, fit error and date. It refuses a polar whose "Plane name" is not the XML's
plane. The polar is read by column name (`alpha`, `CL`, `CD`), so a trimmed XFLR5 polar, a CFD polar or a tunnel polar goes in the
same way. `CLmax` is not set: a VLM polar has no stall.

**Adding a prop:** download its APC `PER3_xxx.dat`, put it in `data_2/propellers/APC_DAT_Files/`, add a row with `format=apc`, `cal_group=apc_per3`.
Props from another source need their own `cal_group` and at least one bench point in `calibration.csv` (otherwise they are used uncalibrated).

## How it works
Per operating point: `T = Ct·ρ·n²·D⁴`, `P = Cp·ρ·n³·D⁵`, `I_motor = Q/Kt + I0`, `I_batt = throttle·I_motor`,
`V_batt = Voc − I_batt·(R_pack + R_wiring)`, `throttle·V_batt = RPM/Kv + I_motor·(Rm + R_esc)`, solved for the RPM where
motor torque = prop torque. The ESC resistance is on the motor side because its FETs carry the motor current for the whole
PWM period (synchronous rectification); pack and leads carry the battery current.
Prop data is scaled per `cal_group` to match real bench tests: a torque factor, and a thrust factor that depends on the
blade tip speed `π·D·n` (so 12" bench points carry over to 10-11" props at the same tip speed, not the same RPM).
Prop RPM limits follow APC: 150,000 / D for thin-electric props (65,000 SF, 105,000 MR, 120,000 folding).

**Current limit modes** (`config.json` or `--limit-mode`):
`cap` (default) assumes the ESC lowers the throttle so that the **motor current** stays within the motor and ESC ratings and
the **battery current** within the pack rating (and `max_system_current_a`), and flags combos that need it. The two differ:
at part throttle `I_motor = I_batt / throttle`, so an over-propped motor held at 79 A battery current would carry ~97 A in
its windings. `reject` drops anything that would exceed a rating at full throttle; `ignore` applies no limits.
`--max-current` / `max_system_current_a` adds one more battery-current cap, the same for all combinations - use it to compare
motors fairly (several rcplanes motors have burst-type ratings) and set it to what your ESC current limit will be.
`results.csv` lists both limits (`motor_limit_A`, `batt_limit_A`) and the peak motor current (`peak_motor_A`).

**With a plane**, each combination gets the heaviest takeoff mass that still lifts off within
`takeoff_distance_limit_m × takeoff_margin`, climbs at ≥ `min_climb_rate_mps`, and finishes the mission
(roll + climb to altitude at full throttle, cruise, approach) within the usable battery charge.
Payload = that mass − airframe − propulsion − drag articles; score = `score_factor × payload_lb × N_articles`.

**Without a plane**, ranking is `net_thrust` (default: mean thrust 0–12 m/s minus the mass of motor + prop + ESC +
battery), `avg_thrust` (mean thrust only, mass ignored), `static_thrust` or `thrust_per_watt`.

## Importing a motor list (rcplanes.online)
```
python3 propcalc.py --import-motors data_2/all_motor.txt
python3 propcalc.py --import-motors data_2/all_motor.txt --kv-min 1000 --kv-max 1300 --min-current 60 --max-mass 180
```
Reads the result table copied from https://rcplanes.online/search.htm (tab-separated: Brand, Type, Power, Imax, Kv, oz, g).
- Writes the whole list to `data_2/motor_list_all.csv` (8,081 motors) with estimated Rm and I0 for reference.
- Adds a shortlist to `motors.csv` (default: Kv 900-1450, rated >= 55 A, 90-220 g, not discontinued). Motors already in
  `motors.csv` with datasheet values are kept; re-importing replaces only the estimated rows.
- These lists do not give Rm or I0, so they are **estimated** from Kv and mass (fit to 16 motors with datasheet values:
  Rm within 13 % rms, I0 within 7 % - roughly +/-2-3 % thrust). Results for these motors carry a note.
- Their "Imax" is often a short burst rating with no duration. Compare motors at an equal current with
  `--max-current 80` (or set `model.max_system_current_a` in `config.json`).
- With many motors the search keeps the best 15 after a quick pre-screen (`model.prescreen_motors`).

## Importing the UIUC Propeller Database
```
python3 propcalc.py --import-uiuc ../UIUC-propDB
```
Point it at the unzipped `UIUC-propDB` folder (volumes 1-4). For every propeller it:
- converts the size from the file name (UIUC drops the decimal point: `12x65` = 12 x 6.5, `125x6` = 12.5 x 6;
  E-Flite, KP, Plantraco and Vapor sizes are in mm) and **skips anything over the rule diameter** (`config.json`);
- uses the static data near the highest RPM tested and the wind-tunnel run(s) at the highest RPM (closest to flight),
  the newest volume if a prop was tested twice, and writes `data_2/props/uiuc/<prefix>_<size>.csv`;
- adds the props to `props.csv` with `cal_group = uiuc` (calibrated on the SunnySky bench points, reference prop
  "APC Thin Electric 12x6 (UIUC)"), replacing earlier `uiuc` rows but keeping your own mass/include edits;
- switches off (`include = no`) props with static data only (no thrust-vs-airspeed data) and research props you cannot buy.
Prop masses are estimated (scaled from a 30 g 12" APC E) and RPM limits use APC-style formulas - edit them if you know better.

**Large libraries:** with many props the search first pre-screens each prop with each motor and keeps the best 40
(`model.prescreen_props` in `config.json`, or `--prescreen N`, `--prescreen 0` = everything). With a plane, the full
mission simulation runs on the 400 combinations with the best net thrust (`model.plane_evaluate_top`, 0 = all).

## Importing your own wind-tunnel data
```
python3 propcalc.py --import-tunnel "Wind tunnel measurements 2024"
python3 propcalc.py --import-tunnel "Wind tunnel measurements 2024" --tunnel-rho 1.18 --tunnel-min-rpm 4000 --tunnel-prop-mass 28
```
Reads Tyto Robotics / RCbenchmark CSV logs (comma or tab separated), one sub-folder per propeller.
- **Prop size** comes from the sub-folder name: `1208` = 12 x 8 in (or `12x8`). For anything else add
  `tunnel.json` in the tunnel folder: `{"1208": {"name": "APC 12x8E (tunnel)", "diameter_in": 12, "pitch_in": 8, "mass_g": 32}}`.
- **Airspeed** comes from each file name: `10m-s`, `6 m/s`, `static`. Identical duplicate files are skipped.
- For every logged point it computes `J = V/(nD)`, `Ct = T/(rho n^2 D^4)`, `Cp = Q*2*pi*n/(rho n^3 D^5)` from the measured
  thrust, torque and optical RPM, fits smooth curves (outliers rejected) and writes `data_2/props/tunnel_<folder>.csv`.
- Adds / updates a row in `props.csv` with `cal_group = tunnel` (used **as measured**, no calibration). Props larger than
  the rule diameter are added with `include = no`. Edit the mass in `props.csv` (default 30 g).
- Saves `results/tunnel_<folder>.png`: measured points (coloured by airspeed), the fitted curve, and the library prop of the
  same size for comparison; the console prints static Ct/Cp/figure of merit, peak efficiency and the % thrust difference.

Notes on the 2024 data (T-Motor 2826, Mezon 55, 11x8 / 12x8 / 14x7):
- Current was limited to ~33.6 A, so the highest RPM is ~7,000 (12x8) - below the ~9,300-10,000 RPM in flight. Coefficients
  change little with RPM, but a full-power run would confirm them.
- Thrust near standstill (J < 0.3) is ~15-23 % below the APC 12x8E data, while torque matches. Typical causes: recirculation
  in a closed test section at low speed and the stand/motor blocking the slipstream. A static test in open air settles it.
  Until then, treat the tunnel props as the pessimistic case and the APC model as the optimistic case.

## Caveats
- Pack resistances in `batteries.csv` are **assumed** – measure yours (charger IR readout) and edit.
- `plane.json` is an **illustrative** airframe – replace it with your design before trusting payload/score.
- ESC resistances in `escs.csv` are **assumed** (3 mΩ) – check masses and ratings against the ESCs you actually use.
- Motors from rcplanes.online have **estimated** Rm/I0 – add datasheet values before choosing one.
- Differences under ~2 % between combinations are a tie.
- Expect ±5–10 % until checked on a thrust stand.
