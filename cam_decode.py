#!/usr/bin/env python3
"""
cam_decode.py - extract the background image from a BioForge (1995, Origin Systems) room
file (ROOMS/*.CAM) to a PNG.

    python cam_decode.py ROOMS/CE31.CAM               # -> CE31.png next to the input
    python cam_decode.py ROOMS/CE31.CAM -o out.png
    python cam_decode.py ROOMS/*.CAM -d outdir/       # batch

Requires Pillow (pip install pillow). You need your own copy of the game; no game data is
included in this repository. See README.md for the format description.
"""
import argparse, bisect, os, struct, sys

W, H = 320, 200
MARK = b'\x40\x01\xc8\x00'          # 320 x 200 as two LE uint16; starts every image section
SPRITE_MAGIC = b'\x01\x00\x0c\x00\x00\x00'
DIR_START = 0x9D                    # chunk directory: 21-byte entries starting here


# ---------------------------------------------------------------- token stream

def _decode_span(d, start, limit, max_px):
    """Decode tokens in d[start:limit] into at most max_px pixels.

    non-zero byte      -> one pixel
    00 nn vv (nn odd)  -> nn>>1 copies of vv
    00 nn    (nn even) -> the next nn>>1 bytes are literal pixels (may include zeros)
    """
    out = bytearray()
    p = start
    try:
        while p < limit and len(out) < max_px:
            b = d[p]
            if b:
                out.append(b); p += 1
            else:
                nn = d[p + 1]
                k = nn >> 1
                if nn == 0x81:
                    p += 3                    # contributes no pixels (see README)
                elif nn & 1:
                    out += bytes([d[p + 2]]) * k; p += 3
                else:
                    out += d[p + 2: p + 2 + k]; p += 2 + k
    except IndexError:
        pass
    return bytes(out[:max_px])


def _row_offsets(d, tab, nrows, end):
    """Row i starts at `tab + 2*i + uint16[tab + 2*i]` (a distance from the entry's own
    position). Identical rows share data. Returns (offsets, sorted unique offsets)."""
    offs = [tab + 2 * i + struct.unpack_from('<H', d, tab + 2 * i)[0] for i in range(nrows)]
    if min(offs) < tab or max(offs) > end:
        raise ValueError('row table out of range')
    return offs, sorted(set(offs))


def _row_end(starts, fo, end):
    """A row ends where the next stored row begins."""
    j = bisect.bisect_right(starts, fo)
    return starts[j] if j < len(starts) else end


def _parse_sprite_row(d, p, e, w):
    """Sprite row = spans of [skip][nn][data], then one closing byte with the right margin.
    `skip` transparent columns come first; nn odd -> nn>>1 copies of the next byte, nn even ->
    nn>>1 literal bytes. Returns [(column, pixel bytes), ...] or None if the row is malformed."""
    spans = []
    col = 0
    while p < e:
        if p == e - 1:                        # closing byte: right margin
            col += d[p]
            break
        col += d[p]
        nn = d[p + 1]
        k = nn >> 1
        if nn & 1:
            if p + 2 >= e:
                return None
            spans.append((col, bytes([d[p + 2]]) * k)); p += 3
        else:
            if p + 2 + k > e:
                return None
            spans.append((col, d[p + 2: p + 2 + k])); p += 2 + k
        col += k
    return spans if col == w else None


# ---------------------------------------------------------------- file structure

def _image_header(d):
    m = d.find(MARK, 0x40)                    # marker #1 ends the palette
    if m < 0:
        raise ValueError('not a room .CAM (no 320x200 image header)')
    h = d.find(MARK, m + 4)                   # marker #2 starts the image section
    if h < 0:
        raise ValueError('not a room .CAM (no image section)')
    return m, h


def base_rows(d):
    """Rows of the base image as (bounded, unbounded) pixel strings. A row may be shorter
    than 320 pixels: pixels covered by a sprite are not stored in it."""
    _, h = _image_header(d)
    nxt = d.find(MARK, h + 8)
    end = nxt if nxt > 0 else len(d)
    offs, starts = _row_offsets(d, h + 8, H, end)
    # a row may share its tail with another one, so its true end can lie beyond the next
    # stored row start: keep the bounded decode and a decode that runs on to `end`
    return [(_decode_span(d, fo, _row_end(starts, fo, end), W), _decode_span(d, fo, end, W))
            for fo in offs]


