#!/usr/bin/env python3
"""
mongoose_app.py — Mongoose: a 119th-Congress accountability dashboard.

Model:
  • PRIORITY  = what a member chooses to put their name on. Scored from
    sponsorship (5) + cosponsorship (3). Votes are EXCLUDED here — every member
    votes on everything that reaches the floor, so votes don't distinguish a
    member's agenda. This is what ranks "top issues."
  • STANCE    = direction on an issue (expand vs restrict), from the member's
    actions × each bill's CRS-derived polarity (this is where votes count).
Reads four CSVs (URLs from Streamlit Secrets); no API/AI calls at runtime.
"""

import re
import pandas as pd
import streamlit as st

# --- data source (URLs live in Streamlit Secrets, not in this repo) ----------
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
:root{
  --ink:#11161d; --muted:#5b6675; --faint:#8a94a3; --hair:#e7eaef; --paper:#fff;
  --bg:#f4f6f8; --brand:#0e6e62; --brand-d:#0a4f47; --exp:#0e7a4f; --res:#b4322f;
}
html,body,[class*="css"]{ font-family:'Inter',sans-serif; color:var(--ink); }
.stApp{ background:var(--bg); }
.block-container{ max-width:1080px; padding-top:1.1rem; }

/* masthead */
.mast{ display:flex; align-items:flex-end; justify-content:space-between;
  border-bottom:2px solid var(--ink); padding-bottom:10px; margin-bottom:6px; }
.mast .t{ font:900 2.05rem/1 'Archivo',sans-serif; letter-spacing:-.03em; }
.mast .t span{ color:var(--brand); }
.eyebrow{ font:700 .7rem/1 'IBM Plex Mono',monospace; letter-spacing:.18em;
  text-transform:uppercase; color:var(--muted); }
.lede{ color:var(--muted); font-size:.92rem; margin:9px 0 4px; }

