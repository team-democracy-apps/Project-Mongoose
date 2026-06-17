#!/usr/bin/env python3
"""
mongoose_app.py — Mongoose: a 119th-Congress accountability dashboard.

Pick your state, then your representative, and see the issues they actually put
their name on — ranked so the issues that *distinguish* them rise to the top,
not the generic ones every member touches. Sponsorship and cosponsorship drive
the ranking (votes are universal, so they're excluded there); votes feed the
expand/restrict stance. No API/AI calls at runtime.
"""

import re
import math
import pandas as pd
import streamlit as st

try:
    SOURCES = dict(st.secrets["sources"])
except Exception:
    SOURCES = {
        "legislators": "data/legislators.csv",
        "bills": "data/bills.csv",
        "bill_subjects": "data/bill_subjects.csv",
        "positions": "data/positions.csv",
    }

PRIORITY_PTS = {"sponsor": 5, "cosponsor": 3, "vote_yea": 0, "vote_nay": 0}
BACKED = {"sponsor": 1, "cosponsor": 1, "vote_yea": 1, "vote_nay": -1}
PARTY = {"D": ("Democrat", "#2563eb"), "R": ("Republican", "#dc2626"),
         "I": ("Independent", "#7c3aed"), "": ("—", "#64748b")}
STANCE_MIN = 2

