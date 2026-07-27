# N-track LR-based B&B + screening paper

`main.tex` / `main.pdf` — companion to `../single_track_paper/`. Covers the **N-track** work:
- faithful Meng–Zhou (2014) LR reproduction (RAS Set 3: LB 1785→2279, UB 3803, gap ~40%);
- the LR-based **branch-and-bound** (Zhou–Zhong 2005 segment enumeration; sound disjunction vs
  swappable selection criterion; toy proven optimal, segment 19 vs cell 51 nodes);
- the exact **DAG shortest path** (~2.9× faster; why approximate paths are inadmissible);
- the **primal/dual/SVD/tensor branch screening** study — the single-node success that **does not
  replicate** across nodes, and the diagnosis (a *learned* screen is needed, not unsupervised);
- the key finding that the gap is **bound-limited, not compute-limited**.

Honest by design: the screening result is reported as the partial negative it is.

Rebuild: `pdflatex main.tex` twice (MiKTeX/TeX Live). All numbers come from the code + data in this
handoff (`n_track/`, `single_track/`, `data/`).
