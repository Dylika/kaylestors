#!/usr/bin/env python3
"""Render a 9:16 text-reveal story reel: post-card, word-by-word reveal, scrolling text,
highlighted keywords, hook cover, soft music (+ optional voiceover).

Usage:
  python make_reel.py story.json out_dir [--voiceover vo.mp3] [--music m.mp3] [--theme cream|maroon] [--fps 24]

story.json:
{
  "title": "The Key Under the Flowerpot",
  "hook": "At 65, I found the letter my daughter hid from me for 30 years.",
  "narrator": "Margaret Ellis",          # shown on the card header
  "label": "Fictional story",            # optional
  "logo": "DSR2",                        # optional small corner logo
  "story": "Text with **highlighted phrases** in double asterisks. Blank line = pause."
}
Outputs: reel.mp4, thumbnail.png, caption.txt (the story text for comments is in story.txt)
"""
import json, math, re, subprocess, sys, os, argparse, wave
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
THEMES = {
    "cream":  dict(bg=(236, 224, 205), card=(253, 248, 238), text=(45, 36, 32), faint=(222, 210, 192),
                   hl=(176, 38, 38), hl2=(24, 110, 120), mark=(255, 232, 140), sub=(120, 105, 95), accent=(176, 38, 38)),
    "maroon": dict(bg=(40, 10, 16), card=(78, 18, 30), text=(246, 236, 220), faint=(112, 52, 62),
                   hl=(240, 190, 80), hl2=(90, 210, 200), mark=None, sub=(205, 175, 150), accent=(240, 190, 80)),
}
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu/"]
def font(bold, size):
    name = "DejaVuSerif-Bold.ttf" if bold else "DejaVuSerif.ttf"
    for d in FONT_DIRS:
        if os.path.exists(d + name):
            return ImageFont.truetype(d + name, size)
    return ImageFont.load_default()

def parse_story(text):
    """-> list of (word, highlighted, para_break_after)"""
    words = []
    for p_i, para in enumerate([p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]):
        hl = False
        para_words = []
        for chunk in para.replace("\n", " ").split():
            txt, any_hl = "", False
            for piece in re.split(r"(\*\*)", chunk):
                if piece == "**":
                    hl = not hl; continue
                txt += piece
                if hl and any(c.isalnum() for c in piece): any_hl = True
            if txt: para_words.append([txt, any_hl, False])
        if para_words:
            para_words[-1][2] = True
            words += para_words
    return words

def layout(words, f_reg, f_bold, max_w, line_h, para_gap):
    """Assign x,y per word. Returns positions list and total height."""
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    space = d.textlength(" ", font=f_reg)
    x = y = 0; pos = []
    for w, hl, brk in words:
        ww = d.textlength(w, font=f_bold if hl else f_reg)
        if x + ww > max_w and x > 0:
            x = 0; y += line_h
        pos.append((x, y, ww)); x += ww + space
        if brk:
            x = 0; y += line_h + para_gap
    return pos, y + line_h

def wrap_center(draw, text, f, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=f) <= max_w: cur = t
        else: lines.append(cur); cur = w
    if cur: lines.append(cur)
    return lines

def draw_header(img, d, th, narrator, label, logo):
    cx, cy, r = 150, 190, 62
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=th["accent"])
    ini = "".join(p[0] for p in narrator.split()[:2]).upper() or "G"
    f = font(True, 56); tw = d.textlength(ini, font=f)
    d.text((cx - tw / 2, cy - 36), ini, font=f, fill=th["card"])
    d.text((250, 140), narrator, font=font(True, 52), fill=th["text"])
    d.text((250, 208), label, font=font(False, 34), fill=th["sub"])
    d.line([(90, 300), (W - 90, 300)], fill=th["faint"], width=3)
    if logo:
        fl = font(True, 30); tw = d.textlength(logo, font=fl)
        d.text((W - 90 - tw, H - 120), logo, font=fl, fill=th["sub"])

def base_card(th, narrator, label, logo):
    img = Image.new("RGB", (W, H), th["bg"]); d = ImageDraw.Draw(img)
    d.rounded_rectangle([50, 90, W - 50, H - 90], radius=48, fill=th["card"])
    draw_header(img, d, th, narrator, label, logo)
    return img

