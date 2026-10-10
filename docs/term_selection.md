# Term selection: descriptive surfaces

Status: **approved by the owner on October 9, 2026** and entered in
`CONFIG["selected_terms"]`. Decisions 1–4 below follow a research review of the
close calls (Section 3). Decision 5 (inference) was settled the same day
(Section 4.2).

Evidence: `outputs/term_selection/<model>__<zone>.md` and `.png` (one per
model), `outputs/term_selection/summary.csv`. Produced by
`.venv/bin/python notebooks/term_evidence.py`, which calls the same engine
functions as `notebooks/explore_terms.ipynb` Steps 1–5. The notebook was also
run top to bottom once and gives the same numbers.

The chosen terms define the **descriptive** surface that is drawn and
explained. Tests and intervals still come from the full quadratic in every
model (D31).

---

## 1. The rule

1. **Hierarchy**: every term set is hierarchical (enforced by the engine).
2. **NCSS search (D31)**: from the full quadratic, drop the cheapest removable
   term while its R² loss is below 0.01.
3. **Tie-break for comparability**: if the reference set **T, L, L², T·L** is
   within 2 AIC of the search result, use the reference set. The reference set
   is the most common NCSS search result over the 16 fits. Models within 2 AIC
   have substantial support (Burnham & Anderson), inference never depends on
   the choice, and the tie-break never overrides a gap larger than 2 AIC.
4. **On-peak robustness rows get no selection of their own.** They use the
   all-hours level choice for the zone, so the robustness comparison is
   like with like (Build Plan 6.5).

A first version also required ΔAIC < 2 for each drop. It changed only Houston
volatility and was removed (Section 3, decision 1).

