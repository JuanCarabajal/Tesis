from flask import Flask, render_template, request, redirect, url_for, flash, current_app
import json, os, uuid, subprocess, sys
from datetime import datetime

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

# --- DB: SQLite local ---
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# --- Login manager ---
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Inicia sesión para continuar."

# Flags simples para templates
@app.context_processor
def inject_nav_flags():
    return {"has_demos": "demos" in current_app.view_functions}

# --- Paths base del proyecto ---
ROOT_DIR   = os.path.dirname(os.path.dirname(__file__))  # raíz del repo
MAP_CFG    = os.path.join(ROOT_DIR, "configs", "maps", "mirage.yml")
STATIC_DIR = os.path.join(ROOT_DIR, "static")

# ---------------------- Modelos ----------------------
class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    username = db.Column(db.String(64), unique=True, nullable=False)
    pw_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    try:
        return User.query.get(int(user_id))
    except Exception:
        return None

# CLI para crear BD
@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("DB creada (web/app.db)")

# (Opcional) autocreate si no existe
with app.app_context():
    if not os.path.exists(os.path.join(os.path.dirname(__file__), "app.db")):
        db.create_all()

# ---------------------- Util FS ----------------------
def out_dir(match_id: str) -> str:
    # Para MVP no namespaciamos por usuario en FS; ownership se controla en DB.
    return os.path.join(ROOT_DIR, "out", match_id)

def upload_dir_for(match_id: str) -> str:
    return os.path.join(ROOT_DIR, "uploads", match_id)

# ---------------------- Fixtures / util ----------------------
def load_fixtures():
    """Carga static/fixtures.json; si no existe, devuelve fallback mínimo."""
    p = os.path.join(STATIC_DIR, "fixtures.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "summaryMock": {
                "matchId": "mirage_demo",
                "date": "",
                "map": "mirage",
                "score10": 7.3,
                "score100": 73,
                "kpis": [],
                "postplant": {"A": {"attempts": 0, "winrate": 0.0, "tsAliveEndAvg": 0},
                              "B": {"attempts": 0, "winrate": 0.0, "tsAliveEndAvg": 0}},
                "topDeathZones": [],
                "quickWins": []
            },
            "eventsSample": [],
            "roundsSample": [],
            "matches": []
        }

def _as_percent01(v):
    """Devuelve porcentaje en 0..1 a partir de 0..1 o 0..100."""
    try:
        x = float(v)
        if x > 1.01:
            return x / 100.0
        return x
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

