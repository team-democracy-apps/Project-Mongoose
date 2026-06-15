#!/usr/bin/env python3
"""
mongoose_app.py — Mongoose legislative accountability dashboard.

Reads four CSV tables (URLs come from Streamlit Secrets; local files in dev),
makes no API/AI calls at runtime. Flow: pick a state → a representative → see
what they work on and how they act on it (sponsor / cosponsor / vote), plus an
optional AI-derived expand/restrict lean when that data is present.
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

WEIGHTS = {"sponsor": 5, "cosponsor": 3, "vote_yea": 1, "vote_nay": 1}
BACKED = {"sponsor": 1, "cosponsor": 1, "vote_yea": 1, "vote_nay": -1}
ROLE_LABEL = {"sponsor": "Sponsored", "cosponsor": "Cosponsored",
              "vote_yea": "Voted yes", "vote_nay": "Voted no"}
PARTY = {"D": ("Democrat", "#2563eb"), "R": ("Republican", "#dc2626"),
         "I": ("Independent", "#7c3aed"), "": ("—", "#64748b")}
DIRECTIONAL_MIN = 2

st.set_page_config(page_title="Mongoose — where your reps stand",
                   page_icon="🦡", layout="wide")

# --- design tokens + CSS ------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700;800&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@500;600&display=swap');

:root{
  --ink:#15202b; --muted:#5b6675; --hair:#e3e7ec; --paper:#ffffff;
  --bg:#eceef1; --accent:#0d7a6a; --accent-deep:#0a5a4f;
  --yea:#15803d; --nay:#b91c1c;
}
html, body, [class*="css"]{ font-family:'Inter',sans-serif; }
.stApp{ background:var(--bg); }
.block-container{ max-width:1040px; padding-top:1.4rem; }
h1,h2,h3,h4{ font-family:'Archivo',sans-serif; color:var(--ink); letter-spacing:-.01em; }

.eyebrow{ font:600 .72rem/1 'Archivo',sans-serif; letter-spacing:.14em;
  text-transform:uppercase; color:var(--accent); margin-bottom:.35rem; }

/* representative scorecard */
.card{ background:var(--paper); border:1px solid var(--hair); border-radius:14px; }
.profile{ padding:20px 22px; margin:6px 0 18px;
  background:linear-gradient(180deg,#0d7a6a 0%, #0a5a4f 100%); color:#fff;
  border:none; border-radius:14px; }
.profile .name{ font:800 1.7rem/1.1 'Archivo',sans-serif; letter-spacing:-.02em; }
.profile .sub{ opacity:.85; font-size:.92rem; margin-top:3px; }
.stats{ display:flex; gap:26px; margin-top:16px; }
.stat .n{ font:600 1.55rem/1 'IBM Plex Mono',monospace; }
.stat .l{ font-size:.72rem; text-transform:uppercase; letter-spacing:.08em; opacity:.8; }

.pchip{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:.72rem;
  font-weight:600; color:#fff; vertical-align:middle; }

/* issue row */
.issue{ background:var(--paper); border:1px solid var(--hair); border-radius:12px;
  padding:13px 16px; margin-bottom:10px; }
.issue-h{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; }
.issue-name{ font:600 1.02rem/1.2 'Archivo',sans-serif; color:var(--ink); }
.ledger{ margin-top:8px; display:flex; flex-wrap:wrap; gap:6px; }
.tag{ font:600 .74rem/1 'IBM Plex Mono',monospace; padding:4px 9px; border-radius:7px;
  border:1px solid var(--hair); color:var(--ink); background:#f6f8f9; }
.tag .c{ color:var(--accent); }
.tag.yea{ color:var(--yea); border-color:#bfe3c9; background:#f0faf3; }
.tag.nay{ color:var(--nay); border-color:#f0c9c9; background:#fdf2f2; }

.lean{ font:700 .74rem/1 'IBM Plex Mono',monospace; padding:4px 10px; border-radius:999px; }
.lean.exp{ color:#0a5a4f; background:#dafbe1; }
.lean.res{ color:#9a2222; background:#ffe5e5; }
.lean.mix{ color:var(--muted); background:#eef1f4; }

.bill{ border-top:1px solid var(--hair); padding:10px 0 4px; }
.bill-t{ font-weight:600; font-size:.92rem; color:var(--ink); }
.evid{ color:#41506a; font-size:.86rem; border-left:3px solid var(--accent); padding-left:9px; margin-top:5px; }
.muted{ color:var(--muted); font-size:.85rem; }
a{ color:var(--accent-deep); }
</style>
""", unsafe_allow_html=True)


# --- loading (hardened: large-file Drive URLs, HTML detection, validation) ----
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
    pos["weight"] = pos["role"].map(WEIGHTS).fillna(1)
    pos["backed"] = pos["role"].map(BACKED).fillna(1)
    return legs, bills, subj, pos


# --- small render helpers -----------------------------------------------------
def party_chip(p):
    label, color = PARTY.get(str(p), PARTY[""])
    return f'<span class="pchip" style="background:{color}">{label}</span>'


