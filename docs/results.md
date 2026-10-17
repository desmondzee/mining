# Policy evaluation results

All rows: 50 episodes per row. Label "435 m / old default" means the old world data
(435 m quarry, old default ramp) that the server used on 2026-10-17.

Columns: success / collision / stops_per_ep / time_to_goal_s / near_miss_rate.

## Default world

| label | code | policy | success | collision | stops_per_ep | time_to_goal_s | near_miss_rate | source |
|---|---|---|---|---|---|---|---|---|
| 435 m / old default | e74cb2b | expert | 0.860 | 0.120 | 0.060 | 36.135 | 0.480 | logs/v2.log |
| 435 m / old default | e74cb2b | bc_v2 | 0.880 | 0.100 | 0.100 | 38.018 | 0.500 | logs/v2.log |
| 435 m / old default | e74cb2b | ppo_v2 | 0.740 | 0.080 | 0.300 | 35.416 | 0.380 | logs/v2.log |
| 435 m / old default | 82d48e8 | ppo_v3 | 0.920 | 0.080 | 0.080 | 37.42 | 0.420 | logs/ppo3.log |
| 435 m / old default | c3d2302 | ppo_v4 | 0.860 | 0.080 | 0.100 | 37.149 | 0.420 | logs/eval_ppo4.log |
| 435 m / old default | c3d2302 | bc_v3 | 0.860 | 0.100 | 0.080 | 37.481 | 0.460 | logs/eval_bc3.log |

## Quarry, uncarved (6 site pairs)

| label | code | policy | success | collision | stops_per_ep | time_to_goal_s | near_miss_rate | source |
|---|---|---|---|---|---|---|---|---|
| 435 m / old default | e74cb2b | expert | 0.880 | 0.120 | 0.160 | 9.702 | 0.460 | logs/v2.log |
| 435 m / old default | e74cb2b | bc_v2 | 0.860 | 0.140 | 0.100 | 9.286 | 0.460 | logs/v2.log |
| 435 m / old default | e74cb2b | ppo_v2 | 0.620 | 0.200 | 0.420 | 8.603 | 0.380 | logs/v2.log |

## Quarry, carved (21 site pairs)

Do not compare these rows with the uncarved rows. The site pairs and the route lengths are different.

| label | code | policy | success | collision | stops_per_ep | time_to_goal_s | near_miss_rate | source |
|---|---|---|---|---|---|---|---|---|
| 435 m / old default | 82d48e8 | expert | 0.880 | 0.120 | 0.080 | 29.045 | 0.580 | logs/eval_v2_quarry_carved.log |
| 435 m / old default | 82d48e8 | bc_v2 | 0.880 | 0.100 | 0.200 | 30.982 | 0.580 | logs/eval_v2_quarry_carved.log |
| 435 m / old default | 82d48e8 | ppo_v2 | 0.560 | 0.280 | 0.440 | 25.707 | 0.440 | logs/eval_v2_quarry_carved.log |
| 435 m / old default | 82d48e8 | ppo_v3 | 0.840 | 0.140 | 0.120 | 27.39 | 0.580 | logs/ppo3.log |
| 435 m / old default | c3d2302 | ppo_v4 | 0.900 | 0.100 | 0.060 | 29.320 | 0.580 | logs/eval_ppo4.log |
| 435 m / old default | c3d2302 | bc_v3 | 0.880 | 0.120 | 0.100 | 29.145 | 0.600 | logs/eval_bc3.log |

## Notes

- ppo_v4: `train_ppo --init runs/bc_v2.pt --envs 32 --total-steps 1500000 --lr 1e-4 --clip 0.1 --ent 0.003 --bc-coef 0.05`,
  SIM2D_WORLDS=default,quarry. Over the 20 updates near 600k steps (561k to 639k), the mean training
  collision is 0.107 and the mean timeout is 0.023. Both are below the 0.15 limit.
- logs/eval_ppo4.log has no world headers. The run order was `--worlds default`, then `--worlds quarry`.
- bc_v3: train_bc on data/bc_v3.npz (3,049,503 samples from 6000 episodes).
- Best checkpoint of this round: runs/ppo_v4.pt (same default success as bc_v3 with lower collision,
  and higher quarry success).
- Later runs on the Vato worlds (bd10f9c, 725 m quarry) get the label "725 m / Vato bd10f9c".
  Do not compare those rows with the "435 m / old default" rows.
