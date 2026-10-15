# Presenter tutorial video

The tutorial explains the end-to-end workflow on six slides, then walks through 45 screens of the live demo with a voice-over.
The finished MP4 and subtitles are delivered separately; they are not stored in git.

| File | What it is |
|---|---|
| `narration.py` | The narration, one entry per screen: image id, on-screen caption, spoken text. Edit this to change what is said. |
| `slides.html` | The six workflow slides (white background, navy and gold, Calibri / Carlito). |
| `capture.cjs` | Drives the running demo through the story and saves 1920×1080 screenshots. The app is rendered at 125% so text is legible. |
| `make_video.py` | Voices each segment with the Kokoro neural TTS, builds one clip per screen, joins them, and writes `tutorial.srt` and `narration_script.md`. |

## Regenerate

```bash
./start.sh --reset                                   # fresh demo data
cd tutorial
node capture.cjs ../frames http://127.0.0.1:8090     # needs Node + Playwright with Chromium
pip install kokoro-onnx soundfile                    # once
# once: download kokoro-v1.0.onnx and voices-v1.0.bin from
#   https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0
python make_video.py ../frames ../video_work kokoro-v1.0.onnx voices-v1.0.bin af_heart
```

To change the voice, pass another Kokoro voice as the last argument, e.g. `bf_emma` (British female) or `bm_george` (British male).
The `SAY` list in `narration.py` fixes pronunciations, such as DPDP spoken as "D P D P" and ABHA as "Aabha".
