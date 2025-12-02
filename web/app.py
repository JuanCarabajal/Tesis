# -*- coding: utf-8 -*-
from flask import Flask, render_template, request, redirect, url_for, flash, current_app, session
import json, os, uuid, subprocess, sys, random
from datetime import datetime
import urllib.request, urllib.error

# --- opcional: .env para DEM_PARSER_CMD, etc. ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --- Auth / DB ---
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, login_user, logout_user, login_required,
    current_user, UserMixin
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev"  # solo local
app.config["MAX_CONTENT_LENGTH"] = 1_500 * 1024 * 1024  # ~1.5 GB
app.config["SIMULATE_UPLOAD"] = True if os.getenv("SIMULATE_UPLOAD", "1") == "1" else False

# --- Novu (env) ---
NOVU_SECRET_KEY = os.getenv("NOVU_SECRET_KEY") or ""
NOVU_WORKFLOW_ID = "verification-code"

# --- DB: SQLite local ---
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# --- Login manager ---
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = None  # evita flash intrusivo al redirigir a login

# Flags simples para templates
@app.context_processor
def inject_nav_flags():
    return {"has_demos": True}

# ---------------------- Paths base del proyecto ----------------------
APP_DIR       = os.path.abspath(os.path.dirname(__file__))           # .../web
REPO_DIR      = os.path.abspath(os.path.join(APP_DIR, ".."))         # raiz del repo
STATIC_DIR    = os.path.join(APP_DIR, "static")                       # /web/static
OUT_DIR       = os.path.join(REPO_DIR, "out")
UPLOADS_DIR   = os.path.join(REPO_DIR, "uploads")
PROCESSOR_DIR = os.path.join(REPO_DIR, "processor-py")
MAP_CFG       = os.path.join(REPO_DIR, "configs", "maps", "mirage.yml")

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

