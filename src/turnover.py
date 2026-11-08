"""Turnover model: how fast do bays free up on each block?

Reads data/snapshots.csv and docs/snapshot.json (run analyze.py first).
Adds per-zone turnover fields and the arrival chip to docs/snapshot.json; writes docs/turnover.png.

Model
- A "free event" is a live bay that reports Unoccupied with a new status_timestamp since the
  previous read (it was taken, then released). Counted per block between consecutive reads.
- Events on a block are Poisson with rate lambda = bays * mu (per-bay rate, events per minute).
- mu gets a gamma prior fitted to all blocks by method of moments (empirical Bayes), so sparse or
  quiet blocks shrink toward the city mean instead of reading as exactly 0 or wildly high.
- P(at least one bay frees within T min) = 1 - exp(-lambda * T), for T = 5 and 10.
- Calibration: fit on the earlier part of the window, check predicted vs observed on the later part.
"""
import json, os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.join(os.path.dirname(__file__), "..")
D, DOCS = os.path.join(ROOT, "data"), os.path.join(ROOT, "docs")
STALE_DAYS = 7
MAX_GAP_MIN = 15        # read pairs further apart than this are not used
TRAIN_SHARE = 0.6       # first 60% of the window fits the model, the rest checks it
LIKELY_P5 = 0.5         # "Likely within 5 min" when P(a bay frees in 5 min) >= 50%
FREE_NOW_MIN = 2        # "Free now" when at least 2 bays are free at the latest read
INK, MUTED, BLUE = "#1f2328", "#6e7781", "#0969da"
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
                     "ytick.color": MUTED, "figure.dpi": 150})


def load():
    s = pd.read_csv(os.path.join(D, "snapshots.csv"), parse_dates=["snapshot_utc", "status_timestamp"])
    s = s.dropna(subset=["zone_number"])
    s["zone_number"] = s["zone_number"].astype(int)
    snaps = sorted(s.snapshot_utc.unique())
    last = s[s.snapshot_utc == snaps[-1]]
    stale = last.loc[(last.snapshot_utc - last.status_timestamp).dt.total_seconds() > STALE_DAYS * 86400, "kerbsideid"]
    return s[~s.kerbsideid.isin(set(stale))].copy(), snaps


def events(s, snaps):
    """One row per (zone, read pair): bays observed, minutes, free events."""
    rows = []
    for a, b in zip(snaps[:-1], snaps[1:]):
        dt = (b - a) / pd.Timedelta(minutes=1)
        if dt > MAX_GAP_MIN:
            continue
        pa = s[s.snapshot_utc == a].set_index("kerbsideid")
        pb = s[s.snapshot_utc == b].set_index("kerbsideid")
        j = pb.join(pa[["status_timestamp"]].rename(columns={"status_timestamp": "status_timestamp_prev"}), how="inner")
        j["ev"] = ((j.status == "Unoccupied") & (j.status_timestamp != j.status_timestamp_prev)).astype(int)
        g = j.groupby("zone_number").agg(bays=("ev", "size"), ev=("ev", "sum")).reset_index()
        g["start"], g["minutes"] = a, dt
        rows.append(g)
    return pd.concat(rows, ignore_index=True)


def fit(e):
    """Per-zone posterior mean of the per-bay rate mu under a gamma prior fitted by moments."""
    z = e.assign(exp=e.bays * e.minutes).groupby("zone_number").agg(k=("ev", "sum"), E=("exp", "sum"))
    m = z.k.sum() / z.E.sum()
    v = max(np.average((z.k / z.E - m) ** 2, weights=z.E) - m * np.mean(1 / z.E), m ** 2 / 50)
    alpha, beta = m * m / v, m / v
    z["mu"] = (alpha + z.k) / (beta + z.E)
    return z, dict(city_mu=m, alpha=alpha, beta=beta)


