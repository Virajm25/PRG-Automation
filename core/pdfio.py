"""Low-level PDF access: chars, lines of text, rotated text, scanned-page detection, OCR fallback."""
import io, os
import pdfplumber


def open_pdf(path):
    return pdfplumber.open(path)


def is_scanned(page, min_chars=40):
    """A page with (almost) no text objects but with images is treated as scanned -> OCR."""
    return len(page.chars) < min_chars and len(page.images) > 0


def page_png(page, resolution=110):
    im = page.to_image(resolution=resolution).original
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


_TESS = ["C:/Program Files/Tesseract-OCR/tesseract.exe", "C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"]


def _find_tesseract():
    """Locate tesseract.exe: TESSERACT_CMD env, a copy bundled next to the app (installer), then the default install folders."""
    import sys, shutil
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for c in (os.environ.get("TESSERACT_CMD"), os.path.join(base, "tesseract", "tesseract.exe"), shutil.which("tesseract"), *_TESS):
        if c and os.path.exists(c):
            return c
    return None


def ocr_available():
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return _find_tesseract() is not None


def ocr_page_words(page, resolution=300):
    """OCR fallback -> list of dict(text,x0,x1,top,bottom) in PDF points. Needs tesseract installed."""
    try:
        import pytesseract
    except ImportError:
        return []
    exe = _find_tesseract()
    if not exe:
        return []
    pytesseract.pytesseract.tesseract_cmd = exe
    tess_dir = os.path.join(os.path.dirname(exe), "tessdata")
    if os.path.isdir(tess_dir):
        os.environ.setdefault("TESSDATA_PREFIX", tess_dir)
    im = page.to_image(resolution=resolution).original
    d = pytesseract.image_to_data(im, output_type=pytesseract.Output.DICT, config="--psm 11")
    s = 72.0 / resolution
    out = []
    for i, t in enumerate(d["text"]):
        t = t.strip()
        if not t or float(d["conf"][i]) < 30:
            continue
        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
        out.append(dict(text=t, x0=x * s, x1=(x + w) * s, top=y * s, bottom=(y + h) * s))
    return out


def ocr_to_chars(words):
    """Make OCR words look like pdf chars so the same parsers work (one 'char' per word)."""
    return [dict(text=w["text"], x0=w["x0"], x1=w["x1"], top=w["top"], bottom=w["bottom"], upright=True)
            for w in words]


def upright_chars(page_or_chars):
    ch = page_or_chars.chars if hasattr(page_or_chars, "chars") else page_or_chars
    return [c for c in ch if c.get("upright", True) and c["text"].strip()]


def rotated_chars(page):
    return [c for c in page.chars if not c.get("upright", True) and c["text"].strip()]


def join_chars_line(chars, space_gap=2.2):
    chars = sorted(chars, key=lambda c: c["x0"])
    out, prev = [], None
    for c in chars:
        if prev is not None and c["x0"] - prev["x1"] > space_gap:
            out.append(" ")
        out.append(c["text"])
        prev = c
    return "".join(out)


def cluster_lines(chars, ytol=5.0):
    """Group chars into visual lines by y (tolerates glyph jitter in some EPLAN exports)."""
    chars = sorted(chars, key=lambda c: c["top"])
    lines = []
    for c in chars:
        if lines and abs(c["top"] - lines[-1]["y"]) <= ytol:
            L = lines[-1]
            L["chars"].append(c)
            L["y"] = sum(x["top"] for x in L["chars"]) / len(L["chars"])
        else:
            lines.append({"y": c["top"], "chars": [c]})
    return lines


def _mk(run):
    return dict(x=sum(c["x0"] for c in run) / len(run), y0=min(c["top"] for c in run),
                y1=max(c["bottom"] for c in run), text="".join(c["text"] for c in run))


def rotated_strings(page, xtol=3.0):
    """Rotated (90 deg) text -> list of dict(x,y0,y1,text); reads bottom-to-top."""
    groups = []
    for c in sorted(rotated_chars(page), key=lambda c: c["x0"]):
        for g in groups:
            if abs(g["x"] - c["x0"]) <= xtol:
                g["chars"].append(c)
                break
        else:
            groups.append({"x": c["x0"], "chars": [c]})
    res = []
    for g in groups:
        cs = sorted(g["chars"], key=lambda c: -c["bottom"])
        run = [cs[0]]
        for c in cs[1:]:
            if run[-1]["top"] - c["bottom"] > 6:
                res.append(_mk(run))
                run = [c]
            else:
                run.append(c)
        res.append(_mk(run))
    return res


def page_text(page):
    return page.extract_text() or ""
