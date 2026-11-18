import os
import re
import sys
import uuid  # noqa: F401
import html
import io
import base64
from pathlib import Path

import fitz
import requests
import urllib3
from PyQt5 import QtWidgets, QtCore, QtGui

# --- LaTeX-рендер через matplotlib ---
try:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    _HAS_MPL = True
except Exception:
    _HAS_MPL = False

# ==============================================================
# НАСТРОЙКИ
# ==============================================================
DEEPSEEK_API_KEY = os.environ.get(
    "DEEPSEEK_API_KEY",
    "sk-3dc66ffeb3074a30be6819c834271f54",
)
DEEPSEEK_API_URL = os.environ.get(
    "DEEPSEEK_API_URL",
    "https://api.deepseek.com/chat/completions",
)
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
SSL_VERIFY = True

# ==============================================================
# QSS
# ==============================================================
INPUT_QSS = """
QLineEdit, QTextEdit, QTextBrowser, QSpinBox {
    background-color: #2a2a3d; color: #e0e0f0;
    border: 2px solid #3a3a55; border-radius: 10px;
    padding: 6px 8px; font-size: 14px;
    selection-background-color: #7c5cff;
    selection-color: #ffffff;
}
QTextBrowser { background-color: #2a2a3d; }
QSpinBox { color: #ffffff; }
QSpinBox::up-button, QSpinBox::down-button {
    background-color: #3a3a55; border: none; width: 16px;
}
QSpinBox::up-arrow, QSpinBox::down-arrow { color: #e0e0f0; }
"""

BUTTON_QSS = """
QPushButton {
    background-color: #7c5cff; color: white;
    border-radius: 10px; padding: 8px 16px;
    font-size: 14px; font-weight: bold;
}
QPushButton:hover { background-color: #9b7dff; }
QPushButton:disabled { background-color: #3a3a55; color: #6a6a8a; }
QPushButton:checked { background-color: #ffd00; color: #1e1e2e; }
"""

LABEL_QSS = "QLabel { color: #e0e0f0; font-size: 14px; }"

SCROLL_QSS = """
QScrollBar:vertical { background: #2a2a3d; width: 12px; }
QScrollBar::handle:vertical { background: #5a5a7a; border-radius: 6px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: #2a2a3d; height: 12px; }
QScrollBar::handle:horizontal { background: #5a5a7a; border-radius: 6px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
"""

MAIN_QSS = "QMainWindow { background-color: #1e1e2e; }"

MENU_QSS = """
QMenu { background-color: #2a2a3d; color: #e0e0f0; border: 1px solid #3a3a55; }
QMenu::item { padding: 6px 24px; }
QMenu::item:selected { background-color: #7c5cff; color: white; }
"""

# ============================================================
# DeepSeek API
# ============================================================
def ask_deepseek_api(prompt: str,
                     temperature: float = 0.1,
                     max_tokens: int = 3500) -> str:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "DEEPSEEK_API_KEY пуст. Задайте его в коде или в переменной окружения."
        )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    response = requests.post(
        DEEPSEEK_API_URL,
        headers=headers,
        json=payload,
        verify=SSL_VERIFY,
        timeout=180,
    )
    if response.status_code != 200:
        raise RuntimeError(f"API {response.status_code}: {response.text}")
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Некорректный ответ API: {data}") from exc


# ============================================================
# Парсинг ответа
# ============================================================
FORMULA_RE = re.compile(
    r"\[\[\s*FORMULA\s+p\.?\s*(\d+)\s*:\s*(.*?)\s*\]\]",
    re.S | re.I,
)
PAGES_RE = re.compile(
    r"\[\[\s*PAGES\s*:?\s*start=(\d+)\s*[,\.]\s*end=(\d+)\s*[,\.]\s*total=(\d+)\s*\]\]",
    re.S | re.I,
)
START_PAGE_RE = re.compile(
    r"\[\[\s*START_PAGE\s*:?\s*(\d+)\s*\]\]",
    re.S | re.I,
)
PLACEHOLDER_RE = re.compile(r"\x00FORMULAIDX(\d+)\x00")


def parse_answer(raw: str):
    """Возвращает (text, formulas, pages). Формулы вынесены в маркеры."""
    pages = None
    m = PAGES_RE.search(raw)
    if m:
        pages = {
            "start": int(m.group(1)),
            "end": int(m.group(2)),
            "total": int(m.group(3)),
        }
    raw = PAGES_RE.sub("", raw)
    raw = START_PAGE_RE.sub("", raw)

    formulas = []

    def _append(latex: str, page=None) -> str:
        latex = (latex or "").strip()
        if not latex:
            return ""
        idx = len(formulas)
        formulas.append({"page": page, "latex": latex})
        return f"\x00FORMULAIDX{idx}\x00"

    def _repl_formula(m):
        return _append(m.group(2), int(m.group(1)))

    raw = FORMULA_RE.sub(_repl_formula, raw)
    raw = re.sub(r"\$\$(.+?)\$\$", lambda m: _append(m.group(1)), raw, flags=re.S)
    raw = re.sub(r"\$(.+?)\$", lambda m: _append(m.group(1)), raw, flags=re.S)
    raw = re.sub(r"\\\[(.+?)\\\]", lambda m: _append(m.group(1)), raw, flags=re.S)
    raw = re.sub(r"\\\((.+?)\\\)", lambda m: _append(m.group(1)), raw, flags=re.S)
    raw = re.sub(
        r"\\begin\{([^{}]+)\}.*?\\end\{\1\}",
        lambda m: _append(m.group(0)),
        raw,
        flags=re.S,
    )

    return raw.strip(), formulas, pages


# ============================================================
# LaTeX → Unicode (fallback)
# ============================================================
GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π",
    "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ",
    "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ",
    "Omega": "Ω",
}

SIMPLE_OPS = {
    "cdot": "·", "times": "×", "div": "÷",
    "pm": "±", "mp": "∓",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "sim": "~",
    "infty": "∞",
    "to": "→", "rightarrow": "→", "leftarrow": "←",
    "Rightarrow": "⇒", "Leftarrow": "⇐",
    "leftrightarrow": "↔", "Leftrightarrow": "⇔",
    "sum": "∑", "prod": "∏", "int": "∫",
    "partial": "∂", "nabla": "∇",
    "in": "∈", "notin": "∉",
    "subset": "⊂", "supset": "⊃",
    "cup": "∪", "cap": "∩",
    "forall": "∀", "exists": "∃",
    "ldots": "…", "dots": "…", "cdots": "⋯",
    "mid": "|", "vert": "|",
}


