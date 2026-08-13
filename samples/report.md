# Weekly performance highlights

Week under review **2026-08-02 to 2026-08-08** (as of 2026-08-13). Run `2026-08-13-3bd775d6a0`.

Every number below is bound to a deterministically computed fact. The bindings are in the audit trail (`audit.json`).

## Summary

| Severity | Metric | Segment | Headline |
| --- | --- | --- | --- |
| high | gmv | channel=display_ads | channel=display_ads stopped trading |
| high | gmv | overall | Single-day spike in GMV for overall |
| high | orders | country=DE | Orders fell in country=DE |
| high | conversion_rate | country=JP | Conversion rate rose in country=JP |
| medium | gmv | channel=marketplace | channel=marketplace appeared for the first time |
| medium | orders | channel=email | Orders rose in channel=email |

## Highlights

### 1. channel=display_ads stopped trading

*gmv · channel=display_ads · severity high*

channel=display_ads recorded $0 for GMV in the week under review, having traded in every earlier week of the comparison window. Its trailing four-week baseline was $45,089.

**HYPOTHESIS (unverified):** A segment going to zero outright is usually caused by a feed, integration or tagging break rather than by demand.

Evidence:

| Fact | Value | Computation |
| --- | --- | --- |
| `gmv/channel=display_ads/complete_week:2026-08-08/value` | $0 | SUM(gmv) over the period |
| `gmv/channel=display_ads/complete_week:2026-08-08/baseline_mean` | $45,089 | mean of the 4 prior complete weeks |

### 2. Single-day spike in GMV for overall

*gmv · overall · severity high*

GMV for overall reached $458,959 on 2026-08-05, a robust score of 37.69 against its same-weekday history. The median of those prior same weekdays is $333,330.

**HYPOTHESIS (unverified):** One-day moves of this shape are usually caused by a campaign burst, a bot surge, or a tracking change; the pipeline cannot tell them apart.

Evidence:

| Fact | Value | Computation |
| --- | --- | --- |
| `gmv/overall/daily:2026-08-05/value` | $458,959 | SUM(gmv) over the period |
| `gmv/overall/daily:2026-08-05/robust_score` | 37.69 | (value - weekday_median) / robust_scale |
| `gmv/overall/daily:2026-08-05/weekday_median` | $333,330 | median of the 8 prior same-weekday values |

### 3. Orders fell in country=DE

*orders · country=DE · severity high*

Orders for country=DE came in at 4,601 in the week ending 2026-08-08, a week-over-week change of -10.3% (-527 in absolute terms). The trailing four-week baseline is 5,096, putting this week at a screening z of -4.27. This segment accounts for -122.6% of the overall week-over-week change.

**HYPOTHESIS (unverified):** Possibly driven by a demand or acquisition shift in this segment.

Evidence:

| Fact | Value | Computation |
| --- | --- | --- |
| `orders/country=DE/complete_week:2026-08-08/value` | 4,601 | SUM(orders) over the period |
| `orders/country=DE/complete_week:2026-08-08/wow_pct` | -10.3% | 100 * (current - prior_week) / \|prior_week\| |
| `orders/country=DE/complete_week:2026-08-08/wow_delta` | -527 | current - prior_week |
| `orders/country=DE/complete_week:2026-08-08/baseline_mean` | 5,096 | mean of the 4 prior complete weeks |
| `orders/country=DE/complete_week:2026-08-08/screening_z` | -4.27 | (current - baseline_mean) / baseline_sd  [sample sd, n-1] |
| `orders/country=DE/complete_week:2026-08-08/contribution_share_pct` | -122.6% | 100 * cut_wow_delta / overall_wow_delta |

### 4. Conversion rate rose in country=JP

*conversion_rate · country=JP · severity high*

Conversion rate for country=JP came in at 3.27% in the week ending 2026-08-08, a week-over-week change of 5.0% (0.16pp in absolute terms). The trailing four-week baseline is 3.11%, putting this week at a screening z of 3.37.

**HYPOTHESIS (unverified):** Possibly caused by a checkout, pricing or traffic-quality change.

Evidence:

| Fact | Value | Computation |
| --- | --- | --- |
| `conversion_rate/country=JP/complete_week:2026-08-08/value` | 3.27% | 100 * SUM(orders) / SUM(sessions) over the period |
| `conversion_rate/country=JP/complete_week:2026-08-08/wow_pct` | 5.0% | 100 * (current - prior_week) / \|prior_week\| |
| `conversion_rate/country=JP/complete_week:2026-08-08/wow_delta` | 0.16pp | current - prior_week |
| `conversion_rate/country=JP/complete_week:2026-08-08/baseline_mean` | 3.11% | mean of the 4 prior complete weeks |
| `conversion_rate/country=JP/complete_week:2026-08-08/screening_z` | 3.37 | (current - baseline_mean) / baseline_sd  [sample sd, n-1] |

### 5. channel=marketplace appeared for the first time

*gmv · channel=marketplace · severity medium*

