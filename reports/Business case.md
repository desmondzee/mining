# R² (Rocky Robotics): business case

**"Mining autonomy."** Retrofit autonomy and voice dispatch for mid-size quarries.

*Prepared 17 Oct 2026 for the hackathon pitch. Every number either links to its source or is marked **[A]** (our assumption) or **[M]** (output of our model, built from sourced inputs plus [A]). Evidence base: `reports/Autonomous quarry haulage financials.md` and `research_notes/Autonomous quarry haulage financials/`.*

---

## Executive summary (one screen)

**What R² does.** R² (pronounced "R squared", short for Rocky Robotics) sells a bolt-on autonomy kit (cameras, lidar and a compute pod) for the haul trucks and excavators a quarry already owns. Supervisors run the fleet by talking to it over a phone radio ("truck one, unload at excavator two"). Our edge is onboarding speed: a drone scan of any quarry becomes a simulation world, with haul roads, load and dump points and design checks, in about 10 seconds. The driving policy is trained and validated in that world before a truck moves.

| | |
|---|---|
| **Problem** | A US quarry truck driver costs **~$80k/yr loaded** ([BLS OEWS](https://api.bls.gov/publicAPI/v1/timeseries/data/OEUN000000021230053303204) + [BLS ECEC](https://www.bls.gov/news.release/ecec.nr0.htm)). **77%** of US firms struggle to hire equipment operators ([AGC 2025](https://www.agc.org/sites/default/files/users/user21902/2025_Workforce_Survey_National_FINAL_m.pdf)). Powered haulage causes **25–43% of US mining deaths** ([MSHA](https://www.msha.gov/safety-and-health/safety-and-health-initiatives/powered-haulage-safety), [MSHA daily report](https://arlweb.msha.gov/stats/charts/combined.php)). |
| **Gap** | OEM autonomy was built for 24/7 mega-mines and costs **~$2.3M/truck all-in** ([Boliden/Komatsu](https://im-mining.com/2021/10/22/boliden-to-become-eu-automation-frontrunner-with-help-of-komatsu-ahs/)). Cat, Komatsu and two Chinese OEMs supply **88% of 3,832** autonomous trucks ([GlobalData](https://www.mining-technology.com/analyst-comment/china-global-leader-autonomous-surface-mining-trucks/)). The sub-100 t quarry class is almost entirely manned. |
| **Offer** [A] | **$90k kit per truck + $75k site onboarding + $4,000/truck/month**, or **$25 per autonomous hour** for single-shift sites. |
| **Customer ROI** [M] | 10-truck US quarry pays back in **~1.0 yr on two shifts**, **~4 months at 24/7**, and **2.3 yr on one shift with per-hour pricing**. |
| **Market** [M] | TAM **$1.3–5.1B/yr** (ex-China/India quarry trucks plus loading machines). SAM **$0.3–1.2B/yr** (US + EU + AU, multi-shift or labour-constrained). |
| **Plan** [M] | **836 trucks at 92 sites by Y5**: **$72M revenue, $39M exit ARR, 61% GM**, EBITDA-positive in Y5. |
| **Ask** [A] | **$7M seed** to fund 20 months: 3 paid pilots, an ISO 17757-aligned safety case, and a 30-person team. Exit criteria for the next round: $1.5M+ ARR and 5+ sites. |
| **Biggest risks** | Pronto/Atoms (**$1.7B** parent raise, [TechCrunch](https://techcrunch.com/2026/07/22/travis-kalanicks-robotics-company-raises-1-7b-led-by-a16z/)) holds the flagship quarry account. OEMs control drive-by-wire. The safety case is still unproven. |

---

## 1. Pitch and problem

**Pitch.** Quarries cannot hire enough drivers, and the autonomy on the market was built for BHP, not for a 6-truck limestone pit. R² turns the trucks and excavators a quarry already owns into a supervised autonomous fleet. Onboarding takes days: we fly a drone, generate a simulation of the site, train and check the driving policy there, bolt on a sensor pod, and supervisors run the fleet by voice over the radio they already carry. The quarry pays a subscription that costs less than the shifts it can no longer staff. **R², "mining autonomy": the quarry crew stays in charge, the machines do the driving.**

**The problem in five numbers.**

| Pain | Evidence |
|---|---|
| **Labour is a third of truck running cost.** The operator is **25–35%** of hourly cost on 55–65 t trucks, against 12–15% on 230 t mine trucks. | Derived from [Cat fuel data](https://wheelercat.com/wp-content/uploads/2023/01/Cat-Performance-Handbook-from-VST-fuel-consumption-2022-12-09T21-20-09.pdf) + [BLS](https://api.bls.gov/publicAPI/v1/timeseries/data/OEUN000000021230053303204) (financials report) |
| **Every added shift multiplies headcount.** A truck needs 1.1 operators on one shift, 2.1 on two and 4.4–4.9 for 24/7, which is ~$90k / $170k / $470–520k per truck per year in the US. | Roster arithmetic (unit_economics.md) |
| **Shortage.** 77% of US construction firms find equipment operators hard to hire. Australia has had 7 machinery-operator occupations in persistent shortage since 2021. | [AGC 2025](https://www.agc.org/sites/default/files/users/user21902/2025_Workforce_Survey_National_FINAL_m.pdf), [JSA OSL 2025](https://www.jobsandskills.gov.au/sites/default/files/2025-10/2025%20OSL%20Key%20Findings%20Report.pdf) |
| **Safety.** Powered haulage caused 13 of 33 US mining deaths in 2025 (39%; the daily report says 10 of 33). About 8 surface haulage deaths a year. Each work death costs **$1.54M** (societal). | [MSHA](https://www.msha.gov/safety-and-health/safety-and-health-initiatives/powered-haulage-safety), [MSHA daily](https://arlweb.msha.gov/stats/charts/combined.php), [NSC](https://injuryfacts.nsc.org/work/costs/work-injury-costs/) |
| **OEM autonomy doesn't scale down.** ~$2.3M per truck at Boliden. Cat says it is only now "learning" to automate smaller fleets that don't run 24/7. Komatsu resells Pronto for quarries instead of FrontRunner. | [IM](https://im-mining.com/2021/10/22/boliden-to-become-eu-automation-frontrunner-with-help-of-komatsu-ahs/), [Equipment World/MarketScale](https://www.marketscale.com/industries/engineering-and-construction/cat-brings-driverless-hauling-on-the-smaller-775-truck-to-two-more-luck-stone-quarries), [Pronto/Komatsu](https://pronto.ai/komatsu-north-america-and-pronto-launch-autonomy-solution-for-quarry-size-trucks/) |

**Why now.** Autonomous trucks went from **2,080 to 3,832 in one year** ([GlobalData](https://www.mining-technology.com/analyst-comment/china-global-leader-autonomous-surface-mining-trucks/)). Heidelberg already runs **~30 autonomous vehicles** with **four vendors** and targets **100+ by 2028** ([IM 2026](https://im-mining.com/2026/05/01/heidelberg-advancing-autonomy-on-multiple-quarry-fronts-with-applied-intuition-pronto-sensmore-epiroc/)). Quarries have moved from asking whether autonomy works to asking which vendor to pick.

---

## 2. Customer and ICP

**Ideal customer profile.**

| Attribute | Target |
|---|---|
| Site | Hard-rock or sand-and-gravel aggregates quarry producing 0.5–5 Mt/yr, with **3–12 rigid trucks** of 40–100 t (Cat 770–777, Komatsu HD465–605, Bell) and 1–3 excavators or loaders. Fleet sizes come from project examples ([market_size.md](../research_notes/Autonomous%20quarry%20haulage%20financials/market_size.md)); no census exists. |
| Operating pattern | **Two shifts, or wants to add a second shift but cannot staff it** (the best ROI). 24/7 sites are a bonus. Single-shift sites only on per-hour pricing. |
| Fleet | **Mixed brands and older trucks** that OEM AHS will not cover. |
| Geography | US Sun Belt/Midwest and Australia first (high wages, labour shortage). DACH/Nordics/UK second. |

**Multi-site operators (land-and-expand targets).**

| Operator | Footprint | Autonomy status |
|---|---|---|
| Heidelberg Materials | ~600 aggregates quarries ([Wikipedia](https://en.wikipedia.org/wiki/Heidelberg_Materials), undated) | 4-vendor bake-off; Pronto exclusive in North America ([Heidelberg](https://www.heidelbergmaterials.com/en/pr-2025-02-24)). **Approach in Europe/APAC, where it is not exclusive.** |
| CRH | **1,247** aggregates quarries/pits, 736 of them in the US ([CRH 10-K FY2025](https://www.sec.gov/Archives/edgar/data/849395/000162828026009043/crh-20251231.htm)) | No public AHS program found |
| Vulcan Materials | **425** active aggregates facilities ([10-K FY2025](https://www.sec.gov/Archives/edgar/data/1396009/000162828026009546/vmc-20251231.htm)) | No public AHS contract found ([costs_competitors.md](../research_notes/Autonomous%20quarry%20haulage%20financials/costs_competitors.md)) |
| Martin Marietta | **400** quarries, mines and yards (89 of them yards) ([10-K FY2025](https://www.sec.gov/Archives/edgar/data/916076/000119312526059193/mlm-20251231.htm)) | No public AHS contract found |
| Cemex | Site count not checked | sensmore wheel-loader work at Rüdersdorf ([sensmore](https://www.sensmore.ai/press/funding-announcement)) |
| Holcim | Site count not checked | No public quarry-truck AHS found |

**Buyer personas.**

| Role | What they care about | What we show them |
|---|---|---|
| **Quarry manager** (champion) | Hitting tonnes with unfilled seats; overtime; turnover | Unmanned second shift; voice dispatch they can run themselves |
| **Regional VP Ops / COO** (economic buyer) | Cost per tonne, capex discipline, a reference site | Payback table (§4); a subscription in place of $2M-per-truck capex |
| **HSE director** (veto) | Powered-haulage fatalities, MSHA citations | Simulated incident replay, geofenced exclusion zones, ISO 17757 safety case |
| **Procurement / IT/OT** | Vendor risk, data ownership, integration | Mixed-fleet support; customer owns the site scan; open APIs |

**Buying process [A].** A first deal takes **9–12 months**: intro, drone scan and simulated "digital twin day" (week 1–2), paid pilot (3–6 months), production rollout, then a multi-site master agreement. Equipment and safety budgets sit at regional or corporate level for the majors. At independents the owner decides.

**Pilot design [A].**

| Item | Design |
|---|---|
| Scope | 1 site, **2–3 trucks + 1 excavator**, one haul route (pit to primary crusher), safety driver on board for phase 1 |
| Price | **$150k fixed** (onboarding + kits at cost); credited against a rollout of 8 or more trucks |
| Timeline | Week 0 drone scan → day 1 simulation world → weeks 2–6 install and sim-to-real validation → months 2–4 supervised autonomy → months 4–6 driverless on a fenced route |
| Success KPIs | ≥90% autonomous availability; tonnes per truck-hour within 5% of manned; zero lost-time incidents; ≥80% of voice commands executed without manual override |
| Exit | Signed rollout order or LOI for the site plus one sister site |

---

## 3. Product and moat

**What is real vs demo today (be honest with judges).**

| Component | Status at hackathon |
|---|---|
| Drone scan → simulation world in ~10 s: collider, nav grid, haul roads, load/dump points, excavators, haul-road design checks sized to the design truck | **Built (software).** Runs on real scans in our pipeline (`envgen/`). Design checks follow industry rules of thumb and are not yet certified. |
| Photoreal sky and backdrops via World Labs Marble | **Built**, depends on a third-party API |
| Isaac Sim + Gym RL environment, vision driving policy trained in simulation | **Built in simulation.** Not validated on a physical truck. |
| Voice dispatch: phone radio → ElevenLabs STT → LLM intent → road-following goals → TTS confirmation | **Built and demoed live** ("truck one unload excavator") |
| Live 3D ops view and dashboard | **Built** |
| Retrofit hardware pod (camera, lidar, Orin) | **Concept + BOM** ($15–60k at list price; [financials report](./Autonomous%20quarry%20haulage%20financials.md)). Not fabricated. |
| Drive-by-wire integration, functional safety, e-stop, field network | **Not started.** This is the largest engineering and certification cost. |

**Product pillars.**

1. **Retrofit kit**: one sensor/compute pod for trucks and excavators. Lean BOM ~$15k, robust ~$50–60k ([Livox](https://lidarseek.com/product/livox-mid360), [Ouster](https://www.ebay.com/itm/305902230376), [Jetson Orin $3,499](https://marketplace.nvidia.com/en-us/enterprise/robotics-edge/jetson-agx-orin-developer-kit/), [Rajant](https://madison.tech/products/rajant-sparrow-breadcrumb-me5-5050cs-wireless-mesh-network-node/)). Drive-by-wire uses OEM CAN where available and aftermarket actuators otherwise.
2. **Voice ops**: replaces the control-room console. The Whittle AHS model assumes **4 staff per shift** for a fleet's control and field roles ([Whittle](https://www.whittleconsulting.com.au/wp-content/uploads/2023/04/Autonomous-Haulage-Study-Report-Rev-F.pdf)). A small quarry cannot carry that, so the supervisor dispatches by radio instead.
3. **Simulation from scan**: the onboarding wedge. Every site becomes a validated simulation world before hardware arrives, so feasibility takes days, against the "years-long feasibility study" Pronto markets itself as avoiding ([Pronto](https://pronto.ai/prontos-ahs-the-right-choice-for-small-fleet-autonomy/)).

**R² moat (in order of defensibility).**

| Moat | Why it compounds |
|---|---|
| **Site-onboarding speed** | Scan → simulation → policy check in days is our wedge. It cuts our onboarding COGS from $40k to $25k by Y5 [A] and shortens sales cycles (a free simulation day is the demo). |
| **Data flywheel** | Every site scan and every hour of operating logs (interventions, near-misses, road changes) feeds back into simulation, and the policy improves for all sites. Few competitors hold quarry geometry at scale. |
| **Truck + excavator in one stack** | Most rivals do trucks only (Pronto, Epiroc) or loaders/excavators only (sensmore, Built, Bedrock). Coordinating loading and hauling is where cycle-time gains are. |
| **Mixed fleet, OEM-agnostic** | Quarries run Cat, Komatsu, Bell and Volvo side by side. OEM systems are brand-tied. |
| **Workflow lock-in** | Once supervisors dispatch by voice and the ops view is the daily dashboard, switching costs rise. |

*Honest caveat:* simulation-from-scan and voice are replicable by well-funded rivals within 12–18 months. The durable moats are the data flywheel and the installed base, so speed to first deployments matters more than the technology lead.

---

## 4. Business model and unit economics

**R² price list [A].**

| Line | Price | COGS | Gross margin | Notes |
|---|---|---|---|---|
| Retrofit kit (truck) | **$90k** | $55k → $45k (Y1→Y5) | 39% → 50% | ~10–25× below OEM AHS ($0.8–2.3M/truck: [IM](https://im-mining.com/2021/10/22/boliden-to-become-eu-automation-frontrunner-with-help-of-komatsu-ahs/), [DirtMatch](https://dirtmatch.com/blog/autonomous-semi-autonomous-haul-trucks-guide-2026)) |
| Excavator kit (upsell) | ~$110k [A] | ~$70k | ~36% | Not in the base projection, so pure upside |
| Site onboarding (scan, simulation, mesh, commissioning) | **$75k** | $40k → $25k | 47% → 67% | Automation from the simulation pipeline drives COGS down |
| Subscription, multi-shift | **$4,000/truck/month** | — | 50% → 78% | Anchored on Built Robotics at $3,000/machine/month in 2022 ([Robots in Construction](https://www.robotsinconstruction.com/robots/built-exo/)) |
| Subscription, single-shift | **$25/autonomous hour** (~$35k/truck/yr at 1,400 h) | — | same | Usage pricing as used by Built and Carbon Robotics ([Future Farming](https://www.futurefarming.com/tech-in-focus/autonomous-semi-autosteering-systems/carbon-robotics-launches-autotractor-kit-for-autonomous-john-deere-tractors/)) |
| Services (re-scans, design-check reports, training) | $20k/site/yr | — | 50% | |

**Customer ROI: 10-truck US quarry (from the financials report).** Assumes $80k/operator, 2 retained staff per shift at 1.25× cost, 8% non-labour saving; productivity and safety gains excluded.

| | One shift (1,400 h) | Two shifts (2,600 h) | 24/7 (5,800 h) |
|---|---|---|---|
| Operator cost removed | $880k | $1,680k | $3,760k |
| Retained supervisor/tech | −$220k | −$420k | −$940k |
| Fuel/tyre/maintenance saving (8%) | $106k | $198k | $441k |
| Subscription | −$480k (flat) / **−$350k (per-hour)** | −$480k | −$480k |
| **Net annual benefit** | $286k / **$416k** | **$978k** | **$2,781k** |
| Upfront (10 × $90k + $75k) | $975k | $975k | $975k |
| **Payback** | 3.4 yr / **2.3 yr** | **1.0 yr** | **0.35 yr** |

**Payback by region and shift pattern** [M] (loaded operator cost: US ~$80k, Australia ~$105k, UK ~$52k):

| Region | One shift (flat / per-hour) | Two shifts | 24/7 |
|---|---|---|---|
| US | 3.4 / **2.3 yr** | **1.0 yr** | 0.35 yr |
| Australia | 2.0 / **1.6 yr** | **0.7 yr** | 0.3 yr |
| UK | 17.6 / 5.3 yr | **1.8 yr** | 0.5 yr |

Takeaway: **we sell shift extension, not driver replacement.** We do not target single-shift UK or EU sites until the kit cost falls.

**Our unit economics per 10-truck, two-shift site [A/M].**

| Metric | Value |
|---|---|
| Upfront revenue (kits + onboarding) | $975k, of which ~$385k gross profit (39%) at Y1 COGS |
| Recurring revenue | $480k subscription + $20k services per year |
| Recurring gross profit at steady state | ~$385k/yr (78% sub GM, 50% services) |
| CAC [A] | **$150k** for a new logo (field sales, solutions engineer, simulation day, travel); **~$50k** for a sister site under a master agreement |
| CAC payback | **< 1 month of rollout**, because hardware gross profit alone covers it. Real cash risk sits in the unpaid pilot phase. |
| 5-year LTV (gross profit) | ~$385k + 5 × ~$350k ≈ **$2.1M** → LTV/CAC **~14×** (new logo) |
| Working capital | Kits are paid 50% on order and 50% at commissioning [A], so we avoid financing hardware |

---

## 5. Market sizing (TAM / SAM / SOM)

**Context.** Analyst AHS estimates range from **$0.54B** (systems only; [Intel MR](https://www.intelmarketresearch.com/autonomous-haulage-systems-market-9613)) to **$4.3B** (including trucks; [MarketsandMarkets](https://www.marketsandmarkets.com/Market-Reports/autonomous-mining-haulage-systems-market-266401441.html)) for 2025. None sizes quarry or retrofit autonomy, so we build bottom-up.

**Sourced inputs.**
- World aggregates output **39 Bt/yr**; US share **6%**, Europe **7%**, China + India **54%** ([GAIN](https://aggbusiness.com/feature/global-aggregates-growth-examined-gain-convenor-jim-obrien), [GAIN shares](https://www.aggbusiness.com/feature/global-aggregates-demand-despite-strong-headwinds)).
- US: **3,500 crushed-stone quarries** and 6,500 sand/gravel pits ([USGS](https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-stone-crushed.pdf), [USGS](https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-sand-gravel.pdf)). EU: ~26,000 sites ([UEPG](https://uepg.eu/pages/facts)).

**Derivation (all multipliers are [A]).**

| Step | Low | High | Basis |
|---|---|---|---|
| US mid-size quarry trucks | 3,000 | 12,000 | 1,000–1,500 quarries × 3–8 trucks [A] |
| Europe trucks | 3,500 | 14,000 | US × (7% / 6%) aggregates share |
| Rest of world ex-China/India | 8,250 | 33,000 | US × (33% / 6%) × 50% mechanisation haircut |
| **Total trucks** | **14,750** | **59,000** | |
| + loading machines (0.3 per truck) | 19,200 | 76,700 machines | Excavators/loaders [A] |
| Value per machine per year | $66k | $66k | $48k subscription + $90k kit ÷ 5 yr |
| **TAM** | **$1.3B/yr** | **$5.1B/yr** | |
| SAM: US + EU + Australia (AU = 10% of US) × 50% multi-shift or labour-constrained, × 1.3 machines per truck | 4,400 machines | 17,700 machines | |
| **SAM** | **$0.29B/yr** | **$1.17B/yr** | |
| **SOM (Y5)**: 836 trucks, $39M ARR | 4.7% of SAM machines (vs high case) | 19% (vs low case) | Model |

Sanity check: SAM is in line with today's **$0.5–0.9B systems-only AHS spend**, nearly all of which currently goes to mines. The quarry segment is additional to it.

---

## 6. Go-to-market

**Phasing.**

| Phase | Timing | Motion | Goal |
|---|---|---|---|
| 0. Simulation days | Months 0–6 | Free drone scan → simulation world → ROI report for 20 quarries | 3 paid pilots signed |
| 1. Paid pilots | Months 3–15 | 3 sites × 2–3 trucks, safety driver → driverless on fenced route | 2 rollout orders, first safety-case audit |
| 2. Site rollouts | Y2 | Full fleets at pilot sites + sister sites | 36 trucks, ~7 sites |
| 3. Multi-site MSAs | Y3+ | Corporate frameworks with 1–2 majors (CRH, Vulcan, Martin Marietta, Heidelberg ex-NA) | 100+ trucks/yr |
| 4. Channel scale | Y3+ | Dealers and integrators install; we supply kit, software and training | 50%+ of installs through partners |

**Channels.**
- **Independent dealers and rebuilders** of used Cat/Komatsu/Bell trucks. They want a service and retrofit revenue line, and **Bell and Komatsu dealers are already taken by Pronto** ([costs_competitors.md](../research_notes/Autonomous%20quarry%20haulage%20financials/costs_competitors.md)). We target Volvo, Hitachi-independent and multi-brand dealers.
- **Integrators** in the style of Sensmore's on-site retrofit: local mechatronics shops certified to fit our kit.
- **Drone survey firms**, which already fly quarries monthly for stockpile volumes. Each flight becomes a simulation world, our lead source.

**Region priority.**

| Rank | Region | Why | Watch-outs |
|---|---|---|---|
| 1 | **US** | Largest addressable fleet; $80k loaded operator; labour shortage; independents plus 3 majors without a public AHS vendor | Pronto exclusive at Heidelberg NA; Cat 775/777 moving into quarries |
| 2 | **Australia** | Best payback (A$51–65/h operators, [WorkPac](https://www.workpac.com/job/dump-truck-operator-298), [Randstad](https://www.randstad.com.au/jobs/dump-truck-operator_sandalwood_60107b4c-a34a-4e07-910e-3a5965f3468b/)); autonomy-literate regulators; 1,024 autonomous trucks already | Smaller quarry count; remote support |
| 3 | **Europe (DACH, Nordics, UK)** | 26,000 sites; Heidelberg/Cemex/Holcim HQs; non-exclusive at Heidelberg EU | Low single-shift wages kill ROI; works only on multi-shift sites |

**Regulatory and safety certification path.**

| Item | Plan |
|---|---|
| **ISO 17757:2019** (safety of autonomous and semi-autonomous earth-moving and mining machine systems) | Build the safety case to it from day 1: hazard analysis, operating-zone definition, machine/infrastructure/software requirements ([ISO](https://www.iso.org/standard/60473.html)). Engage a third-party assessor (e.g. TÜV/Exida class) at the pilot stage. |
| Functional safety | Safety-rated e-stop and a safety PLC, independent of the AI stack; target a recognised performance level for stop and geofence functions [A] |
| **MSHA (US)** | No autonomy-specific rule found. The **Surface Mobile Equipment written safety program rule** applies from Jan 2024 ([Federal Register](https://www.federalregister.gov/documents/2023/12/20/2023-27640/safety-program-for-surface-mobile-equipment)), and our safety case slots into each customer's written program. MSHA has said it supports collision-avoidance systems and "recognizes the need for automation" ([NIOSH partnership minutes](https://www.cdc.gov/niosh/mining/content/2021automationmeetingminutes.html)). Engage district offices before each pilot. |
| EU | Machinery Regulation (EU) 2023/1230 applies from Jan 2027 and covers autonomous mobile machinery ([EUR-Lex](https://eur-lex.europa.eu/eli/reg/2023/1230/oj)). CE route needed before EU rollouts. |
| Australia | State mining regulators' autonomous-mining guidance (WA, NSW). Not yet reviewed, a gap to close. |

**Insurance angle (hypothesis, unsourced).** Removing drivers from the main fatality exposure (powered haulage) should lower workers' comp and liability premiums. We will co-develop a "verified autonomous route" product with a specialty mining insurer, using our ops logs as evidence. No premium data has been gathered yet, so none of this is in the ROI.

---

## 7. Competition matrix

| | Retrofit | Mixed fleet | Quarry focus | Voice ops | Sim onboarding speed | Price (public?) |
|---|---|---|---|---|---|---|
| **R² (Rocky Robotics)** | Yes (trucks + excavators) | Yes | **Core** | **Yes, radio-native** | **Days; scan → sim in ~10 s** | $90k + $4k/mo or $25/h [A] |
| **Pronto → Atoms** (incl. SafeAI tier) | Yes (camera-first) | Yes ([Lake Bridgeport](https://im-mining.com/2026/01/15/prontos-ahs-hauls-over-2-mt-on-mixed-fleet-for-heidelberg-at-lake-bridgeport/)) | **Core**; Heidelberg 100+ trucks | No evidence | "Weeks" to deploy ([Heidelberg](https://www.heidelbergmaterials.com/en/pr-2025-02-24)) | Not public; "no upfront commitment" |
| **Sensmore** | Yes (wheel loaders, trucks) | Yes | Yes (Cemex, Lhoist, Heidelberg) | Not known (VLA models) | Not public | Not public |
| **Epiroc / ASI (LinkOA)** | Yes (conversion kits) | Yes (Roy Hill: Cat + Hitachi) | Mines first; Heidelberg trial | No | Project-based (months) | Project quotes |
| **Applied Intuition** | Software licence via OEMs/operators | Via partners | Pilot (Heidelberg Clarence Sands) | No evidence | Strong simulation heritage | Licence; $15B valuation ([FinSMEs](https://www.finsmes.com/2025/06/applied-intuition-closes-series-f-at-15-billion-valuation.html)) |
| **Bedrock Robotics** | Yes (excavators) | Yes | Construction | No evidence | Not public | Not public; $1.75B valuation ([Pulse 2.0](https://pulse2.com/bedrock-robotics-270-million-series-b-at-1-75-billion-valuation/)) |
| **Built Robotics** | Yes (excavators, now solar) | Yes | Construction/solar | No | Not public | **$3k/machine/mo** + usage (2022) |
| **Caterpillar MineStar Command** | Kits for Cat and some others | Partial | Pre-commercial in aggregates ([Cat](https://www.caterpillar.com/en/news/corporate-press-releases/h/autonomous-truck-luck-stone.html)) | No | Months-long; infrastructure heavy | Dealer quote; ~$1.5–2.5M/truck class (mine) |
| **Komatsu FrontRunner** | New trucks; resells Pronto for quarries | No (own brand) | Via Pronto | No | Months | ~$2.3M/truck all-in (Boliden) |

**Where R² wins:** independent and mid-size multi-shift quarries that want trucks and excavators from one vendor, with no control room and a fast start. **Where we lose:** Heidelberg NA (contractually Pronto), and Cat-only fleets buying through their Cat dealer.

---

## 8. Five-year financial projection [M]

**Assumptions [A].** List prices above, flat in nominal terms. New trucks go live mid-year. 30% of trucks are on single-shift per-hour pricing (~$35k/yr), 70% on $48k/yr. Kit COGS $55k→$45k; onboarding COGS $40k→$25k; subscription GM 50%→78% as remote-support scale builds. Opex = headcount × $180k loaded × 1.35 (cloud/GPU, travel, certification, insurance, legal). Excavator upsell, productivity-share pricing and data products are excluded.

| | Y1 | Y2 | Y3 | Y4 | Y5 |
|---|---|---|---|---|---|
| New sites / new trucks | 2 / 6 | 5 / 30 | 12 / 100 | 28 / 250 | 45 / 450 |
| **Trucks deployed (end)** | **6** | **36** | **136** | **386** | **836** |
| Active sites (end) | 2 | 7 | 19 | 47 | 92 |
| Upfront revenue (kits + onboarding) | $0.7M | $3.1M | $9.9M | $24.6M | $43.9M |
| Recurring revenue (subscription + services) | $0.15M | $1.0M | $4.1M | $12.2M | $28.3M |
| **Total revenue** | **$0.8M** | **$4.1M** | **$14.0M** | **$36.8M** | **$72.2M** |
| **Exit ARR** | $0.3M | $1.7M | $6.4M | $18.0M | **$38.7M** |
| Gross profit (GM %) | $0.4M (42%) | $1.9M (47%) | $7.3M (52%) | $21.1M (57%) | $44.2M (61%) |
| Opex | $3.4M | $7.3M | $14.6M | $26.7M | $41.3M |
| **EBITDA** | **−$3.0M** | **−$5.4M** | **−$7.3M** | **−$5.7M** | **+$2.9M** |
| Headcount (end) | 14 | 30 | 60 | 110 | 170 |

Cumulative burn to break-even: **~$21M**, funded by a $7M seed and a ~$20M Series A. This ramp is deliberately slower in Y1–Y2 than the financials report's (20 trucks in Y1) because a seed-stage company has to pilot first. Y5 lands at a similar point (~840 trucks, ~$70M).

**Sensitivity (Y5 outcomes; opex held at base, which is worst case).**

| Scenario | Y5 revenue | Y5 exit ARR | Y5 EBITDA | Peak cumulative burn |
|---|---|---|---|---|
| **Base** | $72M | $39M | +$2.9M | −$21M |
| Price −20% | $58M | $31M | −$10.1M | −$41M |
| Price +20% | $86M | $46M | +$15.9M | −$12M |
| Adoption 0.5× (418 trucks) | $36M | $20M | −$19.1M* | −$55M* |
| Adoption 1.5× (1,254 trucks) | $108M | $58M | +$24.9M | −$11M |
| Shift mix: 60% single-shift (per-hour) | $70M | $35M | +$1.0M | −$22M |
| Shift mix: 0% single-shift | $75M | $42M | +$4.8M | −$20M |

*Opex would be cut in a slow-adoption world. The figure shows the cost of not adjusting.

**Read-across.** Price matters most: a 20% price cut costs ~$20M of extra burn. Shift mix barely moves company revenue (±$3M), but it decides whether a given customer signs. That is why per-hour pricing exists.

---

## 9. Seed round: milestones, use of funds, KPIs

**R² ask: $7M seed [A]**, giving 20 months of runway (Y1 burn $3.0M + ~8 months of Y2).

| Use of funds | $ | What it buys |
|---|---|---|
| Engineering team (hardware, autonomy, safety, simulation) | $3.6M | Grow 6 → 22 engineers: drive-by-wire, perception, safety PLC, field robotics |
| Pilots (3 sites) | $1.2M | Kits, install crews, safety drivers, on-site engineer; partly offset by $150k pilot fees |
| Safety case and certification | $0.8M | ISO 17757 hazard analysis, third-party assessor, MSHA engagement, test track hire |
| Go-to-market | $0.7M | 2 field sales/solutions engineers, 20 free simulation days, trade shows (ConExpo, Hillhead) |
| Compute, tooling, buffer | $0.7M | Isaac/GPU training, Marble/fallback simulation, insurance, legal |

**Milestones.**

| Month | Milestone |
|---|---|
| 3 | Hardware pod v1 on a test truck; drive-by-wire on 1 Cat + 1 Komatsu model |
| 6 | 20 simulation days done; 3 paid pilots signed |
| 9 | First supervised-autonomy tonnes at pilot 1; ISO 17757 hazard analysis complete |
| 12 | Driverless operation on a fenced route at 1 site; voice dispatch used daily by site supervisors |
| 15 | Third-party safety assessment passed for pilot ODD; first excavator coordinated load |
| 18–20 | 2 rollout orders, ~36 trucks contracted, $1.5M+ ARR → raise Series A |

**KPIs we report monthly.**

| Category | KPI | Seed target |
|---|---|---|
| Safety | Lost-time incidents; safety-driver interventions per 100 h | 0; <1 per 100 h by month 15 |
| Performance | Autonomous availability; tonnes/truck-hour vs manned baseline | ≥90%; ≥95% of manned |
| Onboarding | Days from drone flight to validated simulation and route plan | ≤5 days |
| Ops | Share of voice commands executed without override | ≥80% |
| Commercial | Simulation day → pilot conversion; pilot → rollout conversion; contracted ARR | 15%; 66%; $1.5M |
| Data | Sites scanned; autonomous hours logged; simulation-to-real policy gap | 20; 5,000 h; falling quarter over quarter |

---

## 10. Risks and mitigations

| Risk | Likelihood / impact | Mitigation |
|---|---|---|
| **OEM lock-in of drive-by-wire.** OEMs control CAN/J1939 interfaces and could block or void warranties. | High / High | Target older and mixed fleets where aftermarket actuators work. Partner with independent rebuilders. Pursue an OEM partnership with a brand not tied to Pronto (Volvo, Liebherr, Sany) [A]. |
| **Consolidation and a well-funded incumbent.** Pronto/Atoms ($1.7B parent raise) holds Heidelberg NA and the Komatsu, Bell and Hitachi channels. SafeAI, Teleo, ASI and Pronto were all acquired ([Plant & Equipment](https://www.plantandequipment.com/news/mining/atoms-acquires-pronto-to-expand-autonomous-mining-technology)). | High / High | Avoid head-on fights: mid-size independents, majors with no AHS vendor (CRH, Vulcan, Martin Marietta), and the excavator-plus-truck bundle. Acquisition is a credible exit, and acquirers buy installed base plus data. |
| **Safety liability.** A single serious incident could end the company. | Medium / Severe | Safety stack independent of the AI. Fenced operating zones first. Safety drivers until third-party sign-off. ISO 17757 case. Product liability insurance budgeted. Simulation replay of every intervention. |
| **World Labs / AMD dependency.** AMD agreed to buy World Labs for **~$8.2B** (Sep 2026, pending) ([Northeast Times/CNBC](https://northeasttimes.com/2026/09/28/amd-to-buy-ai-research-lab-world-labs-in-8-2-billion-stock-deal)). Marble access and pricing may shift, and our edge stack is NVIDIA. | Medium / Low–Medium | Marble only supplies cosmetic skies and backdrops. Collider, nav grid, roads and design checks come from our own scan pipeline. Keep a fallback (Gaussian-splat or photogrammetry backdrops) so the simulation never depends on one API. |
| **Sim-to-real gap.** A policy trained in simulation underperforms on dust, rain, night and road changes. | High / High | Real-data fine-tuning from pilot logs. Conservative classical safety layer (lidar obstacle stop, geofence). Frequent re-scans keep the simulation current. Measure the gap as a KPI. |
| **Pricing is unvalidated.** No retrofit vendor publishes prices. Our anchor is Built at $3k/month in 2022. | High / High | Treat pricing as a hypothesis and test it in pilot LOIs. Per-hour option for single-shift sites. Sensitivity shows a −20% price is survivable with more capital. |
| **Weak quarry benefit evidence.** Hard data comes from 2014–2020 iron-ore fleets. No audited quarry before/after exists. | Medium / Medium | Our pilots publish a controlled baseline (manned vs autonomous on the same route). The ROI excludes productivity and safety upside. |
| **Low-wage, single-shift markets.** 17.6-year payback in the UK on one shift. | Certain / Medium | Do not sell there until kit COGS falls. Lead with US/Australia and multi-shift sites. |

---

### Source notes
- Detailed evidence, derivations and source quality flags: [`reports/Autonomous quarry haulage financials.md`](./Autonomous%20quarry%20haulage%20financials.md) and [`research_notes/Autonomous quarry haulage financials/`](../research_notes/Autonomous%20quarry%20haulage%20financials/).
- New in this document: operator footprints ([Vulcan 10-K](https://www.sec.gov/Archives/edgar/data/1396009/000162828026009546/vmc-20251231.htm), [Martin Marietta 10-K](https://www.sec.gov/Archives/edgar/data/916076/000119312526059193/mlm-20251231.htm), [CRH 10-K](https://www.sec.gov/Archives/edgar/data/849395/000162828026009043/crh-20251231.htm), [Heidelberg/Wikipedia](https://en.wikipedia.org/wiki/Heidelberg_Materials)), [ISO 17757](https://www.iso.org/standard/60473.html), [NIOSH/MSHA automation minutes](https://www.cdc.gov/niosh/mining/content/2021automationmeetingminutes.html), [EU Machinery Regulation](https://eur-lex.europa.eu/eli/reg/2023/1230/oj).
- Model script used for §4–§5 and §8 lives outside the repo (scratchpad); all inputs are listed in the assumption lines above.