def latex_to_unicode(s: str) -> str:
    if not s:
        return s
    out = s

    for _ in range(4):
        new_out = re.sub(
            r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
            lambda m: f"({m.group(1).strip()})/({m.group(2).strip()})",
            out,
        )
        if new_out == out:
            break
        out = new_out

    out = re.sub(
        r"\\sqrt\[([^\]]*)\]\{([^{}]*)\}",
        lambda m: f"({m.group(2).strip()})^(1/{m.group(1).strip()})",
        out,
    )
    out = re.sub(r"\\sqrt\s*\{([^}]*)\}", r"√(\1)", out)
    out = re.sub(r"\\left\s*([\[\{\(\|])", r"\1", out)
    out = re.sub(r"\\right\s*([\]\}\)\|])", r"\1", out)
    out = re.sub(
        r"\\(?:text|mathrm|mathbf|mathit|mathbb|mathcal|operatorname)\s*\{([^}]*)\}",
        r"\1",
        out,
    )
    for name, sym in GREEK.items():
        out = re.sub(r"\\" + name + r"\b", sym, out)
    for name, sym in SIMPLE_OPS.items():
        out = re.sub(r"\\" + name + r"\b", sym, out)
    out = re.sub(r"\\[a-zA-Z]+", "", out)
    out = out.replace("{", "").replace("}", "").replace("$", "")
    return out.strip()


# ==============================================================
# LaTeX-рендер (matplotlib → base64 PNG)
# ==============================================================
class LatexRenderer:
    _cache = {}
    MAX_WIDTH_PX = 460
    BASE_DPI = 160
    BASE_FONTSIZE = 15

    @classmethod
    def render_to_html(cls, latex: str, color: str = "#ffe08a",
                       fontsize: int = None) -> str:
        latex = (latex or "").strip()
        if not latex:
            return ""
        fontsize = fontsize or cls.BASE_FONTSIZE
        key = (latex, color, fontsize)
        if key in cls._cache:
            return cls._cache[key]

        img_html = None
        if _HAS_MPL:
            try:
                fig = Figure(figsize=(0.01, 0.01), dpi=cls.BASE_DPI)
                FigureCanvasAgg(fig)
                fig.patch.set_alpha(0.0)
                fig.text(0, 0, f"${latex}$", fontsize=fontsize, color=color)
                fig.canvas.draw()
                buf = io.BytesIO()
                fig.savefig(
                    buf, format="png", transparent=True,
                    bbox_inches="tight", pad_inches=0.03,
                )
                png_bytes = buf.getvalue()
                try:
                    from PIL import Image
                    with Image.open(io.BytesIO(png_bytes)) as im:
                        w_px, h_px = im.size
                except Exception:
                    w_px = h_px = None
                b64 = base64.b64encode(png_bytes).decode("ascii")
                style = (
                    "vertical-align:middle; display:inline-block;"
                    " max-width:100%; height:auto;"
                )
                if w_px:
                    style += f" width:{min(w_px, cls.MAX_WIDTH_PX)}px;"
                img_html = (
                    f'<img src="data:image/png;base64,{b64}" '
                    f'style="{style} margin:2px 0;" />'
                )
            except Exception:
                img_html = None

        if img_html is None:
            pretty = html.escape(latex_to_unicode(latex))
            img_html = (
                f'<span style="color:{color};'
                f"font-family:'Cambria Math','STIXGeneral','DejaVu Serif',serif;"
                f'font-size:{fontsize}px;">{pretty}</span>'
            )

        cls._cache[key] = img_html
        return img_html


# ============================================================
# PDF-документ
# ============================================================
class PDFDocument:
    def __init__(self, path: str):
        self.path = path
        self.doc = fitz.open(path)
        self.pages_text = [page.get_text("text") for page in self.doc]
        self.highlights = {}
        self.search_highlights = {}

    @staticmethod
    def _rect_key(rect):
        return (round(rect.x0, 1), round(rect.y0, 1),
                round(rect.x1, 1), round(rect.y1, 1))

    def search_exact_ci(self, query: str):
        if not query.strip():
            return []
        q = query.lower()
        results = []
        for pno, text in enumerate(self.pages_text):
            t = text.lower()
            start = 0
            while True:
                i = t.find(q, start)
                if i == -1:
                    break
                page = self.doc[pno]
                orig = text[i:i + len(q)]
                rects = (
                    page.search_for(orig)
                    or page.search_for(query)
                    or page.search_for(q)
                )
                for r in rects:
                    results.append({"page": pno, "rect": r})
                start = i + 1
        return results

    def toggle_highlight(self, page_no, rect, color=(1.0, 0.6, 1.0)) -> bool:
        key = (page_no, self._rect_key(rect))
        if key in self.highlights:
            del self.highlights[key]
            return False
        self.highlights[key] = color
        return True

    def add_search_highlight(self, page_no, rect, color=(1.0, 0.9, 0.2)):
        key = (page_no, self._rect_key(rect))
        self.search_highlights[key] = color

    def clear_search_highlights(self):
        self.search_highlights.clear()

    def __len__(self):
        return len(self.doc)