/* profile */
.profile{ background:var(--paper); border:1px solid var(--hair); border-left:6px solid var(--pc,#999);
  border-radius:12px; padding:18px 22px; margin:14px 0 8px;
  box-shadow:0 1px 2px rgba(16,22,30,.04); }
.profile .nm{ font:800 1.6rem/1.1 'Archivo',sans-serif; letter-spacing:-.02em; }
.profile .meta{ color:var(--muted); font-size:.9rem; margin-top:3px; }
.pchip{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:.7rem;
  font-weight:700; color:#fff; vertical-align:middle; }
.tiles{ display:flex; gap:30px; margin-top:15px; }
.tile .n{ font:700 1.5rem/1 'IBM Plex Mono',monospace; }
.tile .l{ font-size:.68rem; text-transform:uppercase; letter-spacing:.09em; color:var(--faint); margin-top:2px; }

/* section */
.sec{ font:700 .74rem/1 'IBM Plex Mono',monospace; letter-spacing:.16em; text-transform:uppercase;
  color:var(--muted); margin:22px 0 10px; }
.note{ color:var(--faint); font-size:.82rem; font-weight:400; text-transform:none; letter-spacing:0; }

/* scorecard rows */
.card{ background:var(--paper); border:1px solid var(--hair); border-radius:12px;
  padding:6px 18px; box-shadow:0 1px 2px rgba(16,22,30,.04); }
.srow{ display:grid; grid-template-columns:minmax(0,1fr) 220px 110px; gap:16px; align-items:center;
  padding:11px 0; border-bottom:1px solid var(--hair); }
.srow:last-child{ border-bottom:none; }
.iname{ font:600 1rem/1.25 'Archivo',sans-serif; }
.ibreak{ color:var(--faint); font-size:.78rem; font-family:'IBM Plex Mono',monospace; margin-top:2px; }
.barwrap{ background:#eef1f4; border-radius:6px; height:12px; overflow:hidden; }
.bar{ height:100%; background:linear-gradient(90deg,var(--brand),var(--brand-d)); border-radius:6px; }
.scorecell{ text-align:right; }
.pts{ font:700 1.05rem/1 'IBM Plex Mono',monospace; }
.ptl{ font-size:.62rem; color:var(--faint); letter-spacing:.08em; text-transform:uppercase; }
.stance{ display:inline-block; margin-top:4px; font:700 .68rem/1 'IBM Plex Mono',monospace;
  padding:3px 8px; border-radius:6px; }
.stance.exp{ color:var(--exp); background:#e3f6ec; }
.stance.res{ color:var(--res); background:#fbe7e6; }
.stance.mix{ color:var(--muted); background:#eef1f4; }

/* bill */
.bill{ border-top:1px solid var(--hair); padding:10px 0 4px; }
.bill-r{ font:600 .72rem/1 'IBM Plex Mono',monospace; color:var(--muted); }
.bill-t{ font-weight:600; font-size:.92rem; }
.evid{ color:#41506a; font-size:.86rem; border-left:3px solid var(--brand); padding-left:9px; margin-top:5px; }
.muted{ color:var(--muted); font-size:.85rem; }
a{ color:var(--brand-d); }
</style>
""", unsafe_allow_html=True)


# --- load (hardened: large-file Drive URLs, HTML detection, validation) -------
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
    legs = _read_one("legislators", SOURCES["legislators"],
                     ["bioguide", "name", "party", "state", "chamber"])
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
    return legs, bills, subj, pos, congress


# --- helpers ------------------------------------------------------------------
def congress_label(n):
    start = 1789 + (n - 1) * 2
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf} Congress · {start}–{start + 2}"


def stance_chip(rows):
    """rows carry backed + polarity_num. Returns ('exp'|'res'|'mix'|None, html)."""
    sig = rows["backed"] * rows["polarity_num"]
    exp = int((sig > 0).sum())
    res = int((sig < 0).sum())
    if exp + res < STANCE_MIN:
        return None, ""
    if exp and not res:
        return "exp", '<span class="stance exp">▲ expand</span>'
    if res and not exp:
        return "res", '<span class="stance res">▼ restrict</span>'
    return "mix", f'<span class="stance mix">mixed {exp}/{res}</span>'


def scorerow(label_html, pts, maxpts, breakdown, stance_html):
    w = int(round(100 * pts / maxpts)) if maxpts else 0
    return (f'<div class="srow"><div><div class="iname">{label_html}</div>'
            f'<div class="ibreak">{breakdown}</div></div>'
            f'<div class="barwrap"><div class="bar" style="width:{w}%"></div></div>'
            f'<div class="scorecell"><span class="pts">{int(pts)}</span> '
            f'<span class="ptl">pts</span><br>{stance_html}</div></div>')


_STOP = {"and", "the", "for", "of", "to", "in", "on", "or", "a", "an"}


def evidence(summary, subject):
    if not summary:
        return ""
    kws = [w for w in re.split(r"\W+", str(subject).lower()) if len(w) > 3 and w not in _STOP]
    for s in re.split(r"(?<=[.!?])\s+", str(summary)):
        if any(k in s.lower() for k in kws):
            return s.strip()[:240]
    return str(summary)[:200]


def congress_url(row, cong):
    seg = {"hr": "house-bill", "s": "senate-bill", "hres": "house-resolution",
           "sres": "senate-resolution", "hjres": "house-joint-resolution",
           "sjres": "senate-joint-resolution", "hconres": "house-concurrent-resolution",
           "sconres": "senate-concurrent-resolution"}.get(str(row.get("type", "")).lower(), "house-bill")
    return f"https://www.congress.gov/bill/{cong}th-congress/{seg}/{row.get('number','')}"


def bill_html(row, subject, cong):
    explainer = row.get("ai_explainer") or ""
    summary = explainer or row.get("ai_summary") or row.get("crs_summary") or ""
    snip = explainer[:320] if explainer else evidence(summary, subject)
    rl = {"sponsor": "Sponsor", "cosponsor": "Cosponsor",
          "vote_yea": "Voted yes", "vote_nay": "Voted no"}.get(row.get("role"), row.get("role", ""))
    pol, act = row.get("polarity_num"), row.get("action") or ""
    dirn = f' · {"▲" if pol > 0 else "▼"} {act}' if act and pd.notna(pol) and pol != 0 else ""
    return (f'<div class="bill"><div class="bill-r">{rl}{dirn}</div>'
            f'<div class="bill-t">{str(row["type"]).upper()} {row["number"]} — {row.get("title","")}</div>'
            f'{f"<div class=evid>{snip}</div>" if snip else ""}'
            f'<div style="margin-top:4px"><a href="{congress_url(row, cong)}" target="_blank">Congress.gov →</a></div></div>')


# --- load ---------------------------------------------------------------------
try:
    legs, bills, subj, pos, CONG = load_data()
except Exception as e:
    st.error(f"Couldn't load the data.\n\n{e}")
    st.stop()

rc = pos["role"].value_counts()
n_votes = int(rc.get("vote_yea", 0) + rc.get("vote_nay", 0))
has_leans = bool(subj["polarity_num"].fillna(0).abs().gt(0).any())
SUBJ_SLIM = subj[["bill_id", "subject", "polarity_num", "action"]]

# --- masthead -----------------------------------------------------------------
st.markdown(
    f'<div class="mast"><div><div class="eyebrow">{congress_label(CONG)}</div>'
    f'<div class="t">Mongoose<span>.</span></div></div>'
    f'<div class="eyebrow">{len(legs):,} members · {len(bills):,} bills</div></div>'
    '<div class="lede">Where members of Congress put their name — ranked by what they '
    'sponsor and cosponsor, with their expand/restrict lean from the record. '
    'Floor votes shape the lean, not the ranking.</div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### Mongoose 🦡")
    st.markdown(f'<span class="muted">{congress_label(CONG)}</span>', unsafe_allow_html=True)
    st.markdown("---")
    st.markdown("**Data loaded**")
    st.markdown(f'<span class="muted">✍ {int(rc.get("sponsor",0)):,} sponsorships<br>'
                f'➕ {int(rc.get("cosponsor",0)):,} cosponsorships<br>'
                f'🗳 {n_votes:,} roll-call votes</span>', unsafe_allow_html=True)
    if n_votes == 0:
        st.warning("No roll-call votes loaded — the expand/restrict lean needs them. "
                   "Run the VoteView phase, then re-export and re-publish.")
    if not has_leans:
        st.info("Directional leans are off. Run the optional AI direction phase to "
                "turn the expand/restrict labels on.")
    st.markdown("---")
    st.markdown('<span class="muted">Scoring: sponsor = 5, cosponsor = 3 (votes excluded '
                'so universal floor votes don\'t flatten the ranking). Issue tags from '
                'Congress.gov (CRS); votes from VoteView; leans AI-assigned — verify on each bill.</span>',
                unsafe_allow_html=True)

mode = st.radio("View", ["By representative", "By issue"], horizontal=True, label_visibility="collapsed")


# ============================ BY REPRESENTATIVE ============================== #
if mode == "By representative":
    states = sorted([s for s in legs["state"].unique() if s])
    cs, cr = st.columns([1, 2])
    state = cs.selectbox("State", states, index=states.index("CA") if "CA" in states else 0)
    insta = legs[legs["state"] == state].copy()
    insta["label"] = insta["name"] + "  ·  " + insta["party"] + " · " + insta["chamber"]
    insta = insta.sort_values(["chamber", "name"])
    person = insta.loc[insta["label"] == cr.selectbox("Representative", insta["label"].tolist())].iloc[0]
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
    eng = eng[eng["points"] > 0].sort_values("points", ascending=False)

    if eng.empty:
        st.info("No sponsored or cosponsored bills with issue tags for this member yet.")
    else:
        st.markdown(f'<div class="sec">Issue priorities '
                    f'<span class="note">— ranked by sponsorship (5) + cosponsorship (3)</span></div>',
                    unsafe_allow_html=True)
        mx = eng["points"].max()
        rows_html = []
        for _, e in eng.head(15).iterrows():
            sub = e["subject"]
            _, sh = stance_chip(mp[mp["subject"] == sub].drop_duplicates("bill_id"))
            bd = " · ".join(filter(None, [
                f'{e.sponsored} sponsored' if e.sponsored else "",
                f'{e.cosponsored} cosponsored' if e.cosponsored else ""]))
            rows_html.append(scorerow(sub, e["points"], mx, bd, sh))
        st.markdown('<div class="card">' + "".join(rows_html) + "</div>", unsafe_allow_html=True)

        st.markdown('<div class="sec">Look inside an issue</div>', unsafe_allow_html=True)
        pick = st.selectbox("Issue", eng["subject"].tolist(), label_visibility="collapsed")
        det = mp[mp["subject"] == pick].drop_duplicates("bill_id").merge(bills, on="bill_id", how="left", suffixes=("", "_b"))
        st.markdown('<div class="card" style="padding:4px 18px 12px">'
                    + "".join(bill_html(b, pick, CONG) for _, b in det.sort_values("ppts", ascending=False).iterrows())
                    + "</div>", unsafe_allow_html=True)


# ================================ BY ISSUE ================================== #
else:
    q = st.text_input("Search issues", placeholder="prescription drugs, border security, firearms…")
    all_subj = sorted(subj["subject"].dropna().unique().tolist())
    matches = [s for s in all_subj if q.lower() in s.lower()] if q else all_subj
    if not matches:
        st.info("No issues match that search.")
        st.stop()
    subject = st.selectbox(f"Issue · {len(matches):,} match", matches)

    isub = SUBJ_SLIM[SUBJ_SLIM["subject"] == subject]
    on = pos[pos["bill_id"].isin(set(isub["bill_id"]))].merge(isub, on="bill_id", how="left")
    eng = (on.groupby("bioguide")
           .agg(points=("ppts", "sum"),
                sponsored=("role", lambda s: int((s == "sponsor").sum())),
                cosponsored=("role", lambda s: int((s == "cosponsor").sum())))
           .reset_index().merge(legs, on="bioguide", how="left"))
    eng = eng[eng["points"] > 0].sort_values("points", ascending=False)

    st.markdown(f'<div class="sec">Who drives “{subject}” '
                f'<span class="note">— authors & cosponsors, ranked. Floor votes excluded.</span></div>',
                unsafe_allow_html=True)
    if eng.empty:
        st.info("No members have sponsored or cosponsored bills tagged with this issue.")
        st.stop()
    mx = eng["points"].max()
    rows_html = []
    for _, e in eng.head(40).iterrows():
        _, sh = stance_chip(on[on["bioguide"] == e["bioguide"]].drop_duplicates("bill_id"))
        _, pcol = PARTY.get(e["party"], PARTY[""])
        label = (f'{e["name"]} <span class="pchip" style="background:{pcol}">{e["party"]}</span> '
                 f'<span class="muted">{e["state"]} · {e["chamber"]}</span>')
        bd = " · ".join(filter(None, [f'{e.sponsored} sponsored' if e.sponsored else "",
                                      f'{e.cosponsored} cosponsored' if e.cosponsored else ""]))
        rows_html.append(scorerow(label, e["points"], mx, bd, sh))
    st.markdown('<div class="card">' + "".join(rows_html) + "</div>", unsafe_allow_html=True)
    if len(eng) > 40:
        st.markdown(f'<div class="muted" style="margin-top:8px">Showing top 40 of {len(eng):,} members.</div>',
                    unsafe_allow_html=True)
