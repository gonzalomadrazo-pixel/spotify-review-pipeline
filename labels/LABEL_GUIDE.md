# Label guide (labels v1)

This guide is the human-readable source for every label the pipeline produces. The enrichment
prompt (`prompts/enrich_v1.md`) is a condensed copy of it. The primary `topic`, `intent` and `severity`
definitions are copied verbatim from `GRADING_CONTRACT.md` and are never changed; the decision rules,
subtopics and examples below are this project's additions.

Examples are drawn from the development files (`cost_100.csv`, `checkpoint_500.csv`,
`analysis_10000.csv`) or written for this guide. **No golden-set review or golden label is used
anywhere in this guide or in any prompt.**

## 1. Classify the text only

Only `review_text` is classified. Star rating, likes, app version and timestamp are kept as metadata
for analysis and are never shown to a model. Stars, angry language or capital letters do not establish
severity; cancellation intent does not raise severity.

## 2. Topic (exactly one; contract definitions)

| `topic` | Definition |
|---|---|
| `access` | Login, signup, password or account access |
| `usability` | Navigation, controls, layout, queue/playlist management, ad interruptions |
| `playback` | Playback failure, crashes, lag, connection failures, audio quality, resource use |
| `downloads` | Downloading, saved music, offline listening, disappearing downloads |
| `catalog` | Missing songs/artists, search/discovery, recommendations, lyrics availability |
| `billing` | Price, charges, subscriptions, paywalls, premium entitlement; explicitly premium-only controls go here |
| `support` | Contacting support and the support response |
| `other` | General praise/criticism, unrelated content, or no supported specific topic |

Decision rules:

1. **Several problems:** choose the problem with the highest supported severity. On a tie, choose the
   first specific problem mentioned.
2. **Positive review:** choose the first specific praised feature ("love the playlists" → `usability`;
   "great song selection" → `catalog`). General praise ("Great app", "Love it") → `other`.
3. **Paid plan mentioned:** a mention of Premium alone is not `billing`. A subscription failing to
   activate is `billing`; music crashing for a paying customer is `playback`.
4. **Free-tier restrictions:** "can't choose a song / can't rewind / can't repeat / only shuffle /
   limited skips without Premium" is an explicitly premium-only control → `billing`, subtopic
   `free_tier_restrictions`. Lyrics that are locked behind Premium are also `billing`; lyrics missing
   or wrong for a song is `catalog`.
5. **Ads:** too many, too long or too loud ads for free users → `usability` (`ads`). Ads still
   appearing for a paying Premium customer → `billing` (`premium_entitlement`).
6. **"No internet connection" while logging in** → `access`. "No internet" while playing → `playback`.
7. **A redesign/update complaint** with no specific broken function ("hate the new UI") → `usability`
   (`ui_redesign`). "Worst update" with no feature named → `other` (`general_criticism`).
8. **Generic criticism** ("bad app", "worst app") is `other`; never infer a specific defect.

## 3. Subtopics (optional richer label; this project's addition)

Subtopic codes are fixed and used to form stable issue IDs (`<topic>.<subtopic>`).

| topic | subtopics |
|---|---|
| access | `login_failure`, `account_hacked_or_locked`, `signup_or_account_creation`, `logged_out_or_lost_account`, `other_access` |
| usability | `ads`, `ui_redesign`, `navigation_or_layout`, `queue_playlist_library_management`, `controls_or_widgets`, `other_usability` |
| playback | `crash_or_wont_open`, `stops_or_skips_unexpectedly`, `connection_or_loading`, `wrong_song_or_autoplay`, `device_integration`, `audio_quality_or_volume`, `performance_battery_storage`, `other_playback` |
| downloads | `downloads_disappear`, `download_failure`, `offline_mode`, `other_downloads` |
| catalog | `missing_or_removed_content`, `search_or_discovery`, `recommendations`, `lyrics`, `podcasts_audiobooks`, `other_catalog` |
| billing | `free_tier_restrictions`, `price_too_high_or_increase`, `unexpected_charge_or_refund`, `premium_entitlement`, `payment_or_plan_management`, `other_billing` |
| support | `no_response_or_unreachable`, `unhelpful_response`, `other_support` |
| other | `general_praise`, `general_criticism`, `boycott_or_political`, `unrelated_or_unclear`, `other_general` |

`device_integration` covers Bluetooth, car / Android Auto, smart watch, casting and smart speakers.
`wrong_song_or_autoplay` covers "I pick a song and it plays a different one" when no paywall is named;
if the review says this happens *because* the user lacks Premium, use `billing.free_tier_restrictions`.

