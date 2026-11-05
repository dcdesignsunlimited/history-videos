# Story spec format

One JSON file per video in `stories/YYYY-MM-DD-slug.json`. Render with:

    python3 pipeline/engine.py stories/<file>.json work/<slug>

Output: `work/<slug>/<slug>.mp4` and `work/<slug>/preview.jpg` (one frame per scene — always look at it).

## Top level
```json
{"slug": "kebab-case-name", "title": "Human title", "scenes": [ ... ]}
```

## Every scene
- `type`: one of `hook`, `date`, `map`, `card`, `counter`, `quote`, `timeline`, `icon`, `units`, `compare`, `split`, `photo`, `end`
- `segments`: list of narration lines, each `{"vo": "...", "cap": "..."}`
  - `vo` is what George says. Write numbers and years as words ("nineteen forty-three", "thirty-eight"),
    spell hard names phonetically ("Glindoor" for Glyndwr, "Wellva" for Huelva), "H.M.S." for HMS.
  - `cap` is the on-screen caption (defaults to `vo`). Use digits here ("1943", "38 days") and correct spellings.
  - `hook` and `end` scenes must use `"cap": ""` (their big text already says it).
- optional `stamp`: `{"text": "TOP SECRET", "at": 0.55, "color": "red"}` — slams a rubber stamp on screen.
- optional `atmosphere` (hook/date/card/counter): `snow`, `embers`, `rain`, `dust`; `date` also accepts `night_sea`.
- optional `hold`: extra seconds at the end of the scene.
- optional `icons` (any scene): pop-in icons `[{"name": "ship", "x": 0.5, "y": 0.17, "size": 170, "color": "a", "at": 0.1, "label": "..."}]`
  — `x`/`y` are fractions of the frame. Keep them out of the caption zone (y 0.62–0.75).
- optional `shake` (any scene): `{"at": 0.15, "dur": 0.6, "amp": 30}` — screen shake for impacts.
- optional `trans`: entry transition `punch` | `whip` | `glitch` | `zoom` (default: rotates automatically).

## Icons
Any [Tabler icon](https://tabler.io/icons) name (MIT, ~4,900 icons, fetched by setup.sh). Find one with
`ls pipeline/icons_src/outline | grep -i ship`. Filled versions are used automatically when they exist.
Useful: ship anchor plane tank bomb flame skull sword swords crown building-castle building-factory train car horse man woman
users-group droplets ripple snowflake temperature-plus sun bolt volcano tornado mountain world compass map-pin clock hourglass
calendar coin cash-banknote gavel scale certificate file-certificate lock key eye spy shield flag trophy rocket radioactive
telescope microscope submarine helicopter barrel paint bucket hammer alert-triangle zoom-exclamation.
- `at` values are FRACTIONS of the scene's length (0–1), so they stay in sync with the narration.

## Scene types
- `hook` — first scene, always. `tag` (e.g. "TRUE STORY · 1943") + `lines`: 3–4 short lines,
  `[{"t": "IN 1943,"}, {"t": "A DEAD MAN"}, {"t": "FOOLED"}, {"t": "HITLER.", "c": "a"}]`. Last line amber gets a red underline.
- `date` — `big` ("APRIL 30, 1943"), `sub` ("4:30 AM · OFF THE COAST OF SPAIN").
- `map` — `header`, `bbox` [lon_min, lat_min, lon_max, lat_max] (country/region scale; never city scale),
  `pins` [{lon, lat, label, color, at, label_offset:[dx,dy]}], `arrows` [{from:[lon,lat], to:[lon,lat], color, at, dur, curve, label}],
  `countries` [{name (Natural Earth NAME, e.g. "Greece"), color, at}], `labels` [{lon, lat, text, at}],
  `big` {text, at, color} — large text under the map.
- `card` — typed index card. `header`, `photo` (true shows a silhouette ID photo; lines max 25 chars, else 38),
  `lines` (max 4 if using `added`, else 6), `strike` {line, at}, `added` {lines:[...], at} typed in red.
- `counter` — big number that counts up. `header`, `value`, `prefix`, `suffix`, `sub`, `color`.
- `quote` — a document being typed. `style` telegram | newspaper | letter, `header`, `meta` (2 short lines),
  `quote` (≤ 80 chars, a REAL verified quote only), `attribution`.
- `timeline` — `header`, `events` (2–5): [{"date": "1914", "label": "short label"}].
- `icon` — hero beat: `icon`, `text` (big word), `sub`, `color`. The fastest way to give a sentence its own picture.
- `units` — unit chart that fills in one icon at a time: `header`, `count` (≤ ~60), `icon` (default `man`), `per_row`,
  `color`, `suffix` (e.g. " DEAD"), `big_color`, `sub`, optional `highlight` {count, color}.
- `compare` — bars that grow to show scale: `header`, `unit`, `items` (2–4) [{label, value, icon, text, color}].
  Every value must be verified (a generic "~6 FT" person is fine as a reference).
- `split` — two panels: `header`, `left`/`right` {title, icon, text (≤ 3 short words), color}, `right_at` (0–1), optional `mid` ("→" or "VS").
  Use two segments: one per panel.
- `photo` — archival photo with slow pan, film grain and vignette: `image` (path from repo root, e.g. `assets/<slug>/1.jpg`),
  `pan` in | out | left | right | up | down, `focus` [x, y] (0–1), `grade` sepia | bw | none, `header`, `credit` (required),
  optional `circle` {x, y (0–1 of the photo), r, at, label}. Public-domain / CC0 photos of places, objects and events only.
- `end` — last scene, always. `lines` (2–3), `question` (comment prompt shown in amber, required),
  optional `cta` (default "Follow for a new true story every day"). Segments: title line, then the question line, both `"cap": ""`.

## Built-in polish (automatic, no spec needed)
Big 1–3-word pop captions, push-in zoom plus a re-frame + punch on every new sentence, rotating cut transitions
(punch-flash, whip-pan, glitch, zoom-blur) with whooshes and impacts,
a progress bar at the top, pulsing color glow behind key text (`color` on a scene picks it), drifting dust,
music bed with ducking, risers before stamps and counters.

## Length and pacing
- Target 62–70 s (TikTok monetization needs more than 60 s). That is roughly 190–215 spoken words at ~180 wpm
  across 14–18 scenes. The engine REFUSES to render under 61 s or over 72 s — add or cut narration and re-run
  (voice clips are cached by text).
- One sentence per scene, 2–5 s each. A scene with two segments gets a re-framed "beat" on the second sentence,
  but no single shot should run past ~6 s (the engine warns).
- Mix at least 6 scene types; never two of the same type in a row (except short `icon` beats); a graphic on every sentence.
- Colors: "w" white, "a" amber, "r" red.
