# Bucket material

Dirt and rock in excavator buckets through the dig cycle, the dump stream, and the load in the Komatsu HD465 bed. Visual only (no collision). Constants: `spec.md` "Bucket material".

## Run

| What | Command |
|---|---|
| Tests (Mac) | `uv run --with numpy --with pytest pytest isaac/material/tests -q` |
| Demo stage (watson) | `downloads/isaac/material-work/run.sh` runs `isaac/material/demo.py --out DIR --cache DIR [--cycles 3] [--perf] [--no-material]` in `nvcr.io/nvidia/isaac-sim:6.2.0` |

## API

| Call | Meaning |
|---|---|
| `BucketMaterial(stage, excavators_cfg, trucks_cfg, config=None, sites=None)` | `excavators_cfg`: `[{id, ground?, colour?}]` (`ground` = Isaac Z of the excavator base); `trucks_cfg`: `[{id, prim?}]` (`prim` = truck Xform; the load becomes `prim/material_load`); `sites`: `[(x, y, radius)]` Isaac XY of crusher/dump sites (`sites_from_world(world)`) |
| `update(dt, excavators, trucks)` | `excavators`: `[(id, bucket_4x4, phase, phase_t)]`, phase in `reach dig lift swing dump return hold` or a float cycle phase; `trucks`: `[(id, truck_4x4, under_excavator_id or None)]` |
| `empty_truck(id)` | slide the load out of the rear over 2.5 s |
| `load(id)` / `tonnes(id)` | bed load fraction of 55 t / tonnes |
| `meshes()` | `[(truck_id, world points, triangles)]` of non-empty bed loads, for raycasts |
| `aim_points()` | `{excavator_id: bed centre XY}` of trucks flagged under that excavator, for `ExcavatorRig.aim` |
| `face_colour(world_dir, x, z)` | linear RGB from `minimap.png` around a face |

| Matrix | Frame |
|---|---|
| `bucket_4x4` | rigged GLB `bucket` node local (origin at the stick pin, glTF axes, unscaled) → Isaac world. `rig.ExcavatorRig.bucket()` returns it |
| `truck_4x4` | truck prim world matrix, local X lateral, Y forward, Z up (the frame of the converted truck USD) |

numpy 4x4 with column vectors or a `Gf.Matrix4d` (row vectors) both work.

## Rig

| Item | Value |
|---|---|
| `ExcavatorRig(id, base, period=8, dump_yaw=1.7, phase=None, lift_deg=0)` | `base = rig.base_matrix(x, y, z, yaw_deg)` in sim (Unity) coordinates, same placement as `scene.py` |
| `step(dt, digging)` | phase advance and joint tracking like `MineExcavatorRig` |
| `state()` | `(phase, phase_t)` for `update` |
| `matrices()` | world matrices of `tracks house boom stick bucket`; meshes from `rig.part_glbs()` are in each node's local frame |
| `aim(xy)` | swing the dump yaw to a truck |

## Technique

| Part | How |
|---|---|
| Bucket heap | 9 × 24 column grid over the bucket opening (cavity from the rigged GLB shell profile); fill from a level plane solved by bisection for the held volume, heap dome above the lip when upright; point writes only |
| Fill | during `dig`, mass ramps to a random 0.92–1.06 pass; the face pile is re-dug first |
| Spill | capacity under gravity (world up in the bucket frame) below the held volume → particles; cohesion holds 30 % before flow starts, pour limited to 0.9 s |
| Stream | numpy ballistic particles (pool 768), rendered as a PointInstancer of 3 procedural rocks with spin |
| Landing | particles into a truck bed footprint below the load surface deposit with a 3 × 3 kernel; on the ground they feed the face pile |
| Bed load | height field over the derived floor; 37° repose relaxation while active; empties by sliding toward the tail |
| Dust | optional translucent puffs (`max_dust`, default 0: dust is left to `isaac/fx`) |

## Files

| File | Isaac | Role |
|---|---|---|
| `rig.py` | no | dig cycle keys, forward kinematics, `ExcavatorRig`, rigged part GLBs |
| `geometry.py` | no | bucket cavity, HD465 bed floor, grids, rock meshes |
| `model.py` | no | mass, particles, bed and pile state |
| `stage.py` | pxr | `BucketMaterial`, prims and per-step writes |
| `demo.py` | yes | test stage: one excavator, one truck, 4 cameras |
| `tests/` | no | conservation, repose, determinism, budget |
