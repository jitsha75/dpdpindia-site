"""Builds the presenter tutorial video from frames (capture.cjs) and narration (narration.py) with the Kokoro neural voice.
Usage: python make_video.py <frames_dir> <work_dir> <kokoro.onnx> <voices.bin> [voice]
Output in <work_dir>: tutorial.mp4, tutorial.srt, narration_script.md"""
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

from narration import SEGMENTS, speakable

FRAMES, WORK, MODEL, VOICES = map(Path, sys.argv[1:5])
VOICE = sys.argv[5] if len(sys.argv) > 5 else "af_heart"
WORK.mkdir(parents=True, exist_ok=True)
SR, LEAD, TAIL = 24000, 0.35, 0.75          # silence before / after each segment (seconds)


def run(*a):
    subprocess.run(a, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def ts(t):
    h, rem = divmod(t, 3600); m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"


kok = Kokoro(str(MODEL), str(VOICES))
clips, srt, md, t0, n = [], [], ["# Presenter tutorial - narration script\n"], 0.0, 1
for i, (img, caption, text) in enumerate(SEGMENTS):
    wav = WORK / f"{i:02d}_{img}.wav"
    if not wav.exists():
        parts = [s for s in re.split(r"(?<=[.!?])\s+", speakable(text)) if s.strip()]
        audio = []
        for s in parts:
            a, sr = kok.create(s, voice=VOICE, speed=0.98, lang="en-us")
            audio += [a, np.zeros(int(0.18 * sr), dtype=a.dtype)]
        a = np.concatenate([np.zeros(int(LEAD * SR), dtype=np.float32)] + audio + [np.zeros(int(TAIL * SR), dtype=np.float32)])
        sf.write(wav, a, SR)
    dur = sf.info(wav).duration
    clip = WORK / f"{i:02d}_{img}.mp4"
    if not clip.exists():
        run("ffmpeg", "-y", "-loop", "1", "-framerate", "25", "-i", str(FRAMES / f"{img}.png"), "-i", str(wav),
            "-vf", f"scale=1920:1080,format=yuv420p,fade=t=in:st=0:d=0.3:color=white,fade=t=out:st={dur - 0.3:.2f}:d=0.3:color=white",
            "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage", "-crf", "20", "-r", "25",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1", "-t", f"{dur:.3f}", str(clip))
    clips.append(clip)
    # subtitles: one cue per sentence, timed by character share
    sents = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    total = sum(len(s) for s in sents)
    t = t0 + LEAD
    speak = dur - LEAD - TAIL
    for s in sents:
        d = speak * len(s) / total
        srt.append(f"{n}\n{ts(t)} --> {ts(t + d)}\n{s}\n"); n += 1; t += d
    md.append(f"## {ts(t0)[:8]} - {caption}\n\n*Screen: `{img}`*\n\n{text}\n")
    print(f"{i:02d} {img:5s} {dur:5.1f}s  {caption}", flush=True)
    t0 += dur

lst = WORK / "clips.txt"
lst.write_text("".join(f"file '{c.name}'\n" for c in clips))
out = WORK / "tutorial.mp4"
run("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out))
(WORK / "tutorial.srt").write_text("\n".join(srt))
(WORK / "narration_script.md").write_text("\n".join(md))
print(f"done: {out} ({t0 / 60:.1f} min)")
