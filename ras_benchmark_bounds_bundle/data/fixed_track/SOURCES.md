# Fixed-track RAS instances (D1–D3)

`D1/`, `D2/`, `D3/` (`params.tsv`, `segments.tsv`, `trains.tsv`): the frozen fixed-route adaptation of the RAS 2012
datasets (`mixed_seeded`, 2026-09-09): every train's route fixed down to the track segment, capacity 1 per segment,
`H = 3`, integer minutes, `wait_after[k] = 1` where the train may stand clear after task `k` (siding points), origin
waits always allowed. Copied verbatim from the block-pair CP-SAT package of 2026-09-23.

`donors/D{1,2,3}_donor.csv`: validated schedules (OBJ-E 2220 / 4231 / 4258). E1 uses one only as the upper bound that
sets its time windows; E2 and E3 use no donor.

Validator: `validator/fixed_track/fixed_track_validate.cpp` (unchanged from the package): every task holds its segment
over `[start, end + H)`, waits only where allowed, OBJ-E recomputed.
