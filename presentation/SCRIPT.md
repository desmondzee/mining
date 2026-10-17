# R² pitch script (about 3 min of slides + 2–3 min live demo)

**Before you start:** the deck is open on slide 1. In other tabs: `localhost:8090/view` (aerial view) and `localhost:8090` (radio). RELEASE has been pressed so everything is idle. Physical sensor pod on the table.

---

**1 · Cover: Rocky Robotics (10 s)**
"Hi, we're R², Rocky Robotics. We build mining autonomy for the sites the big players ignore: quarries."

**2 · Problem: Quarries are left out of autonomy (30 s)**
"Quarries move 39 billion tonnes of rock a year, almost all of it in trucks with a driver in every seat.
Autonomy exists, but it was built for giant 24/7 mines, and it costs about 2.3 million dollars a truck.
The trucks quarries actually run, under 100 tonnes, are almost never autonomous.
And this matters for people: powered haulage is behind a quarter to 43% of US mining deaths."

**3 · Solution: Retrofit the trucks they own (25 s)**
"So we don't sell new trucks. We bolt a sensor pod (cameras, lidar, compute) onto the trucks and excavators a quarry already owns.
A vision policy drives on that camera and lidar.
And you don't program it. You dispatch it over the radio, in plain speech, and every machine answers back."

**4 · Economics: The driver is the cost autonomy removes (30 s)**
"On a quarry-sized truck, the driver is 25 to 35% of the hourly running cost, roughly double the share on a big mine truck. That's why this works for quarries.
Operators running autonomy, like Rio Tinto and BHP, reported 13 to 20% lower haul costs.
On our pricing, a 10-truck quarry running two shifts pays us back in about a year, and in four months if it runs around the clock."

**5 · Market: Kit plus subscription (25 s)**
"Quarry trucks and loaders are a 1.3 to 5 billion dollar a year opportunity. About a billion of it is serviceable today, in multi-shift sites across the US, Europe and Australia.
Autonomous haul trucks grew 84% last year, but almost none of them are quarry-sized.
We charge for a kit, a site onboarding fee, and four thousand dollars per truck per month."

**6 · Why R²: Fastest site onboarding, run by voice (30 s)**
"The OEMs only automate their own big trucks. Pronto and Sensmore retrofit, which proves the demand.
Our edge is speed and simplicity. A new site is a drone flight, not months of mapping.
And it keeps learning: every truck's camera and lidar footage flows back into the site simulation, so the digital quarry stays current as the pit is dug, and the policy gets better every shift."

**7 · How it works: A drone flight to an autonomous site (25 s)**
"Here's the pipeline. We scan the quarry with a drone. In seconds that becomes a simulator: haul roads, safety berms and grades, all sized to industry standards for the real truck, running in NVIDIA Isaac Sim.
The vision policy trains there. On site, ElevenLabs transcribes the radio, an LLM turns speech into goals, and the machines reply.
Let me show you."

**8 · Live demo: Give the quarry an order (2–3 min)**
*(Switch to `/view`, then hand a judge the laptop with the radio tab, or say it yourself. Hold Space to talk.)*
- "This is a real limestone quarry, drone-scanned. Three Komatsu haul trucks on the ready line, three excavators at the faces." *(Drag or zoom the view, then press `2` for the cab and excavator cameras.)*
- Judge: **"Excavator one, start digging."** The excavator digs at the face and replies in its own voice.
- Judge: **"Truck one, go unload at excavator one."** The truck pulls out on the haul road. *(While it drives:)* "Their words became goals. The policy steers on camera and lidar, and a safety layer keeps every truck on the road."
- Judge: **"Truck one, start digging."** → *"I can't dig, I'm a haul truck. Want me to go to Excavator One?"* "It pushes back when an order makes no sense."
- Let them try one in their own words, e.g. "send the second truck to fill up with fuel".
- Close: **"Everyone stop."** Everything halts. "That's R². Mining autonomy. Thank you."

---

**If something breaks:** use the quick buttons or the text box on the radio page. If the sim drops, the radio keeps going in simulated mode. Last resort: play 20 s of the video.

**Likely questions**
- *Is the hardware real?* The pod is a concept prop. The sim, voice dispatch and policy loop are built and running.
- *Pricing?* $90k kit, $75k site onboarding, $4k per truck per month. It's our model; no competitor publishes prices.
- *Safety and regulation?* We'd build an ISO 17757 autonomous-machine safety case. "Everyone stop" and keep-in-bounds are built in.
- *Why not just Pronto?* Faster onboarding from a drone scan, voice ops, and a sim that updates from fleet footage.
