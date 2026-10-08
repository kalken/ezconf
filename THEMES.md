# Themes

How ezconf's themes are put together, and what to keep to when making a new one — a built-in
theme, or one of your own.

## What a theme is

One CSS file with a single `:root` block of variables. Nothing else: no selectors, no layout.
`webroot/style.css` holds every rule and only ever refers to those variables, so a theme can
change colours, fonts, corner radii and spacing, and nothing structural.

The built-in themes are `webroot/theme-<name>.css`: `nixos`, `dark`, `gruvbox`, `osx-dark`,
`osx-light`. (`osx` is not a file: it stands for whichever of the two `osx-*` themes matches the
light or dark appearance of the system the browser runs on.) Any of them is a complete list of the variables there are, grouped under the same
headings in the same order.

## Choosing colours

A theme needs very few colours. Everything on screen comes from three small groups, and the
long list of variables is mostly the same handful of values used again.

**1. One ramp of neutrals.** Pick a single grey — or a single hue at low saturation, as `nixos`
(blue) and `gruvbox` (warm brown) do — and take steps along it:

- four background steps: page, bars, hover, raised (`--bg` to `--bg4`), plus the card
- one text colour at the far end of the ramp, with two dimmer versions mixed back toward the
  background
- borders, popups, selection, the active tab, scrollbars: more steps on the *same* ramp, each
  placed between two of the above

Nothing in this group should introduce a new hue. If a border or a hover shade looks like a
different colour from the surface it sits on, it's off the ramp.

**2. At most one accent.** One hue for links, focus rings and, if wanted, the filled controls.
It is optional: `dark` and both `osx-*` themes have none and use greys from the ramp instead.
Two accents compete; there is no second one to set.

**3. Four signal colours, one job each.** These are the only saturated colours a neutral theme
has, which is what makes them noticeable.

| Colour | Variable | Means |
|---|---|---|
| Green | `--green`, `--green2` | fine: saved, valid — and the colour of a *value* wherever one is quoted: a Nix expression, a default or example in an option's documentation popup |
| Red | `--red` | wrong or destructive: a missing required option, an error, delete |
| Yellow / orange | `--dirty-color` | unsaved changes |
| Purple | `--purple` | reference text: types, wildcards and existing paths in autocomplete, inline code in documents |

Keep them to those jobs. A theme that also uses green or red decoratively takes away the one
thing they are for.

So a new theme is roughly ten decisions: the ramp's hue, its darkest and lightest ends and the
steps between, the text colour, an accent or none, and four signals. As a check, both `osx-*`
themes use about twenty distinct greys and five other colours for the whole interface, and
`dark` gets by with twelve greys and four.

The terminal is the exception — it has sixteen colours of its own, because programs running in
it expect them (see "Terminal" below).

The two `osx-*` themes are the reference for how roles are assigned. Where the others once
differed (a status bar darker than the other bars, popups the same colour as the bars, an
accent-coloured toggle knob, grey standing in for purple) they have been brought in line; a new
theme should match them too. The one deliberate difference left is section names, which `nixos`
and `gruvbox` show in their accent and the neutral themes in a text colour.

## The surfaces

Four backgrounds stack on top of each other. Get these right first; most of a theme's look
comes from them.

| Surface | Variable | Notes |
|---|---|---|
| The editor page | `--editor-bg` (normally `var(--bg)`) | Also the terminal's background, via `--term-bg` |
| Bars and panels: top bar, tab bar, status bar, terminal bar, tree | `--bg2` | The status bar has its own variable, `--statusbar-bg`; keep it `var(--bg2)` so every bar is one colour |
| A top-level section's card | `--card-bg` | Must be told apart from the page it sits on |
| An option's value cell, inside a card | `--value-bg` | Defined as halfway between card and page; leave it derived |

- **Page and card need visible contrast.** A dark theme has the page darkest and the cards a
  step lighter; a light theme has a grey page and white cards.
- **`--bg3` and `--bg4` are hover and raised shades**, used on buttons, list rows and inline
  code. Each has to move one step further away from `--bg2`, in the same direction (lighter in
  a dark theme, darker in a light one), or hover states disappear.
- **Popups** (`--popup-bg`, `--select-option-bg`) sit above everything and want their own
  shade, not the page's. `--popup-bg` is one surface shared by dialogs, the documentation popup
  over an option and the search results, so those always match each other.
- **Edges are shadows, not borders**: `--panel-hairline` is the 1px line along a bar or card,
  `--panel-shadow` the soft shadow beside it. Both are translucent black, so they need lowering
  a lot on a light theme and barely show on a very dark one.

## Text

- `--text` for values and option names, `--text2` for secondary text, `--text3` for the
  dimmest (placeholders, hints). Define the two dimmer ones as a mix of `--text` toward the
  background rather than as separate greys, so they stay in step.
- **Section names** take `--section-key-color`. Full-strength `--text` reads as too heavy for
  them on a light theme; `--text2` is what `osx-light` uses.
