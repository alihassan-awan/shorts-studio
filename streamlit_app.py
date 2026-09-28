"""Shorts Commentary Studio — Streamlit web app.

Free hosting: Streamlit Community Cloud (https://share.streamlit.io).
Repo files: streamlit_app.py, make_short.py, requirements.txt, packages.txt
Secret: GEMINI_API_KEY  (deployed app ki Settings → Secrets mein add karo)
"""
import os

# httpx (google-genai ke andar) no_proxy mein bracketed IPv6 entries
# masalan [::1] par crash hota hai — imports se pehle env saaf karo.
for _k in ("no_proxy", "NO_PROXY"):
    _v = os.environ.get(_k, "")
    if _v:
        os.environ[_k] = ",".join(
            p for p in _v.split(",")
            if not (p.strip().startswith("[") and p.strip().endswith("]"))
        )

import tempfile

import streamlit as st

import make_short

st.set_page_config(page_title="Shorts Commentary Studio", page_icon="🎬")

st.title("🎬 Shorts Commentary Studio")
st.caption("Short upload karo → AI hook likhegi → "
           "white bar + watermark ke saath ready reel download karo.")

if "hook_box" not in st.session_state:
    st.session_state.hook_box = ""


def get_key():
    try:
        k = st.secrets.get("GEMINI_API_KEY", "")
    except Exception:  # noqa: BLE001 — local run without secrets.toml
        k = ""
    return (k or os.environ.get("GEMINI_API_KEY", "")).strip()


uploaded = st.file_uploader("Short video upload karo",
                            type=["mp4", "mov", "webm", "mkv"])
lang = st.selectbox("Hook ki language", ["roman_urdu", "english"], index=0,
                    help="Roman Urdu Pakistani audience ke liye best hai")
watermark = st.text_input("Watermark (optional)", placeholder="@Ali")

if st.button("✨ Reel Banao", type="primary"):
    key = get_key()
    if not key:
        st.error("GEMINI_API_KEY nahi mili. "
                 "App ki Settings → Secrets mein add karo: "
                 "GEMINI_API_KEY = \"tumhari-key\"")
    elif uploaded is None:
        st.error("Pehle short video upload karo.")
    else:
        suffix = "." + (uploaded.name.rsplit(".", 1)[-1]
                        if "." in uploaded.name else "mp4")
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded.getbuffer())
            in_path = tmp.name
        try:
            with st.spinner("AI video dekh rahi hai..."):
                desc, hook = make_short.analyze(in_path, lang, key,
                                                make_short.DEFAULT_MODEL)
        except Exception as e:  # noqa: BLE001
            errmsg = str(e) or repr(e)
            st.error("❌ AI se jawab nahi mila.")
            low = errmsg.lower()
            if ("api key not valid" in low or "api_key_invalid" in low
                    or "permission_denied" in low):
                st.warning("Lagta hai API key ka masla hai: key ghalat, expire ya "
                           "block ho sakti hai. aistudio.google.com par nayi key "
                           "banao aur Streamlit ki Settings → Secrets mein update "
                           "karke dobara try karo.")
            elif ("429" in low or "too_many_requests" in low
                    or "resource_exhausted" in low or "rate limit" in low):
                st.warning("Free daily limit mukammal ho gayi hai. Kuch der ruk kar "
                           "dobara try karo — limit thodi der mein ya kal reset ho "
                           "jayegi.")
            elif ("503" in low or "high demand" in low or "overloaded" in low
                    or "unavailable" in low):
                st.warning("AI model par is waqt bohat load hai. 2-3 minute ruk kar "
                           "dobara try karo.")
            with st.expander("Asal error — is ka screenshot/text mujhe bhejo"):
                st.code(errmsg[:3000])
            st.stop()
        with st.spinner("Reel render ho rahi hai..."):
            out = tempfile.mktemp(prefix="reel_", suffix=".mp4")
            make_short.render(in_path, hook, watermark.strip(), out)
        st.session_state.in_path = in_path
        st.session_state.hook_box = hook
        st.session_state.desc = desc
        st.session_state.out = out
        st.success("Tayyar! Neeche dekho, hook edit kar sakte ho, phir download karo.")

if st.session_state.get("out"):
    st.video(st.session_state.out)
    st.text_input("AI ne video mein kya dekha",
                  value=st.session_state.get("desc", ""), disabled=True)
    st.text_area("Hook (edit kar sakte ho)", key="hook_box")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔁 Hook edit karke dobara render karo"):
            hook_clean = " ".join(st.session_state.hook_box.split())
            if not hook_clean:
                st.error("Hook khali hai.")
            elif not st.session_state.get("in_path"):
                st.error("Video dobara upload karo.")
            else:
                with st.spinner("Dobara render ho raha hai..."):
                    out = tempfile.mktemp(prefix="reel_", suffix=".mp4")
                    make_short.render(st.session_state.in_path, hook_clean,
                                      watermark.strip(), out)
                st.session_state.out = out
                st.success("Nayi reel tayyar!")
    with c2:
        with open(st.session_state.out, "rb") as f:
            st.download_button("⬇️ Reel download karo", f.read(),
                               file_name="ready_reel.mp4", mime="video/mp4")

st.divider()
st.caption("Tip: free hosting par app agar der tak idle rahe to so jati hai — "
           "page kholne/refresh par khud jaag jati hai (30-60 second).")
