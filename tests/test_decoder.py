"""Self-contained tests: they build a tiny synthetic .CAM in memory, so no game data is needed.

    python -m unittest discover -s tests
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import cam_decode as c  # noqa: E402


def encode_row(pixels):
    """Tiny encoder for tests: runs of >=4 equal non-zero bytes become `00 nn vv`, the rest
    literal; zeros are emitted as `00 nn` literal groups."""
    out = bytearray()
    i = 0
    while i < len(pixels):
        v = pixels[i]
        j = i
        while j < len(pixels) and pixels[j] == v and j - i < 127:
            j += 1
        if j - i >= 4:
            out += bytes([0, ((j - i) << 1) | 1, v]); i = j
        elif v:
            out.append(v); i += 1
        else:                               # a zero pixel as a 1-byte literal group
            out += bytes([0, 2, 0]); i += 1
    return bytes(out)


def build_cam(rows, palette6=None):
    palette6 = palette6 or bytes(range(64)) * 12         # 768 bytes, all <= 63
    assert len(palette6) == 768 and max(palette6) <= 63
    head = b'(C) test\0' + bytes(0x40 - 9) + palette6
    hdr = (c.MARK + bytes(10))[:14]                          # first marker ends the palette
    sect = c.MARK + bytes(2)                                 # second marker, then a zero word
    rowdata = [encode_row(r) for r in rows]
    offs, pos = [], 0
    for rd in rowdata:
        offs.append(pos); pos += len(rd)
    table = struct.pack('<H', 0)                             # unused first word (h + 6)
    for i, o in enumerate(offs):                             # entry i sits at h + 8 + 2i
        table += struct.pack('<H', 2 * c.H - 2 * i + o)      # distance to its row's first token
    return head + hdr + sect + table + b''.join(rowdata)


class DecoderTests(unittest.TestCase):
    def setUp(self):
        self.rows = []
        for y in range(c.H):
            row = bytes((x * 3 + y) % 250 + 1 for x in range(c.W))
            if y % 7 == 0:
                row = bytes([9]) * 100 + bytes([0]) * 3 + bytes([200]) * 217   # runs + zero pixels
            self.rows.append(row)
        self.cam = build_cam(self.rows)

    def test_roundtrip(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.CAM', delete=False) as f:
            f.write(self.cam)
        try:
            res = c.decode_file(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(res['missing'], 0)
        self.assertEqual(bytes(v for row in res['pixels'] for v in row), b''.join(self.rows))

    def test_palette(self):
        pal = c.read_palette(self.cam)
        self.assertEqual(len(pal), 256)
        self.assertEqual(pal[1], (12, 16, 20))   # 6-bit (3,4,5) scaled to 8-bit

    def test_odd_run_even_literal(self):
        # 00 07 55 -> 3 x 0x55 ; 00 06 01 00 02 -> literals 1, 0, 2
        px = c._decode_span(bytes([0, 7, 0x55, 0, 6, 1, 0, 2]), 0, 8, 320)
        self.assertEqual(px, bytes([0x55] * 3 + [1, 0, 2]))

    def test_sprite_row_spans(self):
        # [skip 2][nn 6 -> 3 literals 7 8 9][skip 1][nn 5 -> 2 x 4][right margin 12] for w = 20
        row = bytes([2, 6, 7, 8, 9, 1, 5, 4, 12])
        spans = c._parse_sprite_row(row, 0, len(row), 20)
        self.assertEqual(spans, [(2, bytes([7, 8, 9])), (6, bytes([4, 4]))])
        self.assertIsNone(c._parse_sprite_row(row, 0, len(row), 21))      # widths must add up

    def test_short_base_row_filled_by_sprite(self):
        # a sprite covers columns 4..9; the base row stores the other 314 px plus two extra
        # fringe px (one at each edge of the covered stretch) that are skipped
        B = bytes((i % 250) + 1 for i in range(316))
        base = [(B, B)] + [(bytes([7]) * 320,) * 2] * 199
        spr = dict(name='T', depth=0, x0=4, y0=0, w=6, h=1,
                   rows=[[(0, bytes([101, 102, 103, 104, 105, 106]))]])
        row = c.assemble_rows(base, [spr])[0]
        self.assertEqual(row[:4], list(B[:4]))
        self.assertEqual(row[4:10], [101, 102, 103, 104, 105, 106])
        self.assertEqual(row[10:], list(B[6:]))


if __name__ == '__main__':
    unittest.main()
