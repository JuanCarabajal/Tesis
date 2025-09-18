import sys, shutil, os
dem, out_events, out_rounds = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(os.path.dirname(out_events), exist_ok=True)
shutil.copy(os.path.join("samples","events_sample.csv"), out_events)
shutil.copy(os.path.join("samples","rounds_sample.csv"), out_rounds)
print("FAKE parsed:", dem, "->", out_events, out_rounds)
