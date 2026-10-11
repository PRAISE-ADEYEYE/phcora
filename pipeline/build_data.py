#!/usr/bin/env python3
"""PHCORA data pipeline. Standard library only (Python 3.9+).

Inputs   GRID3 health facilities (CSV or GeoJSON), LGA boundaries (GeoJSON), calibration.json
Outputs  data/facilities.geojson, lga.geojson, assumptions.json, cleaning_log.json, evaluation.json

Real:       facility names and locations, LGA boundaries, state survey shares (CheckMyPHC)
Simulated:  monthly stock, use, receipts and report arrival for every clinic
Assumed:    every parameter listed in assumptions.json
Nothing simulated is ever written without simulated=true.
"""
import argparse, csv, json, math, os, random, re, statistics, urllib.request, zipfile, zlib
from collections import Counter

RT, TT, LEAD = 0.40, 0.60, 1.5  # risk threshold, trust threshold, resupply lead time in months
HAZARD = 0.08                   # assumed extra risk per missed report (ASSUMED)
P_STRESS, P_WEAK = 0.22, 0.30   # share of clinics under demand surge, share of weak reporters (ASSUMED)
TYPE_MULT = [("health post", .6), ("dispensary", .7), ("comprehensive", 1.6), ("primary", 1.0), ("health cent", 1.0)]

def norm(s):
    s = re.sub(r"\b(state|lga|local government area)\b", "", str(s or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()

def pick(keys, cands):
    low = {k.lower(): k for k in keys}
    for c in cands:
        if c in low: return low[c]
    for c in cands:
        for k in low:
            if c in k: return low[k]
    return None

def unzip_pick(zpath, prefer, folder="raw"):
    """Pull one CSV or GeoJSON out of a zip file. prefer is a list of words to look for in the file names."""
    z = zipfile.ZipFile(zpath)
    names = [n for n in z.namelist() if n.lower().endswith((".geojson", ".json", ".csv")) and "__MACOSX" not in n]
    if not names: raise SystemExit(f"{zpath} has no CSV or GeoJSON inside (shapefile zips are not supported). Files inside: {z.namelist()[:15]}")
    pick = None
    for key in prefer:
        m = [n for n in names if key in n.lower()]
        m = [n for n in m if "_em" not in n.lower()] or m
        if m: pick = max(m, key=lambda n: z.getinfo(n).file_size); break
    if pick is None:
        if len(names) == 1: pick = names[0]
        else: raise SystemExit(f"{zpath} holds several files and I cannot tell which one to use: {names}. Upload just the right file, or tell me the names.")
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, os.path.basename(pick))
    with open(out, "wb") as f: f.write(z.read(pick))
    print("extracted", pick, "from", os.path.basename(zpath), "->", out); return out

def fetch(src, folder="raw", prefer=()):
    """If src is a web link, download it into the raw folder once. If it is a zip, pull the right file out of it. Returns a local path."""
    src = str(src)
    if not src.lower().startswith(("http://", "https://")):
        return unzip_pick(src, prefer, folder) if src.lower().endswith(".zip") else src
    os.makedirs(folder, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", src.split("?")[0].rstrip("/").split("/")[-1]) or "download"
    dest = os.path.join(folder, name)
    if os.path.exists(dest) and os.path.getsize(dest) > 0: print("using cached", dest)
    else:
        print("downloading", src)
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0 PHCORA-pipeline"})
        with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f: f.write(r.read())
        print("saved", dest, os.path.getsize(dest), "bytes")
    head = open(dest, "rb").read(512).lstrip()
    if head[:2] == b"PK": return unzip_pick(dest, prefer, folder)
    if head[:1] == b"<": raise SystemExit(f"{src} returned a web page, not a data file. Use the direct link of the file itself, for example a GitHub release link.")
    if not dest.lower().endswith((".csv", ".geojson", ".json")):
        new_dest = dest + (".geojson" if head[:1] == b"{" else ".csv"); os.replace(dest, new_dest); dest = new_dest
    return dest

def read_facilities(path):
    if path.lower().endswith((".geojson", ".json")):
        g = json.load(open(path, encoding="utf-8"))
        rows = []
        for f in g["features"]:
            p = dict(f.get("properties") or {})
            if f.get("geometry") and f["geometry"]["type"] == "Point":
                p["__lon"], p["__lat"] = f["geometry"]["coordinates"][:2]
            rows.append(p)
        return rows
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))

