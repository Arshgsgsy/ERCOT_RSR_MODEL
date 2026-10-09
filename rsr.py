"""The RSR engine (Build Plan, Section 6.4).

The NCSS response surface method on top of statsmodels. It knows nothing about
electricity: it takes a table, a response, factors and optional block columns.

    s = Surface(df, response="y", factors=["temp_mean", "load_peak_norm"],
                labels=["T", "L"], block=["post_rtcb"], date="local_date")
    full = s.full()                         # full quadratic, the model for inference
    s.drop_one(["T", "L", "T^2", "T*L"])    # what each term costs
    s.search()                              # NCSS backward path, a guide
    s.compare(["T", "L", "T^2", "L^2", "T*L"], ["T", "L", "T^2", "T*L"])

Terms are written with the labels: "T", "T^2", "T*L", "T^2*L". Internally each
term is a tuple of exponents, one per factor, so "T*L" is (1, 1) and "T^2" is
(2, 0). Factors are min-max coded to [-1, 1] on the data used for the fit. Every
model must be hierarchical unless allow_nonhierarchical=True is passed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats
from scipy.spatial import cKDTree
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.stats.outliers_influence import variance_inflation_factor
from statsmodels.stats.stattools import durbin_watson

Term = tuple[int, ...]

DEFAULTS = {
    "search_cutoff_r2": 0.01,   # NCSS default R² cutoff
    "ridge_tolerance": 0.05,    # smallest/largest |eigenvalue| below this -> ridge
    "inside_radius": 0.15,      # coded units
    "min_pure_error_df": 2,     # report the pure-error test from this many repeat df
}

GROUP_ORDER = ["Linear", "Quadratic", "Lin x Lin", "Cubic", "Lin x Quad", "Lin x Lin x Lin"]


class HierarchyError(ValueError):
    """A term set that is not hierarchical (a term without one of its parents)."""


class RSRError(RuntimeError):
    """The design cannot support the requested fit."""


# --------------------------------------------------------------------------
# Terms
# --------------------------------------------------------------------------

def term_name(term: Term, labels: list[str]) -> str:
    parts = [lab if p == 1 else f"{lab}^{p}" for lab, p in zip(labels, term) if p]
    return "*".join(parts)


def parse_term(term, labels: list[str]) -> Term:
    """'T^2*L' -> (2, 1). Tuples pass through after a length check."""
    k = len(labels)
    if isinstance(term, tuple):
        if len(term) != k or sum(term) == 0:
            raise ValueError(f"term {term} must have {k} exponents and not be constant")
        return tuple(int(p) for p in term)
    exps = [0] * k
    for part in str(term).replace(" ", "").split("*"):
        lab, _, power = part.partition("^")
        if lab not in labels:
            raise ValueError(f"unknown factor '{lab}' in term '{term}'; labels are {labels}")
        exps[labels.index(lab)] += int(power) if power else 1
    if sum(exps) == 0:
        raise ValueError(f"empty term '{term}'")
    return tuple(exps)


def group_of(term: Term) -> str:
    key = tuple(sorted((p for p in term if p), reverse=True))
    return {(1,): "Linear", (2,): "Quadratic", (1, 1): "Lin x Lin", (3,): "Cubic",
            (2, 1): "Lin x Quad", (1, 1, 1): "Lin x Lin x Lin"}.get(key, f"Order {sum(term)}")


def order_terms(terms) -> list[Term]:
    """NCSS display order: linear, quadratic, interactions, then higher orders."""
    def key(t):
        g = group_of(t)
        return (GROUP_ORDER.index(g) if g in GROUP_ORDER else len(GROUP_ORDER), sum(t),
                tuple(-p for p in t))
    return sorted(dict.fromkeys(terms), key=key)


def full_quadratic(k: int) -> list[Term]:
    unit = [tuple(int(i == j) for j in range(k)) for i in range(k)]
    squares = [tuple(2 * p for p in u) for u in unit]
    pairs = [tuple(a + b for a, b in zip(unit[i], unit[j])) for i in range(k) for j in range(i + 1, k)]
    return order_terms(unit + squares + pairs)


def cubic_extension(k: int) -> list[Term]:
    """Pure cubes and x_i^2 x_j terms: 4 for two factors (the complete cubic),
    9 for three (x1 x2 x3 is left out, Build Plan 6.4)."""
    unit = [tuple(int(i == j) for j in range(k)) for i in range(k)]
    cubes = [tuple(3 * p for p in u) for u in unit]
    mixed = [tuple(2 * a + b for a, b in zip(unit[i], unit[j])) for i in range(k) for j in range(k) if i != j]
    return order_terms(cubes + mixed)


def is_parent(a: Term, b: Term) -> bool:
    return a != b and all(x <= y for x, y in zip(a, b))


def immediate_parents(term: Term) -> list[Term]:
    out = []
    for i, p in enumerate(term):
        if p:
            q = list(term)
            q[i] -= 1
            if sum(q):
                out.append(tuple(q))
    return out


def missing_parents(terms) -> dict[Term, list[Term]]:
    present = set(terms)
    return {t: miss for t in terms if (miss := [p for p in immediate_parents(t) if p not in present])}


def check_hierarchy(terms, labels: list[str]) -> None:
    miss = missing_parents(terms)
    if miss:
        msg = "; ".join(f"{term_name(t, labels)} needs {', '.join(term_name(p, labels) for p in ps)}"
                        for t, ps in miss.items())
        raise HierarchyError(f"not hierarchical: {msg}")


def removable(terms) -> list[Term]:
    """Terms that no other remaining term contains."""
    return [t for t in terms if not any(is_parent(t, u) for u in terms)]


def hac_lag(n: int) -> int:
    """Lazarus-Lewis-Stock-Watson rule for Newey-West: ceil(1.3 * sqrt(n))."""
    return math.ceil(1.3 * math.sqrt(n))


def hac_cov(X: np.ndarray, resid: np.ndarray, lags: int, correction: bool = True) -> np.ndarray:
    """Newey-West (Bartlett) covariance, the same computation as statsmodels'
    cov_hac_simple with use_correction (checked in the tests). The bootstrap uses
    it because calling statsmodels thousands of times is slow."""
    xxi = np.linalg.inv(X.T @ X)
    u = X * resid[:, None]
    S = u.T @ u
    for lag in range(1, lags + 1):
        s = u[lag:].T @ u[:-lag]
        S += (1 - lag / (lags + 1)) * (s + s.T)
    cov = xxi @ S @ xxi
    if correction:
        n, k = X.shape
        cov *= n / (n - k)
    return cov


def block_index(n: int, block_len: int, rng: np.random.Generator) -> np.ndarray:
    """Circular moving-block bootstrap rows: random start days, block_len
    consecutive rows from each (wrapping past the end), trimmed to n."""
    starts = rng.integers(0, n, size=-(-n // block_len))
    return ((starts[:, None] + np.arange(block_len)[None, :]) % n).ravel()[:n]


def percentile_interval(draws: np.ndarray, level: float = 0.95) -> tuple[np.ndarray, np.ndarray]:
    a = (1 - level) / 2
    return np.nanquantile(draws, a, axis=0), np.nanquantile(draws, 1 - a, axis=0)


# --------------------------------------------------------------------------
# Coding
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Coding:
    """Min-max coding of each factor to [-1, 1], frozen with the fit."""
    lo: np.ndarray
    hi: np.ndarray

    @classmethod
    def from_data(cls, X: np.ndarray) -> "Coding":
        lo, hi = X.min(axis=0), X.max(axis=0)
        if np.any(hi <= lo):
            raise RSRError("a factor is constant and cannot be coded")
        return cls(lo, hi)

    @property
    def scale(self) -> np.ndarray:
        """d(coded) / d(real) for each factor."""
        return 2.0 / (self.hi - self.lo)

    def code(self, X) -> np.ndarray:
        return (np.asarray(X, float) - self.lo) * self.scale - 1.0

    def real(self, Z) -> np.ndarray:
        return self.lo + (np.asarray(Z, float) + 1.0) / self.scale


# --------------------------------------------------------------------------
# Fit results
# --------------------------------------------------------------------------

@dataclass
class FitResult:
    terms: list[Term]
    names: list[str]          # term names, in design order
    block: list[str]
    params: pd.Series         # Intercept, block columns, then terms (coded units)
    cov: pd.DataFrame
    cov_type: str
    hac_lags: int | None
    n: int
    df_resid: float
    rss: float
    tss: float
    r2: float
    adj_r2: float
    aic: float
    bic: float
    fitted: pd.Series
    resid: pd.Series
    X: pd.DataFrame
    sm_result: object

    @property
    def mse(self) -> float:
        return self.rss / self.df_resid

    def wald(self, names: list[str]) -> dict:
        """Wald test that the named coefficients are all zero, with the fit's
        covariance (HAC or OLS). F = W/q against F(q, df_resid)."""
        b = self.params[names].to_numpy()
        V = self.cov.loc[names, names].to_numpy()
        W = float(b @ np.linalg.solve(V, b))
        q = len(names)
        F = W / q
        return {"q": q, "wald": W, "F": F, "p": float(stats.f.sf(F, q, self.df_resid))}

    def table(self) -> pd.DataFrame:
        se = np.sqrt(np.diag(self.cov.to_numpy()))
        t = self.params.to_numpy() / se
        return pd.DataFrame({
            "coef_coded": self.params, "se": se, "t": t,
            "p_approx": 2 * stats.t.sf(np.abs(t), self.df_resid),
        }, index=self.params.index)


# --------------------------------------------------------------------------
# Surface
# --------------------------------------------------------------------------

class Surface:
    """One response, its factors and optional block columns, ready to fit any
    hierarchical polynomial in the factors."""

    def __init__(self, data: pd.DataFrame, response: str, factors: list[str],
                 labels: list[str] | None = None, block=(), date: str | None = None,
                 cov: str = "hac", hac_lags: int | None = None, coding: Coding | None = None,
                 settings: dict | None = None):
        self.response, self.factors = response, list(factors)
        self.labels = list(labels or factors)
        self.block = list(block)
        self.date = date
        self.settings = {**DEFAULTS, **(settings or {})}
        if len(self.labels) != len(self.factors) or len(set(self.labels)) != len(self.labels):
            raise ValueError("labels must be unique, one per factor")

        cols = [response, *self.factors, *self.block] + ([date] if date else [])
        df = data[cols].copy()
        bad = df[[response, *self.factors, *self.block]].isna().any(axis=1)
        self.n_dropped = int(bad.sum())
        df = df[~bad]
        if date:
            df = df.sort_values(date)
            d = pd.to_datetime(df[date])
            self.n_gaps = int((d.diff().dt.days > 1).sum())
            self.index = pd.Index(d, name=date)
        else:
            self.n_gaps = 0
            self.index = df.index
        self.data = df.reset_index(drop=True)
        self.n = len(df)
        self.k = len(self.factors)
        self.y = df[response].to_numpy(float)
        self.Xreal = df[self.factors].to_numpy(float)
        self.B = df[self.block].to_numpy(float) if self.block else np.empty((self.n, 0))
        self.coding = coding or Coding.from_data(self.Xreal)
        self.Z = self.coding.code(self.Xreal)
        self.cov = cov
        self.hac_lags = hac_lags if hac_lags is not None else hac_lag(self.n)
        self._tree = cKDTree(self.Z)
        self._cache: dict = {}

    # ---- terms and design -------------------------------------------------

    def terms(self, terms=None) -> list[Term]:
        if terms is None:
            return full_quadratic(self.k)
        return order_terms(parse_term(t, self.labels) for t in terms)

    def names(self, terms) -> list[str]:
        return [term_name(t, self.labels) for t in self.terms(terms)]

    def design(self, terms, Z=None, B=None) -> pd.DataFrame:
        Z = self.Z if Z is None else np.atleast_2d(Z)
        B = self.B if B is None else np.atleast_2d(B)
        cols = {"Intercept": np.ones(len(Z))}
        for i, b in enumerate(self.block):
            cols[b] = B[:, i]
        for t in terms:
            cols[term_name(t, self.labels)] = np.prod(Z ** np.array(t), axis=1)
        return pd.DataFrame(cols)

    def _check_support(self, terms) -> None:
        """NCSS rule: a factor with maximum exponent m needs at least m + 1 distinct values."""
        for i, lab in enumerate(self.labels):
            m = max((t[i] for t in terms), default=0)
            u = len(np.unique(self.Z[:, i]))
            if m and u < m + 1:
                raise RSRError(f"{lab} has {u} distinct values, too few for exponent {m}")

    # ---- fitting ------------------------------------------------------------

    def fit(self, terms=None, allow_nonhierarchical: bool = False) -> FitResult:
        terms = self.terms(terms)
        if not allow_nonhierarchical:
            check_hierarchy(terms, self.labels)
        key = (tuple(terms), allow_nonhierarchical)
        if key in self._cache:
            return self._cache[key]
        self._check_support(terms)
        X = self.design(terms)
        if X.shape[1] >= self.n:
            raise RSRError(f"{X.shape[1]} parameters need more than {self.n} rows")
        if np.linalg.matrix_rank(X.to_numpy()) < X.shape[1]:
            raise RSRError("design matrix is not full rank")
        model = sm.OLS(self.y, X)
        if self.cov == "hac":
            res = model.fit(cov_type="HAC", cov_kwds={"maxlags": self.hac_lags, "use_correction": True},
                            use_t=True)
        else:
            res = model.fit()
        tss = float(((self.y - self.y.mean()) ** 2).sum())
        rss = float(res.ssr)
        out = FitResult(
            terms=terms, names=[term_name(t, self.labels) for t in terms], block=self.block,
            params=pd.Series(res.params, index=X.columns),
            cov=pd.DataFrame(np.asarray(res.cov_params()), index=X.columns, columns=X.columns),
            cov_type=self.cov, hac_lags=self.hac_lags if self.cov == "hac" else None,
            n=self.n, df_resid=float(res.df_resid), rss=rss, tss=tss,
            r2=1 - rss / tss, adj_r2=1 - (rss / res.df_resid) / (tss / (self.n - 1)),
            aic=float(res.aic), bic=float(res.bic),
            # np.asarray: the statsmodels series carry a 0..n-1 index, which pandas
            # would otherwise try to align with the date index (all NaN).
            fitted=pd.Series(np.asarray(res.fittedvalues), index=self.index),
            resid=pd.Series(np.asarray(res.resid), index=self.index), X=X, sm_result=res,
        )
        self._cache[key] = out
        return out

    def full(self) -> FitResult:
        return self.fit(full_quadratic(self.k))

    def _r2_without(self, terms, drop) -> float:
        return self.fit([t for t in terms if t not in drop], allow_nonhierarchical=True).r2

    # ---- choosing terms -----------------------------------------------------

    def drop_one(self, terms=None) -> pd.DataFrame:
        """For each term: can it be dropped (hierarchy), and what would dropping it cost.
        r2_loss for every term matches NCSS's 'Last R-Squared'."""
        terms = self.terms(terms)
        base = self.fit(terms)
        can = set(removable(terms))
        rows = []
        for t in terms:
            reduced = self.fit([u for u in terms if u != t], allow_nonhierarchical=True)
            name = term_name(t, self.labels)
            children = [term_name(u, self.labels) for u in terms if is_parent(t, u)]
            rows.append({
                "term": name,
                "removable": t in can,
                "blocked_by": ", ".join(children),
                "r2_loss": base.r2 - reduced.r2,
                "r2_after": reduced.r2,
                "adj_r2_after": reduced.adj_r2,
                "d_aic": reduced.aic - base.aic,
                "d_bic": reduced.bic - base.bic,
                "p_approx": base.wald([name])["p"],
            })
        out = pd.DataFrame(rows).set_index("term")
        return out.sort_values(["removable", "r2_loss"], ascending=[False, True])

    def search(self, terms=None, cutoff: float | None = None) -> dict:
        """NCSS backward elimination. Remove the removable term with the smallest
        R² loss while that loss is below the cutoff. Block terms are never removed."""
        cutoff = self.settings["search_cutoff_r2"] if cutoff is None else cutoff
        current = self.terms(terms)
        check_hierarchy(current, self.labels)
        steps = []
        while True:
            cands = removable(current)
            if not cands:
                break
            r2 = self.fit(current).r2
            losses = {t: r2 - self._r2_without(current, [t]) for t in cands}
            t, loss = min(losses.items(), key=lambda kv: kv[1])
            removed = loss < cutoff
            steps.append({"step": len(steps) + 1, "term": term_name(t, self.labels),
                          "r2_loss": loss, "r2_after": r2 - loss, "removed": removed})
            if not removed:
                break
            current = [u for u in current if u != t]
        return {"path": pd.DataFrame(steps), "final_terms": [term_name(t, self.labels) for t in current],
                "final_r2": self.fit(current).r2 if current else np.nan, "cutoff": cutoff}

    def compare(self, *term_sets, labels: list[str] | None = None) -> pd.DataFrame:
        rows = []
        for i, ts in enumerate(term_sets):
            f = self.fit(ts)
            lof = self.lack_of_fit(ts)
            shape = self.canonical(f).get("shape", "n/a")
            rows.append({
                "model": labels[i] if labels else f"set {i + 1}",
                "terms": ", ".join(f.names), "n_terms": len(f.terms),
                "r2": f.r2, "adj_r2": f.adj_r2, "aic": f.aic, "bic": f.bic,
                "lof_cubic_p": lof["cubic"].get("p") if lof["cubic"] else np.nan,
                "shape": shape,
            })
        return pd.DataFrame(rows).set_index("model")

    # ---- variance decomposition and tests --------------------------------------

    def _rss(self, cols: list[str], X: pd.DataFrame) -> float:
        A = X[cols].to_numpy()
        beta, *_ = np.linalg.lstsq(A, self.y, rcond=None)
        r = self.y - A @ beta
        return float(r @ r)

    def pure_error(self) -> dict:
        """Exact repeats of the factor (and block) combination."""
        keys = pd.DataFrame(np.column_stack([self.Z, self.B])).round(12)
        groups = keys.groupby(list(keys.columns)).ngroup().to_numpy()
        n_groups = int(groups.max()) + 1
        means = pd.Series(self.y).groupby(groups).transform("mean").to_numpy()
        return {"df": self.n - n_groups, "ss": float(((self.y - means) ** 2).sum()),
                "repeat_rows": int(self.n - n_groups)}

    def sequential_ss(self, terms=None) -> pd.DataFrame:
        """NCSS sequential ANOVA: block first, then term groups in order. Each group's
        sum of squares is the RSS drop when it is added. Descriptive only."""
        f = self.fit(terms)
        groups: dict[str, list[str]] = {}
        for t, name in zip(f.terms, f.names):
            groups.setdefault(group_of(t), []).append(name)
        order = [g for g in GROUP_ORDER if g in groups] + [g for g in groups if g not in GROUP_ORDER]
        stages = ([("Block", self.block)] if self.block else []) + [(g, groups[g]) for g in order]
        rows, cols, prev = [], ["Intercept"], self._rss(["Intercept"], f.X)
        for name, add in stages:
            cols = cols + add
            rss = self._rss(cols, f.X)
            ss = prev - rss
            rows.append({"source": name, "df": len(add), "seq_ss": ss, "incremental_r2": ss / f.tss})
            prev = rss
        reg = {"source": "Regression", "df": sum(r["df"] for r in rows), "seq_ss": f.tss - f.rss,
               "incremental_r2": f.r2}
        err = {"source": "Total Error", "df": f.df_resid, "seq_ss": f.rss, "incremental_r2": f.rss / f.tss}
        out = [reg, *rows, err]
        pe = self.pure_error()
        if pe["df"] >= self.settings["min_pure_error_df"]:
            out += [{"source": "Lack of Fit", "df": f.df_resid - pe["df"], "seq_ss": f.rss - pe["ss"],
                     "incremental_r2": (f.rss - pe["ss"]) / f.tss},
                    {"source": "Pure Error", "df": pe["df"], "seq_ss": pe["ss"], "incremental_r2": pe["ss"] / f.tss}]
        return pd.DataFrame(out).set_index("source")

    def _test_sets(self, f: FitResult) -> dict[str, list[str]]:
        """The conditional test sets: each term group, and every term containing a factor."""
        sets = {}
        for t, name in zip(f.terms, f.names):
            sets.setdefault(f"group: {group_of(t)}", []).append(name)
        for i, lab in enumerate(self.labels):
            sets[f"factor: {lab}"] = [n for t, n in zip(f.terms, f.names) if t[i]]
        return sets

    def conditional_tests(self, terms=None) -> pd.DataFrame:
        """Full-model tests that a group's coefficients, or every term containing a
        factor, are zero. Wald F with the fit's covariance (HAC: approximate p).
        last_ss and r2_loss are the OLS sums of squares lost (NCSS 'Last SS')."""
        f = self.fit(terms)
        sets = self._test_sets(f)
        rows = []
        for label, names in sets.items():
            if not names:
                continue
            w = f.wald(names)
            reduced = self.fit([t for t, n in zip(f.terms, f.names) if n not in names], allow_nonhierarchical=True) \
                if len(names) < len(f.names) else None
            last_ss = (reduced.rss if reduced else f.tss) - f.rss
            rows.append({"test": label, "terms": ", ".join(names), "q": w["q"], "F": w["F"], "p_approx": w["p"],
                         "last_ss": last_ss, "r2_loss": last_ss / f.tss})
        return pd.DataFrame(rows).set_index("test")

    def lack_of_fit(self, terms=None) -> dict:
        """Pure-error F test when there are enough exact repeats; the HAC Wald test
        of the cubic extension whenever the augmented design is full rank."""
        f = self.fit(terms)
        pe = self.pure_error()
        out = {"repeat_rows": pe["repeat_rows"], "pure_error": None, "cubic": None}
        if pe["df"] >= self.settings["min_pure_error_df"] and f.df_resid > pe["df"]:
            ss_lof, df_lof = f.rss - pe["ss"], f.df_resid - pe["df"]
            F = (ss_lof / df_lof) / (pe["ss"] / pe["df"])
            out["pure_error"] = {"ss_lof": ss_lof, "df_lof": df_lof, "ss_pe": pe["ss"], "df_pe": pe["df"],
                                 "F": F, "p": float(stats.f.sf(F, df_lof, pe["df"]))}
        extra = [t for t in cubic_extension(self.k) if t not in f.terms]
        try:
            aug = self.fit(f.terms + extra, allow_nonhierarchical=True)
            names = [term_name(t, self.labels) for t in extra]
            w = aug.wald(names)
            if w["q"] >= aug.df_resid:
                raise RSRError("too few residual degrees of freedom")
            out["cubic"] = {"terms": names, **w}
        except RSRError as e:
            out["cubic"] = {"terms": [term_name(t, self.labels) for t in extra], "skipped": str(e)}
        return out

    # ---- block bootstrap (departure from D16/D17, owner decision Oct 9, 2026) ------
    #
    # On the project's designs the HAC Wald tests reject far more often than their
    # nominal level (notebooks/hac_size_check.py). Tests keep the same Wald F
    # statistic but take its reference distribution from a residual block bootstrap
    # under the null; intervals come from a moving-block pairs bootstrap.

    def _wald_F(self, X: np.ndarray, y: np.ndarray, cols: list[int]) -> float:
        """Wald F for the coefficients in columns `cols`, with the surface's
        covariance. Same value as FitResult.wald, without statsmodels."""
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        r = y - X @ beta
        n, k = X.shape
        if self.cov == "hac":
            V = hac_cov(X, r, self.hac_lags)
        else:
            V = np.linalg.inv(X.T @ X) * float(r @ r) / (n - k)
        b = beta[cols]
        return float(b @ np.linalg.solve(V[np.ix_(cols, cols)], b)) / len(cols)

    def bootstrap_test(self, terms, test, B: int = 999, block_len: int = 25, seed: int = 0) -> dict:
        """Block-bootstrap p-value for 'the `test` terms are zero' in the model with
        `terms` plus `test`. Data are generated under the null: fitted values of the
        model without `test`, plus its residuals resampled in circular blocks of
        `block_len` consecutive days. p = (1 + #{F* >= F}) / (B + 1)."""
        test = [parse_term(t, self.labels) for t in test]
        full = order_terms([*self.terms(terms), *test])
        null = [t for t in full if t not in test]
        Xd = self.design(full)
        X = Xd.to_numpy()
        n, k = X.shape
        if k >= n or np.linalg.matrix_rank(X) < k:
            raise RSRError("design for the bootstrap test is not full rank")
        names = [term_name(t, self.labels) for t in test]
        cols = [Xd.columns.get_loc(nm) for nm in names]
        F = self._wald_F(X, self.y, cols)

        X0 = self.design(null).to_numpy()
        beta0, *_ = np.linalg.lstsq(X0, self.y, rcond=None)
        fitted0 = X0 @ beta0
        e0 = self.y - fitted0
        rng = np.random.default_rng(seed)
        Fb = np.array([self._wald_F(X, fitted0 + e0[block_index(n, block_len, rng)], cols) for _ in range(B)])
        q = len(cols)
        return {"terms": names, "q": q, "F": F, "p_asymptotic": float(stats.f.sf(F, q, n - k)),
                "p_boot": float((1 + (Fb >= F).sum()) / (B + 1)), "B": B, "block_len": block_len}

    def lack_of_fit_boot(self, terms=None, B: int = 999, block_len: int = 25, seed: int = 0) -> dict:
        """The cubic-extension lack-of-fit test with a block-bootstrap p-value."""
        f = self.fit(terms)
        extra = [t for t in cubic_extension(self.k) if t not in f.terms]
        return self.bootstrap_test(f.terms, extra, B=B, block_len=block_len, seed=seed)

    def conditional_tests_boot(self, terms=None, B: int = 999, block_len: int = 25, seed: int = 0) -> pd.DataFrame:
        """conditional_tests with block-bootstrap p-values beside the asymptotic ones."""
        f = self.fit(terms)
        rows = []
        for label, names in self._test_sets(f).items():
            if not names:
                continue
            rest = [t for t, nm in zip(f.terms, f.names) if nm not in names]
            r = self.bootstrap_test(rest, names, B=B, block_len=block_len, seed=seed)
            rows.append({"test": label, "terms": ", ".join(names), "q": r["q"], "F": r["F"],
                         "p_asymptotic": r["p_asymptotic"], "p_boot": r["p_boot"], "B": B, "block_len": block_len})
        return pd.DataFrame(rows).set_index("test")

    def bootstrap_pairs(self, fit: FitResult, B: int = 999, block_len: int = 25, seed: int = 0) -> pd.DataFrame:
        """Moving-block pairs bootstrap: resample circular blocks of whole days
        (response, factors and block columns together) and refit by OLS. The coding
        stays frozen from the real fit. One row of coefficients per resample; a
        resample whose design is not full rank (e.g. no pre-RTC+B day) is NaN."""
        X = fit.X.to_numpy()
        n, k = X.shape
        rng = np.random.default_rng(seed)
        draws = np.full((B, k), np.nan)
        for b in range(B):
            idx = block_index(n, block_len, rng)
            Xb = X[idx]
            if np.linalg.matrix_rank(Xb) == k:
                draws[b] = np.linalg.lstsq(Xb, self.y[idx], rcond=None)[0]
        return pd.DataFrame(draws, columns=fit.X.columns)

    def boot_table(self, fit: FitResult, draws: pd.DataFrame, level: float = 0.95) -> pd.DataFrame:
        """Coefficients (coded units) with bootstrap SE and percentile intervals."""
        lo, hi = percentile_interval(draws.to_numpy(), level)
        return pd.DataFrame({"coef_coded": fit.params, "boot_se": draws.std().to_numpy(), "boot_low": lo,
                             "boot_high": hi, "n_boot": draws.notna().all(axis=1).sum()}, index=fit.params.index)

    # ---- shape --------------------------------------------------------------------

    def _block_at(self, block_values) -> np.ndarray:
        if not self.block:
            return np.empty(0)
        if block_values is None:
            return self.B.mean(axis=0)
        return np.array([block_values[b] for b in self.block], float)

    def canonical(self, fit: FitResult | None = None, block_values: dict | None = None,
                  tol: float | None = None) -> dict:
        """Stationary point and eigen-analysis of a quadratic fit (coded units).
        Shape: minimum / maximum / saddle, or ridge when the smallest |eigenvalue|
        is below tol times the largest (then no stationary point is reported)."""
        fit = fit or self.full()
        tol = self.settings["ridge_tolerance"] if tol is None else tol
        if any(sum(t) > 2 for t in fit.terms):
            return {"shape": "n/a", "note": "canonical analysis needs a quadratic model"}
        k = self.k
        b = np.zeros(k)
        Bm = np.zeros((k, k))
        for t, name in zip(fit.terms, fit.names):
            c = fit.params[name]
            nz = [i for i, p in enumerate(t) if p]
            if sum(t) == 1:
                b[nz[0]] = c
            elif len(nz) == 1:
                Bm[nz[0], nz[0]] = c
            else:
                Bm[nz[0], nz[1]] = Bm[nz[1], nz[0]] = c / 2
        evals, evecs = np.linalg.eigh(Bm)
        big = np.abs(evals).max()
        out = {
            "eigenvalues": evals,
            "eigenvectors": pd.DataFrame(evecs, index=self.labels, columns=[f"w{i + 1}" for i in range(k)]),
        }
        if big == 0:
            out["shape"] = "no curvature"
            return out
        if np.abs(evals).min() < tol * big:
            out["shape"] = "ridge"
            return out
        out["shape"] = "minimum" if (evals > 0).all() else "maximum" if (evals < 0).all() else "saddle"
        xs = -0.5 * np.linalg.solve(Bm, b)
        out["stationary_coded"] = pd.Series(xs, index=self.labels)
        out["stationary_real"] = pd.Series(self.coding.real(xs), index=self.factors)
        out["fitted_at_stationary"] = float(self._predict_coded(fit, xs[None, :], block_values)[0])
        dist, _ = self._tree.query(xs)
        out["nearest_day_distance"] = float(dist)
        out["inside_data"] = bool(dist <= self.settings["inside_radius"])
        return out

    # ---- prediction and support ---------------------------------------------------

    def _predict_coded(self, fit: FitResult, Z: np.ndarray, block_values=None) -> np.ndarray:
        Bv = np.tile(self._block_at(block_values), (len(Z), 1))
        X = self.design(fit.terms, Z=Z, B=Bv)
        return X.to_numpy() @ fit.params[X.columns].to_numpy()

    def predict(self, fit: FitResult, real: pd.DataFrame | np.ndarray, block_values: dict | None = None) -> np.ndarray:
        """Fitted response at real-unit factor values (columns in factor order)."""
        real = real[self.factors].to_numpy(float) if isinstance(real, pd.DataFrame) else np.atleast_2d(real)
        return self._predict_coded(fit, self.coding.code(real), block_values)

    def inside_data(self, real, radius: float | None = None) -> pd.DataFrame:
        """Distance (coded units, all factors) from each point to the nearest observed day."""
        radius = self.settings["inside_radius"] if radius is None else radius
        real = real[self.factors].to_numpy(float) if isinstance(real, pd.DataFrame) else np.atleast_2d(real)
        dist, idx = self._tree.query(self.coding.code(real))
        return pd.DataFrame({"distance": dist, "inside": dist <= radius, "nearest_row": idx})

    def predict_grid(self, fit: FitResult, n: int = 60, block_values: dict | None = None,
                     hold: dict | None = None, axes: tuple[int, int] = (0, 1), radius: float | None = None) -> dict:
        """A real-unit grid over two factors for contour plots, with the inside-data
        mask. Other factors are held at `hold` (real units) or their mean."""
        i, j = axes
        gi = np.linspace(self.Xreal[:, i].min(), self.Xreal[:, i].max(), n)
        gj = np.linspace(self.Xreal[:, j].min(), self.Xreal[:, j].max(), n)
        A, Bg = np.meshgrid(gi, gj)
        pts = np.tile(self.Xreal.mean(axis=0), (A.size, 1))
        for f, v in (hold or {}).items():
            pts[:, self.factors.index(f)] = v
        pts[:, i], pts[:, j] = A.ravel(), Bg.ravel()
        Y = self.predict(fit, pts, block_values).reshape(A.shape)
        mask = self.inside_data(pts, radius)["inside"].to_numpy().reshape(A.shape)
        return {"x": A, "y": Bg, "z": Y, "inside": mask,
                "x_name": self.factors[i], "y_name": self.factors[j]}

    def fitted_range(self, fit: FitResult | None = None) -> pd.DataFrame:
        fit = fit or self.full()
        fv = fit.fitted.reset_index(drop=True)
        rows = []
        for which, pos in (("lowest", int(fv.idxmin())), ("highest", int(fv.idxmax()))):
            row = {"which": which, "fitted": fv[pos], "date": self.index[pos] if self.date else pos}
            row.update(dict(zip(self.factors, self.Xreal[pos])))
            rows.append(row)
        return pd.DataFrame(rows).set_index("which")

    def marginal_effects(self, fit: FitResult, at, block_values: dict | None = None,
                         level: float = 0.95, draws: pd.DataFrame | None = None) -> pd.DataFrame:
        """d(fitted)/d(factor) in response units per real factor unit, at real-unit
        points, with delta-method intervals from the fit's covariance and, if
        bootstrap `draws` (from bootstrap_pairs) are given, percentile intervals."""
        real = at[self.factors].to_numpy(float) if isinstance(at, pd.DataFrame) else np.atleast_2d(at)
        Z = self.coding.code(real)
        cols = list(fit.params.index)
        V = fit.cov.loc[cols, cols].to_numpy()
        tq = stats.t.ppf(0.5 + level / 2, fit.df_resid)
        rows = []
        for r, z in enumerate(Z):
            for i, f in enumerate(self.factors):
                g = np.zeros(len(cols))
                for t, name in zip(fit.terms, fit.names):
                    if t[i]:
                        e = np.array(t)
                        e[i] -= 1
                        g[cols.index(name)] = t[i] * np.prod(z ** e)
                s = self.coding.scale[i]
                slope = s * float(g @ fit.params.to_numpy())
                se = s * math.sqrt(max(float(g @ V @ g), 0.0))
                row = {"point": r, "factor": f, **dict(zip(self.factors, real[r])),
                       "slope": slope, "se": se, "low": slope - tq * se, "high": slope + tq * se}
                if draws is not None:
                    sb = s * (draws[cols].to_numpy() @ g)
                    lo, hi = percentile_interval(sb, level)
                    row.update({"boot_se": float(np.nanstd(sb, ddof=1)), "boot_low": float(lo),
                                "boot_high": float(hi)})
                rows.append(row)
        return pd.DataFrame(rows)

    # ---- diagnostics ----------------------------------------------------------------

    def diagnostics(self, fit: FitResult | None = None, top: int = 5) -> dict:
        fit = fit or self.full()
        X = fit.X.to_numpy()
        terms_only = fit.X[fit.names].to_numpy()
        vif = {name: float(variance_inflation_factor(np.column_stack([np.ones(self.n), terms_only]), i + 1))
               for i, name in enumerate(fit.names)} if len(fit.names) > 1 else {fit.names[0]: 1.0}
        infl = sm.OLS(self.y, fit.X).fit().get_influence()
        lev = pd.Series(infl.hat_matrix_diag, index=self.index)
        cook = pd.Series(infl.cooks_distance[0], index=self.index)
        resid = fit.resid.to_numpy()
        nlags = min(10, self.n // 5)
        acf = [float(pd.Series(resid).autocorr(lag)) for lag in range(1, 6)]
        lb = acorr_ljungbox(resid, lags=[nlags], return_df=True)
        return {
            "vif": vif,
            "condition_number": float(np.linalg.cond(terms_only)) if terms_only.size else np.nan,
            "rank": int(np.linalg.matrix_rank(X)),
            "n_params": X.shape[1],
            "leverage_mean": float(lev.mean()),
            "top_leverage": lev.nlargest(top),
            "top_cooks": cook.nlargest(top),
            "resid_acf_1_5": acf,
            "ljung_box": {"lag": nlags, "stat": float(lb["lb_stat"].iloc[0]), "p": float(lb["lb_pvalue"].iloc[0])},
            "durbin_watson": float(durbin_watson(resid)),
            "repeat_rows": self.pure_error()["repeat_rows"],
            "date_gaps": self.n_gaps,
        }

    def summary(self, fit: FitResult | None = None) -> str:
        fit = fit or self.full()
        lof = self.lack_of_fit(fit.terms)
        can = self.canonical(fit)
        cov = f"HAC Newey-West, {fit.hac_lags} lags" if fit.cov_type == "hac" else "OLS"
        lines = [
            f"Response {self.response} | factors {', '.join(f'{l}={f}' for l, f in zip(self.labels, self.factors))}"
            + (f" | block {', '.join(self.block)}" if self.block else ""),
            f"n = {self.n} (dropped {self.n_dropped}, date gaps {self.n_gaps}) | {cov} | p-values approximate",
            f"terms: {', '.join(fit.names)}",
            f"R² = {fit.r2:.4f}   adj R² = {fit.adj_r2:.4f}   AIC = {fit.aic:.1f}   BIC = {fit.bic:.1f}",
            "",
            fit.table().to_string(float_format=lambda v: f"{v:.5g}"),
            "",
        ]
        if lof["pure_error"]:
            pe = lof["pure_error"]
            lines.append(f"Lack of fit (pure error): F = {pe['F']:.3f}, p = {pe['p']:.4f}")
        c = lof["cubic"]
        lines.append(f"Lack of fit (cubic terms {', '.join(c['terms'])}): "
                     + (f"F = {c['F']:.3f}, p = {c['p']:.4f}" if "F" in c else f"skipped ({c['skipped']})"))
        lines.append(f"Shape: {can['shape']}"
                     + (f", stationary point {can['stationary_real'].round(4).to_dict()} "
                        f"(inside data: {can['inside_data']})" if "stationary_real" in can else ""))
        return "\n".join(lines)