channel=marketplace recorded $43,447 for GMV in the week ending 2026-08-08, its first appearance in the comparison window. It has no baseline, so no comparison statistic exists for it yet.

Evidence:

| Fact | Value | Computation |
| --- | --- | --- |
| `gmv/channel=marketplace/complete_week:2026-08-08/value` | $43,447 | SUM(gmv) over the period |

### 6. Orders rose in channel=email

*orders · channel=email · severity medium*

Orders for channel=email came in at 8,454 in the week ending 2026-08-08, a week-over-week change of 9.3% (716 in absolute terms). The trailing four-week baseline is 7,130, putting this week at a screening z of 2.54. This segment accounts for 166.5% of the overall week-over-week change.

**HYPOTHESIS (unverified):** Possibly driven by a demand or acquisition shift in this segment.

Evidence:

| Fact | Value | Computation |
| --- | --- | --- |
| `orders/channel=email/complete_week:2026-08-08/value` | 8,454 | SUM(orders) over the period |
| `orders/channel=email/complete_week:2026-08-08/wow_pct` | 9.3% | 100 * (current - prior_week) / \|prior_week\| |
| `orders/channel=email/complete_week:2026-08-08/wow_delta` | 716 | current - prior_week |
| `orders/channel=email/complete_week:2026-08-08/baseline_mean` | 7,130 | mean of the 4 prior complete weeks |
| `orders/channel=email/complete_week:2026-08-08/screening_z` | 2.54 | (current - baseline_mean) / baseline_sd  [sample sd, n-1] |
| `orders/channel=email/complete_week:2026-08-08/contribution_share_pct` | 166.5% | 100 * cut_wow_delta / overall_wow_delta |

## Coverage

| Metric | Status | Required | Detail |
| --- | --- | --- | --- |
| aov | ok | no | - |
| conversion_rate | ok | yes | - |
| gmv | ok | yes | - |
| orders | ok | yes | - |
| sessions | ok | no | - |

A metric that could not be computed is listed here rather than omitted: silence must never read as 'nothing happened'.

## Considered and set aside

| Candidate | Selected by | Reason |
| --- | --- | --- |
| sessions · country=US | contribution_share_pct | within-baseline-variation |
| conversion_rate · channel=affiliate | screening_z, wow_pct | within-baseline-variation |
| conversion_rate · channel=display_ads | disappeared | data-quality |
| aov · overall | overall | within-baseline-variation |
| aov · country=JP | screening_z, wow_pct | within-baseline-variation |
| aov · country=US | screening_z, wow_pct | within-baseline-variation |
| aov · channel=paid_search | screening_z | within-baseline-variation |
| aov · channel=organic | wow_pct | within-baseline-variation |
| aov · channel=display_ads | disappeared | data-quality |
| orders · channel=display_ads | contribution_share_pct, disappeared, screening_z | duplicate-of-highlight |
| sessions · channel=display_ads | contribution_share_pct, disappeared, screening_z | duplicate-of-highlight |
| orders · channel=marketplace | newly_appeared | duplicate-of-highlight |
| sessions · channel=marketplace | contribution_share_pct, newly_appeared | duplicate-of-highlight |
| conversion_rate · channel=marketplace | newly_appeared | duplicate-of-highlight |
| gmv · channel=affiliate | screening_z | duplicate-of-highlight |
| sessions · country=JP | contribution_share_pct, screening_z | duplicate-of-highlight |
| gmv · country=JP | contribution_share_pct, screening_z | duplicate-of-highlight |
| gmv · country=DE | contribution_share_pct, screening_z | duplicate-of-highlight |
| gmv · channel=email | contribution_share_pct | duplicate-of-highlight |
| sessions · channel=email | screening_z | duplicate-of-highlight |
| orders · country=JP | contribution_share_pct, screening_z | duplicate-of-highlight |
| sessions · country=DE | screening_z | duplicate-of-highlight |
| conversion_rate · country=UK | screening_z | small-absolute-impact |
| conversion_rate · channel=paid_search | screening_z, wow_pct | small-absolute-impact |
| conversion_rate · country=DE | wow_pct | duplicate-of-highlight |
| orders · overall | overall | duplicate-of-highlight |
| aov · channel=affiliate | screening_z, wow_pct | small-absolute-impact |
| sessions · overall | overall | duplicate-of-highlight |
| orders · channel=paid_search | screening_z | duplicate-of-highlight |
| conversion_rate · overall | overall | duplicate-of-highlight |

## How to read this

- Weekly comparisons use complete Sunday-to-Saturday weeks. The current day is never included; a partial week is compared only against the same elapsed slice of earlier weeks.
- `screening_z` and `robust_score` are ranking heuristics on short baselines, not calibrated significance tests.
- Facts and hypotheses are separate. Anything under HYPOTHESIS is an unverified suggestion, not a finding.

Written by `mock` / `deterministic-mock-writer-1` in 1 attempt(s); every claim re-checked against the computed facts before this document was produced.
