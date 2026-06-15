#!/usr/bin/env python3
"""
mongoose_app.py  (v2)
=====================
Streamlit dashboard for Mongoose. Reads flat CSV tables -- local files by
default, or Google Sheets / Drive CSV URLs in production. NO API or AI calls at
runtime, so it runs free on Streamlit Community Cloud.

LOCAL
    pip install streamlit pandas
    python mongoose_build_dataset.py --sample
    streamlit run mongoose_app.py

GOOGLE SHEETS / DRIVE
    Put each table in Drive (or as a tab in one Sheet), publish to the web as
    CSV (File > Share > Publish to web > CSV), and paste the four URLs into
    DATA below. pandas reads the published-CSV URL directly -- no auth, no cost.
    (For private sheets, use the streamlit-gsheets connection with a service
    account instead; same four tables.)

WHAT A "STANCE" IS HERE
    A legislator's stance on an issue is read from what they did: bills they
    sponsored (x5), cosponsored (x3), and votes (x1). Direction comes from each
    bill's polarity (does it expand or restrict the subject) times whether the
    member backed it (sponsor/cosponsor/yea = +, nay = -). We only show a
    directional readout once there are enough signals on a *specific* subject;
    otherwise we just show the activity and let the bill evidence speak.
"""

import re
import pandas as pd
import streamlit as st

# --- DATA SOURCE -------------------------------------------------------------
# The four data URLs live in Streamlit *Secrets*, NOT in this file — so this repo
# can be public without exposing where the data is. On Streamlit Cloud, set them
# under Settings ▸ Secrets (see secrets.toml.example). Locally, with no secrets
# configured, it falls back to the data/*.csv files for development.
#
# A value is read as: an http(s) URL → CSV via pandas; a path ending in .csv →
# local file. (Sheet CSV-export links and Drive download links are both URLs.)
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
BACKED = {"sponsor": 1, "cosponsor": 1, "vote_yea": 1, "vote_nay": -1}  # backed vs opposed the bill
ROLE_LABEL = {"sponsor": "Sponsor", "cosponsor": "Cosponsor",
              "vote_yea": "Voted Yes", "vote_nay": "Voted No"}
PARTY_COLOR = {"D": "#1f6feb", "R": "#cf222e", "I": "#8250df", "": "#6e7781"}
DIRECTIONAL_MIN = 2  # need this many directional signals before showing a lean