def congress_url(row):
    seg = {"hr": "house-bill", "s": "senate-bill", "hres": "house-resolution",
           "sres": "senate-resolution", "hjres": "house-joint-resolution",
           "sjres": "senate-joint-resolution", "hconres": "house-concurrent-resolution",
           "sconres": "senate-concurrent-resolution"}.get(str(row.get("type", "")).lower(), "house-bill")
    cong = str(row.get("congress") or "119").split(".")[0]
    return f"https://www.congress.gov/bill/{cong}th-congress/{seg}/{row.get('number','')}"


_STOP = {"and", "the", "for", "of", "to", "in", "on", "or", "a", "an"}


def evidence(summary, subject):
    if not summary:
        return ""
    kws = [w for w in re.split(r"\W+", str(subject).lower()) if len(w) > 3 and w not in _STOP]
    for s in re.split(r"(?<=[.!?])\s+", str(summary)):
        if any(k in s.lower() for k in kws):
            return s.strip()[:240]
    return str(summary)[:200]


def action_ledger(rows):
    """rows = a member's position rows for one issue. Returns HTML chips of counts."""
    c = rows["role"].value_counts()
    chips = []
    for role, cls in [("sponsor", ""), ("cosponsor", ""), ("vote_yea", "yea"), ("vote_nay", "nay")]:
        n = int(c.get(role, 0))
        if n:
            chips.append(f'<span class="tag {cls}"><span class="c">{n}</span> {ROLE_LABEL[role].lower()}</span>')
    return '<div class="ledger">' + "".join(chips) + "</div>" if chips else ""


def lean_html(rows):
    exp = res = 0
    for _, r in rows.iterrows():
        pol = r.get("polarity_num")
        if pd.isna(pol) or pol == 0:
            continue
        s = r["backed"] * pol
        exp += s > 0
        res += s < 0
    if exp + res < DIRECTIONAL_MIN:
        return ""
    if exp and not res:
        return f'<span class="lean exp">leans toward expanding</span>'
    if res and not exp:
        return f'<span class="lean res">leans toward restricting</span>'
    return f'<span class="lean mix">mixed · {exp} expand / {res} restrict</span>'


def bill_card(row, subject):
    summary = row.get("ai_summary") or row.get("crs_summary") or ""
    snip = evidence(summary, subject)
    role = ROLE_LABEL.get(row.get("role"), row.get("role", ""))
    act = row.get("action") or ""
    pol = row.get("polarity_num")
    dir_note = ""
    if act and pd.notna(pol) and pol != 0:
        dir_note = f' · {"⬆" if pol > 0 else "⬇"} {act}'
    st.markdown(
        f'<div class="bill"><span class="muted">{role}{dir_note}</span><br>'
        f'<span class="bill-t">{str(row["type"]).upper()} {row["number"]} — {row.get("title","")}</span>'
        f'{f"<div class=evid>{snip}</div>" if snip else ""}'
        f'<div style="margin-top:5px"><a href="{congress_url(row)}" target="_blank">View on Congress.gov →</a></div>'
        f'</div>', unsafe_allow_html=True)


# --- load ---------------------------------------------------------------------
try:
    legs, bills, subj, pos = load_data()
except Exception as e:
    st.error(f"Couldn't load the data.\n\n{e}")
    st.stop()

# data-health figures (so it's obvious what's loaded)
rc = pos["role"].value_counts()
n_votes = int(rc.get("vote_yea", 0) + rc.get("vote_nay", 0))
has_leans = bool(subj["polarity_num"].fillna(0).abs().gt(0).any())

