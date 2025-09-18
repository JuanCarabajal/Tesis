import argparse, os, subprocess, sys

def run_dem_parser(dem_path: str, events_out: str, rounds_out: str):
    cmd_tpl = os.environ.get("DEM_PARSER_CMD")
    if not cmd_tpl:
        raise RuntimeError("Seteá DEM_PARSER_CMD (comando del parser de .dem)")

    params = {"in": dem_path, "events": events_out, "rounds": rounds_out}
    cmd = cmd_tpl.format_map(params)

    print(">> CMD:", cmd)
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(">> STDOUT:\n", res.stdout)
    print(">> STDERR:\n", res.stderr)

    if res.returncode != 0:
        raise RuntimeError(
            f"Parser falló (exit {res.returncode}).\nCMD: {cmd}\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
        )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dem", required=True)
    ap.add_argument("--events", required=True)
    ap.add_argument("--rounds", required=True)
    args = ap.parse_args()
    run_dem_parser(args.dem, args.events, args.rounds)

if __name__ == "__main__":
    main()