# ---------------------- Variantes de summary simuladas ----------------------
# Perfiles simples para simular resultados distintos al subir una demo.
VARIANT_SUMMARIES = {
    "aim_heavy": {
        "summary": {
            "map": "Mirage",
            "score": "16-9",
            "duration": "39:12",
            "team": "Sharpix Academy",
            "score100": 88,
            "suggested_role": "Entry",
            "scores": {"entry_trades": 0.82, "utility": 0.55, "postplant": 0.60}
        },
        "insights": {
            "strengths": ["Alto impacto en entries", "Trade rapido en A", "Duelos ganados en mid"],
            "weaknesses": ["Uso irregular de utilidades", "Perdidas en post-plant"],
            "quick_wins": ["Practicar flashes top-mid (10 min)", "Ejercicios de post-plant (15 min)"]
        },
        "training": [
            {"title": "Aim duels en mid", "tip": "Series de 20 duelos con apoyo de flash."},
            {"title": "Post-plant B", "tip": "Roles fijos y crossfire desde bench + short."}
        ]
    },
    "utility_exec": {
        "summary": {
            "map": "Mirage",
            "score": "16-13",
            "duration": "44:03",
            "team": "Sharpix Academy",
            "score100": 80,
            "suggested_role": "Support",
            "scores": {"entry_trades": 0.68, "utility": 0.78, "postplant": 0.72}
        },
        "insights": {
            "strengths": ["Execs consistentes", "Buen gasto de utilidades"],
            "weaknesses": ["Trades tardios en splits", "CT rotando libre por falta de lurk"],
            "quick_wins": ["Mejorar timings en split A (10 min)", "Lurk constante en B apps (10 min)"]
        },
        "training": [
            {"title": "Lineups A y mid", "tip": "Repeticion de humos/flash para top-mid y A site."},
            {"title": "Splits sincronizados", "tip": "Salida en 3 tiempos con flash pop final."}
        ]
    },
    "clutch_defense": {
        "summary": {
            "map": "Mirage",
            "score": "14-16",
            "duration": "47:28",
            "team": "Sharpix Academy",
            "score100": 76,
            "suggested_role": "Closer",
            "scores": {"entry_trades": 0.55, "utility": 0.62, "postplant": 0.85}
        },
        "insights": {
            "strengths": ["Alta conversion en post-plant", "Clutches en retake"],
            "weaknesses": ["Entries negativos", "Rotaciones tardias desde B"],
            "quick_wins": ["Protocolos de rotacion (10 min)", "Apoyo early mid con HE (10 min)"]
        },
        "training": [
            {"title": "Retakes A", "tip": "Utilidad coordinada desde CT + jungle."},
            {"title": "Rotacion B-A", "tip": "Definir trigger y caller unico para agrupar."}
        ]
    },
    "mid_control": {
        "summary": {
            "map": "Mirage",
            "score": "16-10",
            "duration": "41:20",
            "team": "Sharpix Academy",
            "score100": 82,
            "suggested_role": "IGL",
            "scores": {"entry_trades": 0.70, "utility": 0.66, "postplant": 0.72}
        },
        "insights": {
            "strengths": ["Dominio de top mid", "Llamados claros en splits"],
            "weaknesses": ["Execs lentos al cerrar rondas", "Rotaciones tardias a B"],
            "quick_wins": ["Mezclar tempos en mid (10 min)", "Practicar salida rapida a A (10 min)"]
        },
        "training": [
            {"title": "Variantes de mid control", "tip": "Alternar humo ventana/short y peeks escalonados."},
            {"title": "Tempo change A", "tip": "Exec corta con doble flash y molos default."}
        ]
    },
    "utility_denial": {
        "summary": {
            "map": "Mirage",
            "score": "9-16",
            "duration": "38:45",
            "team": "Sharpix Academy",
            "score100": 66,
            "suggested_role": "Support",
            "scores": {"entry_trades": 0.50, "utility": 0.78, "postplant": 0.70}
        },
        "insights": {
            "strengths": ["Buen dano de utilidad", "Control de rampas con molos"],
            "weaknesses": ["Trades tardios", "Lurk inexistente"],
            "quick_wins": ["Sincronizar flashes para entry (8 min)", "Agregar lurk en B apps (10 min)"]
        },
        "training": [
            {"title": "HE/Incendiary setup", "tip": "Practicar rebotes en ramp y apps para frenar rush."},
            {"title": "Timing de flash apoyo", "tip": "Coordinar pop desde jungle/connector para el entry."}
        ]
    },
    "retake_focus": {
        "summary": {
            "map": "Mirage",
            "score": "13-16",
            "duration": "46:10",
            "team": "Sharpix Academy",
            "score100": 74,
            "suggested_role": "Closer",
            "scores": {"entry_trades": 0.58, "utility": 0.64, "postplant": 0.80}
        },
        "insights": {
            "strengths": ["Retakes coordinados", "Clutches 1vX positivos"],
            "weaknesses": ["Openings negativos", "Pocas agresiones en mid"],
            "quick_wins": ["Pivots mas rapidos desde B (10 min)", "Tomas de mid con flash pop (10 min)"]
        },
        "training": [
            {"title": "Protocolos de retake", "tip": "Roles fijos y utilidad escalonada en A y B."},
            {"title": "Openings seguros", "tip": "Trabajar duos en top mid con refrag garantizado."}
        ]
    }
}
# Presets por rol de jugador
ROLE_SUMMARIES = {
    "entry": {
        "summary": {
            "map": "Mirage",
            "score": "16-12",
            "duration": "42:10",
            "team": "Sharpix Academy",
            "score100": 82,
            "suggested_role": "Entry",
            "scores": {"entry_trades": 0.80, "utility": 0.48, "postplant": 0.65}
        },
        "insights": {
            "strengths": ["Alta presion en entries", "Buenos duelos iniciales"],
            "weaknesses": ["Apoyo de utilidad intermitente"],
            "quick_wins": ["Coordinar doble flash en salidas (10 min)", "Revisar rutas de entrada (10 min)"]
        },
        "training": [
            {"title": "Duelos top-mid", "tip": "Trabajar peek + trade con apoyo."},
            {"title": "Ejecutar A rapido", "tip": "Entradas desde ramp con flashes altas."}
        ]
    },
    "awper": {
        "summary": {
            "map": "Mirage",
            "score": "16-11",
            "duration": "40:55",
            "team": "Sharpix Academy",
            "score100": 84,
            "suggested_role": "AWPer",
            "scores": {"entry_trades": 0.72, "utility": 0.40, "postplant": 0.70}
        },
        "insights": {
            "strengths": ["Pick inicial consistente", "Control de angulos clave"],
            "weaknesses": ["Re-picks arriesgados sin apoyo"],
            "quick_wins": ["Evitar re-peek tras kill (5 min)", "Cambiar ritmo con boosts (10 min)"]
        },
        "training": [
            {"title": "Off-angles CT/Jungle", "tip": "Practicar timings de repeek con flash."},
            {"title": "Economia AWP", "tip": "Definir rounds de drop/prioridad."}
        ]
    },
    "lurker": {
        "summary": {
            "map": "Mirage",
            "score": "13-16",
            "duration": "46:22",
            "team": "Sharpix Academy",
            "score100": 74,
            "suggested_role": "Lurker",
            "scores": {"entry_trades": 0.55, "utility": 0.60, "postplant": 0.68}
        },
        "insights": {
            "strengths": ["Flanks con impacto", "informacion tardia util"],
            "weaknesses": ["Timings inconsistentes"],
            "quick_wins": ["Sincronizar lurk con exec (10 min)", "Rutas silenciosas (5 min)"]
        },
        "training": [
            {"title": "Lurk en B apps", "tip": "Evitar info rival, castigar pushes."},
            {"title": "Split A con lurk", "tip": "Entrar tras contacto de conector."}
        ]
    },
    "support": {
        "summary": {
            "map": "Mirage",
            "score": "16-10",
            "duration": "41:03",
            "team": "Sharpix Academy",
            "score100": 81,
            "suggested_role": "Support",
            "scores": {"entry_trades": 0.62, "utility": 0.82, "postplant": 0.72}
        },
        "insights": {
            "strengths": ["Buen gasto de utilidades", "Spacing solido"],
            "weaknesses": ["Trades tardios en split"],
            "quick_wins": ["Lineups consistentes (10 min)", "Call de flash exacta (5 min)"]
        },
        "training": [
            {"title": "Lineups A/B", "tip": "Repetir humos/flash para execs."},
            {"title": "Spacing en entradas", "tip": "Mantener trade distance constante."}
        ]
    },
    "igl": {
        "summary": {
            "map": "Mirage",
            "score": "16-14",
            "duration": "48:10",
            "team": "Sharpix Academy",
            "score100": 79,
            "suggested_role": "IGL",
            "scores": {"entry_trades": 0.60, "utility": 0.74, "postplant": 0.76}
        },
        "insights": {
            "strengths": ["Mid-round calls efectivos", "Adaptacion a setups"],
            "weaknesses": ["Overcalls en mid tardio"],
            "quick_wins": ["Protocolos por info (10 min)", "Pivots A/B mas rapidos (10 min)"]
        },
        "training": [
            {"title": "Mid control", "tip": "Tener 3 variantes con timings claros."},
            {"title": "Mapeo de reacciones", "tip": "Predefinir respuestas a utilidad rival."}
        ]
    },
    "anchor": {
        "summary": {
            "map": "Mirage",
            "score": "12-16",
            "duration": "45:40",
            "team": "Sharpix Academy",
            "score100": 72,
            "suggested_role": "Anchor",
            "scores": {"entry_trades": 0.58, "utility": 0.66, "postplant": 0.70}
        },
        "insights": {
            "strengths": ["Holds solidos en site", "Buena utilidad defensiva"],
            "weaknesses": ["Rotaciones tardias"],
            "quick_wins": ["Timings de info (5 min)", "Jugar con apoyo desde short/CT (10 min)"]
        },
        "training": [
            {"title": "Holds en B", "tip": "Usar molos/HE para frenar pushes."},
            {"title": "Rotaciones CT", "tip": "Definir triggers y pedir apoyo temprano."}
        ]
    }
}

