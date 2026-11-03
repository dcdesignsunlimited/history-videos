# Daily history short — runbook

Each run makes ONE ~70-second narrated, captioned history video and schedules it on
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

## 4. Write the spec
- Follow `pipeline/SPEC_FORMAT.md`. Save as `stories/YYYY-MM-DD-slug.json`.
- Narration: George (British male), plain and gripping, short sentences, hook in the first line.

## 5. Render and check
    python3 pipeline/engine.py stories/<file>.json work/<slug>
- Length must be 65–75 s; fix and re-render if not.
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
