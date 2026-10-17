# R² · Rocky Robotics: demo script

## Part 1: prerecorded video (60 s voiceover + shot list)

| Time | Voiceover | Shot |
|---|---|---|
| 0:00–0:08 | "Quarries move 39 billion tonnes of rock a year, almost all in trucks with a driver in every seat." | Drone scan of the real quarry, slow orbit (`/view` overview) |
| 0:08–0:16 | "Autonomy exists, but it was built for giant 24/7 mines at about $2.3 million a truck. Mid-size quarries are left behind." | Close-up on a Komatsu HD465 cab camera |
| 0:16–0:28 | "R² · Rocky Robotics retrofits the trucks a quarry already owns with a sensor pod: camera, lidar, compute." | The physical sensor pod, then the pod on a virtual truck |
| 0:28–0:40 | "We turn a drone scan of any site into a simulator in ten seconds: haul roads, berms, grades and loading points, built to industry standards, running in NVIDIA Isaac Sim, where a vision policy learns to drive." | Scan → road ribbons appearing → truck and excavator cameras |
| 0:40–0:52 | "A quarry driver is up to a third of truck running cost. Autonomy cuts haul cost 13–20%. A 10-truck quarry on two shifts pays us back in about a year." | Excavator digging at the face and dumping into a truck; trucks hauling |
| 0:52–1:00 | "And you don't program it. You talk to it." | Phone radio push-to-talk screen, R² logo |

## Part 2: live demo (about 3–4 min)

**Setup:** projector on `/view` (aerial view left; truck + excavator cams right; keys 1/2/3 switch layouts). Team dashboard with lidar open in a second tab. Phone radio open. Everything idle (RELEASE pressed). Physical sensor pod on the table.

| Time | Who | Say / do | What they see |
|---|---|---|---|
| 0:00–0:20 | You | "This is a real limestone quarry, drone-scanned and turned into our sim: haul roads, loading points, the crusher. Three Komatsu HD465s on the ready line, three excavators at the faces." Drag or zoom the overview, then double-click to reset. | Aerial quarry, labelled sites, yellow haul roads |
| 0:20–0:35 | You | "Each machine has cameras, and every truck has lidar, matching the pod on the table." Press `2`; switch to the dashboard lidar tab for a few seconds, then come back. | Cab and excavator views, lidar point cloud |
| 0:35–0:50 | Judge | Hand over the phone. **"Excavator one, start digging."** | Excavator swings to the rock face and digs. Radio, in its own voice: "Copy, Excavator One digging at Lower Face." |
| 0:50–1:15 | Judge | **"Truck one, go unload at excavator one."** | Truck One leaves the ready line and follows the haul road; the cab cam shows the road. Radio: "Copy, Truck One heading to Excavator One." |
| 1:15–1:40 | You | While it drives: "Their words become goals. The vision policy steers on camera and lidar, and a safety layer keeps every truck on the drivable road." | Truck arrives; the excavator loads it ("Excavator One loading Truck One") |
| 1:40–2:05 | Judge | **"Truck one, haul to the crusher."** or **"Truck two, go to the middle face."** | Second machine moves; truck bed tips at the crusher |
| 2:05–2:30 | Judge | Free speech. Let them improvise ("send the second truck to fill up with fuel", "status report"). | The LLM interprets loose phrasing; every machine reports in its own voice |
| 2:30–2:45 | You or judge | **"Everyone stop."** | All trucks halt, excavators hold: "Truck One, stopped…" |
| 2:45–3:30 | You | Pod in hand: "The retrofit pod bolts onto trucks a quarry already owns. A 10-truck quarry on two shifts pays it back in about a year, in four months at 24/7. A new site takes a drone flight, not months of mapping. We're raising $7M for three paid pilots and the ISO 17757 safety case. R². Mining autonomy." | R² logo / business slide |

**Tips**
- Mock trucks drive up to 8 m/s, so routes of 300–700 m take 40–90 s. Keep talking over the drive, or pick the nearest targets (Lower Face, Waste Dump).
- If the judge's command isn't understood, the radio says "say again". Nudge them toward a quick-button phrase.
- **If something fails:** use the phone's quick buttons, or type the command. If the brain or sim drops, the radio keeps running in simulated mode and `/view` still moves. Last resort: replay the video.

## Backup answers
- Market: 3,832 autonomous haul trucks worldwide (+84% in a year), almost none under 100 t. US aggregates is about $40B across ~10,000 quarries and pits.
- Model: $90k kit per truck, $75k site onboarding, $4k per truck per month. Seed ask: $7M for 3 paid pilots and an ISO 17757 safety case.
- Safety: powered haulage is 25–43% of US mining deaths (MSHA).
- Competition: Pronto (Atoms), Sensmore and Caterpillar. Our wedge is onboarding any site from a drone scan, plus natural-language operations.
- Honest status: sim, radio, voice and the policy loop are built. The pod is a concept. Pricing is our assumption.

**Credits (end card):** quarry scan by 3Dnovator, Komatsu HD465 by drcrazzie, excavator by Dioramass design (CC-BY 4.0).
