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

## Part 2: live demo (about 60–90 s)

**Setup:** laptop on the projector at `/view` (aerial view left, truck + excavator cams right). Phone radio open. Everything idle (RELEASE pressed).

1. **Hand a judge the phone.** "Hold the button and give the site an order. Try: 'Excavator one, start digging.'"
   - The excavator swings to the rock face and digs. Radio: *"Copy, Excavator One digging at Lower Face."*
2. **Judge:** "Truck one, go unload at excavator one."
   - Truck One pulls out of the ready line and follows the haul road. Radio: *"Copy, Truck One heading to Excavator One at Lower Face."*
3. **Let them improvise** (OpenAI parses free speech): e.g. "Send truck two to the crusher" or "Status report."
4. **Close on safety:** "Everyone stop." All trucks halt and excavators hold. "That's R². Talk to your quarry."

**If something fails:** use the quick buttons on the phone (same commands), or type the command. If the brain or sim drops, the radio keeps running in simulated mode and `/view` still shows the trucks moving.

## Backup answers
- Market: 3,832 autonomous haul trucks worldwide (+84% in a year), almost none under 100 t. US aggregates is about $40B across ~10,000 quarries and pits.
- Model: $90k kit per truck, $75k site onboarding, $4k per truck per month. Seed ask: $7M for 3 paid pilots and an ISO 17757 safety case.
- Safety: powered haulage is 25–43% of US mining deaths (MSHA).
- Competition: Pronto (Atoms), Sensmore and Caterpillar. Our wedge is onboarding any site from a drone scan, plus natural-language operations.
- Honest status: sim, radio, voice and the policy loop are built. The pod is a concept. Pricing is our assumption.

**Credits (end card):** quarry scan by 3Dnovator, Komatsu HD465 by drcrazzie, excavator by Dioramass design (CC-BY 4.0).
