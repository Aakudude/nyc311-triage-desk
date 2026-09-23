"""Phase 1 helper: NYC 311 (erm2-nwe9) schema check -> count -> raw download -> profile.

Stdlib only, except `profile` which uses pandas. No cleaning is performed:
values are written exactly as the Socrata API returns them.

Usage (run from repo root):
  python scripts/fetch_extract.py schema
  python scripts/fetch_extract.py count    --start 2025-03-01 --end 2025-11-01
  python scripts/fetch_extract.py download --start 2025-03-01 --end 2025-11-01
  python scripts/fetch_extract.py profile  | tee data/profile_report.txt

--end is EXCLUSIVE (2025-11-01 == "through 2025-10-31").
Optional: set SODA_APP_TOKEN to avoid throttling.
"""
import argparse, csv, io, json, os, urllib.parse, urllib.request
from collections import defaultdict

DATASET = "erm2-nwe9"
BASE = "https://data.cityofnewyork.us"
COLS = ["unique_key", "created_date", "closed_date", "agency", "complaint_type",
        "descriptor", "borough", "open_data_channel_type", "status", "due_date"]
AGENCIES = ["HPD", "NYPD", "DOT", "DSNY", "DEP", "DPR"]
DEFAULT_OUT = "data/raw/nyc311_2025-03-01_2025-10-31.csv"


def get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    token = os.environ.get("SODA_APP_TOKEN")
    if token:
        req.add_header("X-App-Token", token)
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read().decode("utf-8")


def where(start, end):
    ag = ",".join(f"'{a}'" for a in AGENCIES)
    return (f"created_date >= '{start}T00:00:00' AND created_date < '{end}T00:00:00' "
            f"AND agency in({ag})")


def schema():
    meta = json.loads(get(f"{BASE}/api/views/{DATASET}.json"))
    fields = {c["fieldName"]: c.get("name") for c in meta["columns"]}
    print("Title:", meta["name"])
    print("Column count:", len(fields))
    for c in COLS:
        print(f"  {'OK     ' if c in fields else 'MISSING'} {c}  (UI label: {fields.get(c)})")
    print("Other fields:", [f for f in fields if f not in COLS])


def count(start, end):
    url = f"{BASE}/resource/{DATASET}.json"
    total = int(json.loads(get(url, {"$select": "count(*) as n", "$where": where(start, end)}))[0]["n"])
    print(f"TOTAL {start} to <{end}: {total:,}\n")
    rows = json.loads(get(url, {
        "$select": "agency, date_trunc_ym(created_date) as month, count(*) as n",
        "$where": where(start, end), "$group": "agency, month",
        "$order": "month, agency", "$limit": "1000"}))
    t = defaultdict(dict)
    for r in rows:
        t[r["month"][:7]][r["agency"]] = int(r["n"])
    print("month    " + "".join(f"{a:>10}" for a in AGENCIES) + f"{'total':>10}")
    for m in sorted(t):
        vals = [t[m].get(a, 0) for a in AGENCIES]
        print(f"{m}  " + "".join(f"{v:>10,}" for v in vals) + f"{sum(vals):>10,}")


def download(start, end, out):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    page = 2000
    offset = 0
    total = 0

    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")

        while True:
            q = {
                "$select": ",".join(COLS),
                "$where": where(start, end),
                "$order": ":id",
                "$limit": page,
                "$offset": offset,
            }

            print(f"Downloading rows {offset:,}–{offset + page - 1:,}...")

            # Retry a page a few times if the server times out.
            last_error = None

            for attempt in range(1, 4):
                try:
                    text = get(
                        f"{BASE}/resource/{DATASET}.csv",
                        q
                    )
                    rows = list(csv.reader(io.StringIO(text)))
                    break
                except Exception as e:
                    last_error = e
                    print(f"  Attempt {attempt}/3 failed: {e}")
                    if attempt < 3:
                        import time
                        time.sleep(3)
            else:
                raise RuntimeError(
                    f"Failed to download page at offset {offset:,}"
                ) from last_error

            if not rows:
                break

            header, data = rows[0], rows[1:]

            if offset == 0:
                w.writerow(header)

            if not data:
                break

            w.writerows(data)

            total += len(data)
            offset += len(data)

            print(f"  Downloaded: {total:,} rows")

            if len(data) < page:
                break

    print(f"\nWrote {total:,} rows -> {out}")


def profile(path):
    import pandas as pd
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    blank = lambda s: s.str.strip().eq("")
    print(f"Rows: {len(df):,}  Columns: {df.shape[1]}  {list(df.columns)}")
    print("\nBlank counts (raw, uncleaned):")
    print(df.apply(lambda s: blank(s).sum()).to_string())
    dup = df["unique_key"].duplicated(keep=False)
    print(f"\nDuplicate unique_key: {df['unique_key'].duplicated().sum():,} extra rows "
          f"({df.loc[dup, 'unique_key'].nunique():,} keys repeated)")
    for c in ("created_date", "closed_date"):
        s = pd.to_datetime(df[c].where(~blank(df[c])), errors="coerce")
        bad = (s.isna() & ~blank(df[c])).sum()
        print(f"{c}: min={s.min()}  max={s.max()}  unparseable={bad:,}")
    for c in ("agency", "status", "borough"):
        print(f"\n{c}:")
        print(df[c].value_counts(dropna=False).to_string())
    print("\ncomplaint_type top 15:")
    print(df["complaint_type"].value_counts().head(15).to_string())
    pop = ~blank(df["due_date"])
    print(f"\ndue_date populated: {pop.mean():.1%} overall; by agency (%):")
    print((pop.groupby(df["agency"]).mean() * 100).round(1).to_string())


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["schema", "count", "download", "profile"])
    p.add_argument("--start", default="2025-03-01")
    p.add_argument("--end", default="2025-11-01", help="exclusive")
    p.add_argument("--out", default=DEFAULT_OUT)
    a = p.parse_args()
    {"schema": lambda: schema(),
     "count": lambda: count(a.start, a.end),
     "download": lambda: download(a.start, a.end, a.out),
     "profile": lambda: profile(a.out)}[a.cmd]()
