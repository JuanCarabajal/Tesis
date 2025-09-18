import argparse, json, os, sys
import pandas as pd
import yaml
from pathlib import Path

# Ensure local imports work when running from project root
sys.path.append(os.path.dirname(__file__))
from zone_index import ZoneIndex

# ===== Helpers =====
def load_map_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def ensure_cols(df: pd.DataFrame, cols: list):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise SystemExit(f"Faltan columnas en events.csv: {missing}")

def first_kill_per_round(events: pd.DataFrame) -> pd.DataFrame:
    fk = (
        events[events["event"].eq("kill")]
        .sort_values(["match_id", "round", "ts"], ascending=[True, True, True])
        .groupby(["match_id", "round"], as_index=False)
        .first()
    )
    fk["entry_win_team"] = fk["team_killer"]
    return fk[["match_id", "round", "entry_win_team", "side_killer", "side_victim"]]

def trade_rate_5s(events: pd.DataFrame, window_s=5.0) -> float:
    ks = events[events["event"].eq("kill")][["match_id", "round", "ts", "team_killer", "team_victim", "killer", "victim"]].copy()
    if ks.empty:
        return float("nan")
    ks = ks.sort_values(["match_id", "round", "ts"])
    ks_2 = ks.rename(columns={
        "ts": "ts2",
        "team_killer": "team_killer2",
        "team_victim": "team_victim2",
        "killer": "killer2",
        "victim": "victim2",
    })
    merged = ks.merge(ks_2, on=["match_id", "round"], how="left")
    cond_time = (merged["ts2"] > merged["ts"]) & (merged["ts2"] <= merged["ts"] + window_s)
    cond_trade = (merged["killer2"].eq(merged["victim"])) & (merged["team_killer2"].eq(merged["team_victim"]))
    trades = merged[cond_time & cond_trade].drop_duplicates(["match_id", "round", "ts", "killer"])  # 1 trade por kill

    return len(trades) / max(1, len(ks))

def flash_effectiveness(events: pd.DataFrame) -> float:
    flashes = events[events["event"].eq("flash")]
    if flashes.empty:
        return float("nan")
    blinded = (flashes["flashed_enemies"].fillna(0) > 0).sum()
    flash_assists = events[events["is_flash_assist"].fillna(False)].shape[0]
    denom = max(1, blinded)
    return flash_assists / denom

def utility_damage_per_round(events: pd.DataFrame, rounds: pd.DataFrame) -> float:
    dmg = events["nade_damage"].fillna(0).sum()
    n_rounds = rounds["round"].nunique()
    return dmg / max(1, n_rounds)

def postplant_early_deaths(events: pd.DataFrame, rounds: pd.DataFrame, window_s=5.0) -> float:
    rr = rounds.dropna(subset=["plant_ts"]).copy()
    if rr.empty:
        return float("nan")
    ks = events[events["event"].eq("kill")][["match_id", "round", "ts", "team_victim"]]
    merged = rr.merge(ks, on=["match_id", "round"], how="left")
    merged["after_plant"] = (merged["ts"] - merged["plant_ts"]).between(0, window_s)
    per_round = (
        merged[merged["after_plant"]]
        .groupby(["match_id", "round"], as_index=False)["team_victim"].count()
        .rename(columns={"team_victim": "deaths_early"})
    )
    severe = (per_round["deaths_early"] >= 2).mean() if not per_round.empty else float("nan")
    return severe

def per_round_notes(events: pd.DataFrame, zindex: ZoneIndex) -> pd.DataFrame:
    notes = []
    ev_kill = events[events["event"].eq("kill")].copy()
    for (mid, rnd), g in ev_kill.groupby(["match_id", "round"]):
        g = g.sort_values("ts")
        if g.empty:
            continue
        fk = g.iloc[0]
        zx = zindex.zone_of(fk.get("x", 0.0), fk.get("y", 0.0), fk.get("z", 0.0)) or "zona"
        g2 = g[g["ts"].between(fk["ts"], fk["ts"] + 5.0)]
        traded = g2["victim"].eq(fk["killer"]).any()
        if fk["team_killer"] != fk["team_victim"] and not traded:
            notes.append({"match_id": mid, "round": rnd, "note": f"Entry en contra en {zx} sin trade en ≤5s"})
    return pd.DataFrame(notes)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--rounds", required=True)
    ap.add_argument("--map_config", required=True)
    ap.add_argument("--out_dir", required=True)
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    events = pd.read_csv(args.events)
    rounds = pd.read_csv(args.rounds)

    need_cols = [
        "match_id", "round", "ts", "event", "team_killer", "team_victim", "side_killer", "side_victim",
        "killer", "victim", "is_flash_assist", "flashed_enemies", "nade_damage", "x", "y", "z", "map_name",
    ]
    ensure_cols(events, need_cols)

    cfg = load_map_config(args.map_config)
    zindex = ZoneIndex(cfg)

    kpis = {
        "trade_rate_5s": trade_rate_5s(events),
        "flash_effectiveness": flash_effectiveness(events),
        "utility_dmg_per_round": utility_damage_per_round(events, rounds),
        "postplant_early_deaths": postplant_early_deaths(events, rounds),
    }

    fk = first_kill_per_round(events)
    entry_by_side = fk.groupby("side_killer").size().to_dict()
    kpis["entry_duel_counts_by_side"] = entry_by_side

    notes = per_round_notes(events, zindex)

    with open(out / "kpis_team.json", "w", encoding="utf-8") as f:
        json.dump(kpis, f, ensure_ascii=False, indent=2)

    notes.to_csv(out / "per_round.csv", index=False)

    print(f"OK → {out}")

if __name__ == "__main__":
    main()