# --- header + sidebar ---------------------------------------------------------
st.markdown('<div class="eyebrow">Congressional accountability</div>', unsafe_allow_html=True)
st.markdown("# Where your representatives stand")
st.markdown('<span class="muted">Pick your state and representative to see the issues they '
            'work on and how they act on each — by sponsoring, cosponsoring, and voting.</span>',
            unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### Mongoose 🦡")
    st.markdown(f'<span class="muted">{len(legs):,} legislators · {len(bills):,} bills · '
                f'{subj["subject"].nunique():,} issues</span>', unsafe_allow_html=True)
    st.markdown("---")
    st.markdown("**Data loaded**")
    st.markdown(
        f'<span class="muted">✍ {int(rc.get("sponsor",0)):,} sponsorships<br>'
        f'➕ {int(rc.get("cosponsor",0)):,} cosponsorships<br>'
        f'🗳 {n_votes:,} roll-call votes</span>', unsafe_allow_html=True)
    if n_votes == 0:
        st.warning("No roll-call votes are loaded. Run the VoteView phase, re-export, "
                   "and re-publish to add vote-based positions.")
    if not has_leans:
        st.info("Directional leans (expand/restrict) are off. Run the optional AI "
                "direction phase to turn them on.")
    st.markdown("---")
    st.markdown('<span class="muted">Issue tags and summaries from Congress.gov (CRS); '
                'votes from VoteView. Directional labels are AI-assigned and may err — '
                'open each bill to verify.</span>', unsafe_allow_html=True)

mode = st.radio("View", ["By representative", "By issue"], horizontal=True, label_visibility="collapsed")


# ============================ BY REPRESENTATIVE ============================== #
if mode == "By representative":
    states = sorted([s for s in legs["state"].unique() if s])
    cs, cr = st.columns([1, 2])
    state = cs.selectbox("State", states, index=states.index("CA") if "CA" in states else 0)

    in_state = legs[legs["state"] == state].copy()
    in_state["label"] = (in_state["name"] + "  ·  " + in_state["party"]
                         + " · " + in_state["chamber"])
    in_state = in_state.sort_values(["chamber", "name"])
    choice = cr.selectbox("Representative", in_state["label"].tolist())
    person = in_state.loc[in_state["label"] == choice].iloc[0]
    bio = person["bioguide"]

    mypos = pos[pos["bioguide"] == bio]
    mine = mypos.merge(subj, on="bill_id", how="left").merge(
        bills, on="bill_id", how="left", suffixes=("", "_b"))

    pname, _ = PARTY.get(person["party"], PARTY[""])
    st.markdown(
        f'<div class="profile"><div class="name">{person["name"]}</div>'
        f'<div class="sub">{pname} · {person["state"]} · {person["chamber"]}</div>'
        f'<div class="stats">'
        f'<div class="stat"><div class="n">{int((mypos.role=="sponsor").sum()):,}</div><div class="l">Sponsored</div></div>'
        f'<div class="stat"><div class="n">{int((mypos.role=="cosponsor").sum()):,}</div><div class="l">Cosponsored</div></div>'
        f'<div class="stat"><div class="n">{int(mypos.role.isin(["vote_yea","vote_nay"]).sum()):,}</div><div class="l">Votes</div></div>'
        f'</div></div>', unsafe_allow_html=True)

    rank = (mine[mine["subject"].astype(str) != ""]
            .groupby("subject").agg(score=("weight", "sum"), bills=("bill_id", "nunique"))
            .sort_values("score", ascending=False).reset_index())

    if rank.empty:
        st.info("No issue activity found for this representative yet.")
    else:
        st.markdown(f'<div class="eyebrow">Top issues · {person["name"]}</div>', unsafe_allow_html=True)
        for _, irow in rank.head(18).iterrows():
            sub = irow["subject"]
            rows = mine[mine["subject"] == sub]
            ledger = action_ledger(rows)
            lean = lean_html(rows.drop_duplicates("bill_id"))
            st.markdown(
                f'<div class="issue"><div class="issue-h">'
                f'<span class="issue-name">{sub}</span>{lean}</div>{ledger}</div>',
                unsafe_allow_html=True)
            with st.expander(f"{int(irow['bills'])} bills on “{sub}”"):
                for _, b in rows.drop_duplicates("bill_id").sort_values("weight", ascending=False).iterrows():
                    bill_card(b, sub)


# ================================ BY ISSUE ================================== #
else:
    q = st.text_input("Search issues", placeholder="prescription drugs, border, firearms, Medicaid…")
    all_subj = sorted(subj["subject"].dropna().unique().tolist())
    matches = [s for s in all_subj if q.lower() in s.lower()] if q else all_subj
    if not matches:
        st.info("No issues match that search.")
        st.stop()
    subject = st.selectbox(f"Issue · {len(matches):,} match", matches)

    issue_subj = subj[subj["subject"] == subject]
    ids = set(issue_subj["bill_id"])
    on = (pos[pos["bill_id"].isin(ids)]
          .merge(issue_subj, on="bill_id", how="left")
          .merge(bills, on="bill_id", how="left", suffixes=("", "_b")))
    ranked = (on.groupby("bioguide").agg(score=("weight", "sum"))
              .reset_index().merge(legs, on="bioguide", how="left")
              .sort_values("score", ascending=False))

    pc = on.merge(legs, on="bioguide")["party"].value_counts()
    st.markdown(f'<div class="eyebrow">Activity on this issue</div>'
                f'<span class="muted">' + " · ".join(f"{PARTY.get(p,('?',''))[0]}: {n}" for p, n in pc.items())
                + (f' · showing top 40 of {len(ranked)} legislators' if len(ranked) > 40 else "")
                + "</span>", unsafe_allow_html=True)

    for _, r in ranked.head(40).iterrows():
        rows = on[on["bioguide"] == r["bioguide"]]
        ledger = action_ledger(rows)
        lean = lean_html(rows.drop_duplicates("bill_id"))
        st.markdown(
            f'<div class="issue"><div class="issue-h"><span class="issue-name">'
            f'{r["name"]} {party_chip(r["party"])} '
            f'<span class="muted">{r["state"]} · {r["chamber"]}</span></span>{lean}</div>{ledger}</div>',
            unsafe_allow_html=True)
        with st.expander(f"{rows['bill_id'].nunique()} bills"):
            for _, b in rows.drop_duplicates("bill_id").sort_values("weight", ascending=False).iterrows():
                bill_card(b, subject)