def rings(geom):
    if geom["type"] == "Polygon": return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon": return geom["coordinates"]
    return []

def in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi, xj, yj = ring[i][0], ring[i][1], ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-18) + xi: inside = not inside
        j = i
    return inside

def in_poly(x, y, poly):
    return in_ring(x, y, poly[0]) and not any(in_ring(x, y, h) for h in poly[1:])

def round_geom(geom, n=4):
    f = lambda c: [round(c[0], n), round(c[1], n)]
    if geom["type"] == "Polygon": return {"type": "Polygon", "coordinates": [[f(c) for c in r] for r in geom["coordinates"]]}
    return {"type": "MultiPolygon", "coordinates": [[[f(c) for c in r] for r in p] for p in geom["coordinates"]]}

def phi(x): return 0.5 * math.erfc(-x / math.sqrt(2))

# ---------- simulator (SIMULATED) ----------
def sim_fac(rng, base, ld, interval, stress, rel):
    st, h = base * 2.4, []
    for m in range(1, 14):
        season = 1 + .25 * math.sin(m / 12 * 2 * math.pi)
        dem = max(5, base * season * (1.45 if stress and m >= 10 else 1) * (1 + .18 * rng.gauss(0, 1)))
        rc = base * interval * 1.05 if (m % interval == 0 and rng.random() > ld) else 0
        avail = st + rc; sold = min(dem, avail); short = dem - sold > .5; st = avail - sold
        pm = (1 - rel) + (.3 if stress and m >= 10 else 0)
        miss = rng.random() < pm
        h.append(dict(m=m, st=st, dem=dem, miss=miss, late=(not miss) and rng.random() < .2, short=short))
    return h

def draw_fac(rng, mult):
    base = max(15, min(400, 60 * mult * math.exp(rng.gauss(0, .5))))
    rel = rng.uniform(.35, .6) if rng.random() < P_WEAK else rng.uniform(.85, .97)
    return base, rng.random() < P_STRESS, rel

def calibrate(target, interval, seed):
    """Find the receipt failure rate so the simulated share of clinics with stock at month 12 matches the real survey share."""
    def share(ld):
        r = random.Random(seed); n = 0
        for _ in range(500):
            base, stress, rel = draw_fac(r, 1.0)
            n += sim_fac(r, base, ld, interval, stress, rel)[11]["st"] > 0
        return n / 500
    lo, hi = 0.0, 0.95
    for _ in range(14):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if share(mid) > target else (lo, mid)
    return round((lo + hi) / 2, 3), round(share((lo + hi) / 2), 3)

# ---------- engine (DERIVED and PREDICTED) ----------
def score(h, hz=HAZARD):
    """Stock balance with drift: infer monthly net depletion from consecutive reported months, then project to the end of the resupply lead time."""
    rep = [x for x in h[:12] if not x["miss"]]
    if not rep: return dict(risk=.9, trust=0, cover=0, gap=12, missed=6, late=0, c=0, S=0, trend=0)
    last, gap = rep[-1], 12 - rep[-1]["m"]
    rc = rep[-6:]; c = sum(x["dem"] for x in rc) / len(rc)
    by = {x["m"]: x for x in rep}; nets = []
    for m in range(2, 13):
        if m in by and m - 1 in by:
            inflow = by[m]["st"] - by[m - 1]["st"] + by[m]["dem"]
            nets.append(by[m]["dem"] - max(0, inflow))
    nets = nets[-8:]
    if len(nets) >= 3: mu, sd1 = statistics.mean(nets), max(.15 * c, statistics.pstdev(nets))
    else: mu, sd1 = c, .35 * c
    S = last["st"]; mean = S - mu * (gap + LEAD); sd = sd1 * math.sqrt(gap + LEAD) + 1
    p = phi(-mean / sd); w = h[6:12]; missed = sum(x["miss"] for x in w)
    risk = min(.99, p + (1 - p) * min(.6, hz * missed))
    trust = sum(0 if x["miss"] else .6 if x["late"] else 1 for x in w) / 6
    r3, r2 = rep[-3:], rep[-6:-3]
    trend = (sum(x["dem"] for x in r3) / len(r3)) / (sum(x["dem"] for x in r2) / len(r2)) - 1 if r2 else 0
    cover = max(0, (S / (mu if mu > .2 * c else c) - gap) * 30)
    return dict(risk=risk, trust=trust, cover=cover, gap=gap, missed=missed, late=sum(x["late"] for x in w), c=c, S=S, trend=trend)

