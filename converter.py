"""
Paint PDF -> clean A4 PDF converter.

Uses only free, permissively licensed libraries (no paid API, no AGPL):
pypdfium2 (render), numpy + Pillow (image work), reportlab (write PDF).
"""
import gc
import math
import os
from dataclasses import dataclass

import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from reportlab import rl_config
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

rl_config.useA85 = 0  # binary streams: much faster and ~25% smaller than ASCII85
Image.MAX_IMAGE_PIXELS = None  # we do our own size guard below

# Pixel budget per rendered page. DPI is lowered automatically for huge pages so small
# servers (512 MB RAM) do not crash. Raise it on bigger hosts, e.g. 30000000.
MAX_PIXELS_PER_PAGE = int(os.environ.get("MAX_PIXELS_PER_PAGE", 12_000_000))


@dataclass
class Options:
    dpi: int = 200
    left_margin: int = 120          # extra white space on the left (px at 200 DPI)
    top_margin: int = 45            # header zone (PDF points)
    bottom_margin: int = 45         # footer zone (PDF points)
    side_margin: int = 10
    header_text: str = ""
    page_numbers: bool = True
    keep_diagrams: bool = True      # never split a diagram across two pages


class ConvertError(Exception):
    """Error with a message that is safe to show to the user."""


class Cfg:
    """All pixel values are defined at 200 DPI and scaled to the real DPI."""

    def __init__(self, o: Options, dpi: float):
        k = dpi / 200.0
        px = lambda v: max(1, int(round(v * k)))
        self.o = o
        self.dpi = dpi
        self.left_margin = int(round(o.left_margin * k))
        self.blank_threshold = 245
        self.min_gap_rows = max(1, int(round(3 * k)))
        self.max_gap = px(60)
        self.page_gap = px(60)
        self.pad = px(15)
        self.search = px(250)
        self.diagram_min_h = px(70)
        self.diagram_merge_gap = px(40)
        self.label_reach = px(120)
        self.min_page_fill = 0.35


# ---------------------------------------------------------------- image helpers
def min_rgb(arr):
    """Per-pixel darkest channel. Stays uint8 and is much faster than arr.min(axis=2)."""
    return np.minimum(np.minimum(arr[..., 0], arr[..., 1]), arr[..., 2])


def blank_rows(arr, c):
    dark = min_rgb(arr) < c.blank_threshold
    return dark.sum(axis=1) < c.min_gap_rows


def compress_gaps(arr, c):
    """Trim blank top/bottom (keeping padding) and shrink long blank gaps."""
    blank = blank_rows(arr, c)
    if blank.all():
        return None

    top = max(0, int(np.argmax(~blank)) - c.pad)
    bottom = min(len(blank), len(blank) - int(np.argmax(~blank[::-1])) + c.pad)
    arr = arr[top:bottom]
    blank = blank[top:bottom]

    padded = np.concatenate(([False], blank, [False]))
    diff = np.diff(padded.astype(int))
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0]

    pieces, prev = [], 0
    for s, e in zip(starts, ends):
        if e - s > c.max_gap:
            pieces.append(arr[prev:s + c.max_gap])
            prev = e
    pieces.append(arr[prev:])
    return np.vstack(pieces)


def content_bands(blank):
    padded = np.concatenate(([True], blank, [True]))
    d = np.diff(padded.astype(int))
    return list(zip(np.where(d == -1)[0], np.where(d == 1)[0]))


def find_protected(arr, c):
    blank = blank_rows(arr, c)
    n = len(blank)
    protected = np.zeros(n, dtype=bool)
    if not c.o.keep_diagrams:
        return blank, protected

    groups = []
    for s, e in content_bands(blank):
        if e - s >= c.diagram_min_h:
            if groups and s - groups[-1][1] <= c.diagram_merge_gap:
                groups[-1][1] = e
            else:
                groups.append([s, e])

    for s, e in groups:
        protected[s:min(n, e + c.label_reach)] = True
    return blank, protected


def find_break(arr, target, c):
    n = arr.shape[0]
    target = min(target, n)

    # Only look at the rows around this page (fast, low memory)
    arr = arr[:target + c.diagram_min_h + c.label_reach]
    blank, protected = find_protected(arr, c)
    ok = blank[:target] & ~protected[:target]

    lo = int(target * c.min_page_fill)
    idx = np.where(ok[lo:])[0]
    if len(idx):
        return lo + int(idx[-1])

    # Fallback (diagram taller than a page): cleanest row near the page end
    start = max(1, target - c.search)
    scores = (min_rgb(arr[start:target]) < c.blank_threshold).sum(axis=1)
    return start + int(np.where(scores <= scores.min())[0][-1])