def map_fb_to_summary(fb: dict, match_id: str) -> dict | None:
    """
    Acepta varias formas de feedback.json.
    - Si ya hay fb["summary"] *completa*, la devuelve (normalizando score).
    - Si hay fb["summary"]["scores"] como en tu ejemplo, la traduce.
    - Si no, intenta mapear claves sueltas (kpis_team, etc.).
    """
    if not isinstance(fb, dict):
        return None

    # -------- Caso 1: summary ya completa (con score/kpis/postplant/top zones)
    s = fb.get("summary")
    if isinstance(s, dict) and any(k in s for k in ("kpis", "postplant", "topDeathZones", "score100")):
        score100 = _int(s.get("score100") or s.get("score") or 0)
        s.setdefault("score100", score100)
        s.setdefault("score10", round(score100 / 10.0, 1))
        s.setdefault("matchId", match_id)
        s.setdefault("map", (fb.get("map") or "mirage").lower())
        return s

    # -------- Caso 2: summary con 'scores' + 'quick_wins' (tu JSON)
    if isinstance(s, dict) and isinstance(s.get("scores"), dict):
        scores = s["scores"]  # p.ej. {"entry_trades": 0, "utility": 0, "postplant": 100}

        # 5 KPIs esperados y pesos v1
        WEIGHTS = {
            "trades_5s":      0.30,   # entry_trades
            "postplant":      0.25,   # postplant
            "util_dmg_rd":    0.20,   # utility
            "opening_impact": 0.15,   # sin dato -> 0
            "clutch_2vx":     0.10,   # sin dato -> 0
        }

        # Tomamos lo que venga; valores pueden estar en 0..1 o 0..100
        v_trades    = _int((_as_percent01(scores.get("entry_trades", 0)) * 100))
        v_utility   = _int((_as_percent01(scores.get("utility", 0)) * 100))
        v_postplant = _int((_as_percent01(scores.get("postplant", 0)) * 100))
        # Faltantes
        v_opening   = 0
        v_2vx       = 0

        # Re-ponderación si faltan KPIs (no penaliza falta de evidencia)
        present = {
            "trades_5s": v_trades,
            "postplant": v_postplant,
            "util_dmg_rd": v_utility,
        }
        weight_sum_present = sum(WEIGHTS[k] for k in present.keys())
        def reweight(w):
            return w / weight_sum_present if weight_sum_present > 0 else 0

        kpis = [
            {"id":"trades_5s",      "label":"Trades ≤5s",        "weight": WEIGHTS["trades_5s"],      "value100": v_trades,    "evidence":"", "contribution": reweight(WEIGHTS["trades_5s"])   * v_trades / 10.0},
            {"id":"postplant",      "label":"Post-plant",        "weight": WEIGHTS["postplant"],      "value100": v_postplant, "evidence":"", "contribution": reweight(WEIGHTS["postplant"])   * v_postplant / 10.0},
            {"id":"util_dmg_rd",    "label":"Utility dmg/rd",    "weight": WEIGHTS["util_dmg_rd"],    "value100": v_utility,   "evidence":"", "contribution": reweight(WEIGHTS["util_dmg_rd"]) * v_utility / 10.0},
            {"id":"opening_impact", "label":"Opening impact",    "weight": WEIGHTS["opening_impact"], "value100": v_opening,   "evidence":"Evidencia limitada", "contribution": 0.0},
            {"id":"clutch_2vx",     "label":"2vX conversion",    "weight": WEIGHTS["clutch_2vx"],     "value100": v_2vx,       "evidence":"Evidencia limitada", "contribution": 0.0},
        ]

        # Score visible reponderado
        score100 = 0
        for k in kpis:
            if k["value100"] > 0 or k["id"] in present:
                score100 += reweight(WEIGHTS[k["id"]]) * k["value100"]
        score100 = int(round(score100, 0))
        score10  = round(score100 / 10.0, 1)

        # Quick wins desde summary.quick_wins + findings
        qw = []
        for i, q in enumerate(s.get("quick_wins", [])[:3], 1):
            sev = "med"
            impact = _num(q.get("impact", 0))
            if impact >= 0.7: sev = "high"
            elif impact <= 0.35: sev = "low"
            qw.append({
                "id": f"qw{i}",
                "title": q.get("title") or "Sugerencia",
                "text": q.get("text") or "",
                "severity": sev
            })
        for f in (fb.get("findings") or [])[: (3 - len(qw))]:
            qw.append({
                "id": f.get("id") or f"F{len(qw)+1}",
                "title": f.get("title") or "Hallazgo",
                "text": f.get("recommendation") or f.get("why_it_matters") or "",
                "severity": (f.get("severity") or "med").lower()
            })

        return {
            "matchId": match_id,
            "date": fb.get("date") or "",
            "map": (fb.get("map") or "mirage").lower(),
            "score10": score10,
            "score100": score100,
            "kpis": kpis,
            "postplant": {"A":{"attempts":0,"winrate":0.0,"tsAliveEndAvg":0},
                          "B":{"attempts":0,"winrate":0.0,"tsAliveEndAvg":0}},
            "topDeathZones": [],
            "quickWins": qw
        }

    # -------- Caso 3: fallback vacío (fixtures se encargan de la UI)
    return {
        "matchId": match_id,
        "date": fb.get("date") or "",
        "map": (fb.get("map") or "mirage").lower(),
        "score10": 0.0,
        "score100": 0,
        "kpis": [],
        "postplant": {"A":{"attempts":0,"winrate":0.0,"tsAliveEndAvg":0},
                      "B":{"attempts":0,"winrate":0.0,"tsAliveEndAvg":0}},
        "topDeathZones": [],
        "quickWins": []
    }

# ------------------------- Pipeline real -------------------------
def run_pipeline(events_path: str, rounds_path: str, match_id: str):
    outp = out_dir(match_id)
    os.makedirs(outp, exist_ok=True)

    # build_kpis
    subprocess.run([
        sys.executable, os.path.join(ROOT_DIR, "processor-py", "build_kpis.py"),
        "--events", events_path,
        "--rounds", rounds_path,
        "--map_config", MAP_CFG,
        "--out_dir", outp
    ], check=True)

    # feedback_engine
    subprocess.run([
        sys.executable, os.path.join(ROOT_DIR, "processor-py", "feedback_engine.py"),
        "--kpis", os.path.join(outp, "kpis_team.json"),
        "--per_round", os.path.join(outp, "per_round.csv"),
        "--thresholds", os.path.join(ROOT_DIR, "processor-py", "thresholds.yml"),
        "--out", os.path.join(outp, "feedback.json")
    ], check=True)

# --------------------------- Rutas públicas ---------------------------
@app.route("/")
def home():
    from flask_login import current_user
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
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

# --------------------------- Auth ---------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not username or not email or not password:
            flash("Completá usuario, email y contraseña.")
            return redirect(request.url)

        if User.query.filter((User.username==username) | (User.email==email)).first():
            flash("Usuario o email ya existen.")
            return redirect(request.url)

        u = User(
            username=username,
            email=email,
            pw_hash=generate_password_hash(password, method="pbkdf2:sha256", salt_length=16),
        )
        db.session.add(u)
        db.session.commit()
        login_user(u)
        flash("Cuenta creada. ¡Bienvenido!")
        return redirect(url_for("home"))
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
            flash("Credenciales inválidas.")
            return redirect(request.url)
        login_user(u, remember=("remember" in request.form))
        flash("Sesión iniciada.")
        return redirect(request.args.get("next") or url_for("home"))
    return render_template("auth_login.html", mode="login")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Sesión cerrada.")
    return redirect(url_for("home"))