st.set_page_config(page_title="Mongoose — Legislator Issue Tracker", page_icon="🦡", layout="wide")
st.markdown("""<style>
  .block-container {padding-top: 2rem; max-width: 1100px;}
  .chip {display:inline-block;padding:2px 9px;border-radius:11px;font-size:0.74rem;
         font-weight:600;color:#fff;margin-right:6px;}
  .role-sponsor{background:#1a7f37;} .role-cosponsor{background:#3a7bd5;}
  .role-vote_yea{background:#57606a;} .role-vote_nay{background:#8b5cf6;}
  .muted{color:#57606a;font-size:0.85rem;}
  .billcard{border:1px solid #e1e4e8;border-radius:9px;padding:11px 14px;margin-bottom:9px;background:#fff;}
  .billtitle{font-weight:600;font-size:0.97rem;}
  .evid{color:#3d4350;font-style:italic;border-left:3px solid #d0d7de;padding-left:9px;margin-top:6px;}
  .lean{display:inline-block;padding:3px 10px;border-radius:13px;font-size:0.8rem;font-weight:600;}
  .expand{background:#dafbe1;color:#1a7f37;} .restrict{background:#ffebe9;color:#cf222e;}
  .mixed{background:#eef1f5;color:#57606a;}
</style>""", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
def _read_one(src):
    """Read a single table from a CSV URL (Sheet export / Drive link) or local path."""
    return pd.read_csv(str(src).strip(), dtype=str).fillna("")


@st.cache_data(show_spinner=False, ttl="30m")
def load_data():
    legs = _read_one(SOURCES["legislators"])
    bills = _read_one(SOURCES["bills"])
    subj = _read_one(SOURCES["bill_subjects"])
    pos = _read_one(SOURCES["positions"])

    subj["polarity_num"] = pd.to_numeric(subj["polarity"], errors="coerce")
    pos["weight"] = pos["role"].map(WEIGHTS).fillna(1)
    pos["backed"] = pos["role"].map(BACKED).fillna(1)

    pb = pos.merge(bills, on="bill_id", how="left", suffixes=("", "_b"))
    long = pb.merge(subj, on="bill_id", how="left")
    return legs, bills, subj, pos, long



def congress_url(row):
    seg = {"hr": "house-bill", "s": "senate-bill", "hres": "house-resolution",
           "sres": "senate-resolution", "hjres": "house-joint-resolution",
           "sjres": "senate-joint-resolution", "hconres": "house-concurrent-resolution",
           "sconres": "senate-concurrent-resolution"}.get(str(row.get("type", "")).lower(), "house-bill")
    return f"https://www.congress.gov/bill/{int(float(row.get('congress') or 119))}th-congress/{seg}/{row.get('number','')}"


def party_chip(p):
    return f'<span class="chip" style="background:{PARTY_COLOR.get(str(p), PARTY_COLOR[""])}">{p or "?"}</span>'


def role_chip(role):
    return f'<span class="chip role-{role}">{ROLE_LABEL.get(role, role)}</span>'


_STOP = {"and", "the", "for", "of", "to", "in", "on", "or", "a", "an"}


def evidence_snippet(summary, subject):
    """Pull the sentence from the summary most relevant to the subject (no AI)."""
    if not summary:
        return ""
    kws = [w for w in re.split(r"\W+", subject.lower()) if len(w) > 3 and w not in _STOP]
    sents = re.split(r"(?<=[.!?])\s+", str(summary))
    for s in sents:
        if any(k in s.lower() for k in kws):
            return s.strip()
    return sents[0].strip() if sents else ""


def render_bill_card(row, subject=None):
    summary = row.get("ai_summary") or row.get("crs_summary") or ""
    pol = row.get("polarity_num")
    act = row.get("action") or ""
    dir_tag = ""
    if act and pd.notna(pol) and pol != 0:
        arrow = "⬆" if pol > 0 else "⬇"
        dir_tag = f' <span class="muted">· {arrow} {act}</span>'
    snippet = evidence_snippet(summary, subject) if subject else (str(summary)[:300])
    st.markdown(
        f"""<div class="billcard">{role_chip(row['role'])}
        <span class="billtitle">{str(row['type']).upper()} {row['number']} — {row.get('title','')}</span>{dir_tag}<br>
        <span class="muted">{row.get('status','')}</span>
        <div class="evid">{snippet}</div>
        <div style="margin-top:6px;"><a href="{congress_url(row)}" target="_blank">View on Congress.gov →</a></div>
        </div>""", unsafe_allow_html=True)


def lean_readout(member_issue_rows):
    """Count directional signals: backed * polarity. Returns (expand, restrict)."""
    exp = res = 0
    for _, r in member_issue_rows.iterrows():
        pol = r.get("polarity_num")
        if pd.isna(pol) or pol == 0:
            continue
        sig = r["backed"] * pol
        if sig > 0:
            exp += 1
        elif sig < 0:
            res += 1
    return exp, res


def lean_chip(exp, res):
    total = exp + res
    if total < DIRECTIONAL_MIN:
        return ""
    if exp and not res:
        return f'<span class="lean expand">⬆ tends to expand/strengthen ({exp})</span>'
    if res and not exp:
        return f'<span class="lean restrict">⬇ tends to restrict/reduce ({res})</span>'
    return f'<span class="lean mixed">mixed record — ⬆ {exp} / ⬇ {res}</span>'


# --------------------------------------------------------------------------- #
try:
    legs, bills, subj, pos, long = load_data()
except Exception as e:
    st.error("Couldn't load data. Build it first:\n\n```\npython mongoose_build_dataset.py "
             "--sample\n```\n\n…then `streamlit run mongoose_app.py`.\n\n"
             f"(detail: {e})")
    st.stop()

st.title("🦡 Mongoose")
st.caption("What each legislator works on and where they lean — grounded in the bills they "
           "sponsor (×5), cosponsor (×3), and vote on (×1). Everything is precomputed; no live AI.")

mode = st.sidebar.radio("View", ["By legislator", "By issue"])
st.sidebar.markdown("---")
st.sidebar.markdown(f"<span class='muted'>{len(legs)} legislators · {len(bills)} bills · "
                    f"{subj['subject'].nunique()} issue tags</span>", unsafe_allow_html=True)
st.sidebar.markdown("<span class='muted'>⬆ expand / ⬇ restrict = whether the member's backed "
                    "bills increase or limit that specific issue.</span>", unsafe_allow_html=True)


# ============================ BY LEGISLATOR ================================= #
if mode == "By legislator":
    opt = legs.assign(label=legs["name"] + " (" + legs["party"] + "-" + legs["state"] + ")").sort_values("name")
    choice = st.selectbox("Choose a legislator", opt["label"].tolist())
    bio = opt.loc[opt["label"] == choice, "bioguide"].iloc[0]
    person = legs.loc[legs["bioguide"] == bio].iloc[0]

    mine = long[long["bioguide"] == bio]
    mypos = pos[pos["bioguide"] == bio]
    c1, c2, c3 = st.columns(3)
    c1.metric("Sponsored", int((mypos["role"] == "sponsor").sum()))
    c2.metric("Cosponsored", int((mypos["role"] == "cosponsor").sum()))
    c3.metric("Votes recorded", int(mypos["role"].isin(["vote_yea", "vote_nay"]).sum()))

    st.markdown(f"### Issues {person['name']} works on most")
    st.caption("Ranked by weighted activity. Each issue shows the member's lean (when there's "
               "enough on that specific subject) and the bills as evidence.")

    rank = (mine[mine["subject"].astype(str) != ""]
            .groupby("subject")
            .agg(score=("weight", "sum"), bills=("bill_id", "nunique"))
            .sort_values("score", ascending=False).reset_index())

    if rank.empty:
        st.info("No issue tags yet. Re-run the builder with --api-key to add CRS subjects.")
    for _, irow in rank.head(20).iterrows():
        sub = irow["subject"]
        rows_for_issue = mine[mine["subject"] == sub].drop_duplicates("bill_id")
        exp, res = lean_readout(rows_for_issue)
        chip = lean_chip(exp, res)
        with st.expander(f"{sub}  ·  {int(irow['bills'])} bills"):
            if chip:
                st.markdown(chip, unsafe_allow_html=True)
            for _, b in rows_for_issue.sort_values("weight", ascending=False).iterrows():
                render_bill_card(b, subject=sub)


# ============================== BY ISSUE =================================== #
else:
    st.markdown("### Search the repository by issue")
    q = st.text_input("Filter issue tags", placeholder="e.g. prescription, border, firearms, Medicaid…")
    all_subj = sorted(subj["subject"].dropna().unique().tolist())
    filtered = [s for s in all_subj if q.lower() in s.lower()] if q else all_subj
    if not filtered:
        st.info("No matching issue tags.")
        st.stop()
    subject = st.selectbox(f"Issue ({len(filtered)} match)", filtered)

    on = long[long["subject"] == subject]
    ranked = (on.groupby("bioguide").agg(score=("weight", "sum"), bills=("bill_id", "nunique"))
              .reset_index().merge(legs, on="bioguide", how="left").sort_values("score", ascending=False))

    st.markdown(f"#### Where legislators stand on “{subject}”")
    pcounts = on.merge(legs, on="bioguide")["party"].value_counts()
    st.markdown("<span class='muted'>Activity by party — " +
                " · ".join(f"{p}: {n}" for p, n in pcounts.items()) + "</span>", unsafe_allow_html=True)

    for _, r in ranked.iterrows():
        member_rows = on[on["bioguide"] == r["bioguide"]].drop_duplicates("bill_id")
        exp, res = lean_readout(member_rows)
        st.markdown("---")
        st.markdown(f"{r['name']} {party_chip(r['party'])} "
                    f"<span class='muted'>{r['state']} · {r['chamber']}</span>  {lean_chip(exp, res)}",
                    unsafe_allow_html=True)
        for _, b in member_rows.sort_values("weight", ascending=False).iterrows():
            render_bill_card(b, subject=subject)
