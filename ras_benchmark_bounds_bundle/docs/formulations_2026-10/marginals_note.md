# CPLEX marginals and Lagrange multipliers, in the notation of CSE/IEE/DSE 506 (Baoyu Zhou)

Lecture 2 writes every model as

    min_{x in X} f(x)   s.t.  g_i(x) <= 0 (i = 1..m),   h_j(x) = 0 (j = 1..l).          (P)

## 1. The timetabling problem as (P)

Space-time arc formulation (`solver/python/lp_spacetime.py`):

| (P) | timetabling |
|---|---|
| x | x_a = 1 if a train uses arc a of its own space-time network (depart at t, run on, stand, arrive) |
| X | {0,1}^n: a discrete, constrained, linear problem (Lecture 2: an integer linear program) |
| f(x) | sum_a c_a x_a = sum_k [ alpha (departure_k - R_k) + sum_i p_ki + beta x standing_k ], the MIP objective written on arcs (README, Objective) |
| h_j(x) = 0 | flow conservation: every train leaves its source once and keeps one path (one row per space-time node) |
| g_i(x) <= 0 | capacity, one row per resource r and minute t: g_rt(x) = sum of arcs holding r at t - tracks_r |

Dropping the integrality (X = [0,1]^n) gives the LP relaxation, a lower bound on the optimum.

## 2. Marginal = how the optimal value moves with a right-hand side

Write the capacity row as sum(...) <= kappa_rt and let z(kappa) be the LP optimum. The marginal (CPLEX: "dual value",
written by `write m.sol`) of row (r, t) is

    pi_rt = d z / d kappa_rt  <= 0.

Reading: one more track at resource r in minute t lowers the total delay by -pi_rt minutes. Lecture 2's
electricity example has the same object: the locational marginal price is the marginal of the power-balance row.
Here -pi_rt is the congestion price of track r at minute t.

## 3. Lagrange multiplier = the price put on a relaxed row

Move the capacity rows into the objective with prices lambda_rt >= 0:

    L(lambda) = min_{x in X, h(x) = 0}  f(x) + sum_rt lambda_rt g_rt(x).

Without the capacity rows the trains no longer interact, so L(lambda) splits into one problem per train: a shortest
path with arc cost c_a + (sum of lambda over the (r, t) the arc holds) - the single-train DP of the kernel
(`solver/cpp/siding_lr.cpp`, train_dp). For every lambda >= 0, L(lambda) <= optimum (weak duality). Each train's path
polytope is integral, so max_lambda L(lambda) = LP value (Geoffrion 1974), and the LP marginals are an optimal
lambda:

    lambda*_rt = -pi_rt.

## 4. Check (`experiments/cplex_duals.py`, `duals.json`)

CPLEX solves the LP; lambda = -pi goes into the kernel's DP (`--mode price`). On all 39 instances L(lambda) equals the
LP value to 6 digits (HiGHS duals give the same). Example, RAS ideal D2 whole (optimum 1,994):

| quantity | value |
|---|---:|
| LP (CPLEX) | 1,990.11 |
| L(lambda from CPLEX marginals), one DP pass | 1,990.11 |
| L from the kernel's subgradient, 3,000 steps | 1,987.68 |
| sum of lambda on T8 / T4 (the bottleneck) | 1,060.75 / 79.5 |

## 5. The subgradient method is the iterative scheme of Lectures 2, 5 and 6

L(lambda) is concave and piecewise linear, so it has no gradient everywhere (Lecture 4's first-order condition
grad f = 0 becomes 0 in the subdifferential, and Newton's method of Lecture 6 does not apply). The kernel moves along
a subgradient, the capacity violation of the current paths:

    lambda_{k+1} = max(0, lambda_k + alpha_k g(x_k)),    alpha_k = theta_k (UB - L(lambda_k)) / ||g(x_k)||^2,

with theta halved after P steps without improvement (a Polyak step that diminishes, Lecture 5's step-size rules).
It approaches the LP value from below (at most 0.36 % short after 3,000 steps on the 38 instances above); the CPLEX marginals
reach it in one pricing. Lecture 1 lists LP duality, column generation and Benders decomposition as later topics;
this note uses only the LP marginal and weak duality.
