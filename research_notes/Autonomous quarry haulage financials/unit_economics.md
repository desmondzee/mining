# Haul Truck Operating Economics and Labour Costs (Quarries and Mines)

Researched 2026-10-17. Currency: USD unless stated. Each number has a source; derived figures are labelled as inferences. Source quality is mixed. The primary/official sources are BLS OEWS (May 2025), BLS ECEC (June 2026), the Caterpillar Performance Handbook fuel tables (Dec 2022, from VisionLink telematics) and the Jobs and Skills Australia OSL (2025). Vendor blogs and job adverts are marked as such.

---

## 1. Hourly operating cost of rigid haul trucks (fuel, tyres, maintenance, operator), cost per tonne, and haulage share of total operating cost

### Takeaway
I found no free, authoritative breakdown of cost per hour by component for 40–100 t trucks. CostMine and the USACE EP 1110-1-8 schedules have one, but they are paywalled or out of date. The best defensible approach is to build the cost bottom-up: fuel from Cat telematics data (solid), tyres from tyre price ÷ life (indicative), maintenance from vendor shares (weak), and the operator from BLS (solid). Load and haul is roughly 25–30% of total cost at quarries (one OEM estimate) and 50–65% at open-pit mines.

### Cited Findings
**Component cost shares (vendor/secondary, use with caution)**
- 100 t class trucks (e.g. Cat 777) in GCC quarry/mining service, as a share of operating cost per hour with the operator excluded: fuel 35–45%, maintenance parts and labour 25–35%, tyres 12–20%, wear items and body 5–10%, lube and filters 3–6%. The article gives no dollar total and no date. — [1MERCA blog](https://onemerca.com/blog/cat-777-haul-truck-operating-cost-per-hour-ghana-gold-mining/)
- 150–240 t rigid trucks: owning plus operating cost "typically north of $250/h". The split is roughly fuel 35%, tyres 20%, parts and consumables 18%, labour 15%, capital recovery and overhead 12%. No underlying study is named. — [HeavyVehicleInspection blog](https://heavyvehicleinspection.com/blog/post/off-highway-truck-cost-per-ton)
- Tyres are 20–25% of haul truck operating cost, second only to fuel. — [PitCrew.ai, Sep 2026](https://pitcrew.ai/otr-tyre-costs/)

**Cost per tonne**
- Conventional load and haul: US$2.50–3.50/t. One model table puts direct labour at about $0.90/t of that, falling to about $0.65/t under AHS (autonomous haulage). This is a model for a 40+ truck fleet in a high-wage jurisdiction, not measured data. The source looks like low-quality, possibly AI-generated content, and is inconsistent across its own articles (15–20% vs 15–30% savings). — [Skillings.net](https://www.skillings.net/autonomous-haulage-roi-2026-the-hard-data-behind-the-0.50-tonne-savings-target/)
- European limestone quarry model: a 988H loader with two Cat 772 trucks costs about €0.34/t for load and haul. A 990H with two 775 trucks costs €0.332/t. This comes from a search snippet; the page returned 403 and I could not read the full article or its date. — [Aggregates Business](https://www.aggbusiness.com/node/49170)
- Undated US quarry costing model ("Silo Costing" approach). Load and transport is $0.74/t of a $3.37/t total, about 22%. Drill and blast is $0.52/t, scalping and crushing $0.10/t, load-out $0.25/t, administration $0.50/t. The model is old (likely 1990s) and indicative only. — [911Metallurgist PDF](https://911metallurgist.com/wp-content/uploads/2015/01/The-Silo-Costing-Approach.pdf)
- Open-pit mine in Indonesia (not a quarry). Total cost is $19.48/t, of which transport (haulage) is $10.27/t (53%) and loading $4.51/t (23%). — [ResearchSquare preprint rs-10484471](https://www.researchsquare.com/article/rs-10484471/v1)

**Haulage share of total cost**
- Kumba Iron Ore (Sishen, South Africa): loading and hauling is about two-thirds of the cost of its iron ore (2018). — [BusinessLive](https://www.businesslive.co.za/bd/companies/mining/2018-05-29-workers-remain-the-backbone-at-kumba/)
- Material handling is about 40% of total mining costs. — [University of Alberta MOL 2017 paper](https://sites.ualberta.ca/mol/DataFiles/2017_Papers/202_An%20economic%20evaluation%20of%20a%20primary%20haulage%20system%20for%20a%20Bauxite%20mine%20load%20and%20haul%20versus%20in%20pit%20crushing%20and%20conveying%20%20.pdf)
- Quarries: Bell Equipment estimates load and haul at 25–30% of whole-operation cost at a typical quarry, including site personnel and vehicle costs. This figure came from a search snippet. A follow-up search could not locate the primary Bell document, so treat it as unverified. — [Crown Publications / Capital Equipment News](https://www.crown.co.za/latest-news/capital-equipment-news-latest-news/8900-maximising-load-haul-productivity)

**Free cost schedules (structure only)**
- USACE EP 1110-1-8 gives hourly ownership and operating rates by region and model. Operating cost covers fuel, filters/oil/grease, repairs (including overhauls) and tyre wear. Volumes are at a 2013 price level (Nov 2016 edition, updated by RV1-12 in Aug 2021). I did not extract the off-highway truck rows. — [USACE EP 1110-1-8 Vol 1](https://www.publications.usace.army.mil/Portals/76/Publications/EngineerPamphlets/EP_1110-1-8_Vol_01.pdf?ver=2017-05-15-124253-820); [RV1-12 (2021)](https://www.publications.usace.army.mil/Portals/76/Users/182/86/2486/EP%201110-1-8_RV1-12.pdf)

### Inferences
- **Illustrative operating cost (excluding ownership) per operating hour, US, 2025$, my estimate:**

  | Cost line | Cat 773/775 class (~55–65 t) | Cat 777 class (~90–100 t) | Cat 793 class (~230 t) |
  |---|---|---|---|
  | Fuel (Cat medium rate × ~$3.50/gal) | ~$35–38/h | ~$55/h | ~$147/h |
  | Tyres (6 tyres × price ÷ life, see section 4) | ~$20–35/h | ~$35–60/h | ~$40–110/h |
  | Maintenance/repair (fuel share ≈ 40% implies repairs ≈ 0.6–0.9× fuel) | ~$20–35/h | ~$35–50/h | ~$90–130/h |
  | Operator, loaded (BLS + 30% benefits, section 2) | ~$38/h (quarry) | ~$38–51/h | ~$51/h (metal ore); AU ~$70–80+/h |
  | **Total** | **~$115–145/h** | **~$165–215/h** | **~$330–440/h** |
  | **Operator share** | **~25–35%** | **~20–30%** | **~12–15% in the US** |

  The operator share falls as the truck gets bigger. This is the core quarry-autonomy insight: labour is a bigger fraction of the cost per tonne on small trucks.
- Under the 1MERCA shares, fuel at ~40% of non-labour cost means non-labour cost for a 777 is about $55 ÷ 0.40 ≈ $140/h. Adding about $45/h of operator gives roughly $185/h. That is consistent with the table.
- The Skillings labour figure ($0.90 of $2.50–3.50/t, i.e. 25–35%) is broadly consistent with the table for mid-size trucks. I would not rely on it as a primary source.

### Gaps
- No access to CostMine (subscription) or the Caterpillar Performance Handbook's owning and operating cost chapter. I found no free, dated (2022–26), audited cost per hour for the HD465, 773, 775 or 777.
- I did not extract the USACE EP 1110-1-8 truck rows (large PDFs; 2013 price base, so they would need escalating).
- I found no quarry-specific 2022–26 study on load and haul share. The Bell 25–30% figure is unverified, and the €0.34/t figure's date is unknown.

---

## 2. Operator wages and total employment cost (US, Australia, UK/Germany) and operators per truck

### Takeaway
A US quarry truck driver costs about $27/h in wages, or roughly $80k/yr loaded. A US metal-mine driver costs about $36/h, roughly $106k loaded. Australian mine truck operators earn A$50–65/h plus 12% super (about A$100–130k base, more with allowances or FIFO). A 24/7 roster needs about 4.4–5 operators per truck seat. A quarry running 1–2 shifts needs about 1.1–2.2 per truck.

### Cited Findings
**US — BLS OEWS, May 2025 (pulled from the BLS public API, series OEUN…; [OEWS tables](https://www.bls.gov/oes/tables.htm))**
- Heavy and Tractor-Trailer Truck Drivers (53-3032). BLS has no dedicated "haul truck operator" code; mine haul truck drivers are generally classified here. In Nonmetallic Mineral Mining and Quarrying (NAICS 2123): employment 9,690, mean $26.96/h, mean $56,070/yr. — [BLS API series OEUN000000021230053303204](https://api.bls.gov/publicAPI/v1/timeseries/data/OEUN000000021230053303204)
- 53-3032 in Metal Ore Mining (NAICS 2122): employment 1,940, mean $35.67/h, $74,190/yr. — [BLS API OEUN000000021220053303204](https://api.bls.gov/publicAPI/v1/timeseries/data/OEUN000000021220053303204)
- 53-3032 in Coal Mining (NAICS 2121): employment 1,180, mean $34.01/h, $70,730/yr. — [BLS API OEUN000000021210053303204](https://api.bls.gov/publicAPI/v1/timeseries/data/OEUN000000021210053303204)
- 53-3032 across all industries: mean $59,710/yr. — [BLS API OEUN000000000000053303204](https://api.bls.gov/publicAPI/v1/timeseries/data/OEUN000000000000053303204)
- Excavating and Loading Machine and Dragline Operators, Surface Mining (47-5022): national employment 34,480, mean $59,930/yr. In NAICS 2123: employment 16,740, mean $57,220/yr. In metal ore mining: $65,760/yr. — [BLS API OEUN000000000000047502204](https://api.bls.gov/publicAPI/v1/timeseries/data/OEUN000000000000047502204); [OEUN000000021230047502204](https://api.bls.gov/publicAPI/v1/timeseries/data/OEUN000000021230047502204)
- Operating Engineers (47-2073) in NAICS 2123: $54,660/yr. All occupations in NAICS 2123: $66,090/yr mean. — [BLS API OEUN000000021230047207304](https://api.bls.gov/publicAPI/v1/timeseries/data/OEUN000000021230047207304); [OEUN000000021230000000004](https://api.bls.gov/publicAPI/v1/timeseries/data/OEUN000000021230000000004)
- Earlier trend for 47-5022 national mean: $49,850 (2021), $53,490 (2022), $55,500 (2023), $59,930 (2025). — [BLS OES 47-5022 May 2023](https://www.bls.gov/oes/2023/May/oes475022.htm); [May 2022](https://www.bls.gov/oes/2022/May/oes475022.htm)
- Benefits loading: private-industry benefits are 30.0% of total employer compensation ($14.07 of $46.89/h; wages $32.82/h), June 2026. — [BLS ECEC news release](https://www.bls.gov/news.release/ecec.nr0.htm)

**Australia (job ads and aggregators, 2025–26, AUD)**
- Pilbara FIFO dump truck operator (CAT 793 experience), June 2025: A$53–56/h casual plus super. — [Chandler Macleod ad](https://www.chandlermacleod.com/job-details/dump-truck-operator-in-mining-resources-energy-jobs-1631256)
- Bowen Basin (QLD): A$53.16/h casual or A$51.10/h permanent. Multi-skilled operators earn A$56.53/h casual or A$54.33/h permanent. — [WorkPac](https://www.workpac.com/job/dump-truck-operator-298)
- Kalgoorlie open pit: A$40–48/h plus super. — [Employment Hero / Hampton Mining](https://employmenthero.com/jobs/position/hampton-mining-and-civil-dump-truck-operator-binduli-iznli/)
- SA mineral sands: A$63–65/h plus super. — [Randstad](https://www.randstad.com.au/jobs/dump-truck-operator_sandalwood_60107b4c-a34a-4e07-910e-3a5965f3468b/)
- Seek-based midpoint of about A$113k/yr across 49 listings (June 2026 refresh). — [WhatsTheSalary](https://www.whatsthesalary.com/salaries/au/mining-resources-and-energy/mining-operations)
- Modelled average of about A$99,370/yr. — [Jooble](https://au.jooble.org/salary/mining-dump-truck-operator)

**UK (GBP)**
- ONS SOC 8229 "Mobile machine drivers and operatives" covers operators who transport, excavate and grade earth and gravel, i.e. quarry dump truck work. Career sites give a median of about £30.5k–32.4k (experienced up to about £49–50k). These are aggregators, years unclear. One mirror shows an implausible £52.8k median. — [UCAS career page 8229](https://www.ucas.com/explore/career-page/3.1/8229); [Middlesbrough College](https://www.mbro.ac.uk/career/detail/mobile-machine-drivers-and-operatives)

**Germany (EUR)**
- Kipperfahrer (tipper driver): median €3,488/month gross on a 40 h week; quartiles €2,987–4,074. Not quarry-specific and the year is not stated. — [gehalt.de](https://www.gehalt.de/beruf/kipperfahrer)
- Heavy truck drivers, including Muldenkipper (dump trucks): €2,652–3,902/month (2026). — [WageIndicator](https://wageindicator.org/de-de/arbeiten-in-deutschland/jobs-und-gehalt/fahrer-schwerer-lastkraftwagen)

**Operators per truck**
- A conventional fleet needs 4–5 operators per truck to cover a 24/7 roster, including leave, sickness and training. AHS is claimed to cut net labour headcount per truck by about 60% after adding controllers, technicians and network engineers. This is a single low-quality source and is not independently confirmed. — [Skillings.net](https://www.skillings.net/autonomous-haulage-roi-2026-the-hard-data-behind-the-0.50-tonne-savings-target/)
- Mature AHS operations see 50–70% savings in haulage labour. Same caveat. — [Skillings.net](https://skillings.net/autonomous-haul-trucks-cost-savings-2026-roi-data)

### Inferences
- **Loaded cost (wage ÷ 0.70 for 30% benefits):** a US quarry driver is about $38.5/h, or about $80k/yr. A metal-mine driver is about $51/h, or about $106k/yr. Mining benefits are probably above the private-industry average, so these are likely conservative.
- **Australia loaded cost:** A$52/h × 1.12 super ≈ A$58/h before payroll tax, workers' compensation, FIFO travel and camp. Fully loaded is plausibly A$75–90/h, or about US$50–60/h. That is my estimate; FIFO costs are not sourced.
- **Roster arithmetic:**
  - 24/7 is 8,760 h/yr. At about 1,800–2,000 worked hours per FTE (full-time equivalent) after leave and training, that needs 4.4–4.9 operators per truck. This corroborates the "4–5" figure.
  - 2 shifts × 5 days ≈ 4,000 h needs about 2.0–2.2 FTE.
  - A single-shift quarry (about 2,000–2,200 h) needs about 1.1 FTE.
- **Annual labour cost per truck seat:**

  | Setting | Labour cost per truck per year |
  |---|---|
  | US quarry, single shift | ~$90k |
  | US quarry, 2 shifts | ~$170k |
  | US metal mine, 24/7 | ~$470–520k |
  | Australian mine, 24/7 | ~US$0.6–0.7M |
  | UK quarry, 2 shifts (est. £32k × 1.25 on-costs × 2.1) | ~£84k |

### Gaps
- No direct BLS haul-truck code. I did not pull 53-7032 (excavating and loading machine operators, all industries) or 53-7041/47-5049 (mine shuttle car operators).
- I could not access the 2025 ONS ASHE SOC 8229 table, the German Entgeltatlas, or a UK/DE quarry collective agreement. I found no Fair Work award rate (Mining Industry Award / Black Coal award).
- I found no authoritative roster study; the 4–5 figure is corroborated only by my arithmetic.

---

## 3. Utilisation, availability, payload factor and labour shortage evidence

### Takeaway
Benchmarks are inconsistent and mostly vendor-sourced. A reasonable planning range is physical availability of 85–92% and effective utilisation of 60–80% of available time. Mine trucks typically run about 5,000–6,000+ operating hours per year (24/7); a 1–2 shift quarry truck runs about 1,500–3,500. Labour shortage evidence is qualitative but consistent across the US, UK and Australia.

### Cited Findings
- GMG time-usage model at one underground mine: physical availability 95.7%, use of availability 83.1%, effective utilisation 77.7%. A single site. — [KoreaScience 2022](https://koreascience.or.kr/article/JAKO202225851629278.pdf)
- Haul truck availability, vendor benchmarks: world class 95%, industry average 85%, target 92%+. Others say the industry average is 72–78% vs 92–94% world class. Utilisation is quoted at 70–85%, or "many fleets around 60% effective". Definitions are inconsistent. — [HeavyVehicleInspection](https://heavyvehicleinspection.com/industries/mining/mining-equipment-availability-utilization-metrics); [HVI uptime benchmark](https://heavyvehicleinspection.com/fleet-management/uptime/mining-uptime-benchmark)
- Worked example: a truck scheduled 6,000 h/yr gains about 700 production hours by moving from 80% to 92% availability. — [HeavyVehicleInspection](https://heavyvehicleinspection.com/industries/mining/mining-equipment-availability-utilization-metrics)
- Effective utilisation of 72% for manned fleets vs 88% for autonomous fleets. Low-quality source, methodology unclear. — [Skillings.net](https://skillings.net/autonomous-haulage-productivity-benchmarks-for-2026)
- Rio Tinto's Pilbara AHS fleet (~130 trucks) reportedly saves more than $100M/yr. This is a secondary claim from a low-quality source. — [Skillings.net](https://skillings.net/autonomous-haul-trucks-cost-savings-2026-roi-data)

**Labour shortage evidence**
- **US:** heavy equipment operators are hard to fill for 77% of construction firms (AGC/NCCER 2025 Workforce Survey; construction, not quarrying). — [AGC 2025 Workforce Survey](https://www.agc.org/sites/default/files/users/user21902/2025_Workforce_Survey_National_FINAL_m.pdf)
- **US aggregates:** a December 2024 industry forum cites an aging workforce and fewer young entrants, with operations struggling to meet production and labour costs rising. — [Rock Products Q&A Forum 2024](https://rockproducts.com/2024/12/03/the-quarry-aggregates-qa-forum-2024/)
- **US aggregates:** NSSGA frames the problem as recruitment, retention and retirement, with competition from tech and gig work. — [NSSGA blog](https://www.nssga.org/get-involved/blog-rocks/putting-work-workforce)
- **US aggregates (older):** in one state association, a "very high percentage" of members are over 50. — [Pit & Quarry](https://www.pitandquarry.com/help-wanted-industry-response-to-the-labor-shortage/)
- **UK:** the Minerals Matter Labour Market Intelligence Study (Pye Tait, with MPA, Institute of Quarrying, BAA and MPQC) surveyed 300+ companies in Jul–Sep 2023 (91 responses), with results at Hillhead 2024. I could not see driver-specific figures. — [Agg-Net](https://www.agg-net.com/news/minerals-matter-labour-market-information-survey)
- **UK:** the Institute of Quarrying sees an urgent need to replace a "vast proportion" of the workforce due to retirement (article several years old). — [Agg-Net](https://www.agg-net.com/resources/articles/education-training/getting-age-ready-for-quarryings-future)
- **Australia:** the 2025 Occupation Shortage List puts 29% of occupations in shortage (down from 33% in 2024). Seven Machinery Operators and Drivers occupations have been in persistent shortage from 2021 to 2025; the report does not name them. — [JSA 2025 OSL Key Findings](https://www.jobsandskills.gov.au/sites/default/files/2025-10/2025%20OSL%20Key%20Findings%20Report.pdf)
- **Australia:** mining leaders ranked skills shortages as the top priority for 2024. — [MiningNews / Minerals Council of Australia](https://miningnews.net/company/minerals-council-of-australia)

### Inferences
- **Annual hours:**
  - A 24/7 mine truck: 8,760 × ~88% availability × ~75% utilisation ≈ 5,800 operating h/yr.
  - A 2-shift quarry truck: ~4,000 scheduled h × 0.88 × 0.75 ≈ 2,600 h.
  - A single-shift quarry truck: ~1,300–1,500 h.
- **Autonomy upside in quarries** may come as much from adding unmanned extra shifts and higher utilisation (no breaks or shift changes) as from replacing existing operators.

### Gaps
- No credible public payload-factor benchmark (e.g. actual vs rated payload) was found.
- No GMG-standard industry survey of truck utilisation was found.
- No quantitative quarry-specific vacancy data was found from MPA, NSSGA or MCA. The LMIS 2023 executive summary was not retrieved.

---

## 4. Fuel consumption per hour (55–100 t and larger) and tyre cost per hour

### Takeaway
Cat telematics data (2022) gives medium-load fuel burn of about 37 L/h (9.9 gal/h) for the 773G, about 41 L/h for the 775G, about 59 L/h for the 777G, and about 159 L/h for the 793F. At about $3.50/gal that is roughly $35, $38, $55 and $147 per hour. Tyres on a 777-class truck likely run about $35–60/h for the set of six. This is an inference from price and life data, not a direct source.

### Cited Findings
- **Caterpillar Performance Handbook fuel tables** (© 2022, "America North", generated 9 Dec 2022). Values come from VisionLink telematics, assuming 50%/30%/10% idle for low/medium/high load. — [Cat Performance Handbook fuel consumption PDF via Wheeler Cat](https://wheelercat.com/wp-content/uploads/2023/01/Cat-Performance-Handbook-from-VST-fuel-consumption-2022-12-09T21-20-09.pdf)

  | Model (approx. payload) | Low (gal/h) | Medium (gal/h) | High (gal/h) |
  |---|---|---|---|
  | 770G (~36 t) | 5.22 | 6.70 | 8.18 |
  | 772G (~47 t) | 5.88 | 7.60 | 9.33 |
  | 773G (~55 t) | 7.66 | 9.92 | 12.17 |
  | 775G (~65 t) | 8.26 | 10.73 | 13.21 |
  | 777G (~90–100 t) | 12.00 | 15.63 | 19.27 |
  | 777-07 | — | 13.13 | — |
  | 785-08 (~140 t) | 17.65 | 23.30 | 28.95 |
  | 789-07 (~180 t) | 26.74 | 35.72 | 44.71 |
  | 793F (~230 t) | 32.08 | 41.94 | 51.80 |
  | 797F (~360 t) | 42.92 | 56.73 | 70.54 |
  | 745 ADT (articulated) | — | 7.03 | — |
  | 740 ADT (articulated) | 6.14 | 7.84 | 9.54 |

  DEF (diesel exhaust fluid) adds 3–5% of fuel volume on the newer models.
- **Komatsu HD465-7R (~55 t):** 51.7 L/h modelled vs 51.9 L/h measured, hauling overburden at an Indonesian mine (2024). — [Indonesian Mining Journal (Tekmira)](https://jurnal.tekmira.esdm.go.id/index.php/imj/article/view/1578)
- **Komatsu HD465-10:** up to 1% better fuel efficiency than the HD465-8; 610 kW gross. — [Komatsu HD465-10 page](https://www.komatsu.com/en/products/trucks/mechanical-trucks/hd465-10/)
- **Diesel price:** the EIA Nov 2025 STEO (Short-Term Energy Outlook) projects US on-highway diesel at $3.66/gal for 2025 and $3.50/gal for 2026; 2024 averaged $3.76/gal. Off-road dyed diesel avoids road taxes. — [Rigzone summarising EIA STEO, Dec 2025](https://www.rigzone.com/news/eia_sees_gasoline_diesel_price_dropping_in_2025_2026-02-dec-2025-182432-article/)
- **Tyres:**
  - 40.00R57 (793F / 830E / T264) premium tyres cost $30–45k each; budget tyres $18–25k.
  - Premium ultra-class tyres cost about $7–18 per operating hour. Ambiguous whether this is per tyre; most likely it is.
  - Tyre life is 4,000–6,000 h in iron ore and 8,000–10,000 h in coal.
  - Source: [PitCrew.ai, Sep 2026](https://pitcrew.ai/otr-tyre-costs/)
- Tyre life can halve versus rated expectations on abrasive roads in summer heat. — [1MERCA](https://onemerca.com/blog/cat-777-haul-truck-operating-cost-per-hour-ghana-gold-mining/)
- Some haul truck tyres cost $50,000. — via search summary; low confidence

### Inferences
- **Fuel cost per hour** at $3.50/gal, medium load:
  - 772G: ~$27/h
  - 773G: ~$35/h
  - 775G: ~$38/h
  - 777G: ~$55/h
  - 785: ~$82/h
  - 793F: ~$147/h
- **Litres per hour** (medium): 773G ≈ 37.5 L/h; 777G ≈ 59 L/h; 793F ≈ 159 L/h. The HD465 field value of ~52 L/h sits between the Cat medium (37.5) and high (46) rates for the 773G, consistent with a hard overburden duty.
- **Tyre cost per hour** (6 tyres per rigid truck, price ÷ life):
  - 793 class: 6 × $30–45k ÷ 4,000–8,000 h ≈ $22–68/h. The PitCrew per-tyre range of $7–18/h × 6 gives $42–108/h.
  - 773/775 class (24.00R35) and 777 class (27.00R49): I found no sourced prices. Assuming about $10–20k for 24.00R35 and about $20–30k for 27.00R49, with 4,000–6,000 h life, gives roughly $10–30/h and $20–45/h per truck. These are assumptions, flagged.
- **Fuel price sensitivity:** in Europe/UK (red diesel abolished for quarries in the UK from April 2022) or Australia (with the fuel tax credit), fuel cost per litre differs materially. Recompute locally.

### Gaps
- No primary price for 24.00R35 or 27.00R49 tyres was found.
- No Komatsu official L/h figure was found.
- The 2025 EIA actual annual diesel average was not confirmed (only the forecast).
- No off-road diesel price series or Australian, UK or EU diesel prices were collected.
