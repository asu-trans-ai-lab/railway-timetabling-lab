# Benchmark Lab — size-ladder report (C1 single-track corridors + toy)

One row per method per instance; all numbers from `pipeline_records.csv` (common reporting record) and generator manifests (ground truth).

## toy_native  (trains=3)

| method | obj (UB) | LB | proven | nodes | cols | time s |
|---|---|---|---|---|---|---|
| B0 best (fcfs) | 12 | — | no | | | 0 |
| B2_time_indexed_milp | 12 | — | no | 1 | 0 | 0 |
| B4_cp_sat | 12 | 12 | YES | 0 | 0 | 0 |
| B5_individual_cg | 12 | 12 | no | 0 | 25 | 0 |
| B6_branch_and_price | 12 | 12 | YES | 0 | 30 | 0 |
| P1_group_supercolumn_cg | 12 | — | no | 0 | 1 | 0 |

## C1_L4_n4_seed1  (trains=4, exact optimum=29 PROVEN, serial UB=75)

| method | obj (UB) | LB | proven | nodes | cols | time s |
|---|---|---|---|---|---|---|
| B0 best (class) | 29 | — | no | | | 0.1 |
| B2_time_indexed_milp | 29 | — | no | 0 | 0 | 4.6 |
| B4_cp_sat | 29 | 29 | YES | 639 | 0 | 0 |
| B5_individual_cg | 42 | 23.75 | no | 0 | 62 | 4.5 |
| B6_branch_and_price | 29 | 29 | YES | 185 | 605 | 115.8 |
| P1_group_supercolumn_cg | 30 | — | no | 0 | 45 | 188.4 |
| P1_group_supercolumn_cg | 29 | — | no | 0 | 1 | 114.7 |

**Validity vs exact:** ALL BOUNDS CONSISTENT ✓

## C1_L4_n4_seed2  (trains=4, exact optimum=20 PROVEN, serial UB=71)

| method | obj (UB) | LB | proven | nodes | cols | time s |
|---|---|---|---|---|---|---|
| B0 best (fcfs) | 21 | — | no | | | 0.1 |
| B2_time_indexed_milp | 20 | — | no | 1 | 0 | 1.6 |
| B4_cp_sat | 20 | 20 | YES | 205 | 0 | 0 |
| B5_individual_cg | 20 | 20 | no | 0 | 43 | 3.3 |
| B6_branch_and_price | 20 | 20 | YES | 0 | 49 | 2.7 |
| P1_group_supercolumn_cg | 20 | — | no | 0 | 26 | 124.8 |

**Validity vs exact:** ALL BOUNDS CONSISTENT ✓

## C1_L6_n6_seed3  (trains=6, exact optimum=57 PROVEN, serial UB=276)

| method | obj (UB) | LB | proven | nodes | cols | time s |
|---|---|---|---|---|---|---|
| B0 best (rand_best_of_5) | 75 | — | no | | | 1.6 |
| B2_time_indexed_milp | 63 | — | no | 1 | 0 | 38 |
| B4_cp_sat | 57 | 57 | YES | 38128 | 0 | 0.7 |
| B5_individual_cg | 69 | 51 | no | 0 | 89 | 9 |
| B6_branch_and_price | 57 | 51 | no | 51 | 528 | 150.9 |
| P1_group_supercolumn_cg | 114 | — | no | 0 | 52 | 935.4 |
| P1_group_supercolumn_cg | 114 | — | no | 0 | 119 | 2404.9 |

**Validity vs exact:** ALL BOUNDS CONSISTENT ✓

## C1_L8_n8_seed11  (trains=8, OPEN: best-known [105, 138], serial UB=678)

| method | obj (UB) | LB | proven | nodes | cols | time s |
|---|---|---|---|---|---|---|
| B0 best (rand_best_of_5) | 170 | — | no | | | 4 |
| B4_cp_sat | 138 | 102 | no | 1373114 | 0 | 180 |
| B5_individual_cg | 186 | — | no | 0 | 468 | 74.6 |
| B6_branch_and_price | — | — | no | 2 | 855 | 156.5 |
| B6_branch_and_price | — | 105 | no | 11 | 1415 | 420.2 |

## C1_L10_n10_seed12  (trains=10, OPEN: best-known [110, 189], serial UB=1375)

| method | obj (UB) | LB | proven | nodes | cols | time s |
|---|---|---|---|---|---|---|
| B0 best (rand_best_of_5) | 210 | — | no | | | 6.3 |
| B4_cp_sat | 189 | 106 | no | 1073860 | 0 | 180.3 |
| B5_individual_cg | 279 | — | no | 0 | 612 | 205.8 |
| B6_branch_and_price | — | — | no | 2 | 785 | 151.3 |

## C1_L12_n12_seed13  (trains=12, OPEN: best-known [164, 388], serial UB=2446)

| method | obj (UB) | LB | proven | nodes | cols | time s |
|---|---|---|---|---|---|---|
| B0 best (conflict) | 484 | — | no | | | 1.5 |
| B4_cp_sat | 388 | 155 | no | 1384185 | 0 | 180 |
| B5_individual_cg | 568 | — | no | 0 | 742 | 175.8 |
| B6_branch_and_price | — | — | no | 0 | 744 | 192.6 |
