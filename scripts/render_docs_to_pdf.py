"""Clean markdown -> PDF renderer using fpdf2 primitives (full layout control)."""
import re, sys
from fpdf import FPDF
D = "/usr/share/fonts/truetype/dejavu/"
MAROON = (140, 30, 30)
GRAY = (110, 110, 110)
CODEBG = (244, 244, 244)
RULE = (200, 200, 200)

class PDF(FPDF):
    footer_label = ""
    def footer(self):
        self.set_y(-12); self.set_font("S", "I", 8); self.set_text_color(*GRAY)
        self.cell(0, 8, self.footer_label, align="L")
        self.cell(0, 8, f"Page {self.page_no()}", align="R")
        self.set_text_color(0, 0, 0)

def new_pdf(footer):
    p = PDF(format="Letter"); p.footer_label = footer
    p.set_margins(20, 18, 18); p.set_auto_page_break(True, 16)
    p.add_font("S", "", D+"DejaVuSans.ttf"); p.add_font("S", "B", D+"DejaVuSans-Bold.ttf")
    p.add_font("S", "I", D+"DejaVuSans.ttf"); p.add_font("S", "BI", D+"DejaVuSans-Bold.ttf")
    p.add_font("M", "", D+"DejaVuSansMono.ttf"); p.add_font("M", "B", D+"DejaVuSansMono-Bold.ttf")
    p.add_page(); return p

TOKEN = re.compile(r"(\*\*.+?\*\*|\*[^*]+?\*|`[^`]+?`|\[[^\]]+?\]\([^)]+?\))")
def runs(text):
    out = []
    for part in TOKEN.split(text):
        if not part: continue
        if part.startswith("**") and part.endswith("**"): out.append((part[2:-2], "B"))
        elif part.startswith("`") and part.endswith("`"): out.append((part[1:-1], "C"))
        elif part.startswith("*") and part.endswith("*"): out.append((part[1:-1], "I"))
        elif part.startswith("["):
            m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part)
            label, url = m.group(1), m.group(2)
            out.append((label, "B" if url.endswith(".md") else "L"))  # internal->bold text; ext->link text
            if url.startswith("http"): out.append((f" ({url})", "U"))
        else: out.append((part, "N"))
    return out

def set_run_font(p, style, size):
    if style == "C": p.set_font("M", "", size-0.5); p.set_text_color(80,80,80)
    elif style == "B": p.set_font("S", "B", size); p.set_text_color(0,0,0)
    elif style == "I": p.set_font("S", "I", size); p.set_text_color(0,0,0)
    elif style == "U": p.set_font("S", "I", size-1); p.set_text_color(*GRAY)
    elif style == "L": p.set_font("S", "B", size); p.set_text_color(40,40,120)
    else: p.set_font("S", "", size); p.set_text_color(0,0,0)

def write_rich(p, text, x0, size=10.5, lh=5.2):
    """Word-wrap mixed-style runs from x0 to right margin, with hanging indent."""
    right = p.w - p.r_margin
    p.set_x(x0)
    first = True
    for txt, style in runs(text):
        set_run_font(p, style, size)
        words = re.split(r"(\s+)", txt)
        for w in words:
            if w == "": continue
            ww = p.get_string_width(w)
            if p.get_x() + ww > right and p.get_x() > x0 and not w.isspace():
                p.ln(lh); p.set_x(x0)
            if w.isspace() and p.get_x() == x0: continue
            p.cell(ww, lh, w)
    p.ln(lh)
    p.set_text_color(0,0,0)