def main():
    s, snaps = load()
    e = events(s, snaps)
    starts = sorted(e.start.unique())
    cut = starts[int(len(starts) * TRAIN_SHARE)]
    out = json.load(open(os.path.join(DOCS, "snapshot.json")))
    zones = pd.DataFrame(out["zones"])

    # --- calibration: fit on earlier reads, score the later reads ---
    ztr, ptr = fit(e[e.start < cut])
    te = e[(e.start >= cut) & e.zone_number.isin(zones.zone)].join(ztr.mu, on="zone_number")
    te["mu"] = te.mu.fillna(ptr["city_mu"])
    te["p"] = 1 - np.exp(-te.bays * te.mu * te.minutes)
    te["hit"] = (te.ev > 0).astype(int)
    te["p_city"] = 1 - np.exp(-te.bays * ptr["city_mu"] * te.minutes)
    bins = pd.cut(te.p, [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0001], include_lowest=True)
    cal = te.groupby(bins, observed=True).agg(pred=("p", "mean"), obs=("hit", "mean"), n=("hit", "size")).reset_index()
    brier = float(((te.p - te.hit) ** 2).mean())
    brier_city = float(((te.p_city - te.hit) ** 2).mean())

    # --- final model on the whole window, applied to the latest read ---
    zall, pall = fit(e)
    last = s[s.snapshot_utc == snaps[-1]]
    free_now = last.assign(f=(last.status == "Unoccupied").astype(int)).groupby("zone_number").f.sum()
    zones = zones.join(zall.mu, on="zone").join(free_now.rename("free_now"), on="zone")
    zones["mu"] = zones.mu.fillna(pall["city_mu"])
    zones["free_now"] = zones.free_now.fillna(0).astype(int)
    zones["lam"] = zones.bays * zones.mu
    zones["p5"] = 1 - np.exp(-zones.lam * 5)
    zones["p10"] = 1 - np.exp(-zones.lam * 10)
    zones["chip"] = np.where(zones.free_now >= FREE_NOW_MIN, "Free now",
                             np.where(zones.p5 >= LIKELY_P5, "Likely within 5 min", "Hard"))
    for col, nd in (("lam", 4), ("p5", 3), ("p10", 3)):
        zones[col] = zones[col].round(nd)
    out["zones"] = json.loads(zones.drop(columns="mu").to_json(orient="records"))

    used = sorted(e.start.unique())
    t = dict(
        reads=len(snaps), read_pairs_used=len(used),
        window_local=f"{pd.Timestamp(snaps[0]).tz_convert('Australia/Melbourne'):%a %d %b %Y %H:%M} to "
                     f"{pd.Timestamp(snaps[-1]).tz_convert('Australia/Melbourne'):%H:%M %Z}",
        minutes_observed=round(float(e.drop_duplicates("start").minutes.sum())),
        free_events=int(e[e.zone_number.isin(zones.zone)].ev.sum()),
        city_rate_per_bay_per_hour=round(pall["city_mu"] * 60, 3),
        prior_alpha=round(pall["alpha"], 2),
        lambda_median_per_min=round(float(zones.lam.median()), 3),
        p5_median=round(float(zones.p5.median()), 2), p10_median=round(float(zones.p10.median()), 2),
        chip_counts=zones.chip.value_counts().reindex(["Free now", "Likely within 5 min", "Hard"]).fillna(0).astype(int).to_dict(),
        holdout_from_local=f"{pd.Timestamp(cut).tz_convert('Australia/Melbourne'):%H:%M}",
        holdout_intervals=int(len(te)), holdout_hit_rate=round(float(te.hit.mean()), 3),
        holdout_mean_pred=round(float(te.p.mean()), 3),
        brier_model=round(brier, 4), brier_city_mean=round(brier_city, 4),
        calibration=[dict(pred=round(r.pred, 3), obs=round(r.obs, 3), n=int(r.n)) for r in cal.itertuples()],
    )
    out["summary"]["turnover"] = t
    json.dump(out, open(os.path.join(DOCS, "snapshot.json"), "w"), indent=0)
    print(json.dumps(t, indent=2))

    # --- chart: lambda distribution + calibration ---
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.4))
    a1.hist(zones.lam, bins=30, color=BLUE)
    a1.axvline(-np.log(1 - LIKELY_P5) / 5, color=MUTED, ls="--", lw=1)
    a1.text(-np.log(1 - LIKELY_P5) / 5, a1.get_ylim()[1] * .92, " 50% in 5 min", color=MUTED, fontsize=8)
    a1.set_xlabel("bays freeing per minute (lambda, per block)"); a1.set_ylabel("blocks")
    a1.set_title(f"Turnover across {len(zones)} blocks", loc="left", fontsize=10, color=INK)
    a2.plot([0, 1], [0, 1], color=MUTED, ls="--", lw=1)
    a2.scatter(cal.pred, cal.obs, s=np.clip(cal.n, 10, 300), color=BLUE, alpha=.8)
    for r in cal.itertuples():
        a2.annotate(str(r.n), (r.pred, r.obs), fontsize=7, color=MUTED, xytext=(4, -9), textcoords="offset points")
    a2.set_xlim(0, 1); a2.set_ylim(0, 1)
    a2.set_xlabel("predicted P(a bay frees) per interval"); a2.set_ylabel("observed share")
    a2.set_title(f"Held-out check (from {t['holdout_from_local']}, n={len(te)})", loc="left", fontsize=10, color=INK)
    fig.tight_layout(); fig.savefig(os.path.join(DOCS, "turnover.png")); plt.close(fig)


if __name__ == "__main__":
    main()