# ============================================================
# PDF-окно
# ============================================================
class PDFViewerWindow(QtWidgets.QMainWindow):
    def __init__(self, pdf_doc: PDFDocument, page_no=0, focus_rect=None,
                 search_query=None, parent=None):
        super().__init__(parent)
        self.pdf_doc = pdf_doc
        self.setWindowTitle(f"Просмотр — {Path(pdf_doc.path).name}")
        self.setStyleSheet(MAIN_QSS + SCROLL_QSS)
        self.resize(1000, 700)
        self.zoom = 1.3
        self.page_items = []
        self.search_results = []
        self.search_idx = -1

        central = QtWidgets.QWidget()
        central.setStyleSheet("background-color:#1e1e2e")
        self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)

        tb = QtWidgets.QHBoxLayout()
        v.addLayout(tb)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setStyleSheet(INPUT_QSS)
        self.search_input.setPlaceholderText("Поиск (без учёта регистра)...")
        self.search_input.returnPressed.connect(self.run_search)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        tb.addWidget(self.search_input)

        self.btn_search = QtWidgets.QPushButton("🔍")
        self.btn_search.setStyleSheet(BUTTON_QSS)
        self.btn_search.setFixedWidth(50)
        self.btn_search.clicked.connect(self.run_search)
        tb.addWidget(self.btn_search)

        self.btn_prev = QtWidgets.QPushButton("◀")
        self.btn_prev.setStyleSheet(BUTTON_QSS)
        self.btn_prev.setFixedWidth(50)
        self.btn_prev.clicked.connect(self.prev_match)
        self.btn_prev.setEnabled(False)
        tb.addWidget(self.btn_prev)

        self.btn_next = QtWidgets.QPushButton("▶")
        self.btn_next.setStyleSheet(BUTTON_QSS)
        self.btn_next.setFixedWidth(50)
        self.btn_next.clicked.connect(self.next_match)
        self.btn_next.setEnabled(False)
        tb.addWidget(self.btn_next)

        self.match_label = QtWidgets.QLabel("0/0")
        self.match_label.setStyleSheet(LABEL_QSS)
        tb.addWidget(self.match_label)

        lbl = QtWidgets.QLabel("Стр.:")
        lbl.setStyleSheet(LABEL_QSS)
        tb.addWidget(lbl)

        self.page_spin = QtWidgets.QSpinBox()
        self.page_spin.setStyleSheet(INPUT_QSS)
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(max(1, len(pdf_doc.doc)))
        self.page_spin.setValue(page_no + 1 if page_no < len(pdf_doc.doc) else 1)
        self.page_spin.setFixedWidth(90)
        self.page_spin.setAlignment(QtCore.Qt.AlignCenter)
        self.page_spin.valueChanged.connect(lambda val: self._center_on_page(val - 1))
        tb.addWidget(self.page_spin)

        self.btn_highlight = QtWidgets.QPushButton("Маркер")
        self.btn_highlight.setStyleSheet(BUTTON_QSS)
        self.btn_highlight.setCheckable(True)
        self.btn_highlight.toggled.connect(self._on_highlight_toggled)
        tb.addWidget(self.btn_highlight)

        self.scene = QtWidgets.QGraphicsScene()
        self.view = QtWidgets.QGraphicsView(self.scene)

        self.view.setRenderHints(
            QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform
        )
        self.view.setStyleSheet(
            "QGraphicsView { background-color:#1e1e2e; border:none; }"
        )

        self.view.viewport().installEventFilter(self)
        v.addWidget(self.view, 1)

        self._render_all_pages()
        self._draw_highlights()
        self._draw_search_highlights()

        if 0 <= page_no < len(self.page_items):
            self._center_on_page(page_no)
            if focus_rect is not None:
                self._center_on_rect(page_no, focus_rect)

        if search_query:
            self.search_input.blockSignals(True)
            self.search_input.setText(search_query)
            self.search_input.blockSignals(False)
            self.run_search()

        self._center_on_screen()

    def _center_on_screen(self):
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.move(scr.center() - self.rect().center())

    def _render_all_pages(self):
        self.scene.clear()
        self.page_items = []
        y = 0
        for pno, page in enumerate(self.pdf_doc.doc):
            mat = fitz.Matrix(self.zoom, self.zoom)
            pix = page.get_pixmap(matrix=mat)
            img = QtGui.QImage(
                pix.samples, pix.width, pix.height,
                pix.stride, QtGui.QImage.Format_RGB888,
            )
            pm = QtGui.QPixmap.fromImage(img)
            item = self.scene.addPixmap(pm)
            item.setPos(0, y)
            item.setData(0, pno)
            self.page_items.append((item, pno, page, self.zoom))
            y += pix.height + 20
        self.scene.setSceneRect(self.scene.itemsBoundingRect())

    def _center_on_page(self, pno):
        if pno < 0 or pno >= len(self.page_items):
            return
        item = self.page_items[pno][0]
        self.view.centerOn(item)
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(pno + 1)
        self.page_spin.blockSignals(False)

    def _center_on_rect(self, pno, rect):
        if pno < 0 or pno >= len(self.page_items):
            return
        item, _, _, zoom = self.page_items[pno]
        ox, oy = item.pos().x(), item.pos().y()
        target = QtCore.QRectF(
            ox + rect.x0 * zoom - 50,
            oy + rect.y0 * zoom - 50,
            (rect.x1 - rect.x0) * zoom + 100,
            (rect.y1 - rect.y0) * zoom + 100,
        )
        self.view.centerOn(target.center())

    def _draw_highlights(self):
        for it in list(self.scene.items()):
            if it.data(1) == "user_highlight":
                self.scene.removeItem(it)
        for (hp, hr), _color in self.pdf_doc.highlights.items():
            if hp >= len(self.page_items):
                continue
            item, _, _, zoom = self.page_items[hp]
            ox, oy = item.pos().x(), item.pos().y()
            x0, y0, x1, y1 = hr
            r = QtCore.QRectF(
                ox + x0 * zoom,
                oy + y0 * zoom,
                (x1 - x0) * zoom,
                (y1 - y0) * zoom,
            )
            pen = QtGui.QPen(QtCore.Qt.NoPen)
            brush = QtGui.QBrush(QtGui.QColor(255, 170, 0, 130))
            el = self.scene.addRect(r, pen, brush)
            el.setData(1, "user_highlight")
            el.setZValue(10)

    def _draw_search_highlights(self):
        for it in list(self.scene.items()):
            if it.data(1) == "search_highlight":
                self.scene.removeItem(it)
        items = list(self.pdf_doc.search_highlights.keys())
        for i, hk in enumerate(items):
            hp, hr = hk
            if hp >= len(self.page_items):
                continue
            item, _, _, zoom = self.page_items[hp]
            ox, oy = item.pos().x(), item.pos().y()
            x0, y0, x1, y1 = hr
            r = QtCore.QRectF(
                ox + x0 * zoom - 1,
                oy + y0 * zoom - 1,
                (x1 - x0) * zoom + 2,
                (y1 - y0) * zoom + 2,
            )
            if i == self.search_idx:
                pen = QtGui.QPen(QtGui.QColor(255, 90, 0, 220), 2)
                brush = QtGui.QBrush(QtGui.QColor(255, 170, 0, 130))
            else:
                pen = QtGui.QPen(QtGui.QColor(255, 200, 0, 180), 1)
                brush = QtGui.QBrush(QtGui.QColor(255, 220, 60, 70))
            el = self.scene.addRect(r, pen, brush)
            el.setData(1, "search_highlight")
            el.setZValue(5)

    def _on_search_text_changed(self, text):
        if not text.strip():
            self.pdf_doc.clear_search_highlights()
            self.search_results = []
            self.search_idx = -1
            self.match_label.setText("0/0")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            self._draw_search_highlights()

    def run_search(self):
        q = self.search_input.text().strip()
        self.pdf_doc.clear_search_highlights()
        if not q:
            self.search_results = []
            self.search_idx = -1
            self.match_label.setText("0/0")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            self._draw_search_highlights()
            return
        self.search_results = self.pdf_doc.search_exact_ci(q)
        for r in self.search_results:
            self.pdf_doc.add_search_highlight(r["page"], r["rect"])
        if not self.search_results:
            self.search_idx = -1
            self.match_label.setText("0/0")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            self._draw_search_highlights()
            QtWidgets.QMessageBox.information(
                self, "Поиск", f"«{q}» не найдено.")
            return
        self.search_idx = 0
        self._show_match()

    def _show_match(self):
        if not self.search_results:
            return
        r = self.search_results[self.search_idx]
        self.match_label.setText(
            f"{self.search_idx + 1}/{len(self.search_results)}")
        self.btn_prev.setEnabled(True)
        self.btn_next.setEnabled(True)
        self._draw_search_highlights()
        self._center_on_page(r["page"])
        self._center_on_rect(r["page"], r["rect"])
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(r["page"] + 1)
        self.page_spin.blockSignals(False)

    def next_match(self):
        if not self.search_results:
            return
        self.search_idx = (self.search_idx + 1) % len(self.search_results)
        self._show_match()

    def prev_match(self):
        if not self.search_results:
            return
        self.search_idx = (self.search_idx - 1 + len(self.search_results)) \
            % len(self.search_results)
        self._show_match()

    def _on_highlight_toggled(self, checked):
        if checked:
            self.view.viewport().setCursor(QtCore.Qt.CrossCursor)
        else:
            self.view.viewport().unsetCursor()

    def eventFilter(self, obj, event):
        # КЛЮЧЕВОЕ исправление: обрабатываем ЛКМ только в режиме маркера,
        # и для всех остальных событий/виджетов уходим в super().
        if obj is self.view.viewport():
            if (event.type() == QtCore.QEvent.MouseButtonPress
                    and self.btn_highlight.isChecked()
                    and event.button() == QtCore.Qt.LeftButton):
                scene_pos = self.view.mapToScene(event.pos())
                self._handle_marker_click(scene_pos)
                return True
        return super().eventFilter(obj, event)

    def _handle_marker_click(self, scene_pos):
        for item, pno, page, zoom in self.page_items:
            bounds = item.sceneBoundingRect()
            if not bounds.contains(scene_pos):
                continue
            rel_x = (scene_pos.x() - bounds.x()) / zoom
            rel_y = (scene_pos.y() - bounds.y()) / zoom
            pt = fitz.Point(rel_x, rel_y)

            target_rect = None

            # 1) сначала пробуем line bbox
            try:
                d = page.get_text("dict")
                for block in d.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        lr = fitz.Rect(line["bbox"])
                        if lr.contains(pt):
                            target_rect = lr
                            break
                    if target_rect is not None:
                        break
            except Exception:
                target_rect = None

            # 2) иначе — самое маленькое слово под курсором
            if target_rect is None:
                best, best_area = None, None
                try:
                    for w in page.get_text("words"):
                        wr = fitz.Rect(w[:4])
                        if wr.contains(pt):
                            a = wr.get_area()
                            if best_area is None or a < best_area:
                                best_area, best = a, wr
                except Exception:
                    best = None
                target_rect = best

            # 3) фолбэк — маленький прямоугольник
            if target_rect is None:
                target_rect = fitz.Rect(
                    rel_x - 2, rel_y - 2, rel_x + 2, rel_y + 2
                )

            self.pdf_doc.toggle_highlight(pno, target_rect, color=(1, 0.6, 1))
            self._draw_highlights()
            return


