# Why the rendered mp4 has no audio in VS Code

**Status: file exonerated, VS Code root cause NOT confirmed.**

Written up because I proposed three explanations and disproved all three. The
useful output is the elimination, not a diagnosis.

## The file is correct

Every check passed on `out/eli-health.mp4`:

| Check | Result |
|---|---|
| Audio stream present | `aac, 48 kHz, stereo, 317 kbps, 23.64s` |
| macOS `afinfo` | 1,108 packets, 1,134,592 valid frames |
| Decoded amplitude | peak **18352/32767** (−5 dBFS), RMS 2830 |
| Non-silent samples | **64.2%** above the noise floor |
| Container | faststart — `ftyp → moov → free → mdat` |
| QuickTime | **plays with audio** |

It is a well-formed, faststart, non-silent H.264/AAC file.

## Three theories, all wrong

**1. "VS Code's Electron lacks proprietary codecs."** False.

```
libffmpeg.dylib (Electron 42.8.0, VS Code 1.133.0 stable)
  "AAC (Advanced Audio Coding)"              ×2
  "H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10" ×2
Electron Framework media allowlist:
  mp4a.40.2, avc1, audio/mp4, video/mp4
```

Both decoders are compiled in and both MIME types are allowlisted. My first
grep used symbol names (`ff_aac_decoder`) that aren't retained in the binary,
which produced a false negative — the decoder *long names* are the reliable
marker.

**2. "The preview element is muted."** Not by default.

`extensions/media-preview/media/videoPreview.js`:

```js
video.playsInline = true;
video.controls    = true;
video.autoplay    = settings.autoplay;
video.muted       = settings.autoplay;   // mute is hard-wired to autoplay
video.loop        = settings.loop;
```

`mediaPreview.video.autoPlay` defaults to `false` and is unset in this user's
config, so `muted` is `false`.

**This is still worth knowing:** enabling `mediaPreview.video.autoPlay` silently
force-mutes every video preview, because the mute flag is tied to the autoplay
flag to satisfy Chromium's autoplay policy — and it is never cleared after load.
If audio ever disappears from VS Code previews, check that setting first.

**3. "moov atom is at the end, so Chromium can't stream it."** False — the file
is already faststart (`moov` at offset 32, before `mdat`).

## What's left

Untested candidates, in rough order of likelihood:

- The `vscode-webview://` resource protocol not serving HTTP range requests, so
  the media element stalls on a resource it cannot seek.
- A webview CSP `media-src` restriction.
- An audio output device selection issue inside the Electron process.

Confirming any of these means instrumenting the webview (`Developer: Open
Webview Developer Tools`) and reading `video.error`, `readyState`, and the
`canplaythrough` event. Worth ten minutes on a calm day; not worth it mid-build.

## Practical rule

**Never validate a render inside VS Code.** It reported broken audio on a
provably good file — and earlier in this same project it happily displayed a
video whose captions were rendering at `opacity: 0`, with a clean exit code and
a plausible file size. Use QuickTime or a browser, and extract frames with
`ffmpeg` when you need to check what is actually on screen.

```bash
open out/eli-health.mp4                      # QuickTime, full AAC support
LIB=video/node_modules/@remotion/compositor-darwin-arm64
DYLD_LIBRARY_PATH=$LIB $LIB/ffmpeg -ss 5 -i out/eli-health.mp4 -frames:v 1 out/f5.png
```