def action(s):
    if s["risk"] >= RT: return "Resupply" if s["trust"] >= TT else "Verify then resupply"
    return "Verify" if s["trust"] < TT else "Watch"

def reasons(s):
    r = []
    if s["cover"] < LEAD * 30: r.append(f"Estimated cover is {s['cover']:.0f} days, shorter than the {LEAD * 30:.0f} day resupply time")
    if s["missed"] >= 2: r.append(f"{s['missed']} of the last 6 reports are missing")
    if s["late"] >= 2: r.append(f"{s['late']} of the last 6 reports arrived late")
    if s["gap"] >= 1: r.append(f"The last report was {s['gap']} month{'s' if s['gap'] > 1 else ''} ago")
    if s["trend"] > .25: r.append(f"Reported use rose {s['trend'] * 100:.0f}% over the last 3 reports")
    if s["risk"] < RT and s["trust"] < TT: r.append("Stock looks adequate only because reports are weak, so it needs checking")
    return r

def world(facs, params, seed):
    out = []
    for f in facs:
        rng = random.Random(zlib.crc32(f"{f['id']}|{seed}".encode()))
        base, stress, rel = draw_fac(rng, f["mult"])
        p = params[f["state"]]
        out.append((f, sim_fac(rng, base, p["receipt_failure_rate"], p["receipt_interval_months"], stress, rel)))
    return out

def evaluate(facs, params, n_seeds=60):
    N = max(1, round(.1 * len(facs))); names = ["triage", "stale_stock", "least_reliable", "random"]
    prec = {k: [] for k in names}; rec = {k: [] for k in names}; hid = {"triage": [], "stale_stock": []}; sens = {hz: [] for hz in (0, HAZARD, .2)}; prev = []
    bins = [[0, 0, 0] for _ in range(5)]; edges = [0, .1, .2, .4, .6, 1.01]
    for k in range(n_seeds):
        w = world(facs, params, 1000 + k); sc = [score(h) for _, h in w]; y = [h[12]["short"] for _, h in w]; tot = sum(y) or 1; prev.append(sum(y) / len(y))
        def stale(h):
            rep = [x for x in h[:12] if not x["miss"]]
            return rep[-1]["st"] / max(1, sum(x["dem"] for x in rep[-6:]) / len(rep[-6:])) if rep else 0
        orders = {"triage": sorted(range(len(w)), key=lambda i: (-sc[i]["risk"], sc[i]["cover"])), "stale_stock": sorted(range(len(w)), key=lambda i: stale(w[i][1])),
                  "least_reliable": sorted(range(len(w)), key=lambda i: sc[i]["trust"]), "random": random.Random(k).sample(range(len(w)), len(w))}
        for n, o in orders.items():
            hits = sum(y[i] for i in o[:N]); prec[n].append(hits / N); rec[n].append(hits / tot)
        weak = [i for i in range(len(w)) if sc[i]["trust"] < TT and y[i]]
        for n in hid: hid[n].append(len(set(orders[n][:N]) & set(weak)) / len(weak) if weak else 0)
        for hz in sens:
            z = [score(h, hz) for _, h in w]; o = sorted(range(len(w)), key=lambda i: (-z[i]["risk"], z[i]["cover"])); sens[hz].append(sum(y[i] for i in o[:N]) / N)
        for i, s in enumerate(sc):
            b = next(j for j in range(5) if s["risk"] < edges[j + 1]); bins[b][0] += s["risk"]; bins[b][1] += y[i]; bins[b][2] += 1
    ms = lambda v: {"mean": round(statistics.mean(v), 3), "sd": round(statistics.pstdev(v), 3)}
    return {"note": "Computed entirely inside the simulator. It shows how the method behaves under stated assumptions, not accuracy on real clinics.",
            "clinics": len(facs), "visits_per_cycle": N, "simulated_worlds": n_seeds, "share_of_clinics_with_shortfall_next_month": ms(prev),
            "precision_of_visits": {k: ms(v) for k, v in prec.items()}, "share_of_true_shortfalls_reached": {k: ms(v) for k, v in rec.items()},
            "hidden_risk_capture_among_weak_reporters": {k: ms(v) for k, v in hid.items()},
            "sensitivity_precision_by_extra_risk_per_missed_report": {str(k): ms(v) for k, v in sens.items()},
            "calibration": [{"predicted": round(b[0] / b[2], 3), "observed": round(b[1] / b[2], 3), "clinics": b[2]} for b in bins if b[2]]}