# ============================================================
# Главное окно
# ============================================================
class PDFSearchApp(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI-Ассистент по научным статьям (DeepSeek)")
        self.setStyleSheet(MAIN_QSS + SCROLL_QSS)
        self.resize(980, 680)
        self.pdf_doc = None
        self.viewer = None
        self.answer_window = None
        self.history = []
        self.history_idx = -1
        self._matches = []
        self._match_idx = -1

        central = QtWidgets.QWidget()
        central.setStyleSheet("background-color:#1e1e2e;")
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        grid = QtWidgets.QGridLayout()

        self.btn_load = QtWidgets.QPushButton("Загрузить PDF")
        self.btn_load.setStyleSheet(BUTTON_QSS)
        self.btn_load.clicked.connect(self.load_pdf)
        grid.addWidget(self.btn_load, 0, 0)

        self.btn_open_pdf = QtWidgets.QPushButton("Открыть PDF")
        self.btn_open_pdf.setStyleSheet(BUTTON_QSS)
        self.btn_open_pdf.clicked.connect(lambda: self.open_pdf_viewer(0))
        self.btn_open_pdf.setEnabled(False)
        grid.addWidget(self.btn_open_pdf, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.input_question = QtWidgets.QTextEdit()
        self.input_question.setStyleSheet(INPUT_QSS)
        self.input_question.setPlaceholderText(
            "Задайте вопрос... Enter — отправить, Shift+Enter — новая строка."
        )
        self.input_question.setFixedHeight(80)
        self.input_question.installEventFilter(self)
        layout.addWidget(self.input_question)

        self.btn_ask = QtWidgets.QPushButton("Найти ответ")
        self.btn_ask.setStyleSheet(BUTTON_QSS)
        self.btn_ask.clicked.connect(self.ask_deepseek)
        layout.addWidget(self.btn_ask)

        ans_bar = QtWidgets.QHBoxLayout()
        layout.addLayout(ans_bar)
        lbl_a = QtWidgets.QLabel("Ответ:")
        lbl_a.setStyleSheet(LABEL_QSS)
        ans_bar.addWidget(lbl_a)

        self.btn_prev_answer = QtWidgets.QPushButton("Пред.")
        self.btn_prev_answer.setStyleSheet(BUTTON_QSS)
        self.btn_prev_answer.setFixedWidth(100)
        self.btn_prev_answer.clicked.connect(self.prev_answer)
        self.btn_prev_answer.setEnabled(False)
        ans_bar.addWidget(self.btn_prev_answer)

        self.answer_label = QtWidgets.QLabel("—")
        self.answer_label.setStyleSheet(LABEL_QSS)
        ans_bar.addWidget(self.answer_label)

        self.btn_next_answer = QtWidgets.QPushButton("След.")
        self.btn_next_answer.setStyleSheet(BUTTON_QSS)
        self.btn_next_answer.setFixedWidth(100)
        self.btn_next_answer.clicked.connect(self.next_answer)
        self.btn_next_answer.setEnabled(False)
        ans_bar.addWidget(self.btn_next_answer)
        ans_bar.addStretch(1)

        sb = QtWidgets.QHBoxLayout()
        layout.addLayout(sb)
        self.answer_search = QtWidgets.QLineEdit()
        self.answer_search.setStyleSheet(INPUT_QSS)
        self.answer_search.setPlaceholderText("Поиск по ответу (Enter — искать)...")
        self.answer_search.returnPressed.connect(self.search_answer_main)
        self.answer_search.textChanged.connect(self._on_answer_search_changed)
        sb.addWidget(self.answer_search, 1)

        btn_s = QtWidgets.QPushButton("🔍")
        btn_s.setStyleSheet(BUTTON_QSS)
        btn_s.setFixedWidth(50)
        btn_s.clicked.connect(self.search_answer_main)
        sb.addWidget(btn_s)

        self.btn_prev_match = QtWidgets.QPushButton("◀")
        self.btn_prev_match.setStyleSheet(BUTTON_QSS)
        self.btn_prev_match.setFixedWidth(50)
        self.btn_prev_match.clicked.connect(self.prev_match_main)
        self.btn_prev_match.setEnabled(False)
        sb.addWidget(self.btn_prev_match)

        self.btn_next_match = QtWidgets.QPushButton("▶")
        self.btn_next_match.setStyleSheet(BUTTON_QSS)
        self.btn_next_match.setFixedWidth(50)
        self.btn_next_match.clicked.connect(self.next_match_main)
        self.btn_next_match.setEnabled(False)
        sb.addWidget(self.btn_next_match)

        self.match_label_main = QtWidgets.QLabel("0/0")
        self.match_label_main.setStyleSheet(LABEL_QSS)
        sb.addWidget(self.match_label_main)

        # --- Вывод ответа ---
        self.output = QtWidgets.QTextBrowser()
        self.output.setStyleSheet(INPUT_QSS)
        self.output.setOpenExternalLinks(False)
        self.output.setOpenLinks(False)
        self.output.setPlaceholderText("Здесь появится ответ...")
        self.output.anchorClicked.connect(self.on_link_clicked)
        self.output.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
        self.output.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.output.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.output.setSizeAdjustPolicy(
            QtWidgets.QAbstractScrollArea.AdjustIgnored)
        layout.addWidget(self.output, 1)

        self.btn_open_answer = QtWidgets.QPushButton("Открыть окно ответа")
        self.btn_open_answer.setStyleSheet(BUTTON_QSS)
        self.btn_open_answer.clicked.connect(self.on_open_answer_window)
        self.btn_open_answer.setEnabled(False)
        layout.addWidget(self.btn_open_answer)

        self._center_on_screen()

    def _center_on_screen(self):
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.move(scr.center() - self.rect().center())

    def eventFilter(self, obj, event):
        if obj is self.input_question and event.type() == QtCore.QEvent.KeyPress:
            if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                if event.modifiers() & QtCore.Qt.ShiftModifier:
                    return False
                self.ask_deepseek()
                return True
        return super().eventFilter(obj, event)

    # ----- PDF -----
    def load_pdf(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Выберите PDF", "", "PDF (*.pdf)")
        if not path:
            return
        try:
            self.pdf_doc = PDFDocument(path)
            self.output.setHtml(
                f"<b>Загружено:</b> {html.escape(path)}<br>"
                f"Страниц в документе: {len(self.pdf_doc.doc)}.<br>"
            )
            self.btn_open_pdf.setEnabled(True)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка PDF", str(e))

    def open_pdf_viewer(self, page_no=0, focus_rect=None, search_query=None):
        if not self.pdf_doc:
            return
        if self.viewer is not None and self.viewer.isVisible():
            self.viewer._center_on_page(page_no)
            if focus_rect is not None:
                self.viewer._center_on_rect(page_no, focus_rect)
            if search_query:
                self.viewer.search_input.setText(search_query)
                self.viewer.run_search()
            self.viewer.raise_()
            self.viewer.activateWindow()
            return
        self.viewer = PDFViewerWindow(
            self.pdf_doc, page_no, focus_rect, search_query, parent=self
        )
        self.viewer.show()

    # ----- История ответов -----
    def render_answer(self):
        if self.history_idx < 0 or self.history_idx >= len(self.history):
            self.output.clear()
            self.btn_open_answer.setEnabled(False)
            self.answer_label.setText("—")
            self.btn_prev_answer.setEnabled(False)
            self.btn_next_answer.setEnabled(False)
            return

        entry = self.history[self.history_idx]
        rendered = self._render_text(entry["text"], entry["formulas"])
        self.output.setHtml(rendered)
        self.btn_open_answer.setEnabled(True)
        self.answer_label.setText(f"{self.history_idx + 1}/{len(self.history)}")
        self.btn_prev_answer.setEnabled(self.history_idx > 0)
        self.btn_next_answer.setEnabled(self.history_idx < len(self.history) - 1)
        self.output.verticalScrollBar().setValue(0)
        self._matches = []
        self._match_idx = -1
        self.match_label_main.setText("0/0")
        self.btn_prev_match.setEnabled(False)
        self.btn_next_match.setEnabled(False)
        if self.answer_window is not None and self.answer_window.isVisible():
            self.answer_window.set_entry(entry)

    def prev_answer(self):
        if self.history_idx <= 0:
            return
        self.history_idx -= 1
        self.render_answer()

    def next_answer(self):
        if self.history_idx >= len(self.history) - 1:
            return
        self.history_idx += 1
        self.render_answer()

    def on_open_answer_window(self):
        if self.history_idx < 0 or self.history_idx >= len(self.history):
            return
        entry = self.history[self.history_idx]
        if self.answer_window is None or not self.answer_window.isVisible():
            self.answer_window = AnswerWindow(entry, self, parent=self)
            self.answer_window.show()
        else:
            self.answer_window.set_entry(entry)
            self.answer_window.show()
            self.answer_window.raise_()
            self.answer_window.activateWindow()

    # -------- Поиск в ответе --------
    @staticmethod
    def _find_all(text, q):
        res, start = [], 0
        ql, tl = q.lower(), text.lower()
        while True:
            i = tl.find(ql, start)
            if i == -1:
                break
            res.append((i, i + len(q)))
            start = i + 1
        return res

    def _on_answer_search_changed(self, text):
        if not text.strip():
            self._clear_browser_highlights(self.output)
            self._matches = []
            self._match_idx = -1
            self.match_label_main.setText("0/0")
            self.btn_prev_match.setEnabled(False)
            self.btn_next_match.setEnabled(False)

    @staticmethod
    def _clear_browser_highlights(browser):
        doc = browser.document()
        cur = QtGui.QTextCursor(doc)
        cur.setPosition(0)
        cur.setPosition(len(browser.toPlainText()), QtGui.QTextCursor.KeepAnchor)
        fmt = QtGui.QTextCharFormat()
        fmt.setBackground(QtGui.QBrush(QtCore.Qt.transparent))
        cur.mergeCharFormat(fmt)

    def search_answer_main(self):
        q = self.answer_search.text().strip()
        if not q:
            self._clear_browser_highlights(self.output)
            self._matches = []
            self._match_idx = -1
            self.match_label_main.setText("0/0")
            self.btn_prev_match.setEnabled(False)
            self.btn_next_match.setEnabled(False)
            return
        plain = self.output.toPlainText()
        self._matches = self._find_all(plain, q)
        if not self._matches:
            self._match_idx = -1
            self.match_label_main.setText("0/0")
            self.btn_prev_match.setEnabled(False)
            self.btn_next_match.setEnabled(False)
            QtWidgets.QMessageBox.information(
                self, "Поиск", f"«{q}» не найдено.")
            return
        self._match_idx = 0
        self._highlight_and_center(self.output, self._matches, self._match_idx)
        self.match_label_main.setText(
            f"{self._match_idx + 1}/{len(self._matches)}")
        self.btn_prev_match.setEnabled(len(self._matches) > 1)
        self.btn_next_match.setEnabled(len(self._matches) > 1)

    def prev_match_main(self):
        if not self._matches:
            return
        self._match_idx = (self._match_idx - 1 + len(self._matches)) \
            % len(self._matches)
        self._highlight_and_center(self.output, self._matches, self._match_idx)
        self.match_label_main.setText(
            f"{self._match_idx + 1}/{len(self._matches)}")

    def next_match_main(self):
        if not self._matches:
            return
        self._match_idx = (self._match_idx + 1) % len(self._matches)
        self._highlight_and_center(self.output, self._matches, self._match_idx)
        self.match_label_main.setText(
            f"{self._match_idx + 1}/{len(self._matches)}")

    @staticmethod
    def _highlight_and_center(browser, matches, idx):
        doc = browser.document()
        plain = browser.toPlainText()
        cur0 = QtGui.QTextCursor(doc)
        cur0.setPosition(0)
        cur0.setPosition(len(plain), QtGui.QTextCursor.KeepAnchor)
        fmt_clear = QtGui.QTextCharFormat()
        fmt_clear.setBackground(QtGui.QBrush(QtCore.Qt.transparent))
        cur0.mergeCharFormat(fmt_clear)
        for i, (s, e) in enumerate(matches):
            cur = QtGui.QTextCursor(doc)
            cur.setPosition(s)
            cur.setPosition(e, QtGui.QTextCursor.KeepAnchor)
            fmt = QtGui.QTextCharFormat()
            if i == idx:
                fmt.setBackground(QtGui.QBrush(QtGui.QColor(255, 140, 0)))
            else:
                fmt.setBackground(QtGui.QBrush(QtGui.QColor(255, 220, 90)))
            fmt.setForeground(QtGui.QBrush(QtGui.QColor(30, 30, 46)))
            cur.mergeCharFormat(fmt)
        s, e = matches[idx]
        cur = QtGui.QTextCursor(doc)
        cur.setPosition(s)
        cur.setPosition(e, QtGui.QTextCursor.KeepAnchor)
        browser.setTextCursor(cur)
        browser.ensureCursorVisible()

    # --- Запрос к DeepSeek ---
    def ask_deepseek(self):
        question = self.input_question.toPlainText().strip()
        if not question:
            return
        if not self.pdf_doc:
            QtWidgets.QMessageBox.information(
                self, "PDF", "Сначала загрузите PDF!")
            return

        self.input_question.clear()
        self.btn_ask.setEnabled(False)
        self.btn_ask.setText("DeepSeek думает...")
        QtWidgets.QApplication.processEvents()
        try:
            chunks = []
            for pno in range(len(self.pdf_doc.doc)):
                chunks.append(
                    f"[Страница {pno + 1}]\n{self.pdf_doc.pages_text[pno]}"
                )

            full_text = "\n".join(chunks)
            if len(full_text) > 60000:
                full_text = full_text[:60000] + "\n...[обрезано]..."

            system_prompt = (
                "Ты — научный ассистент по физике и математике. "
                "Отвечай, используя ТОЛЬКО предоставленный текст статьи.\n"
                "\n"
                "=== ОБОЗНАЧЕНИЯ (используй строго так, как в статье) ===\n"
                "- Гамильтониан — H (заглавная латинская H), НЕ G.\n"
                "- Гамильтониан возмущения — H_1, H_int и т.д.\n"
                "- Заряд/энергия — E, уровни энергии — E_n.\n"
                "- Кинетическая энергия — E_k (латинская k), НЕ E_к (кириллица).\n"
                "- Матричные элементы — H_mn, V_mn.\n"
                "- Спин-операторы — σ_γ (σ с символом γ), НЕ h_γ.\n"
                "- Диагональные элементы — пиши как H_mm ≈ 0, а не diag(H) = 0.\n"
                "- Если в статье есть собственные обозначения — копируй их "
                "посимвольно из текста.\n"
                "\n"
                "=== ПРАВИЛА ФОРМАТА ===\n"
                "1. Структурируй ответ: короткие заголовки (##), списки, абзацы.\n"
                "2. После каждого факта — источник [стр. N], N — точный номер "
                "страницы из маркера [Страница N]. Для разных фактов — разные N.\n"
                "3. КАЖДАЯ формула — ОТДЕЛЬНОЙ строкой, строго в виде:\n"
                " [[FORMULA p.<N>: <latex>]]\n"
                " где N — номер страницы, где формула встречается.\n"
                " Пример: [[FORMULA p.5: H_1 = \\frac{2H_{01}-1}{2H_{01}}]]\n"
                " В обычном тексте вместо формулы — только пояснение словами. "
                "Никаких $...$, $$...$, \\(...\\), \\[...\\], \\begin{...} в тексте.\n"
                "\n"
                "=== ПРАВИЛА LaTeX ВНУТРИ [[FORMULA ...]] ===\n"
                "- Дроби — \\frac{a}{b}, индексы — _{...}, степени — ^{...},\n"
                "- Греки — \\alpha, \\beta, \\gamma, \\sigma, \\mu и т.д. "
                "(латиницей внутри LaTeX).\n"
                "- Операторы: \\hat{H}, \\langle m|H_1|n\\rangle, \\sum_{n}, "
                "\\int, \\partial, \\nabla.\n"
                "- Матрицы: \\begin{pmatrix} ... \\end{pmatrix}.\n"
                "- НЕ пиши кириллицу внутри LaTeX — только латиница и греки.\n"
                "- НЕ используй \\text{} с кириллицей.\n"
                "- Одна формула — один маркер. Не склеивай несколько формул в один.\n"
                "\n"
                "=== ОСТАЛЬНОЕ ===\n"
                "4. Если ответа нет в тексте — напиши: "
                "В данной статье эта информация не найдена.\n"
                "5. Не выдумывай факты и не добавляй формулы, которых нет в тексте.\n"
                "6. НЕ добавляй служебные строки [[START_PAGE: ...]] или "
                "[[PAGES: ...]]\n"
            )

            prompt = (
                f"{system_prompt}\n\n"
                f"=== ТЕКСТ СТАТЬИ ===\n{full_text}\n\n"
                f"=== ВОПРОС ===\n{question}"
            )
            raw = ask_deepseek_api(prompt)
            text, formulas, pages = parse_answer(raw)
            entry = {"text": text, "formulas": formulas, "pages": pages}
            self.history.append(entry)
            self.history_idx = len(self.history) - 1
            self.render_answer()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка API", str(e))
        finally:
            self.btn_ask.setEnabled(True)
            self.btn_ask.setText("Найти ответ")

    # ----- Рендер ответа в HTML -----
    def _render_text(self, text: str, formulas) -> str:
        formulas = formulas or []
        t = html.escape(text)
        placeholder_map = {}
        counter = {"n": 0}

        def _formula_placeholder(m):
            idx = int(m.group(1))
            if idx >= len(formulas):
                return ""
            ph = f"\x01F{counter['n']}\x01"
            counter["n"] += 1
            placeholder_map[ph] = idx
            return ph

        t = PLACEHOLDER_RE.sub(_formula_placeholder, t)

        # markdown-мелочи
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", t)
        t = re.sub(r"^#{1,3}\s*(.*)$", r"<h3>\1</h3>", t, flags=re.M)

        t = re.sub(
            r"\[стр\.\s*(\d+)\]",
            lambda m: (
                f'<a href="page:{m.group(1)}" '
                f'style="color:#9b7dff; text-decoration:none;">'
                f"[стр. {m.group(1)}]</a>"
            ),
            t,
        )

        def _make_formula_html(idx: int) -> str:
            if idx >= len(formulas):
                return ""
            f = formulas[idx]
            img = LatexRenderer.render_to_html(f["latex"])
            page = f.get("page")
            base = (
                '<div style="background-color:#2a2a3d; padding:6px 10px; '
                "margin:6px 0; border-radius:8px; "
                "max-width:100%; white-space:normal; word-wrap:break-word; "
                'overflow:hidden;">'
                f"{img} "
            )
            if page is not None:
                base += (
                    f'<a href="formula:{idx}" style="color:#9b7dff; '
                    f'text-decoration:none; font-size:12px;">[стр. {page}]</a>'
                )
            base += "</div>"
            return base

        result_lines = []
        buf = []

        def flush():
            if not buf:
                return
            result_lines.append("".join(buf))
            buf.clear()

        for ln in t.split("\n"):
            phs = re.findall(r"\x01F(\d+)\x01", ln)
            if not phs:
                buf.append(ln + "\n")
                continue
            rest = ln
            for ph in phs:
                idx = placeholder_map.get(ph)
                if idx is None:
                    continue
                before, rest = rest.split(ph, 1)
                if before.strip():
                    buf.append(before)
                flush()
                result_lines.append(_make_formula_html(idx))
                buf.append(rest)
                buf.append("\n")
            flush()

        t = "\n".join(result_lines) if result_lines else t
        return (
            '<div style="color:#e0e0f0; font-size:14px; line-height:1.5; '
            'white-space:normal; word-wrap:break-word;">'
            f"{t}</div>"
        )

    def _resolve_pdf_page(self, doc_page_no: int):
        if not self.pdf_doc:
            return None
        total = len(self.pdf_doc)
        sys_idx = doc_page_no - 1
        return max(0, min(sys_idx, total - 1))

    def on_link_clicked(self, url: QtCore.QUrl):
        s = url.toString()

        if s.startswith("page:"):
            try:
                doc_page_no = int(s.split(":", 1)[1])
            except ValueError:
                return
            sys_idx = self._resolve_pdf_page(doc_page_no)
            if sys_idx is None:
                return
            self.open_pdf_viewer(sys_idx)
            return

        if s.startswith("formula:"):
            try:
                idx = int(s.split(":", 1)[1])
            except ValueError:
                return
            if self.history_idx < 0:
                return
            entry = self.history[self.history_idx]
            if idx >= len(entry["formulas"]):
                return
            f = entry["formulas"][idx]
            page = f.get("page")
            if self.pdf_doc and page is not None:
                sys_page = self._resolve_pdf_page(page)
                if sys_page is not None:
                    self.open_pdf_viewer(sys_page)


# ============================================================
# Окно ответа
# ============================================================
class AnswerWindow(QtWidgets.QMainWindow):
    def __init__(self, entry: dict, main_app: PDFSearchApp, parent=None):
        super().__init__(parent)
        self.main_app = main_app
        self.setWindowTitle("Ответ DeepSeek")
        self.setStyleSheet(MAIN_QSS + SCROLL_QSS)
        self.resize(900, 680)

        central = QtWidgets.QWidget()
        central.setStyleSheet("background-color:#1e1e2e;")
        self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)

        ans_bar = QtWidgets.QHBoxLayout()
        v.addLayout(ans_bar)
        lbl = QtWidgets.QLabel("Ответ:")
        lbl.setStyleSheet(LABEL_QSS)
        ans_bar.addWidget(lbl)

        self.btn_prev_answer = QtWidgets.QPushButton("Пред.")
        self.btn_prev_answer.setStyleSheet(BUTTON_QSS)
        self.btn_prev_answer.setFixedWidth(100)
        self.btn_prev_answer.clicked.connect(self.prev_answer)
        ans_bar.addWidget(self.btn_prev_answer)

        self.answer_label = QtWidgets.QLabel("—")
        self.answer_label.setStyleSheet(LABEL_QSS)
        ans_bar.addWidget(self.answer_label)

        self.btn_next_answer = QtWidgets.QPushButton("След.")
        self.btn_next_answer.setStyleSheet(BUTTON_QSS)
        self.btn_next_answer.setFixedWidth(100)
        self.btn_next_answer.clicked.connect(self.next_answer)
        ans_bar.addWidget(self.btn_next_answer)
        ans_bar.addStretch(1)

        bar = QtWidgets.QHBoxLayout()
        v.addLayout(bar)
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setStyleSheet(INPUT_QSS)
        self.search_input.setPlaceholderText("Поиск по ответу (Enter — искать).")
        self.search_input.returnPressed.connect(self.search_in_answer)
        self.search_input.textChanged.connect(self._on_search_changed)
        bar.addWidget(self.search_input, 1)

        btn_find = QtWidgets.QPushButton("🔍")
        btn_find.setStyleSheet(BUTTON_QSS)
        btn_find.setFixedWidth(50)
        btn_find.clicked.connect(self.search_in_answer)
        bar.addWidget(btn_find)

        self.btn_prev = QtWidgets.QPushButton("◀")
        self.btn_prev.setStyleSheet(BUTTON_QSS)
        self.btn_prev.setFixedWidth(50)
        self.btn_prev.clicked.connect(self.prev_match)
        self.btn_prev.setEnabled(False)
        bar.addWidget(self.btn_prev)

        self.btn_next = QtWidgets.QPushButton("▶")
        self.btn_next.setStyleSheet(BUTTON_QSS)
        self.btn_next.setFixedWidth(50)
        self.btn_next.clicked.connect(self.next_match)
        self.btn_next.setEnabled(False)
        bar.addWidget(self.btn_next)

        self.match_label = QtWidgets.QLabel("0/0")
        self.match_label.setStyleSheet(LABEL_QSS)
        bar.addWidget(self.match_label)

        self.text = QtWidgets.QTextBrowser()
        self.text.setStyleSheet(INPUT_QSS)
        self.text.setOpenExternalLinks(False)
        self.text.setOpenLinks(False)
        self.text.anchorClicked.connect(self.on_link_clicked)
        self.text.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
        self.text.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.text.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        v.addWidget(self.text, 1)

        self.entry = entry
        self._matches = []
        self.match_idx = -1
        self.set_entry(entry)
        self._center_on_screen()

    def _center_on_screen(self):
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.move(scr.center() - self.rect().center())

    def set_entry(self, entry: dict):
        self.entry = entry
        self.text.setHtml(
            self.main_app._render_text(entry["text"], entry["formulas"])
        )
        self.text.verticalScrollBar().setValue(0)
        idx = self.main_app.history_idx
        total = len(self.main_app.history)
        self.answer_label.setText(f"{idx + 1}/{total}" if total else "—")
        self.btn_prev_answer.setEnabled(idx > 0)
        self.btn_next_answer.setEnabled(idx < total - 1)

        self._matches = []
        self.match_idx = -1
        self.match_label.setText("0/0")
        self.btn_prev.setEnabled(False)
        self.btn_next.setEnabled(False)

    def prev_answer(self):
        if self.main_app.history_idx > 0:
            self.main_app.history_idx -= 1
            self.main_app.render_answer()
            self.set_entry(self.main_app.history[self.main_app.history_idx])

    def next_answer(self):
        if self.main_app.history_idx < len(self.main_app.history) - 1:
            self.main_app.history_idx += 1
            self.main_app.render_answer()
            self.set_entry(self.main_app.history[self.main_app.history_idx])

    def on_link_clicked(self, url):
        self.main_app.on_link_clicked(url)

    def _on_search_changed(self, text):
        if not text.strip():
            PDFSearchApp._clear_browser_highlights(self.text)
            self._matches = []
            self.match_idx = -1
            self.match_label.setText("0/0")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)

    def search_in_answer(self):
        q = self.search_input.text().strip()
        if not q:
            PDFSearchApp._clear_browser_highlights(self.text)
            self._matches = []
            self.match_idx = -1
            self.match_label.setText("0/0")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return
        plain = self.text.toPlainText()
        self._matches = PDFSearchApp._find_all(plain, q)
        if not self._matches:
            self.match_idx = -1
            self.match_label.setText("0/0")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            QtWidgets.QMessageBox.information(
                self, "Поиск", f"«{q}» не найдено.")
            return
        self.match_idx = 0
        self._highlight_and_center()

    def _highlight_and_center(self):
        PDFSearchApp._highlight_and_center(
            self.text, self._matches, self.match_idx)
        self.match_label.setText(
            f"{self.match_idx + 1}/{len(self._matches)}")
        self.btn_prev.setEnabled(len(self._matches) > 1)
        self.btn_next.setEnabled(len(self._matches) > 1)

    def next_match(self):
        if not self._matches:
            return
        self.match_idx = (self.match_idx + 1) % len(self._matches)
        self._highlight_and_center()

    def prev_match(self):
        if not self._matches:
            return
        self.match_idx = (self.match_idx - 1 + len(self._matches)) \
            % len(self._matches)
        self._highlight_and_center()


# ============================================================
if __name__ == "__main__":
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(MENU_QSS)
    window = PDFSearchApp()
    window.show()
    sys.exit(app.exec_())