def render(md_path, pdf_path, footer):
    md = open(md_path, encoding="utf-8").read()
    md = md.replace("✅","[ok]").replace("❌","[x]").replace("⚠️","!").replace("⚠","!")
    p = new_pdf(footer)
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        # fenced code block
        if ln.strip().startswith("```"):
            i += 1; code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i]); i += 1
            i += 1
            p.ln(1.5); p.set_font("M","",9); p.set_fill_color(*CODEBG)
            x0 = p.l_margin
            for c in code:
                p.set_x(x0)
                p.cell(p.w-p.l_margin-p.r_margin, 5.0, "  "+c.rstrip(), fill=True)
                p.ln(5.0)
            p.ln(1.5); continue
        # table block
        if "|" in ln and i+1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i+1]):
            rows = []
            while i < len(lines) and "|" in lines[i]:
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            header, body = rows[0], rows[2:]
            render_table(p, header, body); p.ln(2); continue
        # headings
        m = re.match(r"^(#{1,6})\s+(.*)", ln)
        if m:
            level = len(m.group(1)); txt = re.sub(r"[`*]","",m.group(2))
            sizes = {1:18,2:15,3:12.5,4:11}; size = sizes.get(level,11)
            p.ln(3 if level<=2 else 2)
            p.set_font("S","B",size); p.set_text_color(*(MAROON if level<=3 else (0,0,0)))
            p.multi_cell(p.w-p.l_margin-p.r_margin, size*0.5, txt, align="L")
            p.set_text_color(0,0,0); p.ln(1.2); i += 1; continue
        # hr
        if re.match(r"^\s*---+\s*$", ln):
            p.ln(1); p.set_draw_color(*RULE); p.set_line_width(0.3)
            p.line(p.l_margin, p.get_y(), p.w-p.r_margin, p.get_y()); p.ln(2.5); i += 1; continue
        # blockquote
        if ln.startswith(">"):
            txt = ln.lstrip(">").strip()
            p.set_draw_color(*RULE); p.set_line_width(1.2)
            y=p.get_y(); 
            write_rich(p, txt, p.l_margin+5, size=10.5)
            p.line(p.l_margin+1.5, y, p.l_margin+1.5, p.get_y()-1); i+=1; continue
        # list items (ordered or bullet), with one nesting level
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)", ln)
        if m:
            indent = len(m.group(1)); marker = m.group(2); txt = m.group(3)
            x_marker = p.l_margin + (6 if indent>=2 else 0)
            x_text = x_marker + (7 if marker[0].isdigit() else 5)
            bullet = marker if marker[0].isdigit() else "•"
            p.set_x(x_marker); p.set_font("S","",10.5); p.set_text_color(0,0,0)
            p.cell(x_text-x_marker, 5.2, bullet)
            write_rich(p, txt, x_text, size=10.5)
            i += 1; continue
        # blank
        if ln.strip() == "":
            p.ln(2.2); i += 1; continue
        # paragraph (gather until blank/structural)
        para = [ln]; i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6}\s|\s*([-*]|\d+\.)\s|>|```|\s*---+\s*$)", lines[i]) and "|" not in lines[i]:
            para.append(lines[i]); i += 1
        write_rich(p, " ".join(s.strip() for s in para), p.l_margin, size=10.5)

    p.output(pdf_path); print("wrote", pdf_path.split("/")[-1], p.page_no(), "pages")

def render_table(p, header, body):
    usable = p.w - p.l_margin - p.r_margin
    ncol = len(header)
    # weight columns by max content length (capped), give prose columns more room
    weights = []
    for c in range(ncol):
        cells = [header[c]] + [r[c] if c < len(r) else "" for r in body]
        weights.append(min(max(len(x) for x in cells), 60) + 4)
    tot = sum(weights); widths = [usable*w/tot for w in weights]
    lh = 4.8
    def row(cells, bold=False, fill=False):
        p.set_font("S","B" if bold else "",9)
        # measure heights
        heights = []
        for c in range(ncol):
            txt = re.sub(r"[`*]","",cells[c] if c < len(cells) else "")
            n = len(p.multi_cell(widths[c], lh, txt, split_only=True))
            heights.append(max(1,n))
        rh = max(heights)*lh + 1.5
        if p.get_y()+rh > p.h - p.b_margin: p.add_page()
        x = p.l_margin; y = p.get_y()
        for c in range(ncol):
            txt = re.sub(r"[`*]","",cells[c] if c < len(cells) else "")
            if fill: p.set_fill_color(245,245,245); p.rect(x,y,widths[c],rh,style="F")
            p.set_xy(x, y+0.8)
            p.multi_cell(widths[c], lh, txt, align="L")
            x += widths[c]
        p.set_draw_color(*RULE); p.set_line_width(0.2); p.line(p.l_margin,y+rh,p.l_margin+usable,y+rh)
        p.set_xy(p.l_margin, y+rh)
    row(header, bold=True, fill=True)
    for r in body: row(r)

if __name__ == "__main__":
    DOCS = [
        ("docs/CONSULTING_DELIVERABLE.md", "/tmp/Platform_Capabilities_Briefing.pdf",
         "Healthcare Fraud Whistleblower Origination Platform — Confidential"),
        ("docs/SETUP_GUIDE.md", "/tmp/Setup_Guide_Step_by_Step.pdf", "Setup Guide — Confidential"),
        ("docs/platform/12-data-runbook.md", "/tmp/Data_Runbook_Where_To_Download.pdf", "Data Runbook — Confidential"),
        ("docs/READING_THE_OUTPUTS.md", "/tmp/Reading_The_Outputs.pdf", "Reading the Outputs — Confidential"),
        ("docs/TROUBLESHOOTING.md", "/tmp/Troubleshooting.pdf", "Troubleshooting — Confidential"),
        ("docs/WHAT_WAS_BUILT.md", "/tmp/What_Was_Built.pdf", "What Was Built — Confidential"),
    ]
    for md, pdf, foot in DOCS:
        render(md, pdf, foot)
