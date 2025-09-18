from flask import Flask, render_template, request, redirect, url_for, flash
import json, os, uuid, subprocess, sys

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev"  # solo local

from flask import current_app

@app.context_processor
def inject_nav_flags():
    # Expone una variable 'has_demos' a todos los templates
    return {"has_demos": "demos" in current_app.view_functions}


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))  # carpeta del proyecto
MAP_CFG = os.path.join(ROOT_DIR, "configs", "maps", "mirage.yml")

def out_dir(match_id: str) -> str:
    return os.path.join(ROOT_DIR, "out", match_id)

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

@app.route("/")
def home():
    # Antes redirigía al summary. Ahora mostramos el menú principal.
    return render_template("menu.html")

@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        demo = request.files.get("demo_file")
        if demo and demo.filename.lower().endswith(".dem"):
            import uuid, traceback
            match_id = "m_" + uuid.uuid4().hex[:8]
            upload_dir = os.path.join(ROOT_DIR, "uploads", match_id)
            os.makedirs(upload_dir, exist_ok=True)

            dem_path = os.path.join(upload_dir, demo.filename)
            demo.save(dem_path)

            events_path = os.path.join(upload_dir, "events.csv")
            rounds_path = os.path.join(upload_dir, "rounds.csv")

            try:
                completed = subprocess.run([
                    sys.executable, os.path.join(ROOT_DIR, "processor-py", "adapter_dem.py"),
                    "--dem", dem_path, "--events", events_path, "--rounds", rounds_path
                ], check=True, capture_output=True, text=True)
                # (opcional) loggear salida del adapter en la consola de Flask:
                print(completed.stdout)
                print(completed.stderr)
            except subprocess.CalledProcessError as e:
                # Incluimos stdout/stderr del adapter en el flash
                flash(f"Adapter DEM error (exit {e.returncode}). STDOUT:\n{e.stdout}\nSTDERR:\n{e.stderr}")
                return redirect(request.url)
            except Exception as e:
                flash(str(e))
                return redirect(request.url)

            try:
                run_pipeline(events_path, rounds_path, match_id)
            except Exception as e:
                flash(f"Pipeline error: {e}")
                return redirect(request.url)

            return redirect(url_for("summary", match_id=match_id))

        # CSV fallback
        events = request.files.get("events_csv")
        rounds = request.files.get("rounds_csv")
        if not events or not rounds:
            flash("Subí un .dem o los dos CSV: events.csv y rounds.csv")
            return redirect(request.url)

        import uuid
        match_id = "m_" + uuid.uuid4().hex[:8]
        upload_dir = os.path.join(ROOT_DIR, "uploads", match_id)
        os.makedirs(upload_dir, exist_ok=True)

        events_path = os.path.join(upload_dir, "events.csv")
        rounds_path = os.path.join(upload_dir, "rounds.csv")
        events.save(events_path); rounds.save(rounds_path)

        run_pipeline(events_path, rounds_path, match_id)
        flash(f"✅ Demo procesada: {demo.filename}")
        return redirect(url_for("summary", match_id=match_id))

    return render_template("upload.html")


@app.route("/summary/<match_id>")
def summary(match_id):
    fpath = os.path.join(out_dir(match_id), "feedback.json")
    with open(fpath, "r", encoding="utf-8") as f:
        fb = json.load(f)
    return render_template("summary.html", fb=fb, match_id=match_id)

@app.route("/rounds/<match_id>")
def rounds(match_id):
    fpath = os.path.join(out_dir(match_id), "feedback.json")
    with open(fpath, "r", encoding="utf-8") as f:
        fb = json.load(f)
    return render_template("rounds.html", notes=fb.get("per_round_notes", []), match_id=match_id)

# --- AGREGAR ESTAS RUTAS STUB (al final del archivo, antes del if __name__...) ---
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
# --- FIN AGREGADOS ---

if __name__ == "__main__":
    app.run(debug=True)
