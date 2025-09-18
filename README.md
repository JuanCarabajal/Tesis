# CS2 Feedback – Mirage (Starter)

Pipeline para subir una demo `.dem`, calcular KPIs y generar **feedback accionable** (Quick Wins, hallazgos y notas por ronda) para **Mirage**.

## Flujo
1) Parser (externo) → genera `events.csv` y `rounds.csv` por match.
2) `processor-py/build_kpis.py` → KPIs y métricas por ronda.
3) `processor-py/feedback_engine.py` → `feedback.json` + `kpis.json`.
4) `web/app.py` → UI simple: Summary + Rounds (export PDF se suma luego).

## Contrato de archivos (input)
- `events.csv` columnas mínimas:
  - `match_id, round, ts, side_killer, side_victim, team_killer, team_victim, killer, victim, assister, event`
  - `is_headshot, is_flash_assist, flashed_enemies, nade_damage`
  - `x, y, z, map_name`
- `rounds.csv` columnas mínimas:
  - `match_id, round, start_ts, end_ts, plant_ts (nullable), plant_site (A/B/None)`

> Si faltan columnas opcionales, el pipeline **se degrada** con métricas parciales (sin crashear).

## Setup rápido
```bash
python -m venv .venv
source .venv/bin/activate  # (Windows: .venv\Scripts\activate)
pip install -r processor-py/requirements.txt
```

### Ejecutar con samples
```bash
python processor-py/build_kpis.py       --events samples/events_sample.csv       --rounds samples/rounds_sample.csv       --map_config configs/maps/mirage.yml       --out_dir out/match_demo

python processor-py/feedback_engine.py       --kpis out/match_demo/kpis_team.json       --per_round out/match_demo/per_round.csv       --thresholds processor-py/thresholds.yml       --out out/match_demo/feedback.json

FLASK_APP=web/app.py flask run
```

## Extender a más mapas
Agregar `configs/maps/<mapa>.yml` y assets; no cambia el motor.
