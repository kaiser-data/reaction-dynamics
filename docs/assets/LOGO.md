# Logo

Four reactions as ticks along a timeline: three close together, then one late.
Uneven height, uneven spacing. An even row of four would be the same *count* and
a different *behaviour* — which is the distinction the whole product exists to
make, so the mark states the thesis instead of decorating it.

Colours come from the existing hero and dashboards, so the mark does not arrive
as a third visual language.

Open `logo-preview.html` to see every file at every size on both grounds. It
loads the real `.svg` files rather than inlined copies, so the proof sheet cannot
drift from the assets.

## Files

| File | Use |
|---|---|
| `logo-mark.svg` | Primary. **24px and up.** |
| `logo-favicon.svg` | **Below 24px** — tab icons, avatars, inline glyphs |
| `logo-lockup.svg` | Mark + wordmark, for **dark** grounds |
| `logo-lockup-light.svg` | Mark + wordmark, for **light** grounds |
| `logo-mono.svg` | One colour, inherits `currentColor` when inlined |
| `reaction-dynamics-hero.svg` | Pre-existing 1200×630 social/README banner |

## The three rules worth keeping

**Below 24px, switch files.** `logo-favicon.svg` is drawn differently, not scaled
down: the timeline rule and two of the four ticks are dropped and everything left
grows. Shrinking the primary mark instead produces a grey blob, which is the usual
way a good logo dies in a browser tab.

**Outline the text before the lockup leaves this repo.** It uses live text with an
Inter-first font stack, so it re-renders in whatever the viewer has. Fine in a
README, wrong in a press kit or someone else's deck:

```bash
# either tool works; both replace the <text> with paths
inkscape logo-lockup.svg --export-text-to-path --export-filename=logo-lockup-outlined.svg
# or
svgo --enable=convertShapeToPath logo-lockup.svg   # after outlining, not instead of it
```

**Keep clear space of one tick-width** (about 7% of the mark's width) on all sides.
The mark already carries internal padding; crowding it past that starts eating the
straggler, which is the part that means something.

## Two constraints that are not style preferences

`logo-mono.svg` has **no opacity anywhere.** One-colour reproduction — screen
print, laser etch, fax-grade PDF — cannot render it, and the late tick would
silently disappear, taking the meaning with it. It is distinguished by height and
gap instead. If you add a variant, keep that property.

The lockups use **explicit colours, not `currentColor`.** An `<img>` tag cannot
inherit colour from the page, so a single `currentColor` lockup renders black on
black. That is why there are two files rather than one. `logo-mono.svg` *does* use
`currentColor`, because it is meant to be inlined, where inheritance works.

## Image-generator prompt

For a raster hero or social card. The mark itself should always be the real SVG
composited in afterwards — generators will not reproduce it accurately, and an
almost-right logo is worse than none.

> A wide, dark editorial illustration for a developer analytics product. Deep
> indigo-black background (#0b1021) with a soft violet glow (#8b7cff) in the upper
> right. The subject: a horizontal timeline rendered as a thin luminous rule, with
> four vertical light-tick marks rising from it at uneven heights — three clustered
> tightly together on the left, then a long empty span, then a single shorter tick
> far to the right. The empty span between them is the focal point and should feel
> deliberate and quiet, not like a mistake. Precise, restrained, technical;
> reminiscent of a scientific plot or an oscilloscope trace rather than a marketing
> graphic. Flat vector aesthetic, no 3D, no gradients on the ticks themselves, no
> text, no emoji, no faces, no UI chrome. Generous negative space. 1200×630.

Notes on that prompt, if you rewrite it: the gap is the subject — say so explicitly
or generators fill it. Ban emoji and text by name; both get added unbidden to
anything Slack-adjacent. Ask for "oscilloscope trace" over "bar chart" — the latter
reliably produces a business dashboard.

## What this is not

The mark reads as a small bar chart, and that family is well populated. It earns
its place through the uneven spacing and the straggler rather than through novelty
of form. If it ever needs to be more distinctive, the gap is the asset — stretch it,
do not add ornament.
