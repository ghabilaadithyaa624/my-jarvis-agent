# `docs/` — media capture instructions

This folder holds the two pieces of media the main README links to. They are
**not committed yet** because they must be real recordings of the real app on
real hardware — no mock-ups, no generated screenshots, no synthetic GIFs.

Expected files:

| File | What it shows |
|---|---|
| `demo.gif` | ~15 s end-to-end voice demo on Windows 11 |
| `screenshot-hud.png` | the PySide6 dark HUD, full window |

---

## 1. `screenshot-hud.png`

1. Launch the HUD: `scripts\run_jarvis.bat`.
2. Make sure Ollama is running and a model is selected (header dropdown shows it).
3. Say/type one safe command, e.g. *"Jarvis, what time is it?"*, so the HUD shows a real,
   verified conversation — not a blank screen.
4. Capture **only the JARVIS window** (Win+Shift+S → window mode, or ShareX → active window).
5. Save as PNG at the window's native size (1200×700 by default — see `gui.window_width` /
   `gui.window_height` in `jarvis/config/default_config.yaml`). Do not upscale, do not add fake chrome.

## 2. `demo.gif`

Record this exact flow so the GIF demonstrates the security model, not just a chat bubble:

1. Start the HUD, click **Start Listening** (wake-word mode).
2. Say: *"Jarvis, open VS Code."* → tool runs, result badge shows `VERIFIED`.
3. Say: *"Jarvis, delete the file old_notes.txt."* → the **confirmation dialog** appears
   (this frame is the important one — let it sit for ~2 s).
4. Click **Deny** → JARVIS reports the action was declined, file still present.
5. Say: *"Jarvis, check my laptop performance."* → live CPU/RAM card updates.
6. Stop recording, trim to ~15 s, export as GIF ≤ 5 MB (720p, 10–12 fps).

Suggested tools: **ScreenToGif** (Windows, simplest) or **OBS** → `ffmpeg`:

```powershell
ffmpeg -i demo.mp4 -vf "fps=12,scale=1280:-1:flags=lanczos" -loop 0 docs\demo.gif
```

## 3. Keep the README honest

After adding the files, the README demo section already references them. If a clip
is re-recorded after a UI change, re-record both files in the same session so they
match the current build.
