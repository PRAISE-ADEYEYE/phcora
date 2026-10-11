#!/usr/bin/env python3
"""PHCORA data validator. Run after build_data.py. Standard library only.
Usage: python3 pipeline/validate_data.py [data_folder]
Prints PASS, WARN or FAIL for each check. Fix every FAIL before you commit the data."""
import json, os, sys
from collections import Counter

D = sys.argv[1] if len(sys.argv) > 1 else "data"
res = []
def chk(level, msg): res.append((level, msg)); print(f"{level:4}  {msg}")

need = ["facilities.geojson", "lga.geojson", "calibration.json", "assumptions.json", "cleaning_log.json", "evaluation.json"]
for f in need:
    p = os.path.join(D, f)
    if not os.path.exists(p): chk("FAIL", f"missing {p}"); continue
    mb = os.path.getsize(p) / 1e6
    chk("WARN" if mb > 20 else "PASS", f"{f} found, {mb:.2f} MB" + (" (GitHub browser upload limit is 25 MB, use git push instead)" if mb > 20 else ""))
if any(l == "FAIL" for l, _ in res): sys.exit("Stop: required files are missing.")

fac = json.load(open(os.path.join(D, "facilities.geojson"), encoding="utf-8"))["features"]
lga = json.load(open(os.path.join(D, "lga.geojson"), encoding="utf-8"))["features"]
log = json.load(open(os.path.join(D, "cleaning_log.json"), encoding="utf-8"))
asm = json.load(open(os.path.join(D, "assumptions.json"), encoding="utf-8"))

n = len(fac)
chk("PASS" if n >= 500 else "WARN" if n >= 100 else "FAIL", f"{n} clinics after cleaning (a real four state GRID3 run should be in the thousands)")
chk("PASS" if all(f["properties"].get("simulated") is True for f in fac) else "FAIL", "every clinic carries simulated=true")
allowed = {"id", "name", "lga", "state", "type", "risk", "trust", "action", "reasons", "cover_days", "months_since_report", "missed_of_last_6", "simulated"}
extra = set().union(*[set(f["properties"]) for f in fac]) - allowed
chk("PASS" if not extra else "FAIL", "no hidden outcome fields leaked" if not extra else f"unexpected fields in facilities: {sorted(extra)}")

bad = [f for f in fac if not (2.6 <= f["geometry"]["coordinates"][0] <= 14.8 and 4.1 <= f["geometry"]["coordinates"][1] <= 13.95)]
chk("PASS" if not bad else "FAIL", "all coordinates inside Nigeria" if not bad else f"{len(bad)} clinics outside Nigeria bounds")

seen = Counter((f["properties"]["name"].lower(), round(f["geometry"]["coordinates"][0], 3), round(f["geometry"]["coordinates"][1], 3)) for f in fac)
dup = sum(v - 1 for v in seen.values() if v > 1)
chk("PASS" if dup == 0 else "WARN", f"{dup} duplicate name and location pairs")

noname = sum(1 for f in fac if not f["properties"]["name"] or f["properties"]["name"].strip().lower() in ("none", "nan"))
chk("PASS" if noname == 0 else "WARN", f"{noname} clinics without a usable name")

r = [f["properties"]["risk"] for f in fac]; t = [f["properties"]["trust"] for f in fac]
chk("PASS" if all(0 <= x <= 1 for x in r + t) else "FAIL", "risk and trust all between 0 and 1")

lga_names = {(x["properties"]["state"].lower(), x["properties"]["name"].lower()) for x in lga}
used = {(f["properties"]["state"].lower(), f["properties"]["lga"].lower()) for f in fac}
chk("PASS" if used <= lga_names else "FAIL", "every clinic LGA exists in lga.geojson" if used <= lga_names else f"{len(used - lga_names)} clinic LGAs not in lga.geojson")
empty = len(lga_names - used)
chk("PASS" if empty <= .15 * len(lga_names) else "WARN", f"{empty} of {len(lga_names)} LGAs have no clinic (a few is normal, many means a matching problem)")

print("\nClinics by state:")
by = Counter(f["properties"]["state"] for f in fac)
for s, c in by.most_common(): print(f"  {s}: {c}")
print("\nActions:")
for a, c in Counter(f["properties"]["action"] for f in fac).most_common(): print(f"  {a}: {c} ({100*c/n:.0f}%)")
print("\nTop clinic types found before filtering:")
for k, v in list(log.get("facility_types_found", {}).items())[:10]: print(f"  {k}: {v}")
print("\nDropped during cleaning:", json.dumps(log.get("dropped", {})))
print("Survey calibration (target vs simulated):")
print(json.dumps(asm.get("calibration_by_state"), indent=1)[:1200])

fails = [m for l, m in res if l == "FAIL"]
print("\nRESULT:", "FAIL, fix the items above" if fails else "OK to commit")
sys.exit(1 if fails else 0)
