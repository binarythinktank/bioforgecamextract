# bioforgecamextract

Extract the pre-rendered room backgrounds from **BioForge** (Origin Systems, 1995, DOS) `.CAM`
files to PNG.

BioForge draws its rooms as fixed pre-rendered 320x200 8-bit images with real-time 3D characters
composited on top. Each room lives in `ROOMS/*.CAM` on the game disc. This tool rebuilds the full
background image from those files. The format was worked out by reverse engineering; see
[Format](#format).

> **This repository contains no game data.** You need your own copy of BioForge (for example the
> 1995 CD-ROM release). Extract the `.CAM` files from the disc yourself. Do not commit or
> redistribute the game's files or images extracted from them; they are the property of
> Electronic Arts. This project is not affiliated with or endorsed by EA or Origin.

## Usage

Requires Python 3.8+ and [Pillow](https://pypi.org/project/pillow/) (`pip install pillow`).

```bash
python cam_decode.py ROOMS/CE31.CAM                  # writes CE31.png next to the input
python cam_decode.py ROOMS/CE31.CAM -o room.png
python cam_decode.py ROOMS/*.CAM -d out/             # every room into out/
python cam_decode.py ROOMS/AIR1.CAM --frames frames/ # also dump the animation frames
python cam_decode.py ROOMS/X.CAM --palette dac.pal   # use your own 768-byte 6-bit palette
```

The output is an RGBA PNG. Pixels that could not be recovered are fully transparent, and the
status line says how many there were. The palette is read from the file itself.

As a library:

```python
import cam_decode
res = cam_decode.decode_file('CE31.CAM')
# res['pixels']: 200 rows of 320 palette indices (None = unrecovered); res['palette']: 256 (r, g, b)
```

Run the tests (they use a synthetic file, no game data needed): `python -m unittest discover -s tests`

## Status

Tested against all 229 `ROOMS/*.CAM` files of the retail CD:

| Result | Files |
| --- | --- |
| Complete 320x200 background, no unrecovered pixels | 198 |
| Complete apart from a handful of pixels (3-64) at row ends | 11 |
| No room background in the file (character / effect / dialogue-portrait files, `DEMO1`) | 20 |

Accuracy: `CE31` is verified pixel-exact against a frame captured from the running game (apart
from the composited 3D characters). It has no sprites, so it only tests the base image and the
token grammar. For the other rooms I checked the results by eye; the images are seamless, which
strongly suggests the layout below is right, but only one room was checked against ground truth.
Two details are heuristics, see [Known gaps](#known-gaps).

## Format

All integers are little-endian. A `.CAM` file starts with the text `(C) 1994 Origin Systems Inc.`
and a header with these fields (offsets are in the file):

```
0x80  uint32   palette offset (always marker #1 - 768)
0x84  uint32   palette size, 0x300
0x9D  entries  chunk directory, 21 bytes each:  name[8], 0, depth u32, offset u32, size u32
...            palette: 256 x RGB, 6-bit VGA values (0-63)
       40 01 C8 00   320 x 200 as two uint16                        (marker #1)
       ...           10 bytes
       40 01 C8 00   again                                          (marker #2, "h")
       uint16 x 200  row table at h+8, then the base image data
```

The directory is cut short by the palette, so its last entry is a stub without offset/size. Its
chunks are contiguous and the last one runs to the end of the file. `depth` is the distance
used to sort the sprite behind or in front of the 3D characters (88888 / 99999 mean "always
background").

**Row tables.** Entry *i* holds the distance from *its own position* to the first byte of row
*i*, so `row_start[i] = table + 2*i + uint16[table + 2*i]`. Identical rows share data. A row
ends where the next stored row begins, except that rows can also share tails, so the base
image is read once bounded and once running on.

**Base image rows** are token streams:

| Bytes | Meaning |
| --- | --- |
| `vv` (non-zero) | one pixel of colour `vv` |
| `00 nn vv`, `nn` odd | run of `nn >> 1` pixels of colour `vv` |
| `00 nn` + `k` bytes, `nn` even, `k = nn >> 1` | `k` literal pixels follow; they may contain zeros |
| `00 81 vv` | contributes no pixels (rare; purpose unknown) |

A base row is **not** always 320 pixels long. It only stores the pixels that no sprite covers.

**Sprites.** A directory chunk that starts `w h  01 00 0c 00 00 00 <size> w h x y` is a sprite:
a cut-out of the picture (a doorway interior, a pillar, ...) with its own row table at chunk+22.
`(-x, -y)` is its top-left corner on the screen. Its rows are lists of spans:

```
[skip][nn][data] [skip][nn][data] ... [right margin]
```

`skip` transparent columns, then `nn` as above (odd: run of `nn>>1` copies of the next byte;
even: `nn>>1` literal bytes). The single closing byte is the right margin, so a row always adds up
to exactly `w` columns; a row of one byte is empty.

**Assembling a room.** Draw every sprite's spans at their exact columns, farthest first. Then fill
the columns nobody covers from the base row, left to right. The base row holds about one extra
pixel at each edge of every covered stretch (an antialiasing fringe), which is skipped.

**Animation chunks** start with the 320x200 marker instead: a list of frames
(`uint32 offset, uint16 size` per frame), each `flag w h x y`, a row table and span rows. They are
door openings, blinking lights and the like, and are only dumped with `--frames`, not composited.

## How it was found

The disc's room files were unreadable to every generic tool, so the format was recovered from
the running game. DOSBox-X's debugger was driven over its TCP debug protocol to dump the VGA
frame buffer of a known room, giving a *known plaintext* to compare against the bytes of the
matching `.CAM`. That fixed the token grammar. Everything about sprites came from noticing
that base rows are shorter than 320 pixels by exactly the number of pixels the sprites carry,
and then from checking candidate layouts by looking at the seams.

## Known gaps

- The number of fringe pixels skipped per covered stretch is inferred from the row lengths, and
  which edge they belong to is a guess. It only matters for a pixel or two at sprite edges.
- The 20 non-room files have no background at all; their pictures are animation frames
  (`--frames`).
- `00 81 vv` tokens are ignored.
- Depth-sorting between overlapping sprites is by the directory `depth` (nearest wins).
- The data is 8-bit indexed. Some rooms are drawn with a palette that the game alters at run
  time (fades, lighting); this tool uses the palette stored in the file.

## License

MIT, see [LICENSE](LICENSE). This applies to the code and documentation only, not to any game
content.
