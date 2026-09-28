#!/usr/bin/env python3
"""
Shorts Commentary Studio
========================
Ek short video do -> AI usay dekh kar catchy hook likhti hai ->
white background par text upar lagta hai + chhota watermark ->
publish-ready reel tayyar.

Usage:
    GEMINI_API_KEY=... .venv/bin/python make_short.py input.mp4 -o ready.mp4 \
        --lang roman_urdu --watermark "@Ali"

    # AI ke baghair (manual hook, testing ke liye):
    .venv/bin/python make_short.py input.mp4 -o ready.mp4 \
        --hook "Ye dekh kar aap hairan reh jayenge" --watermark "@Ali"
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

STUDIO_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_font():
    cands = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/fonts-dejavu/DejaVuSans-Bold.ttf",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    try:
        r = subprocess.run(["fc-match", "DejaVu Sans:bold", "--format=%{file}"],
                           capture_output=True, text=True, timeout=10)
        p = r.stdout.strip().split("\n")[0]
        if p and os.path.exists(p):
            return p
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError("Bold font nahi mila. fonts-dejavu install karo.")


FONT = _find_font()


def ffmpeg_bin():
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError("ffmpeg nahi mila (packages.txt mein ffmpeg add karo).")
DEFAULT_MODEL = "gemini-3.5-flash-lite"
MODEL_CANDIDATES = ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash"]

LANG_LABELS = {
    "roman_urdu": "Roman Urdu — Urdu written in the Latin/English alphabet, "
                  "the way Pakistanis type on WhatsApp "
                  "(for example: 'Ye dekh kar aap hairan reh jayenge')",
    "urdu": "Urdu written in Urdu script",
    "english": "simple, punchy, everyday English",
}

PROMPT = """You are an expert viral YouTube Shorts copywriter from Pakistan.

You are given a short vertical video. Do two things:

1. Understand it fully: what is happening, who or what is in it, the mood
   (funny, emotional, shocking, informative...), and any speech or on-screen
   text worth noting.

2. Write ONE hook line for a text overlay that will appear at the TOP of the
   video on a white background. Rules for the hook:
   - Maximum 10 words.
   - Written in {LANG}.
   - It must make a scrolling viewer STOP and watch: use a curiosity gap,
     a bold claim, a relatable punchline, or a direct call to curiosity.
   - It must truthfully match what actually happens in the video
     (no lying, no clickbait the video cannot deliver).
   - No hashtags. No emojis. No quotation marks around it.

