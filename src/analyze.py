"""Turn sensor snapshots into a per-zone 'parking likelihood' score + charts.

Reads data/snapshots.csv (from fetch.py). Writes docs/snapshot.json and docs/*.png.
"""
import json, os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.join(os.path.dirname(__file__), "..")
D, DOCS = os.path.join(ROOT, "data"), os.path.join(ROOT, "docs")
STALE_DAYS = 7          # a sensor with no status change in 7 days is treated as dead
MIN_BAYS = 4            # zones with fewer live sensors are too thin to score
TZ = "Australia/Melbourne"
INK, MUTED = "#1f2328", "#6e7781"
COLORS = {"High": "#1a7f37", "Medium": "#bf8700", "Low": "#cf222e"}

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
                     "ytick.color": MUTED, "figure.dpi": 150})


def score(free_share, avg_free):
    """High: a spot is very likely on this block. Low: expect to circle."""
    if free_share >= 0.25 and avg_free >= 2:
        return "High"
    if free_share < 0.10 or avg_free < 1:
        return "Low"
    return "Medium"


def main():
    s = pd.read_csv(os.path.join(D, "snapshots.csv"), parse_dates=["snapshot_utc", "status_timestamp"])
    s = s.dropna(subset=["zone_number", "lat", "lon"])
    s["zone_number"] = s["zone_number"].astype(int)
    snaps = sorted(s.snapshot_utc.unique())
    last = s[s.snapshot_utc == snaps[-1]].copy()
    age_days = (last.snapshot_utc - last.status_timestamp).dt.total_seconds() / 86400
    last["stale"] = age_days > STALE_DAYS
    stale_ids = set(last.loc[last.stale, "kerbsideid"])
    live = s[~s.kerbsideid.isin(stale_ids)].copy()
    live["free"] = (live.status == "Unoccupied").astype(int)

    # turnover: how many live bays changed state between consecutive snapshots
    piv = live.pivot_table(index="kerbsideid", columns="snapshot_utc", values="free")
    changes = piv.diff(axis=1).abs().sum(axis=1)
    mins = (snaps[-1] - snaps[0]) / pd.Timedelta(minutes=1) if len(snaps) > 1 else 0

    per_snap = live.groupby(["zone_number", "snapshot_utc"]).agg(bays=("free", "size"), free=("free", "sum")).reset_index()
    z = per_snap.groupby("zone_number").agg(bays=("bays", "max"), avg_free=("free", "mean"),
                                            any_free=("free", lambda x: (x > 0).mean())).reset_index()
    z["free_share"] = z.avg_free / z.bays
    pos = live.groupby("zone_number")[["lat", "lon"]].mean()
    z = z.join(pos, on="zone_number")
    turn = live[["kerbsideid", "zone_number"]].drop_duplicates().assign(ch=lambda d: d.kerbsideid.map(changes))
    z = z.join(turn.groupby("zone_number").ch.sum().rename("changes"), on="zone_number")
    z = z[z.bays >= MIN_BAYS].copy()
    z["score"] = [score(a, b) for a, b in zip(z.free_share, z.avg_free)]

    streets = pd.DataFrame(json.load(open(os.path.join(D, "streets.json"))))
    name = (streets.groupby("parkingzone").apply(
        lambda g: f"{g.onstreet.iloc[0]} ({g.streetfrom.iloc[0]} – {g.streetto.iloc[0]})", include_groups=False))
    z["name"] = z.zone_number.map(name).fillna(z.zone_number.map(lambda n: f"Zone {n}"))

    local = [pd.Timestamp(t).tz_convert(TZ) for t in snaps]
    summary = {
        "source": "City of Melbourne Open Data – On-street Parking Bay Sensors (CC BY 4.0)",
        "first_snapshot_local": local[0].strftime("%a %d %b %Y %H:%M %Z"),
        "last_snapshot_local": local[-1].strftime("%a %d %b %Y %H:%M %Z"),
        "snapshots": len(snaps), "minutes_covered": round(mins),
        "sensors_total": int(last.shape[0]), "sensors_stale": int(last.stale.sum()),
        "zones_scored": int(len(z)), "bays_scored": int(z.bays.sum()),
        "city_free_share": round(float(z.avg_free.sum() / z.bays.sum()), 3),
        "score_counts": z.score.value_counts().reindex(["High", "Medium", "Low"]).fillna(0).astype(int).to_dict(),
        "bay_changes_per_hour": round(float(changes.sum() / mins * 60)) if mins else None,
        "low_zones_with_a_change": int(((z.score == "Low") & (z.changes > 0)).sum()),
    }
    out = {"summary": summary, "zones": [
        {"zone": int(r.zone_number), "name": r.name, "lat": round(r.lat, 6), "lon": round(r.lon, 6),
         "bays": int(r.bays), "avg_free": round(r.avg_free, 1), "free_share": round(r.free_share, 3),
         "score": r.score} for r in z.itertuples()]}
    json.dump(out, open(os.path.join(DOCS, "snapshot.json"), "w"), indent=0)
    print(json.dumps(summary, indent=2))

    # Chart 1: zones by score
    fig, ax = plt.subplots(figsize=(6, 2.6))
    c = summary["score_counts"]
    ax.barh(list(c)[::-1], list(c.values())[::-1], color=[COLORS[k] for k in list(c)[::-1]])
    for i, v in enumerate(list(c.values())[::-1]):
        ax.text(v + 1, i, str(v), va="center", color=INK)
    ax.set_xlabel("parking zones (block faces)")
    ax.set_title(f"Parking likelihood across {len(z)} Melbourne zones\n{summary['first_snapshot_local']}", loc="left", fontsize=10, color=INK)
    fig.tight_layout(); fig.savefig(os.path.join(DOCS, "zones_by_score.png")); plt.close(fig)

    # Chart 2: distribution of free share
    fig, ax = plt.subplots(figsize=(6, 2.8))
    ax.hist(z.free_share * 100, bins=20, color="#0969da")
    for x in (10, 25):
        ax.axvline(x, color=MUTED, ls="--", lw=1)
    ax.set_xlabel("% of bays free (zone average over snapshots)"); ax.set_ylabel("zones")
    ax.set_title("Most blocks are either nearly full or comfortably open", loc="left", fontsize=10, color=INK)
    fig.tight_layout(); fig.savefig(os.path.join(DOCS, "free_share_hist.png")); plt.close(fig)

    # Chart 3: sensor health
    fig, ax = plt.subplots(figsize=(6, 1.8))
    ok = summary["sensors_total"] - summary["sensors_stale"]
    ax.barh([0], [ok], color="#0969da"); ax.barh([0], [summary["sensors_stale"]], left=[ok], color="#d0d7de")
    ax.text(ok / 2, 0, f"live {ok:,}", ha="center", va="center", color="white")
    ax.text(ok + summary["sensors_stale"] / 2, 0, f"no update >{STALE_DAYS}d {summary['sensors_stale']:,}", ha="center", va="center", color=INK)
    ax.set_yticks([]); ax.set_xlabel("sensors in the feed")
    ax.set_title("Data quality: how many sensors are actually reporting", loc="left", fontsize=10, color=INK)
    fig.tight_layout(); fig.savefig(os.path.join(DOCS, "sensor_health.png")); plt.close(fig)


if __name__ == "__main__":
    main()