st.set_page_config(page_title="Mongoose · 119th Congress", page_icon="🦡", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@600;700;800;900&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@500;600;700&display=swap');
:root{ --ink:#11161d; --muted:#5b6675; --faint:#8a94a3; --hair:#e7eaef; --paper:#fff;
  --bg:#f4f6f8; --brand:#0e6e62; --brand-d:#0a4f47; --exp:#0e7a4f; --res:#b4322f; }
html,body,[class*="css"]{ font-family:'Inter',sans-serif; color:var(--ink); }
.stApp{ background:var(--bg); }
.block-container{ max-width:1020px; padding-top:1.1rem; }
.mast{ display:flex; flex-wrap:wrap; gap:8px; align-items:flex-end; justify-content:space-between;
  border-bottom:2px solid var(--ink); padding-bottom:10px; margin-bottom:6px; }
.mast .t{ font:900 2rem/1 'Archivo',sans-serif; letter-spacing:-.03em; }
.mast .t span{ color:var(--brand); }
.eyebrow{ font:700 .7rem/1 'IBM Plex Mono',monospace; letter-spacing:.16em; text-transform:uppercase; color:var(--muted); }
.lede{ color:var(--muted); font-size:.92rem; margin:9px 0 2px; }
.profile{ background:var(--paper); border:1px solid var(--hair); border-left:6px solid var(--pc,#999);
  border-radius:12px; padding:18px 22px; margin:16px 0 8px; box-shadow:0 1px 2px rgba(16,22,30,.04); }
.profile .nm{ font:800 1.55rem/1.1 'Archivo',sans-serif; letter-spacing:-.02em; }
.profile .meta{ color:var(--muted); font-size:.9rem; margin-top:3px; }
.pchip{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:.7rem; font-weight:700; color:#fff; vertical-align:middle; }
.tiles{ display:flex; flex-wrap:wrap; gap:26px; margin-top:15px; }
.tile .n{ font:700 1.45rem/1 'IBM Plex Mono',monospace; }
.tile .l{ font-size:.66rem; text-transform:uppercase; letter-spacing:.09em; color:var(--faint); margin-top:2px; }
.sec{ font:700 .74rem/1 'IBM Plex Mono',monospace; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); margin:22px 0 10px; }
.note{ color:var(--faint); font-size:.82rem; font-weight:400; text-transform:none; letter-spacing:0; }
.card{ background:var(--paper); border:1px solid var(--hair); border-radius:12px; padding:6px 18px; box-shadow:0 1px 2px rgba(16,22,30,.04); }
.srow{ display:grid; grid-template-columns:minmax(0,1fr) 190px 92px; gap:14px; align-items:center;
  padding:11px 0; border-bottom:1px solid var(--hair); }
.srow:last-child{ border-bottom:none; }
.iblock{ min-width:0; }
.iname{ font:600 1rem/1.25 'Archivo',sans-serif; overflow-wrap:anywhere; }
.ibreak{ color:var(--faint); font-size:.78rem; font-family:'IBM Plex Mono',monospace; margin-top:2px; }
.barwrap{ background:#eef1f4; border-radius:6px; height:12px; overflow:hidden; }
.bar{ height:100%; background:linear-gradient(90deg,var(--brand),var(--brand-d)); border-radius:6px; }
.scorecell{ text-align:right; white-space:nowrap; }
.pts{ font:700 1.05rem/1 'IBM Plex Mono',monospace; }
.ptl{ font-size:.6rem; color:var(--faint); letter-spacing:.07em; text-transform:uppercase; }
.stance{ display:inline-block; margin-top:4px; font:700 .66rem/1 'IBM Plex Mono',monospace; padding:3px 7px; border-radius:6px; }
.stance.exp{ color:var(--exp); background:#e3f6ec; } .stance.res{ color:var(--res); background:#fbe7e6; } .stance.mix{ color:var(--muted); background:#eef1f4; }
@media (max-width:680px){
  .srow{ grid-template-columns:1fr auto; grid-template-areas:"name score" "bar bar"; gap:6px 12px; }
  .iblock{ grid-area:name; } .scorecell{ grid-area:score; } .barwrap{ grid-area:bar; height:10px; }
}
.bill{ border-top:1px solid var(--hair); padding:10px 0 4px; }
.bill-r{ font:600 .72rem/1 'IBM Plex Mono',monospace; color:var(--muted); }
.bill-t{ font-weight:600; font-size:.92rem; }
.evid{ color:#41506a; font-size:.86rem; border-left:3px solid var(--brand); padding-left:9px; margin-top:5px; }
.muted{ color:var(--muted); font-size:.85rem; } a{ color:var(--brand-d); }
</style>
""", unsafe_allow_html=True)


def _drive_direct(url):
    if "drive.google.com" in url:
        m = re.search(r"[?&]id=([\w-]+)", url) or re.search(r"/d/([\w-]+)", url)
        if m:
            return f"https://drive.usercontent.google.com/download?id={m.group(1)}&export=download&confirm=t"
    return url


def _read_one(name, src, required):
    try:
        df = pd.read_csv(_drive_direct(str(src).strip()), dtype=str).fillna("")
    except Exception as e:
        raise RuntimeError(f"[{name}] couldn't be read ({type(e).__name__}: {e}).")
    head = " ".join(map(str, df.columns)).lower()
    if df.shape[1] <= 1 and ("<html" in head or "<!doctype" in head or "google drive" in head):
        raise RuntimeError(f"[{name}] returned a web page, not CSV — check its share link.")
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"[{name}] missing column(s) {missing}; got {list(df.columns)[:8]}.")
    return df


@st.cache_data(show_spinner="Loading…", ttl="30m")
def load_data():
    legs = _read_one("legislators", SOURCES["legislators"], ["bioguide", "name", "party", "state", "chamber"])
    bills = _read_one("bills", SOURCES["bills"], ["bill_id", "type", "number", "title"])
    subj = _read_one("bill_subjects", SOURCES["bill_subjects"], ["bill_id", "subject"])
    pos = _read_one("positions", SOURCES["positions"], ["bioguide", "bill_id", "role"])
    subj["polarity_num"] = pd.to_numeric(subj["polarity"], errors="coerce") if "polarity" in subj else pd.NA
    if "action" not in subj:
        subj["action"] = ""
    pos["ppts"] = pos["role"].map(PRIORITY_PTS).fillna(0)
    pos["backed"] = pos["role"].map(BACKED).fillna(1)
    cong = pd.to_numeric(bills["congress"], errors="coerce").dropna() if "congress" in bills else pd.Series([119])
    congress = int(cong.mode().iloc[0]) if len(cong) else 119
    # specificity weight: issues carried by few bills are more distinguishing
    nbills = max(1, bills["bill_id"].nunique())
    cnt = subj.groupby("subject")["bill_id"].nunique()
    idf = {s: math.log((nbills + 1) / (c + 1)) + 0.5 for s, c in cnt.items()}
    return legs, bills, subj, pos, congress, idf


def congress_label(n):
    start = 1789 + (n - 1) * 2
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf} Congress · {start}–{start + 2}"


def stance_chip(rows):
    sig = rows["backed"] * rows["polarity_num"]
    exp, res = int((sig > 0).sum()), int((sig < 0).sum())
    if exp + res < STANCE_MIN:
        return ""
    if exp and not res:
        return '<span class="stance exp">▲ expand</span>'
    if res and not exp:
        return '<span class="stance res">▼ restrict</span>'
    return f'<span class="stance mix">mixed {exp}/{res}</span>'


_STOP = {"and", "the", "for", "of", "to", "in", "on", "or", "a", "an", "this", "act", "bill"}
_SEC_RE = re.compile(r"^\s*(sec(tion)?\.?\s*\d|title\s+[ivxl]+|\(\w\)|\d+\s*\.)", re.I)


def evidence(summary, subject):
    """A readable sentence relevant to the subject — never a bare section header."""
    if not summary:
        return ""
    kws = [w for w in re.split(r"\W+", str(subject).lower()) if len(w) > 3 and w not in _STOP]
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(summary)) if s.strip()]
    good = [s for s in sents if not _SEC_RE.match(s) and len(s.split()) >= 5]
    for s in good:
        if any(k in s.lower() for k in kws):
            return s[:240]
    return (good[0] if good else (sents[0] if sents else ""))[:240]


def congress_url(row, cong):
    seg = {"hr": "house-bill", "s": "senate-bill", "hres": "house-resolution", "sres": "senate-resolution",
           "hjres": "house-joint-resolution", "sjres": "senate-joint-resolution",
           "hconres": "house-concurrent-resolution", "sconres": "senate-concurrent-resolution"}.get(
        str(row.get("type", "")).lower(), "house-bill")
    return f"https://www.congress.gov/bill/{cong}th-congress/{seg}/{row.get('number','')}"


def bill_html(row, subject, cong):
    explainer = (row.get("ai_explainer") or "").strip()
    # prefer the clean AI explainer, then the CRS summary; the bill-text excerpt
    # (ai_summary) is last because it can be raw section boilerplate.
    if explainer:
        snip = explainer[:320]
    else:
        snip = evidence(row.get("crs_summary") or row.get("ai_summary") or "", subject)
    rl = {"sponsor": "Sponsor", "cosponsor": "Cosponsor", "vote_yea": "Voted yes",
          "vote_nay": "Voted no"}.get(row.get("role"), row.get("role", ""))
    pol, act = row.get("polarity_num"), row.get("action") or ""
    dirn = f' · {"▲" if pol > 0 else "▼"} {act}' if act and pd.notna(pol) and pol != 0 else ""
    return (f'<div class="bill"><div class="bill-r">{rl}{dirn}</div>'
            f'<div class="bill-t">{str(row["type"]).upper()} {row["number"]} — {row.get("title","")}</div>'
            f'{f"<div class=evid>{snip}</div>" if snip else ""}'
            f'<div style="margin-top:4px"><a href="{congress_url(row, cong)}" target="_blank">Congress.gov →</a></div></div>')


try:
    legs, bills, subj, pos, CONG, IDF = load_data()
except Exception as e:
    st.error(f"Couldn't load the data.\n\n{e}")
    st.stop()

rc = pos["role"].value_counts()
n_votes = int(rc.get("vote_yea", 0) + rc.get("vote_nay", 0))
has_leans = bool(subj["polarity_num"].fillna(0).abs().gt(0).any())
SUBJ_SLIM = subj[["bill_id", "subject", "polarity_num", "action"]]

st.markdown(
    f'<div class="mast"><div><div class="eyebrow">{congress_label(CONG)}</div>'
    f'<div class="t">Mongoose<span>.</span></div></div>'
    f'<div class="eyebrow">{len(legs):,} members · {len(bills):,} bills</div></div>'
    '<div class="lede">Pick your state and representative to see the issues they put their '
    'name on — ranked so the ones that set them apart rise to the top.</div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### Mongoose 🦡")
    st.markdown(f'<span class="muted">{congress_label(CONG)}</span>', unsafe_allow_html=True)
    st.markdown("---")
    st.markdown("**Data loaded**")
    st.markdown(f'<span class="muted">✍ {int(rc.get("sponsor",0)):,} sponsorships<br>'
                f'➕ {int(rc.get("cosponsor",0)):,} cosponsorships<br>'
                f'🗳 {n_votes:,} roll-call votes</span>', unsafe_allow_html=True)
    if n_votes == 0:
        st.warning("No roll-call votes loaded — the stance lean needs them.")
    if not has_leans:
        st.info("Directional leans are off until the AI direction phase runs.")
    st.markdown("---")
    st.markdown('<span class="muted">Ranking = sponsorship (5) + cosponsorship (3), weighted toward '
                'issues few members touch. Votes feed the lean, not the ranking. Tags from Congress.gov '
                "(CRS); votes from VoteView; leans AI-assigned — verify on each bill.</span>",
                unsafe_allow_html=True)

# --- state → representative (no default selection) ----------------------------
states = sorted([s for s in legs["state"].unique() if s])
cs, cr = st.columns([1, 2])
state = cs.selectbox("State", ["Select a state…"] + states, index=0)

if state == "Select a state…":
    st.info("Choose your state above to begin.")
    st.stop()

insta = legs[legs["state"] == state].copy()
insta["label"] = insta["name"] + "  ·  " + insta["party"] + " · " + insta["chamber"]
insta = insta.sort_values(["chamber", "name"])
pick = cr.selectbox("Representative", ["Select a representative…"] + insta["label"].tolist(), index=0)

if pick == "Select a representative…":
    st.info(f"Choose a representative from {state}.")
    st.stop()

person = insta.loc[insta["label"] == pick].iloc[0]
bio = person["bioguide"]
mp = pos[pos["bioguide"] == bio].merge(SUBJ_SLIM, on="bill_id", how="left")
mp = mp[mp["subject"].astype(str) != ""]
pname, pcol = PARTY.get(person["party"], PARTY[""])

st.markdown(
    f'<div class="profile" style="--pc:{pcol}"><div class="nm">{person["name"]} '
    f'<span class="pchip" style="background:{pcol}">{pname}</span></div>'
    f'<div class="meta">{person["state"]} · {person["chamber"]} · {congress_label(CONG)}</div>'
    f'<div class="tiles">'
    f'<div class="tile"><div class="n">{int((pos[pos.bioguide==bio].role=="sponsor").sum()):,}</div><div class="l">Sponsored</div></div>'
    f'<div class="tile"><div class="n">{int((pos[pos.bioguide==bio].role=="cosponsor").sum()):,}</div><div class="l">Cosponsored</div></div>'
    f'<div class="tile"><div class="n">{int(pos[pos.bioguide==bio].role.isin(["vote_yea","vote_nay"]).sum()):,}</div><div class="l">Votes cast</div></div>'
    f'</div></div>', unsafe_allow_html=True)

eng = (mp.groupby("subject")
       .agg(points=("ppts", "sum"),
            sponsored=("role", lambda s: int((s == "sponsor").sum())),
            cosponsored=("role", lambda s: int((s == "cosponsor").sum())),
            bills=("bill_id", "nunique"))
       .reset_index())
eng = eng[eng["points"] > 0].copy()
eng["score"] = (eng["points"] * eng["subject"].map(lambda s: IDF.get(s, 1.0))).round().astype(int)
eng = eng.sort_values("score", ascending=False)

if eng.empty:
    st.info("No sponsored or cosponsored bills with issue tags for this member yet.")
else:
    st.markdown('<div class="sec">Issue priorities '
                '<span class="note">— what they sponsor & cosponsor, weighted toward distinctive issues</span></div>',
                unsafe_allow_html=True)
    mx = max(1, eng["score"].max())
    rows_html = []
    for _, e in eng.head(15).iterrows():
        sub = e["subject"]
        sh = stance_chip(mp[mp["subject"] == sub].drop_duplicates("bill_id"))
        bd = " · ".join(filter(None, [f'{e.sponsored} sponsored' if e.sponsored else "",
                                      f'{e.cosponsored} cosponsored' if e.cosponsored else ""]))
        w = int(round(100 * e["score"] / mx))
        rows_html.append(
            f'<div class="srow"><div class="iblock"><div class="iname">{sub}</div>'
            f'<div class="ibreak">{bd}</div></div>'
            f'<div class="barwrap"><div class="bar" style="width:{w}%"></div></div>'
            f'<div class="scorecell"><span class="pts">{e["score"]}</span> '
            f'<span class="ptl">score</span><br>{sh}</div></div>')
    st.markdown('<div class="card">' + "".join(rows_html) + "</div>", unsafe_allow_html=True)

    st.markdown('<div class="sec">Look inside an issue</div>', unsafe_allow_html=True)
    chosen = st.selectbox("Issue", ["Select an issue…"] + eng["subject"].tolist(), index=0, label_visibility="collapsed")
    if chosen != "Select an issue…":
        det = (mp[mp["subject"] == chosen].drop_duplicates("bill_id")
               .merge(bills, on="bill_id", how="left", suffixes=("", "_b")))
        st.markdown('<div class="card" style="padding:4px 18px 12px">'
                    + "".join(bill_html(b, chosen, CONG) for _, b in det.sort_values("ppts", ascending=False).iterrows())
                    + "</div>", unsafe_allow_html=True)
