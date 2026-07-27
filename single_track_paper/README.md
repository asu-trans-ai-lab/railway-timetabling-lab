# Single-track screening paper (working draft)

Draft of the single-track results: gradient-weighted compressed-response screening for B&B in single-track train timetabling.

- main.tex / main.pdf -- the paper draft
- Solution-method lineage grounded in Meng & Zhou (2014) §5 (LR + time-space TDSP + subgradient + priority-rule UB) and Zhou & Zhong (2007) B&B.
- Results sourced from: spectral_ttbl/{ttbl_bnb,learn_branch,screening_theory,tensor_features,tensor_response,tt_lb_sweep}.py
- N-track outlook instance: spectral_ttbl/nt_spacetime.py (RAS data).
