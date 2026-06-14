# Plum Design System (extracted from plumhq.com)

Reference for matching Plum's brand in the claims UI. Pulled from live computed styles + CSS vars.

## Brand feel
Premium fintech-meets-editorial. Dark plum backgrounds, warm cream text, a single hot
coral/red accent, and a serif display face paired with a clean grotesque sans. Generous
whitespace, large fluid type, rounded corners, calm and confident — not "dashboard-y".

## Color palette
| Role | Hex | Notes |
|------|-----|-------|
| Brand dark (primary bg) | `#2c0b21` | `--plum-claims-experience---swatch--dark-900`, the signature plum |
| Darker plum | `#460932` / `#340926` | hero gradient depths, `--plum-vision-dark`, `--indigo` |
| Darkest | `#11040d` | `--swatch--dark-950` |
| Nav dark | `#1d0716` | |
| Accent (CTA) | `#ff4052` | `--plum` — the hot coral on every primary button |
| Accent hover/light | `#ff6a75` / `#ff9199` | `--light-coral`, `--darker-pink` |
| Crimson | `#cc3342` | secondary red |
| Cream bg (light mode) | `#fffaf2` / `#fff9f2` | `--plum-promise-light-bg`, `--light-1` |
| Cream text on dark | `#fff1e5` / `#fff8f2` | `--commen-text`, nav white |
| Text on light | `#2c0b21` | plum-on-cream for body |
| Bisque/peach accents | `#ffe1bf` `#ffebdb` `#ffecd6` | soft card fills |
| Supporting (charts/tags) | growth green `#92bd33`, blue `#1d9bf0`, yellow `#ffbf21`, molten `#ff5600` | use sparingly for status |

### Status-color mapping for the claims app (my picks, on-brand)
- APPROVED → growth green `#92bd33`
- PARTIAL → sunshine yellow `#ffbf21`
- REJECTED → accent/crimson `#cc3342` (not the CTA coral, to avoid confusion)
- MANUAL_REVIEW → blue `#1d9bf0`
- Doc-problem / blocked → molten `#ff5600`

## Typography
- **Display / headings:** GT Alpina (serif, weight 400), italic used for emphasis words
  (e.g. "team *deserves*"). Fallback stack: `Georgia, serif`. Large fluid sizes:
  H1 `clamp(3rem, …, 5rem)`, display up to `7rem`, line-height tight (1.0–1.2),
  letter-spacing `-0.02em`.
- **Body / UI:** "Passenger" grotesque sans (Passengersans), weights 300/400/500/600.
  Fallback `Arial, sans-serif`. Body size `1rem`, text-large up to `1.25rem`.
- Self-host substitutes for the build: **Fraunces** (or Playfair Display) for the serif
  display, **Inter** (or Geist) for the sans. Close enough to read as the same brand.

## Spacing / shape
- Radius: main `1rem`, small `0.25rem`, pill `100vw`. Cards are softly rounded (~16px).
- Fluid spacing scale (`space-1`…`space-8`) ~0.5rem → 4rem; section spacing 3–7rem.
- 12-column grid, max content width `90rem` (1440px), generous gutters.
- Nav height `4rem`, sticky, transparent over hero.

## Components observed
- **Primary button:** coral `#ff4052` fill, white text, slightly rounded, hover → dark plum
  `#2c0b21`. **Secondary button:** transparent with thin border, plum text.
- **Nav:** logo left (lowercase "plum" wordmark in coral), centered menu with `+` prefixes,
  outlined "LOG IN" + filled "GET A QUOTE" right.
- **Inputs:** white pill input with inline coral submit button (email capture pattern).
- **Hero:** full-bleed dark illustration, centered serif headline with one italic word,
  short sub, single CTA.
- **Content sections:** big centered serif heading, muted cream sub-paragraph, then
  rounded translucent panels with serif sub-heads + sans body.

## How to apply to the claims app
- Submission + review UI on a **cream `#fffaf2`** canvas with plum text for legibility/forms;
  use the **dark plum** for the top nav/header band and the hero/landing of the app.
- Serif (Fraunces) for page titles and decision verdict; Inter for forms, tables, the trace.
- Coral reserved for the primary action ("Submit claim"); status pills use the mapping above.
- Decision trace = clean timeline/stepper on cream cards with rounded-1rem corners,
  thin `color-mix` plum borders (`#2c0b21` @ 20% on transparent).