def draw_marked(d, th, x, y, w, size, bold, text, hl, f):
    if hl and th["mark"]:
        d.rectangle([x - 4, y + 6, x + w + 4, y + size + 14], fill=th["mark"])
    col = (th["hl"] if hl else th["text"])
    d.text((x, y), text, font=f, fill=col)

def hook_card(th, hook, narrator, label, logo):
    img = base_card(th, narrator, label, logo); d = ImageDraw.Draw(img)
    f = font(True, 78); lines = wrap_center(d, hook, f, W - 260)
    y = 420
    for i, ln in enumerate(lines):
        tw = d.textlength(ln, font=f)
        d.text(((W - tw) / 2, y), ln, font=f, fill=th["hl"] if i % 2 else th["text"]); y += 120
    cta = "Listen to her story  ▶"
    fc = font(True, 44); tw = d.textlength(cta, font=fc)
    d.rounded_rectangle([(W - tw) / 2 - 40, H - 420, (W + tw) / 2 + 40, H - 320], radius=50, fill=th["accent"])
    d.text(((W - tw) / 2, H - 400), cta, font=fc, fill=th["card"])
    return img

def synth_music(path, dur, sr=44100, seed=7):
    """Gentle piano-ish arpeggio over a soft pad. Offline, no samples needed."""
    rng = np.random.default_rng(seed)
    t_total = int(sr * (dur + 2)); out = np.zeros(t_total, dtype=np.float32)
    # Am - F - C - G, slow
    chords = [[57, 60, 64, 69], [53, 57, 60, 65], [48, 52, 55, 60], [55, 59, 62, 67]]
    f = lambda m: 440.0 * 2 ** ((m - 69) / 12)
    step = 0.62; n = 0; t0 = 0.0
    while t0 < dur + 1.5:
        ch = chords[(n // 8) % 4]; note = ch[[0, 1, 2, 3, 2, 1, 3, 2][n % 8]]
        L = int(sr * 2.6); tt = np.arange(L) / sr
        env = np.exp(-tt * 2.1) * (1 - np.exp(-tt * 60))
        s = sum(a * np.sin(2 * np.pi * f(note) * k * tt) for k, a in [(1, 1), (2, .35), (3, .12), (4, .05)])
        i = int(t0 * sr); seg = (s * env * 0.16).astype(np.float32)
        out[i:i + L] += seg[: max(0, t_total - i)]
        if n % 8 == 0:  # low pad root
            Lp = int(sr * step * 8); tp = np.arange(Lp) / sr
            pad = np.sin(2 * np.pi * f(ch[0] - 12) * tp) * 0.08 * np.minimum(1, tp * 2) * np.minimum(1, (Lp / sr - tp) * 2)
            out[i:i + Lp] += pad[: max(0, t_total - i)].astype(np.float32)
        n += 1; t0 += step
    out /= max(1e-6, np.abs(out).max()) / 0.8
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((out * 32767).astype(np.int16).tobytes())

def probe_dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return float(r.stdout.strip())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("story_json"); ap.add_argument("out_dir")
    ap.add_argument("--voiceover"); ap.add_argument("--music")
    ap.add_argument("--theme", default="cream", choices=THEMES); ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--wps", type=float, default=2.3, help="reading speed when no voiceover (words/sec)")
    a = ap.parse_args()
    cfg = json.load(open(a.story_json)); os.makedirs(a.out_dir, exist_ok=True)
    th = THEMES[a.theme]; narrator = cfg.get("narrator", "Grandma"); label = cfg.get("label", "Fictional story")
    logo = cfg.get("logo", ""); hook = cfg["hook"]
    words = parse_story(cfg["story"]); n = len(words)

    HOOK_S, TAIL_S = 2.5, 2.0
    story_dur = probe_dur(a.voiceover) if a.voiceover else n / a.wps
    # weight each word by length + pause after paragraph/sentence so reveal tracks speech
    wt = []
    for w, hl, brk in words:
        base = 0.6 + min(len(w), 12) / 12 * 0.8
        if w[-1] in ".!?": base += 1.2
        elif w[-1] in ",;:": base += 0.5
        if brk: base += 1.0
        wt.append(base)
    cum = np.cumsum(wt); starts = HOOK_S + (cum - wt) / cum[-1] * story_dur
    total = HOOK_S + story_dur + TAIL_S
    nf = int(total * a.fps)

    f_reg, f_bold = font(False, 56), font(True, 56); line_h = 92; para_gap = 46
    text_w = W - 2 * 130
    pos, th_h = layout(words, f_reg, f_bold, text_w, line_h, para_gap)
    view_top, view_h = 340, H - 340 - 190
    anchor = view_h * 0.55

    base = base_card(th, narrator, label, logo)
    hook_img = hook_card(th, hook, narrator, label, logo)
    hook_img.save(os.path.join(a.out_dir, "thumbnail.png"))

    cache = {}
    def text_layer(k):  # k = number of revealed words
        if k in cache: return cache[k]
        lay = Image.new("RGBA", (text_w + 20, th_h + 40), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
        for i, (w, hl, _) in enumerate(words):
            x, y, ww = pos[i]; fnt = f_bold if hl else f_reg
            if i < k: draw_marked(d, th, x, y, ww, 56, hl, w, hl, fnt)
            else: d.text((x, y), w, font=fnt, fill=th["faint"])
        cache.clear(); cache[k] = lay
        return lay

    scroll = 0.0
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(a.fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", os.path.join(a.out_dir, "_video.mp4")]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for fi in range(nf):
        t = fi / a.fps
        if t < HOOK_S:
            frame = hook_img
        else:
            k = int(np.searchsorted(starts, t, side="right"))  # words revealed
            cur = max(0, min(n - 1, k - 1))
            target = max(0.0, pos[cur][1] - anchor)
            scroll += (target - scroll) * 0.12  # smooth continuous scroll
            lay = text_layer(k)
            frame = base.copy()
            crop = lay.crop((0, int(scroll), text_w + 20, int(scroll) + view_h))
            frame.paste(crop, (130, view_top), crop)
            # soft fade at top/bottom edges of the text window
            d = ImageDraw.Draw(frame)
            for j in range(40):
                c = tuple(int(th["card"][q]) for q in range(3))
                d.line([(70, view_top + j), (W - 70, view_top + j)], fill=c) if scroll > 5 and j < 14 else None
            if logo:
                fl = font(True, 30); tw = d.textlength(logo, font=fl)
                d.text((W - 90 - tw, H - 120), logo, font=fl, fill=th["sub"])
        p.stdin.write(frame.convert("RGB").tobytes())
    p.stdin.close(); p.wait()

    music = a.music
    if not music:
        music = os.path.join(a.out_dir, "_music.wav"); synth_music(music, total)
    vid = os.path.join(a.out_dir, "_video.mp4"); out = os.path.join(a.out_dir, "reel.mp4")
    fade = f"afade=t=in:d=1.5,afade=t=out:st={max(0, total - 2.5):.2f}:d=2.5"
    if a.voiceover:
        fc = (f"[1:a]volume=0.22,{fade},aloop=loop=-1:size=2e9[m];"
              f"[2:a]adelay={int(HOOK_S * 1000)}|{int(HOOK_S * 1000)}[v];"
              f"[m][v]amix=inputs=2:duration=longest:normalize=0,atrim=0:{total:.2f}[a]")
        ins = ["-i", vid, "-i", music, "-i", a.voiceover]
    else:
        fc = f"[1:a]volume=0.8,{fade},atrim=0:{total:.2f}[a]"; ins = ["-i", vid, "-i", music]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + ins + ["-filter_complex", fc, "-map", "0:v", "-map", "[a]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", out], check=True)
    for tmp in ("_video.mp4", "_music.wav"):
        try: os.remove(os.path.join(a.out_dir, tmp))
        except OSError: pass
    plain = " ".join(w for w, _, _ in words)
    open(os.path.join(a.out_dir, "story.txt"), "w").write(re.sub(r"\*\*", "", cfg["story"]).strip() + "\n")
    open(os.path.join(a.out_dir, "caption.txt"), "w").write(hook + "\n\n(" + label + ")\n")
    print(f"OK reel.mp4 {total:.1f}s, {n} words, voiceover={'yes' if a.voiceover else 'no'}")

if __name__ == "__main__":
    main()
