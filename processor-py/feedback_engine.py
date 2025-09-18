import argparse, json, yaml
from pathlib import Path
import pandas as pd

SEVERITY = {"low": 1, "medium": 2, "high": 3}

def load_json(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def load_yaml(p):
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def prioritize(findings):
    return sorted(findings, key=lambda x: (SEVERITY[x["severity"]], -x.get("impact", 0)), reverse=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kpis", required=True)
    ap.add_argument("--per_round", required=True)
    ap.add_argument("--thresholds", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    kpis = load_json(args.kpis)
    per_round = pd.read_csv(args.per_round)
    th = load_yaml(args.thresholds)

    findings = []

    # Trades ≤5s
    tr = kpis.get("trade_rate_5s")
    t_target = th["trade_rate_5s_target"]["global"]
    if tr is not None and tr < t_target:
        rounds_bad = per_round[per_round["note"].str.contains("sin trade", na=False)]["round"].tolist()[:5]
        findings.append({
            "id": "F_TRADES",
            "title": "Trade lento tras el entry",
            "severity": "high" if tr < 0.55 else "medium",
            "confidence": 0.8,
            "impact": 0.8,
            "metric_before": {"trade_5s": round(tr, 2)},
            "target": {"trade_5s": t_target},
            "evidence": {"rounds": rounds_bad},
            "why_it_matters": "Sostener la ventaja evita colapsos de site.",
            "recommendation": "Asignar trade buddy y evitar picos aislados sin flash.",
        })

    # Flash Effectiveness
    fe = kpis.get("flash_effectiveness")
    fe_t = th["flash_effectiveness_target"]["global"]
    if fe is not None and fe < fe_t:
        findings.append({
            "id": "F_FLASH",
            "title": "Picos secos: baja efectividad de flashes",
            "severity": "medium",
            "confidence": 0.7,
            "impact": 0.6,
            "metric_before": {"flash_eff": round(fe, 2)},
            "target": {"flash_eff": fe_t},
            "evidence": {},
            "why_it_matters": "La utilidad barata compra ventajas.",
            "recommendation": "Agregar pop-flash antes del peek y humos de corte.",
        })

    # Post-plant early deaths
    pp = kpis.get("postplant_early_deaths")
    pp_m = th["postplant_early_deaths_max"]["global"]
    if pp is not None and pp > pp_m:
        findings.append({
            "id": "F_PP",
            "title": "Post-plant frágil (muertes tempranas)",
            "severity": "medium",
            "confidence": 0.7,
            "impact": 0.7,
            "metric_before": {"pp_early": round(pp, 2)},
            "target": {"pp_early": pp_m},
            "evidence": {},
            "why_it_matters": "Mantener números tras plantar aumenta la probabilidad de retención.",
            "recommendation": "Priorizar crossfires y roles de post-plant, evitar peek aislado.",
        })

    findings = prioritize(findings)

    quick_wins = [
        {"title": f["title"], "impact": f.get("impact", 0.5), "effort": "bajo"} for f in findings[:3]
    ]

    payload = {
        "summary": {
            "quick_wins": quick_wins,
            "scores": {
                "entry_trades": int((kpis.get("trade_rate_5s", 0)) * 100),
                "utility": int(min(100, (kpis.get("utility_dmg_per_round", 0) / 20) * 100)),
                "postplant": int((1 - min(1, kpis.get("postplant_early_deaths", 0))) * 100),
            },
        },
        "findings": findings,
        "per_round_notes": per_round.to_dict(orient="records"),
    }

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"OK → {outp}")

if __name__ == "__main__":
    main()
