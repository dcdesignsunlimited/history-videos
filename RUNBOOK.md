# Daily history short — runbook

Each run makes ONE 62–70-second narrated, captioned history video and schedules it on
TikTok (@historicalleaders), Instagram (@semper_lit) and YouTube Shorts via Metricool.

## 1. Setup
    git clone (repo is attached) && bash pipeline/setup.sh

## 2. Pick the story
- Read `posted.json`. Never repeat an event already listed.
- If `stories/` holds a spec dated today that is not in `posted.json`, use it (already written and fact-checked).
- Otherwise pick a real, well-documented event with a strong hook and a twist. Vary eras and regions
  (ancient, medieval, early modern, 19th c., WWI, WWII, Cold War, exploration, disasters, heists, inventions).
  Avoid gore-focused topics, partisan modern politics, and anything after 2000.

## 3. Research and fact-check (mandatory)
- Verify EVERY date, name, number and quote against at least two reliable sources (encyclopedias,
  museums, official histories, major outlets). If a detail can't be verified, cut it.
- Quotes on screen must be real and verified. Never invent quotes, headlines or documents' wording
  presented as real — card/quote text must be either verified wording or clearly a summary label.

### 3b. Archival photos (1–3 per video when available)
- Find public-domain / CC0 photos of the actual event, place or object on Wikimedia Commons:
      python3 pipeline/fetch_photo.py search "<event> <year>"
      python3 pipeline/fetch_photo.py get "File:<exact title>" <slug>
  `get` refuses anything that isn't public domain / CC0, saves `assets/<slug>/<n>.jpg` (+ credits.json) and prints a credit line.
- Wikimedia rate-limits cloud IPs: the script waits and retries; leave ~5 s between downloads. If it still fails
  after a couple of tries, skip photos for today and build the video from the other graphics — never block the post on photos.
- No portraits of named people — scenes, places, objects, crowds. Read each saved image to confirm it shows what the
  narration says, use it in a `photo` scene with the printed `credit`, and commit `assets/<slug>/` with the spec.

## 4. Write the spec
- Follow `pipeline/SPEC_FORMAT.md`. Save as `stories/YYYY-MM-DD-slug.json`.
- Narration: George (British male), plain and gripping, short sentences.

### Retention rules (these decide reach — follow every one)
1. **Hook = the most shocking true fact, stated in the first 2 seconds, max ~10 spoken words.**
   Lead with the outcome, not the setup ("A wave of molasses once killed twenty-one people.",
   "In 1943, a dead man fooled Hitler."). Never open with "In the year...", "Let me tell you..." or a date alone.
   Hook `lines` are 3–4 short lines, 1–3 words each; the last line is the payoff in amber.
   Follow the hook line with a 2–4 word credibility kicker when it fits ("This really happened.").
2. **Change the picture every 2–5 seconds.** 14–18 scenes, one sentence each; no shot longer than ~6 s; mix at least
   6 scene types; every sentence gets its own graphic (`icon`, `units`, `compare`, `split`, `photo`, `counter`, `map`...).
   Use a `map` whenever geography matters, `stamp`s / `shake` for reveals, and 1–3 archival `photo`s when available (section 3b).
3. **Open loop around the middle**: one line that promises a twist still to come
   ("But the strangest part came after." / "And that wasn't even the worst of it.") — only if true.
4. **Concrete numbers beat adjectives**: "25 feet high at 35 miles an hour", not "huge and fast".
5. **End fast**: the final reveal, then the `end` scene with the title, a `question` that invites comments
   (opinion or "had you heard of this?" — never bait that misleads) and the follow CTA. VO for the end scene:
   the title line, then the question, e.g. "Had you ever heard of this one? Tell me in the comments."
6. **Pick stories with a twist or an "I can't believe that's real" factor**: deceptions, bizarre disasters,
   impossible escapes, accidental inventions, unlikely heroes, blunders that changed history.
   Rotate eras and regions; avoid two similar topics in a row.

## 5. Render and check
    python3 pipeline/engine.py stories/<file>.json work/<slug>
- Length must be 62–70 s (the engine refuses under 61 s); fix and re-render if not.
- Look at `work/<slug>/preview.jpg`; fix overlapping or cut-off text.

## 6. Publish the file
- Copy the mp4 to `videos/YYYY-MM-DD-slug.mp4`, add the spec, append to `posted.json`
  ({"date", "slug", "title"}), commit and push to `main`.
- Media URL: `https://raw.githubusercontent.com/dcdesignsunlimited/history-videos/main/videos/<file>.mp4`
  (check it returns HTTP 200 before scheduling).

## 7. Schedule in Metricool (brand blogId 7836497, timezone America/New_York)
- Publish time: 7:00 PM America/New_York, same day.
- providers: instagram, tiktok, youtube.
- text: a 1–2 sentence description (hook + payoff, no full spoiler, optional one emoji), blank line,
  then EXACTLY 5 hashtags: 2 broad (#history plus an era tag like #ww2), 1 for the event, 2 niche.
- instagramData: {"type": "REEL", "showReelOnFeed": true, "isAiGenerated": true}
- tiktokData: {"privacyOption": "PUBLIC_TO_EVERYONE", "title": "<short title>", "isAigc": true, others default}
- youtubeData: {"title": "<punchy title> | <event> #shorts" (≤100 chars), "type": "short", "privacy": "public",
  "tags": [5–8 tags], "category": "EDUCATION", "madeForKids": false, "isAiGeneratedContent": false}
- Monthly cap: 20 published posts on the free plan; the schedule runs 20 days a month (19 in February).

## 8. If anything fails
Do not post a broken or unverified video. Stop and report what failed.
