# Projection art, and how to fit a meme to it

Every race and winner has a finished picture in `decision-desk/assets/calls/`, named `<RACE>_<PARTY>.png` (`GA-SEN_D.png`, `NE-SEN_I.png`). When that race is called, the bot posts the file exactly as it is. A retraction always gets a freshly drawn card instead, so a meme can't follow a call that got pulled back.

There are three ways to change one, from least to most work:

| You want to | Do this |
|---|---|
| Move a meme to another race, or try another caption | Edit one line of `decision-desk/projection_memes.json` and redraw that race |
| Nudge the design or add your own touches | Export its layers and edit them in Photopea, GIMP or Photoshop |
| Use something else entirely | Save your image over the file with the same name. A `.gif` keeps its animation |

Once you've changed a picture by hand, the tools won't overwrite it. To go back, delete your file and redraw that race.

## Redrawing

Run these from `decision-desk/`:

```bash
.venv/Scripts/python tools/make_projection_art.py GA-SEN_D
```

```bash
.venv/Scripts/python tools/make_projection_art.py --memes --sheet
```

The first redraws one race. Name a race without the party (`GA-SEN`) to redraw all its winners. The second redraws every meme race and writes a contact sheet of them to `assets/kit/_sheet_memes.jpg`.

## Pick the layout from the meme's shape

| The meme is | Layout | Example | Why |
|---|---|---|---|
| Wide or square (screenshot, photo, Fox chyron) | `stage` | `never-more-over.png` | The picture gets the whole top of the card, and the winner stands beside it |
| Tall (tweet thread, poster) | `side` | `messianic-delusions.webp` | It sits as a tilted print the full height of the card |
| Just words | no `meme`, only a `caption` | "PLEASE CLAP" | The poster type goes over the plain card |
| A picture with a spot for a face | `stage` with `photo_slot` | `stunt-locker.png` | The winner's photo goes inside the meme itself |

A meme wider than about 3:2 fills `stage` best. Taller than 1:1, use `side`.

## The plan file

One line per race and winner. Any race not listed gets the plain card.

```json
"TX-SEN_D": {"meme": "red-army-lion.webp", "layout": "side", "max_w": 700},
"ME-SEN_D": {"caption": "PLEASE CLAP"},
"IA-SEN_D": {"caption": "IF ONLY YOU KNEW|HOW BAD IT IS"},
```

| Option | Layouts | What it does |
|---|---|---|
| `meme` | stage, side | File name in `assets/memes/templates/` |
| `caption` | all | Poster text. `\|` starts a new line |
| `caption_size` | all | Starting type size. It shrinks to fit |
| `crop` | stage, side | `[left, top, right, bottom]` as fractions of the picture, to trim it first |
| `photo_slot` | stage, side | Same fractions, marking where the winner's photo goes |
| `no_portrait` | stage, side | Leave out the cutout of the winner. Use it with `photo_slot` so they don't appear twice |
| `headline` | stage | Replaces the name in the lower third (`DIARRHEA FOREVER`) |
| `tag` | stage | Replaces the word `WINNER` |
| `portrait_behind` | stage | Stand the winner behind the lower third, for a photo that ends early (a podium, a crowd) |
| `face` | stage, side | Face height in pixels, to make the winner bigger or smaller |
| `max_w`, `max_h`, `tilt`, `cx` | side | The print's largest size, its angle, and the x position of its center |

To find `photo_slot` or `crop` numbers, hover over the corners of the spot in any image editor, then divide x by the picture's width and y by its height. In the 575 × 434 locker meme, the photo's left edge at x = 313 gives `0.545`.

## Adding a new meme

1. Save it to `assets/memes/templates/` with a plain name.
2. Add a line to `projection_memes.json`, choosing the layout from its shape.
3. Redraw that race and look at it at the size Discord shows it.

## Editing by hand

```bash
.venv/Scripts/python tools/make_projection_art.py --kit GA-SEN_D
```

That writes every layer as a numbered PNG to `assets/kit/GA-SEN_D/`: `glass`, `state-ghost`, `state-outline`, `portrait`, `name`, `meme` and so on. All are 1600 × 900, so they line up when you open them as layers. In Photopea, use File > Open & Place. Export the finished picture to `assets/calls/GA-SEN_D.png`.

## What makes these read well

- **Judge it small.** Discord previews the 1600-wide picture at about 550 pixels, a third of its size. Text under about 40 pixels tall won't read without a click. Crop tweets down to the punchline, and use the translation in the Punjabi and Amharic ones rather than the whole thread.
- **Never cover the joke.** Top-and-bottom-text memes need both lines showing, which is why `stage` shrinks a picture to fit above the lower third rather than running it behind.
- **Keep three things on every card.** Show the winner's name, the race and the party color. The card has to work as the call even for someone who doesn't get the joke.
- **Enlarge up to about 3×.** The tool sharpens after enlarging. Past that, look for a bigger copy. A crunchy meme is fine, but a blurry one looks like a mistake.
- **Keep the corners transparent.** The glass floats on Discord's background because the PNG has no background of its own. Export as PNG, and save a JPG only for full-bleed pictures.
- **Match the joke to the race, not just the side.** The vaccine squad on Amy Acton's card is funny because of who she is. The same picture on a random House seat is only a picture.
- **Spread them out.** A meme lands once. Use each picture on one big race, or two at most, and let the text captions cover the rest.

## Photos and cutouts

Portraits come from Wikipedia, free-licensed only. `assets/candidates/credits.json` records the author and license of each. `tools/fetch_portraits.py` downloads missing ones, and `tools/cut_portraits.py` removes the backgrounds. Neither is needed to run the bot.

To add a portrait that's missing or fix a bad one, save the photo as `assets/candidates/<RACE>_<PARTY>.jpg`, run `cut_portraits.py <RACE>_<PARTY>`, then redraw that race. If you'd rather cut it out yourself, save a transparent PNG straight to `assets/candidates/cutouts/`. It also needs a face box in `cutouts/_faces.json`, which `cut_portraits.py` writes for you.
