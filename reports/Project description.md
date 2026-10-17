# R² · Rocky Robotics: Mining autonomy

## Project description
Quarries move 39 billion tonnes of rock a year, and almost every haul truck still needs a driver in the seat. The autonomy that exists was built for giant 24/7 mines and costs about $2.3M per truck, so mid-size quarries are left behind.

R² retrofits the trucks and excavators a quarry already owns with a sensor pod (cameras, lidar, compute), and lets you run the whole site by **talking to it over the radio**. Say "Excavator one, start digging" or "Truck one, go unload at excavator one": the excavator digs at the rock face and loads the truck, and the truck drives the haul road, steered by a vision policy on camera and lidar. Every machine answers in its own voice. Give an impossible order and the driver talks back: "I can't dig, I'm a haul truck. Want me to go to Excavator One?" "Everyone stop" halts the site.

Onboarding a new site takes a drone flight, not months of mapping. Our pipeline turns a drone scan of any quarry into a simulator in seconds: collider, haul roads, berms, grades, loading and dump points, sized to industry design standards for the real truck (Komatsu HD465). The policy trains and runs there before it touches a real machine. A 10-truck quarry on two shifts pays the kit back in about a year.

## Tech used
- **Environment:** drone photogrammetry scan imported into a sim world, with a nav grid, a haul-road graph and an industry-standard mine design spec (ISO/MSHA-informed bench, road and berm geometry). A procedural forest and terrain extend the scan; World Labs Marble provides the sky panorama. Python (numpy, scipy, trimesh, Embree).
- **Simulation:** NVIDIA Isaac Sim 6.2 on a DGX Spark (GB10), plus Unity 6 URP, with a USD stage, RTX cameras and Warp raycast depth/lidar. Includes a rigged excavator (house/boom/stick/bucket IK), truck bed tipping, keep-in-bounds safety, and a Gymnasium RL environment.
- **Autonomy:** a vision driving policy and a navgrid route planner ("brain", FastAPI) over a TCP sim protocol (camera frames, depth, lidar, actions).
- **Voice radio:** ElevenLabs speech-to-text (Scribe) and per-machine TTS voices (Flash v2.5); OpenAI GPT-4.1-mini tool calling turns free speech into structured commands, with a regex/fuzzy fallback; FastAPI and WebSockets; a web push-to-talk UI.
- **Ops views:** three.js live 3D aerial view with truck and excavator cab cameras, plus a web dashboard with H.264 camera streams and a lidar point cloud.
- **Infra:** Docker Compose, Cloudflare Tunnel, GitHub; JSON Schemas for all data.
- **Research:** sourced market and unit economics (MSHA, BLS, operator SEC filings) behind the business case.
