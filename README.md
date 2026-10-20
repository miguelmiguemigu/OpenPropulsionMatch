# OpenPropulsionMatch (OPM)

**Find the right propulsion setup for your RC aircraft.**

OpenPropulsionMatch (OPM) is a source-available tool for evaluating, matching, and optimizing electric propulsion systems for radio-controlled aircraft.

Given aircraft requirements, a database of available components, and a set of engineering rules, OPM evaluates possible combinations of motors, propellers, batteries, and electronic speed controllers (ESCs) to identify and rank suitable setups.

The goal is to make RC aircraft propulsion selection more systematic, transparent, reproducible, and accessible.

> **Project status:** Early development. Features, calculation methods, component data, and configuration formats may change.

## ✈️ Motivation

Selecting an electric propulsion system involves more than choosing a motor with enough power. The motor, propeller, battery, and ESC must work together within electrical, thermal, and mechanical limits while meeting the aircraft's performance requirements.

Finding a suitable combination can involve manually comparing datasheets, estimating current draw, checking component compatibility, and evaluating competing setups.

OPM aims to automate this process.

Instead of manually testing individual combinations, users define their requirements and let OPM evaluate the available options according to explicit rules and constraints.

## 🎯 Goals

- **Automated component matching** — evaluate compatible motor, propeller, battery, and ESC combinations.
- **Constraint-based selection** — reject setups that violate specified electrical, mechanical, or performance limits.
- **Multi-objective optimization** — rank valid setups according to criteria such as thrust, efficiency, weight, and cost.
- **Transparent calculations** — make the rules, assumptions, and calculation methods inspectable.
- **Configurable requirements** — adapt the selection process to different aircraft and flight objectives.
- **Reproducible results** — use defined inputs, component specifications, and rules to produce comparable recommendations.
- **Community-maintained data** — support improvements to component specifications, validation data, and calculation methods.

## ⚙️ How It Works

OPM is designed around three main inputs:

1. **Aircraft requirements** — define the desired performance and operating constraints.
2. **Component database** — provide the available motors, propellers, batteries, and ESCs.
3. **Selection rules** — define compatibility checks, hard constraints, and optimization priorities.

The optimizer evaluates candidate combinations, rejects invalid configurations, and ranks the remaining candidates.

Conceptually:

```text
Aircraft requirements
        +
Available component database
        +
Engineering rules and constraints
        |
        v
 Generate candidate combinations
        |
        v
 Check compatibility and limits
        |
        v
 Evaluate performance
        |
        v
 Rank valid configurations
        |
        v
 Recommended propulsion setups
```

The result is intended to be a shortlist of promising configurations, together with the relevant specifications and the reasoning behind their ranking.

## 🔋 Components

The primary components considered by OPM are:

| Component | Relevant properties |
|---|---|
| Motor | KV rating, resistance, maximum current, voltage range, weight, power limits |
| Propeller | Diameter, pitch, blade configuration, thrust and power characteristics |
| Battery | Cell count, nominal voltage, capacity, discharge capability, weight |
| ESC | Current rating, voltage range, supported battery chemistry, weight |

Additional parameters may be introduced as the calculation models and component database evolve.

Component specifications should distinguish manufacturer ratings, measured values, estimated values, and model-derived predictions wherever possible.

## 📐 Optimization Criteria

Depending on the available data and enabled calculation models, OPM can be designed to evaluate criteria such as:

- Static thrust and thrust-to-weight ratio
- Electrical current and input power
- Propulsion efficiency
- Battery capacity and estimated flight endurance
- Component weight and total propulsion-system weight
- Component cost
- Electrical and mechanical compatibility
- Operating margins relative to component ratings

Not every criterion can be evaluated accurately from basic datasheet specifications alone. Reliable estimates of thrust, current, and efficiency may require motor test data, propeller performance data, validated physical models, or empirical measurements.

OPM should distinguish calculated estimates from experimentally verified performance.

## 🧩 Design Principles

### Explicit rules

Selection criteria should be configurable and inspectable rather than hidden inside an opaque scoring system.

