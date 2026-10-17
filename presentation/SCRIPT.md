# R² pitch: bullet cheat sheet (5 slides, about 2 min + live demo)

**Setup:** deck on slide 1. Tabs: `localhost:8090/view` + `localhost:8090` (radio). RELEASE pressed. Pod on the table.

**1 · Cover**
- R², Rocky Robotics: mining autonomy for quarries

**2 · The opportunity** (all the startup facts on one slide)
- 39 Bt quarried a year, a driver in every truck
- OEM autonomy: $2.3M per truck, big mines only; almost none under 100 t
- Safety: powered haulage = 25–43% of US mining deaths; BHP saw 90%+ fewer serious truck incidents with autonomy
- Driver = 25–35% of quarry-truck cost; autonomy cuts haul cost 13–20%
- Serviceable market $0.3–1.2B/yr; $90k kit + $4k per truck per month
- Payback ~1 year for a 10-truck quarry on 2 shifts (~4 months at 24/7)

**3 · Our solution**
- Bolt-on sensor pod on the trucks and excavators quarries already own
- Vision policy drives; keep-in-bounds safety layer
- Radio dispatch by voice; every machine answers back
- "Everyone stop" halts the site
- New site = a drone flight; fleet footage keeps the sim current

**4 · The tech behind the demo (built today)**
- Drone scan → simulator in seconds; NVIDIA Isaac Sim; voice radio; live ops view; RL environment
- Hardware-ready: sensors match the pod, one protocol for sim and truck, drive-by-wire ready
- Next: pod hardware, ISO 17757 safety case

**5 · Live demo**
- `/view`, press `2` for the cameras
- "Excavator one, start digging." → "Truck one, go unload at excavator one." → "Truck one, start digging." (it talks back) → a judge's own command → "Everyone stop."
- "R². Mining autonomy. Thank you."

**Fallbacks:** quick buttons or the text box; simulated mode; 20 s of the video.
**Q&A:** pod = concept prop · pricing = our model · ISO 17757 · vs Pronto: drone-scan onboarding, voice ops, sim fed by fleet footage
