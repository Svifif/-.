"""Генерация отчётов по лабораторным работам в формате .docx.

Оформление: A4, поля 2/1/2/1.5 см, Times New Roman 14 пт, полуторный интервал,
выравнивание по ширине, автонумерация рисунков и таблиц, нумерация страниц.
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from . import config as C


class Report:
    def __init__(self, lab_no: int, lab_title: str):
        self.lab_no = lab_no
        self.lab_title = lab_title
        self.doc = Document()
        self.fig_no = 0
        self.tab_no = 0
        self._setup()

    # ------------------------------------------------------------ оформление
    def _setup(self):
        st = self.doc.styles["Normal"]
        st.font.name = "Times New Roman"
        st.font.size = Pt(14)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
        pf = st.paragraph_format
        pf.line_spacing = 1.5
        pf.space_after = Pt(0)
        pf.first_line_indent = Cm(1.25)
        pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

        s = self.doc.sections[0]
        s.page_width, s.page_height = Cm(21.0), Cm(29.7)
        s.left_margin, s.right_margin = Cm(2.5), Cm(1.5)
        s.top_margin, s.bottom_margin = Cm(2.0), Cm(2.0)
        s.different_first_page_header_footer = True   # титульный лист без номера
        self._page_numbers(s)

    def _page_numbers(self, section):
        p = section.footer.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run()
        fld = OxmlElement("w:fldSimple")
        fld.set(qn("w:instr"), "PAGE")
        run._r.addnext(fld)

    # --------------------------------------------------------------- титул
    def title_page(self):
        d = self.doc
        for text, size, bold in [
            ("МИНИСТЕРСТВО НАУКИ И ВЫСШЕГО ОБРАЗОВАНИЯ РОССИЙСКОЙ ФЕДЕРАЦИИ", 12, False),
        ]:
            self._line(text, size=size, bold=bold, align="center", spacing=1.0)
        self._line("", spacing=1.0)
        self._line("ОТЧЁТ", size=20, bold=True, align="center", spacing=1.0)
        self._line(f"по лабораторной работе №{self.lab_no}", size=14, align="center",
                   spacing=1.0)
        self._line(f"по курсу «{C.COURSE}»", size=14, align="center", spacing=1.0)
        self._line("", spacing=1.0)
        self._line(self.lab_title, size=15, bold=True, align="center", spacing=1.0)
        for _ in range(5):
            self._line("", spacing=1.0)
        self._line(f"Выполнил: {C.STUDENT}, {C.GROUP}", align="right", spacing=1.0)
        self._line(f"Проверил: {C.TEACHER}", align="right", spacing=1.0)
        for _ in range(6):
            self._line("", spacing=1.0)
        self._line(f"{C.CITY}, {C.YEAR}", align="center", spacing=1.0)
        d.add_page_break()

    def _line(self, text, size=14, bold=False, align="left", spacing=1.5):
        p = self.doc.add_paragraph()
        p.paragraph_format.first_line_indent = Cm(0)
        p.paragraph_format.line_spacing = spacing
        p.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT,
                       "center": WD_ALIGN_PARAGRAPH.CENTER,
                       "right": WD_ALIGN_PARAGRAPH.RIGHT,
                       "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}[align]
        r = p.add_run(text)
        r.font.size = Pt(size)
        r.bold = bold
        return p

    # -------------------------------------------------------------- блоки
    def h1(self, text):
        p = self.doc.add_paragraph()
        p.paragraph_format.first_line_indent = Cm(0)
        p.paragraph_format.space_before = Pt(12)
        p.paragraph_format.space_after = Pt(6)
        p.paragraph_format.keep_with_next = True
        r = p.add_run(text)
        r.bold = True
        r.font.size = Pt(15)
        return p

    def h2(self, text):
        p = self.doc.add_paragraph()
        p.paragraph_format.first_line_indent = Cm(1.25)
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.keep_with_next = True
        r = p.add_run(text)
        r.bold = True
        r.font.size = Pt(14)
        return p

    def p(self, text, bold=False, italic=False, indent=True):
        p = self.doc.add_paragraph()
        if not indent:
            p.paragraph_format.first_line_indent = Cm(0)
        r = p.add_run(text)
        r.bold = bold
        r.italic = italic
        return p

    def bullets(self, items, numbered=False):
        for i, it in enumerate(items, 1):
            p = self.doc.add_paragraph()
            p.paragraph_format.first_line_indent = Cm(1.25)
            p.paragraph_format.left_indent = Cm(0.5)
            p.paragraph_format.space_after = Pt(2)
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p.add_run(f"{i}) " if numbered else "— ")
            p.add_run(it)

    def code_block(self, text):
        for line in text.strip("\n").split("\n"):
            p = self.doc.add_paragraph()
            p.paragraph_format.first_line_indent = Cm(0)
            p.paragraph_format.left_indent = Cm(1.0)
            p.paragraph_format.line_spacing = 1.0
            p.paragraph_format.space_after = Pt(0)
            r = p.add_run(line if line else " ")
            r.font.name = "Consolas"
            r.font.size = Pt(10)

    def table(self, headers, rows, caption=None, widths=None, font=11, align_center=True):
        self.tab_no += 1
        if caption:
            self._caption(f"Таблица {self.tab_no} — {caption}", before=True)
        t = self.doc.add_table(rows=1, cols=len(headers))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        # шапка повторяется на новой странице, строки не разрываются
        trpr = t.rows[0]._tr.get_or_add_trPr()
        trpr.append(OxmlElement("w:tblHeader"))
        trpr.append(OxmlElement("w:cantSplit"))
        hdr = t.rows[0].cells
        for i, h in enumerate(headers):
            hdr[i].text = ""
            p = hdr[i].paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.first_line_indent = Cm(0)
            p.paragraph_format.line_spacing = 1.0
            r = p.add_run(str(h))
            r.bold = True
            r.font.size = Pt(font)
        for row in rows:
            cells = t.add_row().cells
            t.rows[-1]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
            for i, v in enumerate(row):
                cells[i].text = ""
                p = cells[i].paragraphs[0]
                p.paragraph_format.first_line_indent = Cm(0)
                p.paragraph_format.line_spacing = 1.0
                p.alignment = (WD_ALIGN_PARAGRAPH.CENTER if align_center and i > 0
                               else WD_ALIGN_PARAGRAPH.LEFT)
                r = p.add_run(str(v))
                r.font.size = Pt(font)
        if widths:
            for row in t.rows:
                for i, w in enumerate(widths):
                    row.cells[i].width = Cm(w)
        # все строки, кроме последней, держатся со следующей — таблица не рвётся
        for row in t.rows[:-1]:
            for cell in row.cells:
                for par in cell.paragraphs:
                    par.paragraph_format.keep_with_next = True
        self.p("", indent=False)
        return t

    def figure(self, path, caption, width_cm=16.0):
        self.fig_no += 1
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.first_line_indent = Cm(0)
        p.paragraph_format.space_before = Pt(6)
        p.add_run().add_picture(str(path), width=Cm(width_cm))
        self._caption(f"Рисунок {self.fig_no} — {caption}", before=False)

    def _caption(self, text, before=False):
        p = self.doc.add_paragraph()
        p.paragraph_format.first_line_indent = Cm(0)
        p.paragraph_format.space_before = Pt(2 if not before else 8)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.keep_with_next = before
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(text)
        r.font.size = Pt(12)
        r.italic = True

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(path))
        return path