## 4. Intent (exactly one; contract precedence)

Precedence: `cancellation` → `complaint` → `request` → `praise` → `unclear`.

| `intent` | Use when |
|---|---|
| `cancellation` | Explicitly leaving, uninstalling, cancelling, switching to another app, or threatening to do so ("I'm going to uninstall", "switching to YouTube Music") |
| `complaint` | A negative experience, including mixed praise and criticism ("great app but too many ads") |
| `request` | A desired change or feature with no reported failure ("please add a sleep timer") |
| `praise` | Positive experience with no complaint |
| `unclear` | Meaningless, unrelated, unreadable, or bare boycott slogans with no product complaint or personal departure |

"Bring back the old feature, the new update removed it" is a `complaint` (a reported loss), not a
`request`. "Ban Spotify!!" with nothing else is `unclear`.

## 5. Severity (contract scale)

| `severity` | Meaning |
|---|---|
| 1 | No reported problem: praise, neutral/unclear content, or a pure feature request |
| 2 | Dislike, generic criticism, minor annoyance, or a cosmetic issue; no supported functional loss |
| 3 | A degraded or restricted function; some use or workaround remains |
| 4 | A clearly blocked core task, such as inability to log in or play music |
| 5 | Explicit serious financial, privacy, or data harm; an expensive plan, a crash, or angry language alone is insufficient |

Worked examples (development data or invented):

| Review (abridged) | topic.subtopic | intent | sev | Why |
|---|---|---|---|---|
| "Great" | other.general_praise | praise | 1 | No problem reported |
| "Thik thak hi hai" (Hindi: "it's okay") | other.general_praise | praise | 1 | Mildly positive, non-English handled by meaning; `needs_review` false |
| "Worst app" | other.general_criticism | complaint | 2 | Generic criticism, no specific defect |
| "Too many ads, 6 or 7 between songs. I will be searching for a new music app" | usability.ads | cancellation | 3 | Free listening still works but is degraded; departure threat sets intent, not severity |
| "Can't play the song I want without premium, only 6 skips" | billing.free_tier_restrictions | complaint | 3 | Restricted function; music still plays |
| "New UI is horrible and crashes the app after about five seconds" | playback.crash_or_wont_open | complaint | 4 | Crash blocks use; higher severity than the UI dislike |
| "Stuck on the logo when I want to login" | access.login_failure | complaint | 4 | Cannot log in |
| "Song just stops playing after 15–20 seconds, have to open app again" | playback.stops_or_skips_unexpectedly | complaint | 3 | Degraded; a workaround exists |
| "I was charged twice this month and no refund" | billing.unexpected_charge_or_refund | complaint | 5 | Explicit financial harm |
| "Someone hacked my account and changed my email" | access.account_hacked_or_locked | complaint | 5 | Explicit account/privacy harm |
| "Please add a sleep timer" | usability.controls_or_widgets | request | 1 | Pure feature request |
| "Hate Spotify, ban Spotify 😡😡" | other.boycott_or_political | unclear | 1 | Bare slogan, no product complaint |
| "My downloaded songs vanished after the update" | downloads.downloads_disappear | complaint | 4 | Offline listening blocked |
| "Paying premium and I still get ads in podcasts" | billing.premium_entitlement | complaint | 3 | Paid entitlement not delivered; app still usable |

## 6. Sentiment

Five levels mapped to the contract range: −2 → −1.0, −1 → −0.5, 0 → 0.0, +1 → 0.5, +2 → 1.0.
Sentiment describes the review's tone toward Spotify, independent of stars. Mixed reviews are usually −0.5.

## 7. Evidence quote, entities and needs_review

* `evidence_quote` must be an exact substring of the original text that supports the topic and
  intent. For reviews of 120 characters or fewer, code uses the whole stripped text (no model output).
  For longer reviews the model copies the shortest supporting span verbatim; code checks membership
  and repairs only whitespace/escape differences by locating the exact source span.
* `entities` are extracted by code from a fixed lexicon of explicit product terms (`srp/entities.py`),
  so no entity can be invented. An empty list is allowed.
* `needs_review` is a prediction that a human should look: the text is ambiguous, non-English and
  hard to interpret, sarcastic, has missing context needed for severity, or code had to repair the
  quote. It is evaluated as a prediction, not a guarantee of correctness.

## 8. Unsupported language and hard cases

Non-English reviews are classified by meaning when the model can read them. If the meaning cannot be
determined, use `other.unrelated_or_unclear`, `unclear`, severity 1 and `needs_review: true`. Hard
cases are never dropped from the denominator.
