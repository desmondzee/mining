# R² · Rocky Robotics: handoff (hackathon winner, 2026-10-17)

## What exists
| Area | Where | State |
|---|---|---|
| Drone scan → sim world (roads, berms, grades, load/dump points, design checks) | `envgen/`, `data/worlds/quarry/` | Working; tests in `envgen/tests/` |
| Isaac Sim (team pipeline) | `isaac/sim/` (Des) + our add-ons `isaac/excavators.py`, `isaac/rl_env.py`, `isaac/patches/` | Add-ons tested locally (76 tests); not yet run on watson |
| Unity integration | `unity/Assets/MineSim/` | Written, never compiled |
| Voice radio (ElevenLabs + OpenAI) | `radio/` | Working; 234 pytest + 71 parser tests |
| Live 3D ops view | `envgen/preview/` (served at radio `/view`) | Working |
| Data schemas + validator | `schema/` | 61 checks pass |
| Business case, financial research, pitch | `reports/`, `presentation/`, `research_notes/` | Final |
| Brand (logo, mark, pod renders) | `assets/brand/` | Final |

## Do next (in order)
1. **Rotate every API key** used today: OpenAI, ElevenLabs, World Labs and the Hugging Face token. All were pasted into chats or terminals.
2. **Make the GitHub repo private** (owner: Settings → Danger Zone → Change visibility). It is currently public.
3. **Agree the license** (`LICENSE`, proprietary, all rights reserved) as a team, and decide company ownership and IP assignment between team members.
4. **Talk to a lawyer about a provisional patent** on the core method (drone scan → standards-based sim → vision policy, continuous camera/lidar site updates, voice operations) before further public demos.
5. **Keep the CC-BY credits** (`assets/models/CREDITS.md`) in anything public: 3Dnovator (quarry scan), drcrazzie (Komatsu HD465), Dioramass design (excavator).
6. **Technical next steps:**
   - first Isaac run on watson (`isaac/run_watson.sh`, `isaac/CHECKLIST.md`)
   - compile the Unity package
   - pod hardware prototype
   - drive-by-wire interface
   - ISO 17757 safety case
7. **Follow up with the judges and sponsors** while it's fresh. Sensmore and microagi are the most relevant (see `reports/Business case.md`).

## Run it again
- Radio + views: `bash radio/run.sh`, then open `localhost:8090` (radio) and `localhost:8090/view` (aerial). Keys go in `radio/secrets/` (`zsh radio/add_keys.sh`).
- Docker: `bash radio/docker-up.sh`.
- Regenerate the quarry: `python -m envgen.scan data/worlds/quarry/_src/mining_quarry.glb --id quarry --extent 725 --cell 2.5 --layout hillside`, then `python -m envgen.exc_aim data/worlds/quarry`. The source scan is not in git; it's in the private HF dataset `pi-hack-mining/mine-assets`, or re-download it from Sketchfab.
