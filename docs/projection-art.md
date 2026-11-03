# Projection art

Every race and winner has a finished picture in `decision-desk/assets/calls/`, named `<RACE>_<PARTY>.png` (`GA-SEN_D.png`, `NE-SEN_I.png`). When that race is called, the bot posts the file exactly as it is. A retraction always gets a freshly drawn card instead.

Every card is the same design: the winner and the state on the party-colored glass, and the loser standing in the fog under THE FOG CLAIMETH.

| You want to | Do this |
|---|---|
| Put something funny in the fog for one race | Make a transparent PNG, add it to `FOG_ART` in `tools/make_projection_art.py`, and redraw that race. `docs/loser-meme-ideas.md` has a list of ideas |
| Nudge the design or add your own touches | Open its layered PSD in Photoshop (see Editing by hand) |
| Use something else entirely | Save your image over the file with the same name. A `.gif` keeps its animation |

Once you've changed a picture by hand, the tools won't overwrite it. To go back, delete your file and redraw that race.

## Redrawing

Run these from `decision-desk/`:

```bash
.venv/Scripts/python tools/make_projection_art.py GA-SEN_D
```

```bash
.venv/Scripts/python tools/make_projection_art.py --sheet
```

The first redraws one race. Name a race without the party (`GA-SEN`) to redraw all its winners. The second redraws every card and writes a contact sheet to `assets/kit/_sheet_all.jpg`.

## Who stands in the fog

| The winner is | The loser shown is |
|---|---|
| D or I | The R, or the other listed name if no R is listed |
| R | The D, or the independent if no D is listed |
| US-HOUSE majority | Nobody. There's no single loser |

The name comes from `candidates.json` and the photo from `assets/candidates/cutouts/<RACE>_<PARTY>.png`. If the loser has no listed name or no cutout, the fog stays empty, but the caption still shows. Add the photo (see Photos and cutouts) and redraw to fill it.

`OH-SEN_D` uses `assets/memes/fog-husted.png`, Anthony's cartoon taken from his PSD, through `FOG_ART`. The caption is `assets/memes/fog-claimeth.png`, reworded from the pixels of his THE FOG COMETH layer.

## Editing by hand

To change the look of every card, edit the one master file, `decision-desk/assets/kit/projection-template-fog.psd`. Save it and ask Claude to apply it. Claude compares it with `_projection-template-fog-original.psd`, carries your changes into the drawing code and redraws the cards. Neither file is in git. To rebuild the template:

```bash
.venv/Scripts/python tools/make_projection_art.py --template
```

To change just one card, make a PSD of it in `assets/kit/psd/`:

```bash
.venv/Scripts/python tools/make_projection_art.py --psd GA-SEN_D
```

Neither command touches a PSD that already exists, so your edits are safe. Add `--replace` to start over.

Each layer is a separate piece of the card, in groups that keep the card's stacking order:

| Group | Layers |
|---|---|
| Text | Each line of the name, the check mark, `WINNER`, the race title, the alert pill, the flip badge |
| Portrait | The photo, its shadow, the party-colored shadow tint and the rim light. Hide the last two to remove the color grade |
| State | The ghost, the edges, the outline past the card, the district and the small badge |
| Fog | The forest image, your images group (the loser and the caption), then what goes over them: a faint copy of the forest and the mist. Move an image lower to sink it further |
| Card | The glass gradient, the light streaks, the shadow, the footer strip and the backdrop |

A group can appear twice when something sits between its layers. The footer strip has to be in front of the portrait, for example. Text is pixels rather than live type, so to change wording, hide the layer and type over it. The fonts are Arial Bold and Anton, which is in `assets/fonts`.

The bottom layer is Discord's dark background, hidden by default. Turn it on to preview the card, then hide it again before exporting, or the transparent corners will export as solid grey.

When you're done, use File > Export > Export As > PNG with transparency on, and save over `decision-desk/assets/calls/GA-SEN_D.png`. The bot posts your file from then on, and the tools leave it alone. Keep the PSD if you want to edit it again.

Paint.NET can't open PSDs without a third-party plugin. To edit there, export the layers as PNGs instead and open them with Layers > Import From File:

```bash
.venv/Scripts/python tools/make_projection_art.py --kit GA-SEN_D
```

## Photos and cutouts

Portraits come from Wikipedia, free-licensed only. `assets/candidates/credits.json` records the author and license of each. `tools/fetch_portraits.py` downloads missing ones, and `tools/cut_portraits.py` removes the backgrounds. Neither is needed to run the bot.

To add a portrait that's missing or fix a bad one, save the photo as `assets/candidates/<RACE>_<PARTY>.jpg`, run `cut_portraits.py <RACE>_<PARTY>`, then redraw that race. If you'd rather cut it out yourself, save a transparent PNG straight to `assets/candidates/cutouts/`. It also needs a face box in `cutouts/_faces.json`, which `cut_portraits.py` writes for you.