def read_palette(d):
    """The 256-entry palette (6-bit VGA values, R,G,B) stored in the 768 bytes directly
    before marker #1; the header also gives its offset (uint32 at 0x80). Returns None if
    that region is not a valid palette."""
    m, _ = _image_header(d)
    raw = d[m - 768:m]
    if m < 768 or max(raw) > 63:
        return None
    return [(raw[i] * 255 // 63, raw[i + 1] * 255 // 63, raw[i + 2] * 255 // 63)
            for i in range(0, 768, 3)]


def load_palette_file(path):
    """768 raw bytes of 6-bit VGA DAC values (R,G,B x 256)."""
    with open(path, 'rb') as f:
        raw = f.read()
    if len(raw) != 768 or max(raw) > 63:
        raise ValueError('palette file must be 768 bytes of 6-bit (0-63) values')
    return [(raw[i] * 255 // 63, raw[i + 1] * 255 // 63, raw[i + 2] * 255 // 63)
            for i in range(0, 768, 3)]


def sprites(d):
    """Cut-out layers listed in the chunk directory: dicts with name, depth, x0, y0, w, h and
    rows (a list of spans per row, or None for a malformed row). Chunks that are animation
    frame lists (they start with the 320x200 marker) are skipped."""
    out = []
    o = DIR_START
    while o + 21 <= len(d) and d[o:o + 1].isalnum():
        name = d[o:o + 8].split(b'\0')[0].decode('latin1')
        depth, b, size = struct.unpack_from('<III', d, o + 9)
        o += 21
        if b == 0 or b + size > len(d) or size < 24 or d[b + 4:b + 10] != SPRITE_MAGIC:
            continue
        w, h = struct.unpack_from('<HH', d, b)
        if not (0 < w <= W and 0 < h <= H) or 22 + 2 * h > size:
            continue
        x, y = struct.unpack_from('<hh', d, b + 18)
        try:
            offs, starts = _row_offsets(d, b + 22, h, b + size)
        except (ValueError, struct.error):
            continue
        rows = []
        for fo in offs:
            e = _row_end(starts, fo, b + size)
            rows.append(_parse_sprite_row(d, fo, e, w) if e > fo else None)
        out.append(dict(name=name, depth=depth, x0=-x, y0=-y, w=w, h=h, rows=rows))
    return out


# ---------------------------------------------------------------- assembly

def assemble_rows(base, sprs):
    """Reassemble the full 320x200 image. Returns 200 rows of 320 entries (palette index,
    or None where nothing could be recovered).

    Model. Sprites are cut-outs of the picture. Each one draws its spans at exact columns
    (x0 + span column); nearer sprites (smaller depth) draw over farther ones. The base row
    stores only the pixels that no sprite covers, left to right, plus about one extra pixel
    at each edge of every covered stretch (antialiasing fringe) which is skipped."""
    grid = []
    for y in range(H):
        b, b_long = base[y]
        act = []
        for s in sprs:
            k = y - s['y0']
            if 0 <= k < s['h'] and s['rows'][k]:
                act.append((s, s['rows'][k]))
        act.sort(key=lambda t: -t[0]['depth'])          # far first, near painted last
        out = [None] * W
        cov = [False] * W
        for s, spans in act:
            for col, px in spans:
                for k, v in enumerate(px):
                    x = s['x0'] + col + k
                    if 0 <= x < W:
                        out[x] = v; cov[x] = True
        slots = []                                       # covered stretches: [a, b)
        x = 0
        while x < W:
            if cov[x]:
                a = x
                while x < W and cov[x]:
                    x += 1
                slots += [a, x - 1] if x - a > 1 else [a]
            else:
                x += 1
        unc = W - sum(cov)
        if len(b) < unc:                                 # row shares its tail: use the longer read
            b = b_long
            extra = len(slots)
        else:
            extra = len(b) - unc
        skip = set(slots[:max(0, extra)])
        bi = 0
        for x in range(W):
            if not cov[x]:
                if bi < len(b):
                    out[x] = b[bi]; bi += 1
            elif x in skip:
                bi += 1
        grid.append(out)
    return grid


def frames(d):
    """Animation frames from the chunks that start with the 320x200 marker (door openings,
    blinking lights, character parts...). Each frame: dict(chunk, index, x0, y0, w, h, rows)
    in the same span format as sprites()."""
    out = []
    o = DIR_START
    while o + 21 <= len(d) and d[o:o + 1].isalnum():
        name = d[o:o + 8].split(b'\0')[0].decode('latin1')
        depth, b, size = struct.unpack_from('<III', d, o + 9)
        o += 21
        if b == 0 or b + size > len(d) or d[b:b + 4] != MARK:
            continue
        count = struct.unpack_from('<H', d, b + 4)[0]
        for i in range(count):
            try:
                off, ln = struct.unpack_from('<IH', d, b + 12 + 6 * i)
            except struct.error:
                break
            p = b + off
            if off == 0 or ln < 12 or p + ln > len(d):
                continue
            _, w, h, x, y = struct.unpack_from('<HHHhh', d, p)
            if not (0 < w <= W and 0 < h <= H):
                continue
            try:
                offs, starts = _row_offsets(d, p + 10, h, p + ln)
            except (ValueError, struct.error):
                continue
            rows = []
            for fo in offs:
                e = _row_end(starts, fo, p + ln)
                rows.append(_parse_sprite_row(d, fo, e, w) if e > fo else None)
            out.append(dict(chunk=name, index=i, x0=-x, y0=-y, w=w, h=h, rows=rows))
    return out


def frame_image(fr, pal):
    """RGBA image of one animation frame, cropped to its own box (transparent elsewhere)."""
    from PIL import Image
    im = Image.new('RGBA', (fr['w'], fr['h']), (0, 0, 0, 0))
    px = im.load()
    for y, spans in enumerate(fr['rows']):
        for col, data in spans or ():
            for k, v in enumerate(data):
                px[col + k, y] = pal[v] + (255,)
    return im


def decode_file(path, palette=None):
    """Returns dict(pixels=200 rows of 320 (index|None), palette, missing, sprites, embedded)."""
    with open(path, 'rb') as f:
        d = f.read()
    sp = sprites(d)
    grid = assemble_rows(base_rows(d), sp)
    emb = None if palette else read_palette(d)
    pal = palette or emb or [(i, i, i) for i in range(256)]
    missing = sum(v is None for row in grid for v in row)
    return dict(pixels=grid, palette=pal, missing=missing, sprites=[s['name'] for s in sp],
                embedded=emb is not None)


def save_png(res, out):
    """RGBA PNG; pixels that could not be recovered are fully transparent."""
    from PIL import Image
    pal = res['palette']
    im = Image.new('RGBA', (W, H))
    px = im.load()
    for y, row in enumerate(res['pixels']):
        for x, v in enumerate(row):
            px[x, y] = (0, 0, 0, 0) if v is None else pal[v] + (255,)
    im.save(out)


def dump_frames(path, pal, outdir):
    with open(path, 'rb') as f:
        d = f.read()
    stem = os.path.splitext(os.path.basename(path))[0]
    os.makedirs(outdir, exist_ok=True)
    for fr in frames(d):
        name = f"{stem}_{fr['chunk']}_{fr['index']:02d}_x{fr['x0']}_y{fr['y0']}.png"
        frame_image(fr, pal).save(os.path.join(outdir, name))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+')
    ap.add_argument('-o', '--output', help='output PNG (single input only)')
    ap.add_argument('-d', '--outdir', help='output directory for batch mode')
    ap.add_argument('--frames', metavar='DIR', help='also write the animation frames of each file to DIR')
    ap.add_argument('--palette', help='768-byte raw 6-bit palette file (overrides embedded palette)')
    a = ap.parse_args()
    if a.output and len(a.files) > 1:
        ap.error('-o only works with a single input; use -d for several')
    pal = load_palette_file(a.palette) if a.palette else None
    failed = 0
    for f in a.files:
        base = os.path.splitext(os.path.basename(f))[0] + '.png'
        out = a.output or os.path.join(a.outdir or os.path.dirname(f) or '.', base)
        try:
            res = decode_file(f, pal)
            os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
            save_png(res, out)
            if a.frames:
                dump_frames(f, res['palette'], a.frames)
            note = ''
            if res['missing']:
                note = f"  [{res['missing']} px unrecovered -> transparent]"
            if not res['embedded'] and not a.palette:
                note += '  [grayscale: no embedded palette]'
            print(f"{f}: ok  {len(res['sprites'])} sprite layers -> {out}{note}")
        except Exception as e:
            failed += 1
            print(f'{f}: FAILED ({e})', file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