# Unificar variantes disponibles (genericas + por rol)
ALL_VARIANTS = {}
ALL_VARIANTS.update(VARIANT_SUMMARIES)
ALL_VARIANTS.update(ROLE_SUMMARIES)
ALL_VARIANTS.update({f"role_{k}": v for k, v in ROLE_SUMMARIES.items()})

# Helpers para variantes por decenas on-demand
def _scoreline_for(s: int) -> str:
    try:
        s = int(s)
    except Exception:
        s = 60
    if s >= 90:
        return "16-7"
    if s >= 80:
        return "16-9"
    if s >= 70:
        return "16-12"
    if s >= 60:
        return "16-14"
    if s >= 50:
        return "14-16"
    if s >= 40:
        return "12-16"
    return "9-16"

def _build_decile_variant(score: int):
    s = min(100, max(10, int(round(score/10.0)*10)))
    scoreline = _scoreline_for(s)
    val = s/100.0
    return {
        "summary": {
            "map": "Mirage",
            "score": scoreline,
            "duration": "43:00",
            "team": "Sharpix Academy",
            "score100": s,
            "scores": {"entry_trades": val, "utility": val, "postplant": val}
        },
        "insights": {
            "strengths": ["Puntos claros de mejora", "Ejecuciones consistentes por tramo"],
            "weaknesses": ["Ajustar timings y utilidad"],
            "quick_wins": ["Lineups clave (10 min)", "Coordinacion de trades (10 min)"]
        },
        "training": [
            {"title": "Timings en Mid", "tip": "Alinear peeks con flash pop."},
            {"title": "Post-plant basico", "tip": "Cruces y roles definidos."}
        ]
    }

def _pick_variant(name):
    keys = list(ALL_VARIANTS.keys())
    if name and name in ALL_VARIANTS:
        return name, ALL_VARIANTS[name]
    # Acepta on-demand variantes scoreXX (decenas)
    if name:
        try:
            n = int(''.join(ch for ch in str(name) if ch.isdigit()))
            if str(name).lower().startswith('score') and n >= 10 and n <= 100:
                return f"score{int(round(n/10.0)*10)}", _build_decile_variant(n)
        except Exception:
            pass
    choice = random.choice(keys)
    return choice, ALL_VARIANTS[choice]

# ---------------------- Modelos ----------------------
class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    username = db.Column(db.String(64), unique=True, nullable=False)
    pw_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    email_verified = db.Column(db.Boolean, default=False)
    email_code = db.Column(db.String(8), nullable=True)
    email_code_sent_at = db.Column(db.DateTime, nullable=True)