- **Anything used as a text colour must be readable on a card**: `--accent`, `--green`,
  `--red`, `--purple`, `--dirty-color`. On a light theme that means the darker variants of a
  colour, not the bright ones that work on black.

## Accent

`--accent` is used as text (links, inline code); `--accent2` for outlines: focus rings, drop
targets, the flash on a newly added option. The filled controls have their own variables —
`--toggle-on-bg`, `--btn-primary-bg`, `--selected-bg`, `--select-option-hover` — so they can
follow the accent or not.

- A theme can be entirely neutral: `dark` and both `osx-*` themes use greys for all of these.
- A toggle's knob (`--toggle-on-knob`) is white, or the palette's lightest neutral — not the
  accent — whatever the toggle itself is filled with.
- If the primary button is filled with the accent, check its label (`--btn-primary-text`)
  against that fill.
- `--select-option-hover` is behind menu and dropdown items whose text keeps its normal
  colour. Don't make it so saturated that `--text` on it stops being readable.
- A destructive menu item fills with `--red` on hover and shows `--danger-text` on it, so
  those two have to work together.

## Spacing

The "Editor layout" block is the same in every built-in theme, on purpose:

| Variable | Value | |
|---|---|---|
| `--page-margin` | 20px | around the cards, on every side |
| `--section-gap` | 16px | between two top-level cards |
| `--card-gap` | 12px | around a bordered sub-section |
| `--block-pad` | 10px | inside a card, at the sides |

These follow the figures Apple has published for window margins and for spacing between and
inside groups. A theme that is only about colour should leave the whole block alone; change it
in all of them or none.

Corner radii are the same in every built-in theme too — `--radius` 8px (cards, panels, popups),
`--radius-sm` 5px (tabs, buttons), `--input-radius` 6px — taken from the `osx-*` themes. They
stay variables in each theme file so a user theme can still choose its own.

`--font` is the theme's to choose. Keep it monospaced: values are configuration and Nix code.

## Terminal

`--term-*` are xterm.js's colours, read out of the page once the theme has loaded.

- `--term-bg` normally stays `var(--editor-bg)`.
- All sixteen ANSI colours have to be readable on it, "white" and "bright black" included.
  On a light background that takes a deliberately darker set; a palette designed for a black
  terminal will not do.

## The swatch

Each theme has a dot in the header. Paint it with the theme's own page background (`--bg`) and
give it a ring in the accent, or a neutral grey if the theme has no accent. Swatches are sorted
from darkest to lightest by that colour every time the page loads, so there is no order to
maintain — but a swatch painted with some other colour will sort into the wrong place.

## Adding a built-in theme

1. `webroot/theme-<name>.css`: copy the closest existing one. It must define exactly the same
   set of variables as the others.
2. `bin/server.py`: add the name to `BUILTIN_THEMES`.
3. `webroot/index.html`: add it to `_THEMES`, and add its swatch button to `.theme-switcher`.
4. `webroot/style.css`: add its `.theme-swatch-<name>` colours.
5. `modules/ezconf-common.nix`: add it to `builtinThemes`, and to the `theme` description.
6. `README.md` and `example/ezconf.example.toml`: add it where the themes are listed.

Names are lowercase letters, digits, `-` and `_`.

**A new variable goes into every built-in theme file at once.** `style.css` has no fallbacks,
so a variable missing from one theme is simply a missing colour there.

Renaming a theme breaks every config that names it. Add the old name to `THEME_ALIASES` in
`server.py` (and to `themeNames` in the module) rather than dropping it.

## Your own theme

A user theme is the same kind of file, with one difference: it doesn't have to be complete. A
built-in theme is loaded underneath it, so it only sets what it wants to change, and it keeps
working when a later version adds a variable.

```css
/* base: osx-dark */
:root {
  --bg:     #1b1622;
  --bg2:    #261f30;
  --accent: #c792ea;
}
```

- The `base` comment, near the top of the file, names the built-in theme underneath. It is
  `nixos` when left out. Pick a base of the same kind, light or dark, as the theme you're
  making: everything the file doesn't set comes from there.
- Set `--bg` and `--accent` as plain hex colours. They are what the swatch is painted with;
  without them it is a grey dot that sorts into the middle of the row.
- Hooking it up is in the README, under "Your own themes": `services.ezconf.themes` in the
  module, or a `themes_dir` folder of `<name>.css` files standalone. The folder is read when
  the server starts.
- A name already taken by a built-in theme is skipped.

## Before calling it done

Look at it in the browser, at least at:

- a file with several sections, nested ones folded and unfolded, and a list of objects
- a toggle on and off, a dropdown open, a right-click menu, a hover tooltip
- a required-but-missing option and the unsaved-changes indicator
- the terminal, with something colourful running (`ls --color`, `git diff`)
- the login page, including a field the browser has autofilled