**Stated limitation**: under block resampling the selected terms change often in
most zones (Houston volatility 58/38 between two sets, West 43/25/13, North
59/24; South and SYSTEM about 80% stable). So differences in *which terms were
picked* between zones are not findings. Differences in *surface shape* are
(the surfaces are stable, and West's need for T² is robust: ΔAIC +10.9).

---

## 2. Chosen terms

ΔAIC and ΔBIC are against the full quadratic (negative = the smaller model is
preferred). "Agreement" is the correlation between the chosen and the full
surface on the masked grid (radius 0.15 coded, post_rtcb = 1).

| Model | Zone | Terms | How | R² (full) | ΔAIC | Shape | Agreement |
|---|---|---|---|---|---|---|---|
| vol_zone | Houston | T, L, L², T·L | search | 0.238 (0.247) | +2.22 | saddle | 0.983 |
| vol_zone | North | T, L, L², T·L | search | 0.266 (0.272) | +0.98 | saddle | 0.982 |
| vol_zone | South | T, L, L², T·L | tie-break (search: T, L, T·L) | 0.206 (0.206) | −1.92 | saddle | 1.000 |
| vol_zone | West | T, L, T² | search | 0.239 (0.248) | +0.32 | ridge | 0.985 |
| lvl_zone | Houston | full quadratic | search | 0.091 (0.091) | 0 | minimum | 1 |
| lvl_zone | North | T, L, L², T·L | search | 0.046 (0.049) | −0.70 | saddle | 0.947 |
| lvl_zone | South | T, L, L², T·L | search | 0.068 (0.069) | −1.72 | saddle | 0.996 |
| lvl_zone | West | T, L, L², T·L | tie-break (search: T, L, T·L) | 0.066 (0.066) | −1.92 | saddle | 0.999 |
| vol_sys | SYSTEM | T, L, L², T·L | search | 0.258 (0.259) | −1.69 | saddle | 0.999 |
| lvl_sys | SYSTEM | T, L, L², T·L | search | 0.057 (0.062) | −0.23 | saddle | 0.954 |
| lvl_zone_onpeak | Houston | full quadratic | mirrors lvl_zone | 0.063 (0.063) | 0 | minimum | 1 |
| lvl_zone_onpeak | North | T, L, L², T·L | mirrors lvl_zone | 0.027 (0.030) | −1.08 | saddle | 0.924 |
| lvl_zone_onpeak | South | T, L, L², T·L | mirrors lvl_zone | 0.042 (0.043) | −1.96 | saddle | 0.999 |
| lvl_zone_onpeak | West | T, L, L², T·L | mirrors lvl_zone | 0.044 (0.055) | +1.21 | saddle | 0.901 |

---

## 3. Decisions on the close calls (October 9, 2026)

A research agent reviewed the close calls with extra analyses (scratch
scripts, not part of the pipeline) and the literature. The owner accepted its
recommendations 1–4.

1. **Houston volatility: T, L, L², T·L** (was the full quadratic). NCSS drops
   T² (R² loss 0.0088); the AIC guard (ΔAIC +2.2) was the only reason to keep
   it, and BIC prefers dropping it (−1.7). T²'s HAC p = 0.096 is optimistic
   (Section 4.2). The two surfaces correlate 0.983, differ by at most about 10%
   of the surface range, and share the same highest-fitted day (Jan 26). The
   guard was removed from the rule.
2. **West volatility: T, L, T²** (unchanged). Adding T·L does not improve
   comparability: West's T·L coefficient is positive (+0.56) where the other
   zones' are negative, and the set still lacks L². West curving in
   temperature rather than load is the finding to report.
3. **On-peak rows mirror the all-hours level choice.** Houston on-peak's own
   search result (T, T²) rests on one day: Jan 26 has leverage 0.59 and Cook's
   D 1.89, and without it the temperature and load roles reverse (T terms
   p 2.5e-7 → 0.47; L terms 0.39 → 0.010). Jan 26 has Cook's D 1.8–2.4 in all
   four on-peak zones.
4. **Winter Storm Fern stays in the main fit**; the no-Fern refit becomes a
   visible column in the results table (R² without Fern and the correlation of
   the two surfaces over ordinary days). No Fern dummy and no Huber/Tukey fit:
   the dummy is insignificant for level and removes the very corner the surface
   describes, and M-estimators do not guard against high-leverage points.

---

## 4. Findings from the selection work (for the report)

### 4.1 Winter Storm Fern

Diagnostic refit with **January 23 – February 1, 2026 left out** (10 days,
2.8% of the sample):

| Model | Full R², all days | Full R², without Fern days |
|---|---|---|
| vol_zone (4 zones) | 0.206 – 0.272 | 0.063 – 0.090 |
| vol_sys | 0.259 | 0.073 |
| lvl_zone (4 zones) | 0.049 – 0.091 | 0.007 – 0.031 |
| lvl_sys | 0.062 | 0.013 |

From the research review (to be reproduced by the runner before it is quoted):

- Fern days supply about 74–90% of the explained sum of squares.
- **Volatility**: the storm days are high-leverage but fit the surface
  (externally studentized residuals −1.5 to +0.6). Without them the surface
  over ordinary days has the same shape (correlation 0.97–0.98). They extend
  the factor range; they do not change the relationship.
- **Level**: the same days are influential (residuals +1.6 to +2.8, Cook's D
  0.2–0.38). Without them the ordinary-day level surface tilts the other way
  (correlation −0.19 to −0.53; Houston +0.70). Level contours should not be read
  as describing ordinary days.

### 4.2 Lack of fit and HAC p-values (decision 5: option C, Oct 9, 2026)

The HAC tests were verified to be badly oversized on these designs (lack of
fit 27–39%, single terms 8–40% at nominal 5%). The owner chose option C: all
reported tests and intervals use a block bootstrap (25-day blocks; 10 and 50 as
sensitivity), validated at 4.2–4.8% false rejections and 95% coverage (West
about 93%). With it, lack of fit passes for every volatility model (p
0.26–0.79) and is marginal for level (p 0.011–0.082). Details in
`docs/findings_and_decisions.md`, Sections 2.11 and 3 (Inference).

### 4.3 Shapes

No chosen surface is a clean maximum or minimum inside the data. Most are
saddles; West volatility is a ridge. The fitted range over observed days (in
each evidence file) is the better summary (D41).