def main():
    a = argparse.ArgumentParser()
    a.add_argument("--facilities", required=True); a.add_argument("--lga", required=True)
    a.add_argument("--calibration", default="data/calibration.json"); a.add_argument("--out", default="data")
    a.add_argument("--states", nargs="+", default=["osun", "anambra", "sokoto", "taraba"])
    a.add_argument("--seed", type=int, default=7); a.add_argument("--inspect", action="store_true", help="print column names and sample values, then stop"); a.add_argument("--keep-all-levels", action="store_true")
    a = a.parse_args(); os.makedirs(a.out, exist_ok=True)
    a.facilities, a.lga = fetch(a.facilities), fetch(a.lga, prefer=("admin2", "adm2", "lga"))
    if a.inspect:
        rr = read_facilities(a.facilities); print("FACILITY ROWS", len(rr)); print("COLUMNS", list(rr[0].keys()))
        for key in rr[0]:
            if key.startswith("__"): continue
            vals = Counter(str(r.get(key)) for r in rr[:5000]).most_common(6); print(f"  {key}: {vals}")
        gg = json.load(open(a.lga, encoding="utf-8")); print("LGA FEATURES", len(gg["features"]), "COLUMNS", list(gg["features"][0]["properties"].keys())); print("SAMPLE", gg["features"][0]["properties"]); return
    cal = json.load(open(a.calibration)); want = {norm(s) for s in a.states}; log = {}
    # LGAs
    g = json.load(open(a.lga, encoding="utf-8")); k0 = g["features"][0]["properties"].keys()
    ln, sn = pick(k0, ["lganame", "lga_name", "admin2name_en", "admin2name", "admin2_name", "adm2_name", "adm2_en", "lga", "name"]), pick(k0, ["statename", "state_name", "admin1name_en", "admin1name", "admin1_name", "adm1_name", "adm1_en", "state"])
    if not ln or not sn: raise SystemExit(f"Could not find LGA or state name columns in the LGA file. Columns are: {list(k0)}")
    L = []
    for f in g["features"]:
        s = norm(f["properties"].get(sn))
        if s in want and f.get("geometry"):
            L.append(dict(name=str(f["properties"][ln]).strip(), state=s, polys=rings(f["geometry"]), geom=f["geometry"]))
    for l in L:
        xs = [c[0] for p in l["polys"] for c in p[0]]; ys = [c[1] for p in l["polys"] for c in p[0]]; l["bb"] = (min(xs), min(ys), max(xs), max(ys))
    log["lgas_in_selected_states"] = len(L); log["lgas_by_state"] = dict(Counter(l["state"] for l in L))
    # facilities
    rows = read_facilities(a.facilities); log["facility_rows_read"] = len(rows); k = rows[0].keys()
    cn, cs, ct = pick(k, ["primary_name", "facility_name", "name", "facility"]), pick(k, ["state_name", "statename", "state"]), pick(k, ["category", "functional_level", "facility_level", "type", "level"])
    tcols = [x for x in k if re.search(r"categor|functional_level|facility_level|^type$|^level$|facility_type", x.lower())] or ([ct] if ct else [])
    for r in rows: r["__type"] = " | ".join(str(r.get(x) or "") for x in tcols)
    cla, clo = pick(k, ["latitude", "lat", "y"]), pick(k, ["longitude", "lon", "long", "x"])
    log["columns_used"] = dict(name=cn, state=cs, type=ct, lat=cla, lon=clo)
    if cs: rows = [r for r in rows if norm(r.get(cs)) in want]
    else: log["warning"] = "no state column in facility file, relying on LGA polygons only"
    log["rows_in_selected_states"] = len(rows); log["type_columns_used"] = tcols
    log["facility_types_found"] = dict(Counter(r["__type"] for r in rows).most_common(15))
    if not a.keep_all_levels:
        rows = [r for r in rows if re.search(r"primary|phc|health cent|health post|dispensar", r["__type"].lower()) and not re.search(r"secondary|tertiary|teaching|general hospital|specialist", r["__type"].lower())]
    log["rows_after_primary_level_filter"] = len(rows)
    clean, seen, drop = [], set(), Counter()
    for r in rows:
        try: lat, lon = float(r.get("__lat", r.get(cla))), float(r.get("__lon", r.get(clo)))
        except (TypeError, ValueError): drop["bad_or_missing_coordinates"] += 1; continue
        if not (4 <= lat <= 14 and 2.5 <= lon <= 15): drop["outside_nigeria"] += 1; continue
        nm = re.sub(r"\s+", " ", str(r.get(cn) or "").strip())
        if len(nm) < 3: drop["missing_name"] += 1; continue
        key = (norm(nm), round(lat, 3), round(lon, 3))
        if key in seen: drop["duplicate"] += 1; continue
        seen.add(key)
        lga = next((l for l in L if l["bb"][0] <= lon <= l["bb"][2] and l["bb"][1] <= lat <= l["bb"][3] and any(in_poly(lon, lat, p) for p in l["polys"])), None)
        if not lga: drop["not_inside_any_selected_lga"] += 1; continue
        segs = [x.strip() for x in r["__type"].split("|") if x.strip()]; ty = max((x for x in segs if re.search(r"primary|phc|health cent|health post|dispensar", x.lower())), key=len, default=(segs[0] if segs else "Primary health facility")); mult = next((m for kw, m in TYPE_MULT if kw in ty.lower()), 1.0)
        clean.append(dict(id=f"F{len(clean) + 1:05d}", name=nm, lga=lga["name"], state=lga["state"], type=ty, c=[round(lon, 5), round(lat, 5)], mult=mult))
    log["dropped"] = dict(drop); log["clinics_after_cleaning"] = len(clean); log["clinics_by_state"] = dict(Counter(c["state"] for c in clean))
    # calibrate per state to real survey shares
    params = {}
    for s in {c["state"] for c in clean}:
        key = next((x for x in cal["supplies"] if norm(x) == s), None)
        if not key: raise SystemExit(f"No real survey calibration for state {s}")
        t = cal["supplies"][key]["Medications"]["share_available"]; interval = 1 if s == "anambra" else 2
        ld, got = calibrate(t, interval, a.seed)
        params[s] = dict(target_share_with_medications=t, achieved_share_in_simulator=got, receipt_failure_rate=ld, receipt_interval_months=interval)
    # final world, scoring, outputs (the hidden next month outcome is never written)
    feats = []
    for f, h in world(clean, params, a.seed):
        s = score(h)
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": f["c"]}, "properties": dict(id=f["id"], name=f["name"], lga=f["lga"], state=f["state"].title(), type=f["type"],
            risk=round(s["risk"], 3), trust=round(s["trust"], 3), action=action(s), reasons=reasons(s), cover_days=round(s["cover"]), months_since_report=s["gap"], missed_of_last_6=s["missed"], simulated=True)})
    json.dump({"type": "FeatureCollection", "features": feats}, open(f"{a.out}/facilities.geojson", "w"), separators=(",", ":"))
    json.dump({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": l["name"], "state": l["state"].title()}, "geometry": round_geom(l["geom"])} for l in L]}, open(f"{a.out}/lga.geojson", "w"), separators=(",", ":"))
    json.dump({"real": ["Facility names and locations (GRID3)", "LGA boundaries", "State survey shares of PHCs with medications (CheckMyPHC)"],
               "simulated": ["Monthly stock, use, receipts and report arrival for every clinic"],
               "assumed": {"resupply_lead_time_months": LEAD, "extra_risk_per_missed_report": HAZARD, "share_of_clinics_under_demand_surge": P_STRESS, "share_of_weak_reporters": P_WEAK,
                           "clinics_under_stress_also_report_less": "yes, missed report chance rises by 0.3 from month 10", "base_monthly_use_packs": "about 60, scaled by facility type and lognormal spread",
                           "receipt_interval_months": "1 for Anambra (real restock answers cluster at 4 weeks), 2 elsewhere", "risk_threshold": RT, "trust_threshold": TT},
               "calibration_by_state": params, "seed": a.seed}, open(f"{a.out}/assumptions.json", "w"), indent=1)
    json.dump(log, open(f"{a.out}/cleaning_log.json", "w"), indent=1)
    ev = evaluate(clean, params); json.dump(ev, open(f"{a.out}/evaluation.json", "w"), indent=1)
    c = Counter(f["properties"]["action"] for f in feats)
    print("clinics", len(feats), dict(c)); print(json.dumps(log, indent=1)); print(json.dumps({k:ev[k] for k in ["share_of_clinics_with_shortfall_next_month","precision_of_visits","hidden_risk_capture_among_weak_reporters","sensitivity_precision_by_extra_risk_per_missed_report","calibration"]}, indent=1)); print(json.dumps(params, indent=1))

if __name__ == "__main__": main()