def trim_top(arr, c):
    """Drop blank rows at the top (keeping padding); scans in chunks to stay fast."""
    step = 400
    for start in range(0, arr.shape[0], step):
        blank = blank_rows(arr[start:start + step], c)
        if not blank.all():
            return arr[max(0, start + int(np.argmax(~blank)) - c.pad):]
    return None


def page_height_px(width_px, o):
    page_w, page_h = A4
    content_w = page_w - 2 * o.side_margin
    content_h = page_h - o.top_margin - o.bottom_margin
    return int(width_px * content_h / content_w)


def draw_part(pdf, arr, page_no, o):
    page_w, page_h = A4
    content_w = page_w - 2 * o.side_margin

    img = Image.fromarray(arr)
    new_h = img.height * (content_w / img.width)
    pdf.drawImage(
        ImageReader(img), o.side_margin, page_h - o.top_margin - new_h,
        width=content_w, height=new_h,
    )

    pdf.setFont("Helvetica", 9)
    if o.header_text:
        pdf.drawString(o.side_margin + 20, page_h - o.top_margin / 2 - 3, o.header_text)
    if o.page_numbers:
        pdf.drawCentredString(page_w / 2, o.bottom_margin / 2 - 3, f"Page {page_no}")
    pdf.showPage()


# ---------------------------------------------------------------- main entry
def convert(input_path, output_path, opts: Options, max_pages=120, progress=None):
    """
    Convert a Paint-made PDF into a clean A4 PDF.
    progress(done, total) is called after each source page.
    Returns the number of output pages.
    """
    try:
        doc = pdfium.PdfDocument(input_path)
    except Exception:
        raise ConvertError("This file could not be opened. Is it a valid, unlocked PDF?")

    total = len(doc)
    if total == 0:
        raise ConvertError("The PDF has no pages.")
    if total > max_pages:
        raise ConvertError(f"The PDF has {total} pages. The limit is {max_pages}.")

    # Pick one DPI for the whole file so no page exceeds the pixel budget
    dpi = float(opts.dpi)
    biggest = max(doc[i].get_size()[0] * doc[i].get_size()[1] for i in range(total))
    max_dpi = math.sqrt(MAX_PIXELS_PER_PAGE / biggest) * 72
    dpi = min(dpi, max_dpi)
    c = Cfg(opts, dpi)

    pdf = canvas.Canvas(output_path, pagesize=A4)
    state = {"buffer": None, "pages": 0}
    target_w = None

    def flush(final=False):
        while state["buffer"] is not None:
            buffer = state["buffer"]
            h_px = page_height_px(buffer.shape[1], opts)

            if buffer.shape[0] <= h_px:
                if not final:
                    return
                part, state["buffer"] = buffer, None
            else:
                cut = find_break(buffer, h_px, c)
                part = buffer[:cut]
                state["buffer"] = trim_top(buffer[cut:], c)

            if part is not None and not blank_rows(part, c).all():
                state["pages"] += 1
                draw_part(pdf, part, state["pages"], opts)

    try:
        for i in range(total):
            img = doc[i].render(scale=dpi / 72).to_pil().convert("RGB")

            wide = Image.new("RGB", (img.width + c.left_margin, img.height), "white")
            wide.paste(img, (c.left_margin, 0))
            img = wide

            if target_w is None:
                target_w = img.width
            elif img.width != target_w:
                img = img.resize(
                    (target_w, int(img.height * target_w / img.width)), Image.LANCZOS
                )

            arr = compress_gaps(np.array(img), c)
            if arr is not None:
                if state["buffer"] is None:
                    state["buffer"] = arr
                else:
                    gap = np.full((c.page_gap, arr.shape[1], 3), 255, dtype=np.uint8)
                    state["buffer"] = np.vstack([state["buffer"], gap, arr])
                flush()

            del img, arr
            gc.collect()
            if progress:
                progress(i + 1, total)

        flush(final=True)
        if state["pages"] == 0:
            raise ConvertError("All pages in this PDF are blank.")
        pdf.save()
    finally:
        doc.close()

    return state["pages"]