# --------------------------- Rutas protegidas ---------------------------
@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        print("DEBUG files:", list(request.files.keys()))
        demo = request.files.get("demo_file")

        if not demo or not demo.filename.lower().endswith(".dem"):
            flash("Subí un archivo .dem válido.")
            return redirect(request.url)

        match_id = "m_" + uuid.uuid4().hex[:8]
        upload_dir = upload_dir_for(match_id)
        os.makedirs(upload_dir, exist_ok=True)

        dem_path = os.path.join(upload_dir, demo.filename)
        demo.save(dem_path)

        events_path = os.path.join(upload_dir, "events.csv")
        rounds_path = os.path.join(upload_dir, "rounds.csv")

        # 1) Adapter: .dem -> CSVs
        try:
            completed = subprocess.run([
                sys.executable, os.path.join(ROOT_DIR, "processor-py", "adapter_dem.py"),
                "--dem", dem_path, "--events", events_path, "--rounds", rounds_path
            ], check=True, capture_output=True, text=True)
            print("ADAPTER STDOUT:\n", completed.stdout)
            print("ADAPTER STDERR:\n", completed.stderr)
        except subprocess.CalledProcessError as e:
            import traceback; traceback.print_exc()
            flash(f"Adapter DEM error (exit {e.returncode}).<br>STDOUT:<pre>{e.stdout}</pre><br>STDERR:<pre>{e.stderr}</pre>")
            return redirect(request.url)
        except Exception as e:
            import traceback; traceback.print_exc()
            flash(f"Adapter DEM error: {e}")
            return redirect(request.url)

        # 2) Pipeline
        try:
            run_pipeline(events_path, rounds_path, match_id)
        except Exception as e:
            import traceback; traceback.print_exc()
            flash(f"Pipeline error: {e}")
            return redirect(request.url)

        # 3) Persistir match -> user
        score100 = 0
        try:
            with open(os.path.join(out_dir(match_id), "feedback.json"), "r", encoding="utf-8") as f:
                fb_raw = json.load(f)
            s = fb_raw.get("summary") or {}
            score100 = int(s.get("score100") or (s.get("scores", {}).get("postplant") or 0))
        except Exception:
            pass

        db.session.add(Match(match_id=match_id, user_id=current_user.id, map="mirage", score100=score100))
        db.session.commit()

        return redirect(url_for("summary", match_id=match_id))

    return render_template("upload.html")

@app.route("/summary/<match_id>")
@login_required
def summary(match_id):
    # Ownership
    m = Match.query.filter_by(match_id=match_id, user_id=current_user.id).first()
    if not m:
        flash("No tenés acceso a este match.")
        return redirect(url_for("home"))

    fpath = os.path.join(out_dir(match_id), "feedback.json")
    with open(fpath, "r", encoding="utf-8") as f:
        fb_raw = json.load(f)

    fb_summary = map_fb_to_summary(fb_raw, match_id)

    fixtures = load_fixtures()
    fb_payload = {"summary": fb_summary}

    print("SUMMARY DEBUG:", {
        "match_id": match_id,
        "score100": (fb_summary or {}).get("score100"),
        "kpi_vals": [k.get("value100") for k in (fb_summary or {}).get("kpis", [])][:5],
        "qw":   len((fb_summary or {}).get("quickWins", [])),
    })

    return render_template("summary.html", fb=fb_payload, fixtures=fixtures, match_id=match_id)

@app.route("/rounds/<match_id>")
@login_required
def rounds(match_id):
    m = Match.query.filter_by(match_id=match_id, user_id=current_user.id).first()
    if not m:
        flash("No tenés acceso a este match.")
        return redirect(url_for("home"))

    fpath = os.path.join(out_dir(match_id), "feedback.json")
    with open(fpath, "r", encoding="utf-8") as f:
        fb = json.load(f)
    fixtures = load_fixtures()
    return render_template("rounds.html", fixtures=fixtures, notes=fb.get("per_round_notes", []), match_id=match_id)

# --------- ALIAS DEMO (sin match_id, usan fixtures.json) ---------
@app.route("/summary", methods=["GET"])
def summary_root():
    fixtures = load_fixtures()
    return render_template("summary.html", fixtures=fixtures, match_id=None)

@app.route("/rounds", methods=["GET"])
def rounds_root():
    fixtures = load_fixtures()
    return render_template("rounds.html", fixtures=fixtures, match_id=None)

# --- Rutas informativas / navegación básica ---
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

# --------------------------- Main ---------------------------
if __name__ == "__main__":
    app.run(debug=True)
