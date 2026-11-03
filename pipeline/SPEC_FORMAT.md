# Story spec format

One JSON file per video in `stories/YYYY-MM-DD-slug.json`. Render with:

    python3 pipeline/engine.py stories/<file>.json work/<slug>

Output: `work/<slug>/<slug>.mp4` and `work/<slug>/preview.jpg` (one frame per scene — always look at it).

## Top level
```json
{"slug": "kebab-case-name", "title": "Human title", "scenes": [ ... ]}
```

## Every scene
- `type`: one of `hook`, `date`, `map`, `card`, `counter`, `quote`, `timeline`, `end`
- `segments`: list of narration lines, each `{"vo": "...", "cap": "..."}`
  - `vo` is what George says. Write numbers and years as words ("nineteen forty-three", "thirty-eight"),
    spell hard names phonetically ("Glindoor" for Glyndwr, "Wellva" for Huelva), "H.M.S." for HMS.
  - `cap` is the on-screen caption (defaults to `vo`). Use digits here ("1943", "38 days") and correct spellings.
  - `hook` and `end` scenes must use `"cap": ""` (their big text already says it).
- optional `stamp`: `{"text": "TOP SECRET", "at": 0.55, "color": "red"}` — slams a rubber stamp on screen.
- optional `atmosphere` (hook/date/card/counter): `snow`, `embers`, `rain`, `dust`; `date` also accepts `night_sea`.
- optional `hold`: extra seconds at the end of the scene.
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
- `end` — last scene, always. `lines` (2–3), optional `cta` (default "Follow for more true history").

## Length and pacing
- Target 65–75 s. That is roughly 165–195 spoken words across 8–11 scenes. The engine prints the length;
  if it is outside 65–75 s, add or cut narration and re-render (voice clips are cached by text).
- Colors: "w" white, "a" amber, "r" red.