### Hard constraints versus preferences

A configuration that violates a mandatory safety or compatibility limit should not become a recommendation simply because it scores well on other criteria.

Hard constraints determine which candidates are valid. Optimization objectives determine how valid candidates are ranked.

### Explainable results

Where possible, each recommendation should include:

- The selected components
- Predicted electrical and performance characteristics
- The constraints evaluated
- The reasons the configuration was ranked highly
- Warnings, assumptions, and limitations

### Data quality

Component data is fundamental to the quality of the results. Sources, units, test conditions, and uncertainty should be documented whenever possible.

## 🛠️ Usage

The intended workflow is:

1. Define the aircraft's requirements and operating conditions.
2. Select or import the available components.
3. Configure mandatory constraints and optimization priorities.
4. Run the combination search.
5. Compare the highest-ranked valid setups.
6. Review the underlying calculations and validate the preferred setup before flight.

**Usage examples and installation instructions will be added as the implementation and command-line interface stabilize.**

## 🗺️ Roadmap

The following is a proposed development roadmap, not a list of features already implemented.

- [ ] Define aircraft requirement and component data models
- [ ] Establish consistent units and input validation
- [ ] Implement component compatibility checks
- [ ] Generate candidate propulsion combinations
- [ ] Add configurable engineering constraints
- [ ] Implement motor and propeller performance calculations
- [ ] Evaluate battery current, capacity, and endurance estimates
- [ ] Implement candidate scoring and ranking
- [ ] Explain recommendation scores and rejected candidates
- [ ] Support component database import and export
- [ ] Add automated tests and calculation validation
- [ ] Compare predictions against experimental test data
- [ ] Document calculation models, assumptions, and limitations
- [ ] Provide example aircraft configurations

## 🤝 Contributing

Contributions that improve the accuracy, transparency, usability, and reliability of OPM are welcome, subject to the project's license.

Potential areas for contribution include:

- Component specifications and data importers
- Engineering calculations and physical models
- Compatibility rules
- Optimization algorithms
- Test cases and experimental validation
- Documentation and example configurations
- Bug reports and reproducible calculation errors

When contributing component data or calculation methods, please provide relevant sources, units, assumptions, and test conditions wherever possible.

Before submitting a contribution, review the project's contribution guidelines and license.

## ⚠️ Safety Disclaimer

OPM is an engineering assistance tool, not a substitute for manufacturer documentation, physical testing, or sound engineering judgment.

Calculated performance values are estimates and may differ from actual results due to component tolerances, operating conditions, cooling, battery voltage sag, propeller characteristics, measurement uncertainty, and other factors.

Before operating an aircraft:

- Verify the voltage and current limits of every component.
- Confirm that the ESC is compatible with the motor and battery.
- Check the propeller's speed and mechanical limits.
- Verify battery discharge capability and connector ratings.
- Validate predicted current, thrust, and power with suitable test equipment.
- Secure the aircraft and follow appropriate propeller and battery safety procedures.
- Conduct initial tests under controlled conditions.

**Never rely solely on an OPM recommendation to determine whether a propulsion setup is safe to operate.**

## 📄 License

OpenPropulsionMatch is intended to be distributed under a **source-available, non-commercial license**.

Subject to the final license terms, the intention is to permit users to inspect, use, modify, and redistribute the software for non-commercial purposes while reserving commercial-use rights.

Commercial use requires separate authorization from the relevant rights holder.

**The final license text governs all permissions and restrictions.** Until a license file is published, these statements describe the project's intended licensing model rather than grant specific legal permissions.

The license for the software may differ from the terms governing third-party component data, manufacturer specifications, and other contributed materials.

## 💡 Philosophy

OpenPropulsionMatch aims to make electric RC aircraft propulsion selection more accessible through transparent calculations, explicit engineering rules, and reproducible optimization.

The objective is not simply to find a combination that works, but to explain **why it is a suitable combination, what trade-offs it involves, and how confident we can be in the predicted results.**

---

**OpenPropulsionMatch (OPM)**

*Calculate. Compare. Optimize. Fly.*