Respond with ONLY this JSON, nothing else:
{{"description": "<one sentence describing the video>", "hook": "<the hook line>"}}
"""


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("Command failed: %s\n%s" % (" ".join(cmd), r.stderr[-2000:]))
    return r


def probe(path):
    fp = shutil.which("ffprobe")
    if not fp:
        return {"width": 0, "height": 0, "duration": 0}
    r = run([fp, "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", path])
    info = json.loads(r.stdout)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    return {
        "width": int(vs["width"]),
        "height": int(vs["height"]),
        "duration": float(info["format"].get("duration", 0)),
    }


def _sanitize_proxy_env():
    # httpx (google-genai ke andar) no_proxy mein bracketed IPv6 entries
    # masalan [::1] par crash hota hai. Sirf wahi entries hatate hain,
    # proxy khud rehne dete hain taake Google API tak rasaai rahe.
    for k in ("no_proxy", "NO_PROXY"):
        v = os.environ.get(k, "")
        if v:
            os.environ[k] = ",".join(
                p for p in v.split(",")
                if not (p.strip().startswith("[") and p.strip().endswith("]"))
            )


def _guess_mime(path):
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp4": "video/mp4",
        ".mov": "video/mov",
        ".webm": "video/webm",
        ".avi": "video/avi",
        ".3gp": "video/3gpp",
        ".mkv": "video/mp4",
    }.get(ext, "video/mp4")


def _interaction_text(client, model, video_uri, mime, prompt):
    """Interactions API (Google ka recommended tareeqa) se video analyze karo."""
    inter = client.interactions.create(
        model=model,
        input=[
            {"type": "video", "mime_type": mime, "uri": video_uri},
            {"type": "text", "text": prompt},
        ],
        timeout=300.0,
    )
    text = (getattr(inter, "output_text", None) or "").strip()
    if not text:
        steps = getattr(inter, "steps", None) or []
        for st in reversed(steps):
            for c in (getattr(st, "content", None) or []):
                t = (getattr(c, "text", None) or "").strip()
                if t:
                    text = t
                    break
            if text:
                break
    if not text:
        raise RuntimeError("Interactions API ne khaali jawab diya (model=%s)" % model)
    return text


def analyze(path, lang, api_key, model):
    _sanitize_proxy_env()
    from google import genai
    client = genai.Client(api_key=api_key)
    print("Video AI ko bhej raha hoon, analysis ho rahi hai...", flush=True)
    up = client.files.upload(file=path)
    up = client.files.get(name=up.name)
    deadline = time.time() + 300
    while up.state.name == "PROCESSING":
        if time.time() > deadline:
            raise TimeoutError("AI video processing timeout (5 min)")
        time.sleep(3)
        up = client.files.get(name=up.name)
    if up.state.name != "ACTIVE":
        raise RuntimeError("Video AI ke liye ready nahi hui (state=%s)" % up.state.name)

    prompt = PROMPT.format(LANG=LANG_LABELS[lang])
    mime = _guess_mime(path)
    models = [model] + [m for m in MODEL_CANDIDATES if m != model]
    text, last = None, None
    for m in models:
        # Interactions API (Google ka recommended tareeqa) — har model par
        # sirf 1 call taake free quota zaya na ho.
        try:
            text = _interaction_text(client, m, up.uri, mime, prompt)
            print("Model used: %s" % m, flush=True)
            break
        except Exception as e:  # noqa: BLE001
            last = e
            msg = str(e)
            print("fail [%s]: %s" % (m, msg[:300]), flush=True)
            if ("400" in msg or "404" in msg or "NOT_FOUND" in msg
                    or "INVALID_ARGUMENT" in msg):
                # Ho sakta hai input/method ka masla ho — purana tareeqa try karo
                try:
                    resp = client.models.generate_content(model=m, contents=[up, prompt])
                    text = (resp.text or "").strip()
                    if not text:
                        raise RuntimeError("generateContent ne khaali jawab diya")
                    print("Model used (generate_content): %s" % m, flush=True)
                    break
                except Exception as e2:  # noqa: BLE001
                    last = e2
                    print("generate_content fail [%s]: %s" % (m, str(e2)[:300]),
                          flush=True)
            # 503/429 (load ya quota) par foran agla model — doosra tareeqa
            # try karna quota zaya karega.
            continue
    if text is None:
        # Aakhri koshish: available models khud dhoond kar ek flash model try karo
        try:
            avail = [m.name for m in client.models.list()]
            flashes = [n for n in avail if "flash" in n.lower()]
            pick = flashes[0] if flashes else (avail[0] if avail else None)
            if pick:
                print("Khud model dhoonda: %s" % pick, flush=True)
                text = _interaction_text(client, pick, up.uri, mime, prompt)
                models.append(pick)
        except Exception as e:  # noqa: BLE001
            last = e
            print("dynamic pick fail: %s" % str(e)[:300], flush=True)
    if text is None:
        raise RuntimeError(
            "AI se jawab nahi mila. Tried models: %s. Last error: %s"
            % (", ".join(models), str(last)[:600]))
    try:
        client.files.delete(name=up.name)
    except Exception:  # noqa: BLE001
        pass
    if text.startswith("```"):
        text = text.strip().strip("`").strip()
        if text[:4].lower() == "json":
            text = text[4:].strip()
    data = json.loads(text)
    hook = str(data["hook"]).strip().strip('"').strip()
    desc = str(data.get("description", "")).strip()
    if not hook:
        raise RuntimeError("AI ne hook nahi diya, dobara try karo.")
    return desc, hook


def render(inp, hook, watermark, out):
    # Style: full-width white bar at the very top (trending commentary format),
    # bold black text centered inside it. Font size auto-shrinks until the
    # longest line fits (measured, not estimated).
    from PIL import ImageFont
    max_w = 940
    fs, lines = 72, []
    while fs >= 36:
        font = ImageFont.truetype(FONT, fs)
        tmp, cur = [], ""
        for w in hook.split():
            t = (cur + " " + w).strip()
            if font.getlength(t) <= max_w:
                cur = t
            else:
                if cur:
                    tmp.append(cur)
                cur = w
        if cur:
            tmp.append(cur)
        if tmp and all(font.getlength(l) <= max_w for l in tmp):
            lines = tmp
            break
        fs -= 4
    if not lines:  # ultra-long single word fallback
        fs = 36
        lines = textwrap.wrap(hook, width=20) or [hook]

    bar_h = len(lines) * (fs + 16) + 70
    tmpd = tempfile.mkdtemp(prefix="shorts_")
    hook_file = os.path.join(tmpd, "hook.txt")
    with open(hook_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    vf = [
        "scale=1080:1920:force_original_aspect_ratio=increase",
        "crop=1080:1920",
        # Full-width white bar at the top
        "drawbox=x=0:y=0:w=iw:h=%d:color=white:t=fill" % bar_h,
        # Hook text centered inside the bar
        "drawtext=fontfile=%s:textfile=%s:fontsize=%d:fontcolor=black"
        ":x=(w-text_w)/2:y=35:line_spacing=16:expansion=none"
        % (FONT, hook_file, fs),
    ]
    if watermark:
        wm_file = os.path.join(tmpd, "wm.txt")
        with open(wm_file, "w", encoding="utf-8") as f:
            f.write(watermark)
        vf.append(
            "drawtext=fontfile=%s:textfile=%s:fontsize=34:fontcolor=white"
            ":box=1:boxcolor=black@0.45:boxborderw=14"
            ":x=w-text_w-36:y=h-text_h-54:expansion=none" % (FONT, wm_file)
        )

    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-i", inp,
           "-vf", ",".join(vf),
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart", "-shortest", out]
    print("Rendering...", flush=True)
    run(cmd)


def main():
    ap = argparse.ArgumentParser(description="Shorts Commentary Studio")
    ap.add_argument("input", help="input short video (mp4)")
    ap.add_argument("-o", "--output", default="ready_reel.mp4")
    ap.add_argument("--lang", default="roman_urdu", choices=list(LANG_LABELS))
    ap.add_argument("--watermark", default="", help="chhota watermark text, masalan @Ali")
    ap.add_argument("--hook", default="", help="manual hook (AI skip ho jayegi)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not args.hook and not api_key:
        sys.exit("GEMINI_API_KEY nahi mili. Ya --hook do, ya env var set karo.")
    if not os.path.exists(FONT):
        sys.exit("Font nahi mila: %s" % FONT)
    if args.lang == "urdu":
        sys.exit("Urdu script ke liye Urdu font darkar hai (filhal installed nahi). "
                 "roman_urdu ya english use karo.")
    if not os.path.exists(args.input):
        sys.exit("Input file nahi mili: %s" % args.input)

    info = probe(args.input)
    print("Input: %dx%d, %.1f sec" % (info["width"], info["height"], info["duration"]),
          flush=True)

    if args.hook:
        desc, hook = "(manual hook)", " ".join(args.hook.split())
    else:
        desc, hook = analyze(args.input, args.lang, api_key, args.model)
        print("Video samajh aagayi: %s" % desc, flush=True)
        print("Hook: %s" % hook, flush=True)

    render(args.input, hook, args.watermark, args.output)
    print("Tayyar! Output: %s" % args.output, flush=True)


if __name__ == "__main__":
    main()
