---
version: alpha
name: OpenLinc
description: A calm, light look for pages that show live data from a headband.
colors:
  primary: "#6662AC"
  surface: "#FEF9F2"
  surface-raised: "#FFFFFF"
  on-surface: "#322D60"
  on-surface-muted: "#58546C"
  inverse-surface: "#2B2757"
  on-inverse-surface: "#FEF9F2"
  data-series-1: "#2A78D6"
  data-series-2: "#EB6834"
  data-series-3: "#1BAF7A"
  data-ambient: "#B87400"
  status-ok: "#67DDA0"
  status-attention: "#EBC48B"
  dark-primary: "#8A82FF"
  dark-surface: "#05060C"
  dark-surface-raised: "#0D0E18"
  dark-on-surface: "#F8F6FF"
  dark-data-series-1: "#3987E5"
  dark-data-series-2: "#D95926"
  dark-data-series-3: "#199E70"
  dark-data-ambient: "#EBC48B"
typography:
  headline-display:
    fontFamily: Satoshi
    fontSize: 48px
    fontWeight: 300
    lineHeight: 1
    letterSpacing: -0.05em
  title-md:
    fontFamily: Bai Jamjuree
    fontSize: 16px
    fontWeight: 700
  body-md:
    fontFamily: Bai Jamjuree
    fontSize: 16px
    fontWeight: 400
  label-sm:
    fontFamily: Bai Jamjuree
    fontSize: 13px
    fontWeight: 400
rounded:
  md: 20px
  lg: 32px
  full: 999px
---

# OpenLinc

## Overview

Calm, light and soft: a warm cream page, deep violet ink and white cards. Content comes first;
colour marks what matters and decoration is rare. A dark mode exists beside the light one, near
black with a brighter violet.

## Colors

- **primary** is a muted violet: for what is selected, for focus and for the main line in a plot.
- **surface** is the cream page, **surface-raised** the white cards on it.
- **on-surface** is the violet ink for text, **on-surface-muted** for units, notes and labels.
- **inverse-surface** is a deep violet for the one area per page that carries the large headline,
  with **on-inverse-surface** for the text on it.
- The **data-** colours are for the lines of a plot. A plot with one line uses **primary**; with
  several, the lines take **data-series-1**, **data-series-2** and **data-series-3** in this
  fixed order, so that the same quantity has the same colour everywhere. **data-ambient** is for
  ambient light.
- The **status-** colours are for a small dot or mark, not for text: **status-ok** when all is
  well, **status-attention** when the reader has to do something.
- The **dark-** colours hold the same roles for the dark mode.

## Typography

Satoshi, light and tightly spaced, only for the one large headline. Bai Jamjuree for everything
else: bold for titles, regular for text and labels.

## Elevation & Depth

Flat. A card lifts off the page by a soft, wide shadow in the ink colour at low opacity, not by a
border. In the dark mode a faint light border takes the shadow's place.

## Shapes

Round and soft: cards use `md`, the area with the large headline `lg`, buttons and tags are pills
(`full`).

## Do's and Don'ts

- Do keep text on its surface at a contrast of 4.5:1 or more.
- Do use the primary colour sparingly, so that it still marks something.
- Don't use pure black or pure grey; shades come from the violet ink.
- Don't add gradients or shadows for decoration; only the cards carry a shadow.
