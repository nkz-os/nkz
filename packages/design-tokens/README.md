# @nekazari/design-tokens

Canonical design tokens for the Nekazari platform (CSS custom properties, Tailwind
preset, and native/RN values), generated from `src/tokens.config.ts`. Do not hand-edit
generated output (`dist/`, `.css`) — edit `tokens.config.ts` and rebuild.

## Semantic color usage (fill vs foreground)

Each semantic color (`success`, `warning`, `danger`, `info`) ships three variants.
They are not interchangeable — pick by role, not by "which one looks right":

| Variant | Tailwind class | Role |
|---|---|---|
| base | `bg-nkz-success`, `text-nkz-success` | **Fills and large accents.** Solid button backgrounds, large icons/graphics, colored chart marks. Bright and saturated by design — **not guaranteed AA-compliant as text on a light surface.** |
| `-strong` | `text-nkz-success-strong`, `border-nkz-success-strong` | **Text and meaning-bearing icons/borders on light surfaces.** Darkened to meet WCAG AA (4.5:1) as foreground on white/`nkz-surface`/tinted (`-soft`) backgrounds. Use for: paragraph/label text conveying status, standalone status icons (no adjacent text stating the same status), badge text, link text. |
| `-soft` | `bg-nkz-success-soft` | **Tinted backgrounds** — badges, callout panels, subtle highlight fills. Pair with `-strong` for the text/icon inside it, never with base. |

Rule of thumb: **if a human has to read it as text (or as the sole color-coded
signal with no text label nearby), use `-strong`. If it's a fill — a button, a large
icon, a background tint — base (or `-soft` for tints) is correct.**

An icon sitting directly next to a text label that already states the same status
(e.g. a green `CheckCircle` next to the word "Success") is decorative/redundant —
base color is fine there; the text carries the AA requirement, not the icon.

`info` base (`#2563EB`) was already darkened during token design and is AA-safe as
text; `success`/`warning`/`danger` bases are not — hence the `-strong` variants.
See `src/tokens.config.ts` (`semanticColors` — inline comment documents the exact
contrast fixes) for the source values per profile (page/field/HMI/dark).

### Known gap: `-light` is not a defined suffix

A large amount of existing `apps/host` code uses `bg-nkz-{success,warning,danger,info}-light`
/ `text-nkz-*-light`. **This suffix does not exist in this package's Tailwind preset**
(`src/build-tailwind.ts` only emits base / `-soft` / `-strong`) — those classes
compile to nothing under Tailwind's JIT and render no background/color at all. The
canonical tint suffix is `-soft`. This is a pre-existing gap, not something this
change fixes; flagging it here so it isn't rediscovered as a mystery each time.
