# Recording the STT evaluation clips (10 English + 10 Tamil)

These recordings are **yours** — they are what makes the WER number real for
your voice, your microphone and your room. `*.wav` is git-ignored, so nothing is
uploaded or committed.

## Requirements

* **16 kHz, mono, 16-bit PCM WAV**
* 2–5 seconds per clip, spoken at a normal pace
* One sentence per clip, no long pauses
* Same microphone and distance as real use (this is a *usage* test, not a studio test)

### Recording on Windows 11

1. **Voice Recorder** (built in) → record each sentence → the files are `.m4a`.
2. Convert with `ffmpeg` (a copy ships with most Piper/whisper installs):

   ```powershell
   ffmpeg -i clip1.m4a -ar 16000 -ac 1 -acodec pcm_s16le en\01_open_vscode.wav
   ```

3. Write the transcript next to the clip as `01_open_vscode.txt`.

### Recording on Linux

```bash
arecord -f S16_LE -r 16000 -c 1 -d 4 en/01_open_vscode.wav
```

### Faster alternative: let the project transcribe nothing, just record prompts

Read the sentence below aloud, then type **exactly what you said** into the
`.txt` sidecar. If you add or drop a word while speaking, write what you actually
said — WER compares against the reference you provide.

## Folder layout

```text
eval/stt_clips/
├── README.md                 # this file
├── metadata.example.jsonl    # optional single-file reference list
├── en/
│   ├── 01_open_vscode.wav
│   └── 01_open_vscode.txt    # "Jarvis, open VS Code"
└── ta/
    ├── 01_time.wav
    └── 01_time.txt           # "ஜார்விஸ், நேரம் என்ன"
```

If you prefer one metadata file over `.txt` sidecars, copy
`metadata.example.jsonl` to `metadata.jsonl` and pass it with `--references`.

## The 10 English sentences

| # | Say this | Suggested file |
|---|---|---|
| 1 | Jarvis, open VS Code. | `en/01_open_vscode.wav` |
| 2 | Jarvis, check my laptop performance. | `en/02_laptop_performance.wav` |
| 3 | Jarvis, set the volume to 45 percent. | `en/03_volume_45.wav` |
| 4 | Jarvis, organize my Downloads folder. | `en/04_organize_downloads.wav` |
| 5 | Jarvis, find my Blender project. | `en/05_find_blender.wav` |
| 6 | Jarvis, take a screenshot. | `en/06_screenshot.wav` |
| 7 | Jarvis, what time is it? | `en/07_time.wav` |
| 8 | Jarvis, set a timer for ten minutes. | `en/08_timer_ten.wav` |
| 9 | Jarvis, search the web for python asyncio tutorial. | `en/09_web_search.wav` |
| 10 | Jarvis, how much disk space is left? | `en/10_disk_space.wav` |

## The 10 Tamil sentences

| # | Say this | Suggested file |
|---|---|---|
| 1 | ஜார்விஸ், நேரம் என்ன? | `ta/01_time.wav` |
| 2 | ஜார்விஸ், ஸ்கிரீன்ஷாட் எடு. | `ta/02_screenshot.wav` |
| 3 | ஜார்விஸ், நோட்பேடைத் திற. | `ta/03_open_notepad.wav` |
| 4 | ஜார்விஸ், டவுன்லோட்ஸ் கோப்புறையை ஒழுங்குபடுத்து. | `ta/04_organize_downloads.wav` |
| 5 | ஜார்விஸ், வால்யூமை ஐம்பது சதவீதமாக வை. | `ta/05_volume_50.wav` |
| 6 | ஜார்விஸ், என் லேப்டாப் ஸ்பெக்களைச் சொல். | `ta/06_laptop_specs.wav` |
| 7 | ஜார்விஸ், பத்து நிமிட டைமர் வை. | `ta/07_timer_ten.wav` |
| 8 | ஜார்விஸ், பிளெண்டர் திட்டத்தைக் கண்டுபிடி. | `ta/08_find_blender.wav` |
| 9 | ஜார்விஸ், இன்று என்ன தேதி? | `ta/09_date.wav` |
| 10 | ஜார்விஸ், டெஸ்க்டாப் கோப்புகளைக் காட்டு. | `ta/10_desktop_files.wav` |

Then run:

```powershell
python eval/run_stt_eval.py --audio-dir eval/stt_clips/en --lang en --out-dir eval/results
python eval/run_stt_eval.py --audio-dir eval/stt_clips/ta --lang ta --out-dir eval/results
```

Optional second pass: repeat all 20 clips **with a fan or background noise** to
measure the noisy-room degradation quoted in the README limitations section.
