# Design Brief — Garmin Coach Dashboard

## Direction

Dark, data-focused, athletic. The aesthetic matches performance-tech tools like
Garmin Connect's own dark mode: clean hierarchy, colour-coded by sport, numbers
first. No gradients, no hero images, no decorative flourishes.

## Colour tokens

```css
/* backgrounds — three layers */
--bg:     #070d14   /* page background */
--s1:     #0d1820   /* card surface */
--s2:     #132030   /* elevated / hover surface */
--border: #18293c   /* dividers and card borders */

/* text */
--fg:     #ddeaf8   /* primary text */
--muted:  #4f6880   /* secondary labels, metadata */
--dim:    #243344   /* disabled, empty states */

/* interactive */
--accent: #4d8ef0   /* tabs, links, today highlight */

/* sport colours (used consistently everywhere) */
--run:    #1cc47a   /* running — green */
--bike:   #f09530   /* cycling — amber */
--swim:   #38b5e5   /* swimming — sky blue */
--str:    #9b72e8   /* strength — violet */

/* status */
--warn:   #f0a030   /* race countdown, warnings */
--ok:     #1cc47a   /* done, success (same as --run) */
```

## Typography

System font stack: `-apple-system, "SF Pro Text", "Segoe UI", system-ui, sans-serif`

| Role | Size | Weight | Other |
|------|------|--------|-------|
| Page title | 1rem | 700 | `letter-spacing: -.01em` |
| Section heading | .71rem | 600 | uppercase, `letter-spacing: .08em` |
| Stat big number | 1.65rem | 700 | `letter-spacing: -.03em`, tabular-nums |
| Card label | .67rem | 600 | uppercase, `letter-spacing: .07em` |
| Body / card text | .82rem | 400 | `line-height: 1.55` |
| Meta / dates | .72–.75rem | 400 | `color: var(--muted)`, tabular-nums |
| Pre / descriptions | .72rem | 400 | `font-family: inherit` |

`font-variant-numeric: tabular-nums` on all numeric columns and stat numbers.

## Component inventory

### Stat card
- Thin border on `--s1` background, `--r-m` radius.
- Label (uppercase `--muted`) → big number → subtitle.
- Minimal: no shadow, no gradient.

### Progress bar
- Track: `--s2`, 5px height, pill radius.
- Fill: sport colour, animated width transition.
- Labels above: left (done) / right (target), `--muted`.

### Day card (Upcoming / Decisions)
- Same `--s1` + `--border` base.
- `border-color: --accent` for today.
- `opacity: .7` for past days.
- `<details>` expando for workout description.

### History chart
- Inline SVG, `viewBox="0 0 800 120"`.
- Bars filled `--run` at 45% opacity; current week at 85%.
- Current week outlined with `--accent` stroke box.
- Labels: week start date (`--muted`, 9px).

### Sport dot
- 7px circle, inline, `margin-right: 6px`.
- Colour matches the sport token.

### Filter pills (Training tab)
- Pill shape, `--s1` / `--border` default.
- Active state: `--s2` background + sport border + sport text colour.

### Tab bar
- Horizontal scroll on narrow screens (hidden scrollbar).
- Active tab: `--s2` background, `--fg` text.
- Inactive: `--muted` text, no background.

## Spacing

- Page gutter: `max(16px, safe-area-inset)` on both sides.
- Section heading margin-top: 24px.
- Card gap: 10px.
- Inner card padding: 12–15px.

## Layout

- Max-width: 980px, centred.
- Stat grid: `repeat(auto-fill, minmax(175px, 1fr))` — 4 cards across on desktop.
- Week grid: `repeat(auto-fill, minmax(194px, 1fr))` — 4–5 cards across.
- Decision list: single column.
- Activity table: scrollable on narrow screens.

## Single-theme

The dashboard is a personal localhost tool always viewed on Mario's own machine.
It uses a single dark theme; no light-mode support is needed.