class Match(db.Model):
    __tablename__ = "matches"
    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.String(32), index=True, nullable=False)  # p.ej. "m_123abc"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    map = db.Column(db.String(32), default="mirage")
    score100 = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    # Compatible con SQLAlchemy 2.x (evita LegacyAPIWarning)
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None

# CLI para crear BD
@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("DB creada (web/app.db)")

# (Opcional) autocreate si no existe
with app.app_context():
    if not os.path.exists(os.path.join(APP_DIR, "app.db")):
        db.create_all()

# ---------------------- Helpers Novu ----------------------
def trigger_novu_verification(target, code: str):
    """Envía código usando Novu. Acepta user o dict con email/username."""
    if not NOVU_SECRET_KEY:
        return False
    email = getattr(target, "email", None) if target is not None else None
    if not email and isinstance(target, dict):
        email = target.get("email")
    username = getattr(target, "username", None) if target is not None else None
    if not username and isinstance(target, dict):
        username = target.get("username")
    subscriber_id = getattr(target, "id", None) if target is not None else None
    if not subscriber_id and isinstance(target, dict):
        subscriber_id = target.get("subscriber_id") or email

    if not email:
        return False

    payload = {
        "name": NOVU_WORKFLOW_ID,
        "to": {
            "subscriberId": str(subscriber_id or email),
            "firstName": username or "Jugador",
            "lastName": "",
            "email": email,
        },
        "payload": {
            "user": username or "Jugador",
            "code": int(code)
        }
    }
    req = urllib.request.Request(
        "https://api.novu.co/v1/events/trigger",
        method="POST",
        headers={
            "Authorization": f"ApiKey {NOVU_SECRET_KEY}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload).encode("utf-8")
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return True
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        print(f"[novu] {e.code} for {user.email}: {detail or e.reason}")
        return False
    except Exception as e:
        print(f"[novu] trigger failed for {user.email}: {e}")
        return False

def send_verification_email(user):
    code = str(random.randint(100000, 999999))
    user.email_code = code
    user.email_code_sent_at = datetime.utcnow()
    db.session.commit()

    sent = trigger_novu_verification(user, code)
    if sent:
        flash(f"Te enviamos un codigo a {user.email}. Revisa tu bandeja.")
    else:
        flash(f"No se pudo enviar notificacion. Usa este codigo: {code}", "warning")

# ---------------------- Util FS ----------------------
def out_dir(match_id: str) -> str:
    return os.path.join(OUT_DIR, match_id)

def upload_dir_for(match_id: str) -> str:
    return os.path.join(UPLOADS_DIR, match_id)

# ---------------------- Utils JSON / fixtures ----------------------
def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def load_fixtures():
    """Carga static/fixtures.json; si no existe, devuelve un set minimo."""
    p = os.path.join(STATIC_DIR, "fixtures.json")
    data = load_json(p)
    if data:
        return data
    # fallback minimo
    return {
        "summary": {
            "match_id": "m_demo1234",
            "map": "Mirage",
            "score": "16-12",
            "duration": "45:32",
            "team": "Sharpix Academy",
            "score100": 83
        },
        "kpis": [
            {"name":"Kills / Deaths","value":"1.15","label":"Buen rendimiento"},
            {"name":"Eficiencia de Trades","value":"72%","label":"Buena"},
            {"name":"Control de Mid","value":"58%","label":"Inestable"},
            {"name":"Uso de Utilidades","value":"41%","label":"Bajo"},
            {"name":"Plant/Defuse","value":"62%","label":"Correcto"}
        ],
        "insights": {
            "strengths": ["Control temprano de B","Coordinacion en retakes","Coberturas cruzadas en A"],
            "weaknesses": ["Falta de humo en CT","Peek sin flash en mid","Rotaciones tardias desde B"],
            "quick_wins": ["Practicar retake B (2v3)  - 15 min","Timings mid->A  - 10 min","Set de humos A  - 10 min"]
        },
        "training": [
            {"title":"Set de humos A (Mirage)","tip":"Pulir lineup desde Tetris y Palace - consistencia 90%."},
            {"title":"Retake B (2v3)","tip":"Roles claros y flash pop desde short - 3 reps por sesion."}
        ]
    }

# ---------------------- Rounds helpers ----------------------
DEFAULT_ROUNDS_SAMPLE = [
    {"round": 1, "side": "T", "winner": "T", "score_t": 1, "score_ct": 0, "plant_site": "A", "plant_ts": 48.2, "result": "post-plant ganado", "clutch": "1v2", "entry": "ganado", "notes": "Pistol: split A, retake lento."},
    {"round": 5, "side": "T", "winner": "CT", "score_t": 2, "score_ct": 3, "plant_site": "B", "plant_ts": 62.5, "result": "post-plant perdido", "clutch": "0/1", "entry": "perdido", "notes": "Exec B sin humo short, trade tardio."},
    {"round": 9, "side": "CT", "winner": "CT", "score_t": 4, "score_ct": 5, "plant_site": "", "plant_ts": None, "result": "defensa", "clutch": "1v1 ganado", "entry": "ganado", "notes": "Pick agresivo mid con apoyo HE."},
    {"round": 12, "side": "CT", "winner": "T", "score_t": 7, "score_ct": 5, "plant_site": "A", "plant_ts": 54.1, "result": "retake fallido", "clutch": "0/2", "entry": "perdido", "notes": "Sin kits en retake, quedo 2v3."},
    {"round": 14, "side": "T", "winner": "T", "score_t": 8, "score_ct": 6, "plant_site": "B", "plant_ts": 58.7, "result": "post-plant ganado", "clutch": "1v1 ganado", "entry": "ganado", "notes": "Lurk apps captura rotacion, retake denegado."},
    {"round": 22, "side": "CT", "winner": "CT", "score_t": 10, "score_ct": 12, "plant_site": "", "plant_ts": None, "result": "defensa limpia", "clutch": "-", "entry": "ganado", "notes": "Doble setup en A, buen uso de utilidad."}
]

def load_rounds_data(match_id: str | None):
    """Carga rondas desde per_round.csv si existe; si no, usa fixtures o fallback default."""
    if match_id:
        pr_path = os.path.join(out_dir(match_id), "per_round.csv")
        if os.path.exists(pr_path):
            import csv
            rows = []
            try:
                with open(pr_path, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        try:
                            row["round"] = int(row.get("round", row.get("Round", 0)) or 0)
                        except Exception:
                            pass
                        rows.append(row)
                if rows:
                    return rows
            except Exception as e:
                print(f"[rounds] no se pudo leer per_round.csv: {e}")
    fx = load_fixtures() or {}
    if isinstance(fx, dict) and fx.get("roundsSample"):
        return fx["roundsSample"]
    return DEFAULT_ROUNDS_SAMPLE

def compute_side_stats(rounds):
    """Calcula stats simples por lado a partir de la lista de rondas."""
    sides = {
        "T": {"rounds": 0, "won": 0, "plants": 0, "plants_won": 0},
        "CT": {"rounds": 0, "won": 0, "plants": 0, "plants_won": 0},
    }
    for r in rounds or []:
        get = r.get if isinstance(r, dict) else getattr
        side = (get("side", "") if callable(get) else getattr(r, "side", "")) or ""
        winner = (get("winner", "") if callable(get) else getattr(r, "winner", "")) or ""
        side = str(side).upper()
        winner = str(winner).upper()
        if side not in sides:
            continue
        s = sides[side]
        s["rounds"] += 1
        if winner == side:
            s["won"] += 1
        plant_site = (get("plant_site", "") if callable(get) else getattr(r, "plant_site", "")) or ""
        if plant_site:
            s["plants"] += 1
            if winner == side:
                s["plants_won"] += 1

    # tasas
    for key, val in sides.items():
        val["win_rate"] = int(round((val["won"] / val["rounds"]) * 100)) if val["rounds"] else 0
        val["plant_conv"] = int(round((val["plants_won"] / val["plants"]) * 100)) if val["plants"] else 0
    # mejor lado
    best = max(sides.items(), key=lambda kv: kv[1]["win_rate"])[0] if sides else None
    return {"sides": sides, "best": best}

# ---------------------- Normalizacion para summary.html ----------------------
def _as_percent01(v):
    try:
        x = float(v)
        return x/100.0 if x > 1.01 else x
    except Exception:
        return 0.0

def _num(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return float(default)

def _int(v, default=0):
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return int(default)

def map_fb_to_summary(fb: dict, match_id: str):
    """
    Devuelve un objeto con la forma que espera templates/summary.html:
    {
      "summary": {...},
      "kpis": [...],
      "insights": {"strengths":[], "weaknesses":[], "quick_wins":[]},
      "training": [...]
    }
    """
    fb = fb or {}

    # Summary
    s = fb.get("summary", {})
    summary_norm = {
        "match_id": match_id,
        "map": s.get("map") or fb.get("map") or "Mirage",
        "score": s.get("score") or "16-12",
        "duration": s.get("duration") or fb.get("duration") or "45:32",
        "team": s.get("team") or fb.get("team") or "Sharpix Academy",
        "score100": _int(s.get("score100") or s.get("score") or 83),
        "suggested_role": s.get("suggested_role") or fb.get("suggested_role")
    }

    # KPIs
    if isinstance(fb.get("kpis"), list):
        kpis = fb["kpis"]
    elif isinstance(s.get("scores"), dict):
        # compat: summary.scores con porcentajes 0..1 o 0..100
        scores = s["scores"]
        v_trades    = int(_as_percent01(scores.get("entry_trades", 0))*100)
        v_utility   = int(_as_percent01(scores.get("utility", 0))*100)
        v_postplant = int(_as_percent01(scores.get("postplant", 0))*100)
        kpis = [
            {"name":"Trades =5s","value":f"{v_trades}%", "label":"impacto en entries"},
            {"name":"Utility","value":f"{v_utility}%", "label":"dano por ronda"},
            {"name":"Post-plant","value":f"{v_postplant}%", "label":"conversion"},
        ]
    else:
        kpis = [
            {"name":"Kills / Deaths","value":"1.15","label":"Buen rendimiento"},
            {"name":"Eficiencia de Trades","value":"72%","label":"Buena"},
            {"name":"Control de Mid","value":"58%","label":"Inestable"},
            {"name":"Uso de Utilidades","value":"41%","label":"Bajo"},
            {"name":"Plant/Defuse","value":"62%","label":"Correcto"},
        ]

    # Insights
    ins = fb.get("insights") or {}
    insights_norm = {
        "strengths": ins.get("strengths") or ["Control temprano de B","Coordinacion en retakes","Coberturas cruzadas en A"],
        "weaknesses": ins.get("weaknesses") or ["Falta de humo en CT","Peek sin flash en mid","Rotaciones tardias desde B"],
        "quick_wins": ins.get("quick_wins") or ["Practicar retake B (2v3)  - 15 min","Timings mid->A  - 10 min","Set de humos A  - 10 min"],
    }

    # Training
    training = fb.get("training") or [
        {"title":"Set de humos A (Mirage)","tip":"Pulir lineup desde Tetris y Palace - consistencia 90%."},
        {"title":"Retake B (2v3)","tip":"Roles claros y flash pop desde short - 3 reps por sesion."},
        {"title":"Peeks en Mid con flash","tip":"Evitar dry peeks - usar one-way top mid."},
        {"title":"Rotaciones mas tempranas","tip":"Definir trigger al primer info de presencia."}
    ]

    # Score fallback si solo hay 'scores' crudos
    if not s.get("score100") and isinstance(s.get("scores"), dict):
        vals = []
        for k in ("entry_trades","utility","postplant"):
            vals.append(int(_as_percent01(s["scores"].get(k, 0))*100))
        if vals:
            summary_norm["score100"] = int(sum(vals)/len(vals))

    return {
        "summary": summary_norm,
        "kpis": kpis,
        "insights": insights_norm,
        "training": training
    }

# ------------------------- Pipeline real -------------------------
def run_pipeline(events_path: str, rounds_path: str, match_id: str):
    outp = out_dir(match_id)
    os.makedirs(outp, exist_ok=True)

    # build_kpis
    subprocess.run([
        sys.executable, os.path.join(PROCESSOR_DIR, "build_kpis.py"),
        "--events", events_path,
        "--rounds", rounds_path,
        "--map_config", MAP_CFG,
        "--out_dir", outp
    ], check=True)

    # feedback_engine
    subprocess.run([
        sys.executable, os.path.join(PROCESSOR_DIR, "feedback_engine.py"),
        "--kpis", os.path.join(outp, "kpis_team.json"),
        "--per_round", os.path.join(outp, "per_round.csv"),
        "--thresholds", os.path.join(PROCESSOR_DIR, "thresholds.yml"),
        "--out", os.path.join(outp, "feedback.json")
    ], check=True)

# --------------------------- Rutas publicas ---------------------------
# --- reemplaza tu "/" actual por este ---
@app.route("/")
def home():
    if current_user.is_authenticated:
        # dashboard de usuario
        recent = []
        try:
            recent = (Match.query
                      .filter_by(user_id=current_user.id)
                      .order_by(Match.created_at.desc())
                      .limit(10)
                      .all())
        except Exception:
            pass
        return render_template("menu.html", recent_matches=recent)
    # landing publica
    return render_template("landing.html")


# --------------------------- Auth ---------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not username or not email or not password:
            flash("Completa usuario, email y contrasena.")
            return redirect(request.url)
        if User.query.filter((User.username==username) | (User.email==email)).first():
            flash("Usuario o email ya existen.")
            return redirect(request.url)

        code = str(random.randint(100000, 999999))
        session["pending_reg"] = {
            "username": username,
            "email": email,
            "pw_hash": generate_password_hash(password, method="pbkdf2:sha256", salt_length=16),
            "code": code,
            "created_at": datetime.utcnow().isoformat()
        }

        sent = trigger_novu_verification({"email": email, "username": username}, code)
        if sent:
            flash(f"Te enviamos un codigo a {email}. Revisa tu bandeja.")
        else:
            flash(f"No se pudo enviar notificacion. Usa este codigo: {code}", "warning")
        return redirect(url_for("verify_email", email=email))
    return render_template("auth_login.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = (request.form.get("identifier") or "").strip()  # username o email
        password = request.form.get("password") or ""
        u = User.query.filter(
            (User.username==identifier) | (User.email==identifier.lower())
        ).first()
        if not u or not check_password_hash(u.pw_hash, password):
            flash("Credenciales invalidas.")
            return redirect(request.url)
        if not getattr(u, "email_verified", False):
            send_verification_email(u)
            flash("Verifica tu email para ingresar.")
            return redirect(url_for("verify_email", email=u.email))
        login_user(u, remember=("remember" in request.form))
        flash("Sesion iniciada.")
        return redirect(request.args.get("next") or url_for("home"))
    return render_template("auth_login.html", mode="login")

@app.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    email = (request.args.get("email") or request.form.get("email") or "").strip().lower()
    pending = session.get("pending_reg") if isinstance(session.get("pending_reg"), dict) else {}
    pending_match = pending.get("email") == email if pending else False

    u = User.query.filter_by(email=email).first()
    if not email:
        flash("Ingresa tu email para verificar.")
        return redirect(url_for("login"))
    if not u and not pending_match:
        flash("Email no encontrado. Crea tu cuenta.")
        return redirect(url_for("register"))
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        if not code:
            flash("Ingresa el codigo enviado.")
            return redirect(request.url)
        if u:
            if code == (u.email_code or ""):
                u.email_verified = True
                u.email_code = None
                db.session.commit()
                login_user(u)
                flash("Email verificado. Sesion iniciada.")
                return redirect(url_for("home"))
        elif pending_match and code == pending.get("code"):
            u = User(
                username=pending["username"],
                email=pending["email"],
                pw_hash=pending["pw_hash"],
                email_verified=True
            )
            db.session.add(u)
            db.session.commit()
            session.pop("pending_reg", None)
            login_user(u)
            flash("Email verificado. Sesion iniciada.")
            return redirect(url_for("home"))
        flash("Codigo invalido. Revisa tu correo.")
        return redirect(request.url)
    return render_template("verify_email.html", email=email)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))

# --------------------------- Auth SSO (stubs) ---------------------------
@app.route("/auth/steam")
def auth_steam():
    flash("Steam SSO pronto. Por ahora usa email/contrase\u00f1a.")
    return redirect(url_for("login"))

@app.route("/auth/faceit")
def auth_faceit():
    flash("FaceIt SSO pronto. Por ahora usa email/contrase\u00f1a.")
    return redirect(url_for("login"))

# --------------------------- Upload protegido ---------------------------
@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        demo = request.files.get("demo_file")
        if not demo or not demo.filename.lower().endswith(".dem"):
            flash("Sube un archivo .dem valido.")
            return redirect(request.url)

        match_id = "m_" + uuid.uuid4().hex[:8]
        up_dir = upload_dir_for(match_id)
        os.makedirs(up_dir, exist_ok=True)

        dem_path = os.path.join(up_dir, demo.filename)
        demo.save(dem_path)

        # SIMULACION: en lugar de correr el pipeline real, generamos feedback.json
        # a partir de una variante hard-codeada y persistimos el Match.
        if current_app.config.get("SIMULATE_UPLOAD", False):
            variant_param = request.args.get("variant") or request.form.get("variant")
            variant_name, fb_variant = _pick_variant(variant_param)
            fb_norm = map_fb_to_summary(fb_variant, match_id=match_id)

            outp = out_dir(match_id)
            os.makedirs(outp, exist_ok=True)
            try:
                with open(os.path.join(outp, "feedback.json"), "w", encoding="utf-8") as f:
                    json.dump(fb_norm, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

            score100 = 0
            try:
                score100 = int(fb_norm.get("summary", {}).get("score100") or 0)
            except Exception:
                score100 = 0

            db.session.add(Match(match_id=match_id, user_id=current_user.id, map="mirage", score100=score100))
            db.session.commit()
            flash(f"Demo simulada ({variant_name}).")
            return redirect(url_for("summary_with_id", match_id=match_id))

        events_path = os.path.join(up_dir, "events.csv")
        rounds_path = os.path.join(up_dir, "rounds.csv")

        # 1) Adapter: .dem -> CSVs
        try:
            completed = subprocess.run([
                sys.executable, os.path.join(PROCESSOR_DIR, "adapter_dem.py"),
                "--dem", dem_path, "--events", events_path, "--rounds", rounds_path
            ], check=True, capture_output=True, text=True)
            print("ADAPTER STDOUT:\n", completed.stdout)
            print("ADAPTER STDERR:\n", completed.stderr)
        except subprocess.CalledProcessError as e:
            flash(f"Adapter DEM error (exit {e.returncode}).<br>STDOUT:<pre>{e.stdout}</pre><br>STDERR:<pre>{e.stderr}</pre>")
            return redirect(request.url)
        except Exception as e:
            flash(f"Adapter DEM error: {e}")
            return redirect(request.url)

        # 2) Pipeline
        try:
            run_pipeline(events_path, rounds_path, match_id)
        except Exception as e:
            flash(f"Pipeline error: {e}")
            return redirect(request.url)

        # 3) Persistir match -> user (intenta leer score100)
        score100 = 0
        try:
            fb_raw = load_json(os.path.join(out_dir(match_id), "feedback.json")) or {}
            s = fb_raw.get("summary") or {}
            score100 = int(s.get("score100") or 0)
        except Exception:
            pass

        db.session.add(Match(match_id=match_id, user_id=current_user.id, map="mirage", score100=score100))
        db.session.commit()

        return redirect(url_for("summary_with_id", match_id=match_id))

    return render_template("upload.html")

# --------------------------- Summary (real y demo) ---------------------------
@app.route("/summary/<match_id>")
@login_required
def summary_with_id(match_id):
    # Ownership
    m = Match.query.filter_by(match_id=match_id, user_id=current_user.id).first()
    if not m:
        flash("No tenes acceso a este match.")
        return redirect(url_for("home"))

    fb_path = os.path.join(out_dir(match_id), "feedback.json")
    if not os.path.exists(fb_path):
        flash("No se encontro feedback de ese match. Mostramos una demo para referencia.", "warning")
        return redirect(url_for("summary_demo"))

    fb_raw = load_json(fb_path)
    if not fb_raw:
        flash("feedback.json invalido. Mostramos demo.", "warning")
        return redirect(url_for("summary_demo"))

    fb_norm = map_fb_to_summary(fb_raw, match_id=match_id)
    fb_norm['sides'] = compute_side_stats(load_rounds_data(match_id))
    return render_template('summary.html', fb=fb_norm)

@app.route("/summary")
def summary_demo():
    """Vista demo: usa fixtures.json para sacar capturas sin subir nada."""
    fixtures = load_fixtures()
    # si fixtures ya esta en forma nueva, lo usamos directo; si no, normalizamos
    if all(k in fixtures for k in ("summary","kpis","insights")):
        fb_norm = fixtures
        fb_norm["summary"]["match_id"] = fb_norm["summary"].get("match_id") or "m_demo1234"
    else:
        fb_norm = map_fb_to_summary(fixtures, match_id='m_demo1234')
    fb_norm['sides'] = compute_side_stats(load_rounds_data(None))
    return render_template('summary.html', fb=fb_norm)

# --- ALIASES para compatibilidad con navbar antigua ---
# (Permite que url_for('summary_root') y url_for('rounds_root') funcionen)
app.add_url_rule("/summary", endpoint="summary_root", view_func=summary_demo)

# --------------------------- Summary por rol ---------------------------
@app.route("/summary/role/<role>")
def summary_role(role):
    role_key = (role or "").lower()
    data = ROLE_SUMMARIES.get(role_key)
    if not data:
        flash("Rol desconocido. Opciones: entry, awper, lurker, support, igl, anchor.", "warning")
        return redirect(url_for("summary_demo"))
    fb_norm = map_fb_to_summary(data, match_id=f"role_{role_key}")
    return render_template("summary.html", fb=fb_norm)

# --------------------------- Summary por decena ---------------------------
@app.route("/summary/score/<int:score>")
def summary_score(score: int):
    try:
        key, data = f"score{int(round(score/10.0)*10)}", _build_decile_variant(score)
    except Exception:
        flash("Valor invalido de score.", "warning")
        return redirect(url_for("summary_demo"))
    fb_norm = map_fb_to_summary(data, match_id=key)
    return render_template("summary.html", fb=fb_norm)

# --------------------------- Rounds (placeholder compatibles) ---------------------------
@app.route("/rounds/<match_id>")
@login_required
def rounds_with_id(match_id):
    # Ownership
    m = Match.query.filter_by(match_id=match_id, user_id=current_user.id).first()
    if not m:
        flash("No tenes acceso a este match.")
        return redirect(url_for("home"))

    tpl_rounds = os.path.join(APP_DIR, "templates", "rounds.html")
    if os.path.exists(tpl_rounds):
        fixtures = load_fixtures()
        rounds = load_rounds_data(match_id)
        return render_template("rounds.html", fb=fixtures, match_id=match_id, rounds=rounds)

    # fallback: mostrar summary del match
    return redirect(url_for("summary_with_id", match_id=match_id))

@app.route("/rounds")
def rounds_demo():
    tpl_rounds = os.path.join(APP_DIR, "templates", "rounds.html")
    fixtures = load_fixtures()
    if os.path.exists(tpl_rounds):
        rounds = load_rounds_data(None)
        return render_template("rounds.html", fb=fixtures, match_id=None, rounds=rounds)
    return redirect(url_for("summary_demo"))

# Alias para navbar antigua
app.add_url_rule("/rounds", endpoint="rounds_root", view_func=rounds_demo)

# --- Rutas informativas / navegacion basica ---
@app.route("/profile")
def profile():
    return render_template("profile.html")

@app.route("/analytics")
def analytics():
    return render_template("analytics.html")

@app.route("/team")
def team():
    return render_template("team.html")

@app.route("/settings")
def settings():
    return render_template("settings.html")

@app.route("/help")
def help_page():
    return render_template("help.html")

@app.route("/pricing")
def pricing():
    return render_template("pricing.html")

@app.route("/faq")
def faq():
    return render_template("faq.html")


# --------------------------- Main ---------------------------
if __name__ == "__main__":
    app.run(debug=True)








