# R² pitch: bullet cheat sheet (6 slides, about 2.5 min + live demo)

**Setup:** deck on slide 1. Tabs: `localhost:8090/view` + `localhost:8090` (radio). RELEASE pressed. Pod on the table.

**1 · Cover**
- R², Rocky Robotics: mining autonomy for quarries

**2 · Problem**
- 39 Bt of rock quarried a year, a driver in every truck
- OEM autonomy is about $2.3M per truck, built for giant 24/7 mines
- ~0 autonomous trucks under 100 t (quarry size)
- Powered haulage = 25–43% of US mining deaths

**3 · Solution**
- Retrofit pod on trucks and excavators quarries already own: cameras, lidar, compute
- A vision policy drives; a safety layer keeps trucks on the road; "Everyone stop" halts the site
- New site = a drone flight; fleet footage keeps the site sim current
- Dispatch by radio in plain speech; machines answer back

**4 · Economics**
- Driver = 25–35% of quarry-truck running cost (vs 12–15% on big mine trucks)
- Autonomy operators: 13–20% lower haul cost (Rio Tinto, BHP)
- 10-truck quarry payback: ~1 year on 2 shifts, ~4 months at 24/7
- Safety: BHP saw 90%+ fewer significant truck incidents; nobody in the cab at the face

**(cut) Market**
- TAM $1.3–5.1B/yr; SAM ~$0.3–1.2B/yr (US/EU/AU multi-shift)
- Autonomous haul trucks +84% last year, almost none quarry-size
- $90k kit + $75k onboarding + $4k per truck per month

**(cut) Why R²**
- OEMs automate only their own big trucks; Pronto and Sensmore prove retrofit demand
- New site = a drone flight, not months of mapping
- Fleet camera + lidar footage feeds the sim, so it stays current and the policy keeps learning

**(cut) How it works**
- Scan → Simulate (Isaac Sim, industry-standard roads, berms, grades) → Train (vision policy) → Talk (ElevenLabs + LLM)

**5 · Built today**
- Drone scan → sim world in seconds
- Isaac Sim + Unity: cameras, lidar, rigged excavators, tipping beds, keep-in-bounds
- Voice radio; machines talk back; "Everyone stop"
- Live 3D ops view, Gym RL environment
- Physical-ready: sim sensors = pod spec (90° 640×360 camera; lidar 120°×30°, 10 Hz, 100 m), same protocol for sim and truck, policy outputs throttle + steer
- Next: pod hardware, drive-by-wire, ISO 17757 safety case

**6 · Live demo**
- Show `/view`, press `2` for the cameras
- "Excavator one, start digging."
- "Truck one, go unload at excavator one."
- "Truck one, start digging." → it talks back
- A judge's own command
- "Everyone stop." → "R². Mining autonomy. Thank you."

**Fallbacks:** quick buttons or the text box; the radio's simulated mode if the sim drops; 20 s of the video.

**Q&A:** pod = concept prop · pricing = our model · ISO 17757 safety case · vs Pronto: drone-scan onboarding + voice ops + sim fed by fleet footage
