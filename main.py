import sys
import re
import uuid
import html
from pathlib import Path

import fitz
import requests
from PyQt5 import QtWidgets, QtCore, QtGui

# ==========================================================
# НАСТРОЙКИ
# ==========================================================
GIGACHAT_CLIENT_SECRET = (
    "MDFhMWVhZmEtNmVhYy03MzFhLTk1OGEtM2ExY2Q1ZDE0N2YyOjlhY2I5MTQ4LTZmZ"
    "DgtNGY5MS1iMmY2LWNiNzVjMDhlM2RiMw=="
)
GIGACHAT_CLIENT_ID = "01a1eafa-6eac-731a-958a-3a1cd5d147f2"
AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

# ==========================================================
# QSS
# ==========================================================
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
QPushButton:checked { background-color: #ff9d00; color: #1e1e2e; }
"""

LABEL_QSS = "QLabel { color: #e0e0f0; font-size: 14px; }"

SCROLL_QSS = """
QScrollBar:vertical { background: #2a2a3d; width: 12px; }
QScrollBar::handle:vertical { background: #5a5a7a; border-radius: 6px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #2a2a3d; height: 12px; }
QScrollBar::handle:horizontal { background: #5a5a7a; border-radius: 6px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""

MAIN_QSS = "QMainWindow { background-color: #1e1e2e; }"

MENU_QSS = """
QMenu { background-color: #2a2a3d; color: #e0e0f0; border: 1px solid #3a3a55; }
QMenu::item { padding: 6px 24px; }
QMenu::item:selected { background-color: #7c5cff; color: white; }
"""


# ============================================================
# GigaChat API
# ============================================================
def get_gigachat_token() -> str:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {GIGACHAT_CLIENT_SECRET}",
    }
    r = requests.post(
        AUTH_URL, headers=headers,
        data={"scope": "GIGACHAT_API_PERS"},
        verify=False, timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def ask_gigachat_api(token: str, prompt: str,
                     temperature: float = 0.1, max_tokens: int = 3500) -> str:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {
        "model": "GigaChat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    r = requests.post(API_URL, headers=headers, json=payload,
                      verify=False, timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# ============================================================
# Парсинг ответа GigaChat
# ============================================================
# [[FORMULA p.12: \frac{a}{b}]]  или  [FORMULA p.12: ...]
FORMULA_RE = re.compile(
    r"\[\[?\s*FORMULA\s+p\.\s*(\d+)\s*:\s*(.*?)\s*\]\]?",
    re.S,
)
PAGES_RE = re.compile(r"\[\[?\s*PAGES\s*:.*?\]\]?", re.S)
START_PAGE_RE = re.compile(r"\[\[?\s*START_PAGE\s*:.*?\]\]?", re.S)


def parse_answer(raw: str):
    """Возвращает (text, formulas, pages). Все формулы вынесены в formulas."""
    pages = None
    m = re.search(r"\[\[?\s*PAGES\s*:\s*start=(\d+).*?end=(\d+).*?total=(\d+)\]\]?", raw, re.S)
    if m:
        pages = {"start": int(m.group(1)),
                 "end": int(m.group(2)),
                 "total": int(m.group(3))}
    raw = PAGES_RE.sub("", raw)
    raw = START_PAGE_RE.sub("", raw)

    formulas = []

    def _repl(m):
        page = int(m.group(1)) if m.group(1) else None
        latex = (m.group(2) or "").strip()
        if not latex:
            return ""
        idx = len(formulas)
        formulas.append({"page": page, "latex": latex})
        return f"\x00FORMULAIDX{idx}\x00"

    raw = FORMULA_RE.sub(_repl, raw)

    # Одиночные $...$ и $$...$$ — только если не пустые
    def _dollar(m):
        latex = (m.group(1) or "").strip()
        if not latex:
            return ""
        idx = len(formulas)
        formulas.append({"page": None, "latex": latex})
        return f"\x00FORMULAIDX{idx}\x00"

    raw = re.sub(r"\$\$(.+?)\$\$", _dollar, raw, flags=re.S)
    raw = re.sub(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", _dollar, raw, flags=re.S)

    # \[ ... \] и \( ... \)
    raw = re.sub(r"\\\[(.+?)\\\]", _dollar, raw, flags=re.S)
    raw = re.sub(r"\\\((.+?)\\\)", _dollar, raw, flags=re.S)

    # \begin{...} ... \end{...}
    raw = re.sub(r"\\begin\{([^{}]+)\}(.+?)\\end\{\1\}", _dollar, raw, flags=re.S)

    # Остатки служебных маркеров
    raw = re.sub(r"\[\[[A-Z_]+:.*?\]\]", "", raw, flags=re.S)

    return raw.strip(), formulas, pages


# ============================================================
# LaTeX → Unicode (безопасная версия)
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
    "dots": "…", "ldots": "…", "cdots": "⋯",
    "mid": "|", "vert": "|",
}


def latex_to_unicode(s: str) -> str:
    if not s:
        return s
    out = s

    # \frac{a}{b}, \dfrac, \tfrac  (повторяем — вложенные)
    def _frac(m):
        a, b = m.group(1).strip(), m.group(2).strip()
        return f"({a})/({b})"

    for _ in range(4):
        new_out = re.sub(
            r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
            _frac, out,
        )
        if new_out == out:
            break
        out = new_out

    # \sqrt[n]{x} → ⁿ√(x);  \sqrt{x} → √(x)
    out = re.sub(
        r"\\sqrt\s*\[([^\]]+)\]\s*\{([^{}]*)\}",
        lambda m: f"{m.group(1)}√({m.group(2)})", out,
    )
    out = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"√(\1)", out)

    # \left( \right) и т.п.
    out = re.sub(r"\\left\s*([\(\[\{|.])", r"\1", out)
    out = re.sub(r"\\right\s*([\)\]\}|.])", r"\1", out)

    # \text{...} \mathrm{...} \mathbf{...} \operatorname{...} — просто содержимое
    for _ in range(3):
        new_out = re.sub(
            r"\\(?:text|mathrm|mathbf|mathit|mathsf|mathcal|operatorname)\s*\{([^{}]*)\}",
            r"\1", out,
        )
        if new_out == out:
            break
        out = new_out

    # Греческие и операторы — по \b, чтобы не портить слова
    for name, ch in GREEK.items():
        out = re.sub(rf"\\{name}\b", ch, out)
    for name, ch in SIMPLE_OPS.items():
        out = re.sub(rf"\\{name}\b", ch, out)

    # Оставшиеся \{ \} \, \; \! \: — убираем
    out = out.replace("\\{", "{").replace("\\}", "}")
    out = re.sub(r"\\[,;:!]", " ", out)
    out = re.sub(r"\\quad\b|\\qquad\b|\\,", " ", out)
    out = re.sub(r"\\[a-zA-Z]+", "", out)  # прочие незнакомые команды

    # Экранированные символы
    out = out.replace("\\_", "_").replace("\\%", "%")
    out = out.replace("\\&", "&").replace("\\#", "#")
    out = out.replace("\\$", "$")

    # Оставшиеся обратные слэши
    out = out.replace("\\", "")

    # Схлопываем пробелы
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


# ============================================================
# PDF
# ============================================================
class PDFDocument:
    def __init__(self, path: str):
        self.path = path
        self.doc = fitz.open(path)
        self.pages_text = [page.get_text("text") for page in self.doc]
        self.highlights = {}         # {(page, rect_key): color}
        self.search_highlights = {}  # {(page, rect_key): color}

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
                rects = (page.search_for(orig)
                         or page.search_for(query)
                         or page.search_for(q))
                for r in rects:
                    results.append({"page": pno, "rect": r})
                start = i + 1
        return results

    def toggle_highlight(self, page_no, rect, color=(1, 0.6, 1)) -> bool:
        key = (page_no, self._rect_key(rect))
        if key in self.highlights:
            del self.highlights[key]
            return False
        self.highlights[key] = color
        return True

    def add_search_highlight(self, page_no, rect, color=(1, 0.9, 0.2)):
        key = (page_no, self._rect_key(rect))
        self.search_highlights[key] = color

    def clear_search_highlights(self):
        self.search_highlights.clear()


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
        central.setStyleSheet("background-color:#1e1e2e;")
        self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)

        tb = QtWidgets.QHBoxLayout()
        v.addLayout(tb)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setStyleSheet(INPUT_QSS)
        self.search_input.setPlaceholderText("Поиск (без учёта регистра)...")
        self.search_input.returnPressed.connect(self.run_search)
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
        self.page_spin.valueChanged.connect(lambda v: self._center_on_page(v - 1))
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
            self.search_input.setText(search_query)
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
            img = QtGui.QImage(pix.samples, pix.width, pix.height,
                               pix.stride, QtGui.QImage.Format_RGB888)
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
        for (hp, hr), color in self.pdf_doc.highlights.items():
            if hp >= len(self.page_items):
                continue
            item, _, _, zoom = self.page_items[hp]
            ox, oy = item.pos().x(), item.pos().y()
            x0, y0, x1, y1 = hr
            r = QtCore.QRectF(ox + x0 * zoom, oy + y0 * zoom,
                              (x1 - x0) * zoom, (y1 - y0) * zoom)
            pen = QtGui.QPen(QtCore.Qt.NoPen)
            brush = QtGui.QBrush(QtGui.QColor(255, 170, 0, 130))
            el = self.scene.addRect(r, pen, brush)
            el.setData(1, "user_highlight")

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
            r = QtCore.QRectF(ox + x0 * zoom - 1, oy + y0 * zoom - 1,
                              (x1 - x0) * zoom + 2, (y1 - y0) * zoom + 2)
            if i == self.search_idx:
                pen = QtGui.QPen(QtGui.QColor(255, 90, 0), 3)
                brush = QtGui.QBrush(QtGui.QColor(255, 170, 0, 130))
            else:
                pen = QtGui.QPen(QtGui.QColor(255, 200, 0, 180), 1)
                brush = QtGui.QBrush(QtGui.QColor(255, 220, 60, 70))
            el = self.scene.addRect(r, pen, brush)
            el.setData(1, "search_highlight")

    def run_search(self):
        q = self.search_input.text().strip()
        if not q:
            return
        self.pdf_doc.clear_search_highlights()
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
                self, "Поиск", f"«{q}» не найдено."
            )
            return

        self.search_idx = 0
        self._show_match()

    def _show_match(self):
        if not self.search_results:
            return
        r = self.search_results[self.search_idx]
        self.match_label.setText(
            f"{self.search_idx + 1}/{len(self.search_results)}"
        )
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
        if (obj is self.view.viewport()
                and event.type() == QtCore.QEvent.MouseButtonPress):
            if self.btn_highlight.isChecked() and event.button() == QtCore.Qt.LeftButton:
                self._handle_marker_click(self.view.mapToScene(event.pos()))
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
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    lr = fitz.Rect(line["bbox"])
                    if lr.contains(pt):
                        target_rect = lr
                        break
                if target_rect is not None:
                    break

            if target_rect is None:
                best, best_area = None, None
                for w in page.get_text("words"):
                    wr = fitz.Rect(w[:4])
                    if wr.contains(pt):
                        a = wr.get_area()
                        if best_area is None or a < best_area:
                            best_area, best = a, wr
                if best is not None:
                    target_rect = best

            if target_rect is None:
                target_rect = fitz.Rect(rel_x - 2, rel_y - 2,
                                        rel_x + 2, rel_y + 2)

            self.pdf_doc.toggle_highlight(pno, target_rect, color=(1, 0.6, 1))
            self._draw_highlights()
            return


# ============================================================
# Главное окно
# ============================================================
class PDFSearchApp(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI-Ассистент по научным статьям")
        self.setStyleSheet(MAIN_QSS + SCROLL_QSS)
        self.resize(980, 680)
        self.pdf_doc: PDFDocument = None
        self.access_token = None
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
            "Задайте вопрос...\nEnter — отправить, Shift+Enter — новая строка."
        )
        self.input_question.setFixedHeight(80)
        self.input_question.installEventFilter(self)
        layout.addWidget(self.input_question)

        self.btn_ask = QtWidgets.QPushButton("Найти ответ")
        self.btn_ask.setStyleSheet(BUTTON_QSS)
        self.btn_ask.clicked.connect(self.ask_gigachat)
        layout.addWidget(self.btn_ask)

        ans_bar = QtWidgets.QHBoxLayout()
        layout.addLayout(ans_bar)

        lbl_a = QtWidgets.QLabel("Ответ:")
        lbl_a.setStyleSheet(LABEL_QSS)
        ans_bar.addWidget(lbl_a)

        self.btn_prev_answer = QtWidgets.QPushButton("◀ Пред.")
        self.btn_prev_answer.setStyleSheet(BUTTON_QSS)
        self.btn_prev_answer.setFixedWidth(100)
        self.btn_prev_answer.clicked.connect(self.prev_answer)
        self.btn_prev_answer.setEnabled(False)
        ans_bar.addWidget(self.btn_prev_answer)

        self.answer_label = QtWidgets.QLabel("—")
        self.answer_label.setStyleSheet(LABEL_QSS)
        ans_bar.addWidget(self.answer_label)

        self.btn_next_answer = QtWidgets.QPushButton("След. ▶")
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
        self.answer_search.setPlaceholderText(
            "Поиск по ответу (Enter — искать, ◀ ▶ — вхождения)..."
        )
        self.answer_search.returnPressed.connect(self.search_answer_main)
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

        self.output = QtWidgets.QTextBrowser()
        self.output.setStyleSheet(INPUT_QSS)
        self.output.setOpenExternalLinks(False)
        self.output.setOpenLinks(False)
        self.output.setPlaceholderText("Здесь появится ответ...")
        self.output.anchorClicked.connect(self.on_link_clicked)
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
                self.ask_gigachat()
                return True
        return super().eventFilter(obj, event)

    def _ensure_token(self):
        if self.access_token:
            return True
        try:
            self.access_token = get_gigachat_token()
            return True
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка токена", str(e))
            return False

    # --- PDF ---
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
            self.viewer.raise_()
            self.viewer.activateWindow()
            return
        self.viewer = PDFViewerWindow(
            self.pdf_doc, page_no, focus_rect, search_query, parent=self
        )
        self.viewer.show()

    # --- История ответов ---
    def _render_answer(self):
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
        self._render_answer()

    def next_answer(self):
        if self.history_idx >= len(self.history) - 1:
            return
        self.history_idx += 1
        self._render_answer()

    def on_open_answer_window(self):
        if self.history_idx < 0:
            return
        entry = self.history[self.history_idx]
        if self.answer_window is None or not self.answer_window.isVisible():
            self.answer_window = AnswerWindow(entry, self, parent=self)
        else:
            self.answer_window.set_entry(entry)
            self.answer_window.show()
        self.answer_window.raise_()
        self.answer_window.activateWindow()

    # --- Поиск по ответу (главный экран) ---
    def _find_all(self, text, q):
        res, start = [], 0
        ql, tl = q.lower(), text.lower()
        while True:
            i = tl.find(ql, start)
            if i == -1:
                break
            res.append((i, i + len(q)))
            start = i + 1
        return res

    def search_answer_main(self):
        q = self.answer_search.text().strip()
        if not q:
            return
        plain = self.output.toPlainText()
        ranges = self._find_all(plain, q)
        self._matches = ranges
        if not ranges:
            self._match_idx = -1
            self.match_label_main.setText("0/0")
            self.btn_prev_match.setEnabled(False)
            self.btn_next_match.setEnabled(False)
            QtWidgets.QMessageBox.information(
                self, "Поиск", f"«{q}» не найдено."
            )
            return
        self._match_idx = 0
        self._highlight_and_center(self.output, self._matches, self._match_idx)
        self.match_label_main.setText(f"1/{len(self._matches)}")
        self.btn_prev_match.setEnabled(len(self._matches) > 1)
        self.btn_next_match.setEnabled(len(self._matches) > 1)

    def prev_match_main(self):
        if not self._matches:
            return
        self._match_idx = (self._match_idx - 1 + len(self._matches)) \
            % len(self._matches)
        self._highlight_and_center(self.output, self._matches, self._match_idx)
        self.match_label_main.setText(
            f"{self._match_idx + 1}/{len(self._matches)}"
        )

    def next_match_main(self):
        if not self._matches:
            return
        self._match_idx = (self._match_idx + 1) % len(self._matches)
        self._highlight_and_center(self.output, self._matches, self._match_idx)
        self.match_label_main.setText(
            f"{self._match_idx + 1}/{len(self._matches)}"
        )

    @staticmethod
    def _highlight_and_center(browser, matches, idx):
        doc = browser.document()
        plain = browser.toPlainText()

        # Сброс всех подсветок
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
                fmt.setForeground(QtGui.QBrush(QtGui.QColor(30, 30, 46)))
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

    # --- GigaChat ---
    def ask_gigachat(self):
        question = self.input_question.toPlainText().strip()
        if not question:
            return
        if not self.pdf_doc:
            QtWidgets.QMessageBox.information(
                self, "PDF", "Сначала загрузите PDF!"
            )
            return
        if not self._ensure_token():
            return

        self.input_question.clear()
        self.btn_ask.setEnabled(False)
        self.btn_ask.setText("GigaChat думает...")
        QtWidgets.QApplication.processEvents()

        chunks = []
        for pno, _ in enumerate(self.pdf_doc.doc):
            chunks.append(
                f"[Страница {pno + 1}]\n{self.pdf_doc.pages_text[pno]}"
            )
        full_text = "\n".join(chunks)
        if len(full_text) > 60000:
            full_text = full_text[:60000] + "\n...[обрезано]..."

        system_prompt = (
            "Ты — научный ассистент. Отвечай, используя ТОЛЬКО предоставленный текст.\n"
            "Правила оформления:\n"
            "1. Структурируй ответ: заголовки, списки, короткие абзацы.\n"
            "2. После каждого факта — источник вида [стр. N], где N — ТОЧНЫЙ "
            "номер страницы из маркеров [Страница N]. Для разных страниц — "
            "разные номера.\n"
            "3. ЛЮБАЯ формула должна быть вынесена ОТДЕЛЬНОЙ строкой в виде "
            "служебного маркера:\n"
            " [[FORMULA p.<номер страницы>: <latex-код>]]\n"
            " Например: [[FORMULA p.5: \\frac{1}{H_{01}} = \\frac{2H_{01}-1}{2H_{01}}]]\n"
            " В обычном тексте вместо формулы — только пояснение словами. "
            "Никаких $...$ и \\begin{...} в обычном тексте.\n"
            "4. Если ответа нет — 'В данной статье эта информация не найдена'.\n"
            "5. Не выдумывай факты.\n"
            "6. НЕ добавляй служебные строки вида [[START_PAGE: ...]] или "
            "[[PAGES: ...]]."
        )

        prompt = f"{system_prompt}\n\nТекст:\n{full_text}\n\nВопрос: {question}"

        try:
            raw = ask_gigachat_api(self.access_token, prompt)
            text, formulas, pages = parse_answer(raw)
            entry = {"text": text, "formulas": formulas, "pages": pages}
            self.history.append(entry)
            self.history_idx = len(self.history) - 1
            self._render_answer()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка API", str(e))
        finally:
            self.btn_ask.setEnabled(True)
            self.btn_ask.setText("Найти ответ")

    # --- Рендер ответа ---
    def _render_text(self, text: str, formulas) -> str:
        # 1) Экранируем
        t = html.escape(text)

        # 2) Плейсхолдеры для формул до любых regex
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

        t = re.sub(r"\x00FORMULAIDX(\d+)\x00", _formula_placeholder, t)

        # 3) Мини-маркдаун (без ломки формул — они уже плейсхолдеры)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", t)
        t = re.sub(r"^#{1,3}\s*(.+)$", r"<h3>\1</h3>", t, flags=re.M)

        # 4) Ссылки [стр. N] → page:N (каждая — на свою страницу)
        t = re.sub(
            r"\[стр\.\s*(\d+)\]",
            lambda m: (
                f'<a href="page:{m.group(1)}" '
                f'style="color:#9b7dff; text-decoration:none;">'
                f'[стр. {m.group(1)}]</a>'
            ),
            t,
        )

        # 5) Восстановление формул
        def _make_formula_html(idx: int) -> str:
            if idx >= len(formulas):
                return ""
            f = formulas[idx]
            pretty = html.escape(latex_to_unicode(f["latex"]))
            page = f.get("page")
            base = (
                '<span style="color:#ffe08a; '
                'font-family:Consolas,monospace;">'
                f" {pretty} </span>"
            )
            if page is not None:
                base += (
                    f'<a href="formula:{idx}" '
                    f'style="color:#ff9d00; text-decoration:none;">'
                    f"[стр. {page}]</a>"
                )
            return base

        for ph, idx in placeholder_map.items():
            t = t.replace(ph, _make_formula_html(idx))

        # 6) Списки
        lines = t.split("\n")
        out, in_ul = [], False
        for ln in lines:
            m = re.match(r"^\s*[-*]\s+(.+)$", ln)
            if m:
                if not in_ul:
                    out.append("<ul>")
                    in_ul = True
                out.append("<li>" + m.group(1) + "</li>")
            else:
                if in_ul:
                    out.append("</ul>")
                    in_ul = False
                out.append(ln)
        if in_ul:
            out.append("</ul>")

        # 7) Абзацы
        result_lines = []
        buf = []

        def flush():
            if buf:
                chunk = " ".join(s.strip() for s in buf if s.strip())
                if chunk:
                    result_lines.append(f"<p>{chunk}</p>")
                buf.clear()

        for ln in out:
            if (ln.startswith("<ul>") or ln.startswith("</ul>")
                    or ln.startswith("<li>") or ln.startswith("<h3>")
                    or ln.startswith("</h3>")):
                flush()
                result_lines.append(ln)
            elif not ln.strip():
                flush()
            else:
                buf.append(ln)
        flush()

        t = "\n".join(result_lines) if result_lines else t

        return (
            "<div style='color:#e0e0f0; font-size:14px; line-height:1.5;'>"
            f"{t}</div>"
        )

    def _resolve_pdf_page(self, doc_page_no: int):
        if not self.pdf_doc:
            return None
        total_pages = len(self.pdf_doc.doc)
        sys_idx = doc_page_no - 1
        sys_idx = max(0, min(sys_idx, total_pages - 1))
        return sys_idx

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
    def __init__(self, entry: dict, main_app: 'PDFSearchApp', parent=None):
        super().__init__(parent)
        self.main_app = main_app
        self.setWindowTitle("Ответ GigaChat")
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
        self.search_input.setPlaceholderText("Поиск по ответу (Enter — искать)...")
        self.search_input.returnPressed.connect(self.search_in_answer)
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
        v.addWidget(self.text, 1)

        self.entry = entry
        self._matches = []
        self._match_idx = -1
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
        self._match_idx = -1
        self.match_label.setText("0/0")
        self.btn_prev.setEnabled(False)
        self.btn_next.setEnabled(False)

    def prev_answer(self):
        if self.main_app.history_idx > 0:
            self.main_app.history_idx -= 1
        self.main_app._render_answer()
        if self.main_app.history_idx >= 0:
            self.set_entry(self.main_app.history[self.main_app.history_idx])

    def next_answer(self):
        if self.main_app.history_idx < len(self.main_app.history) - 1:
            self.main_app.history_idx += 1
        self.main_app._render_answer()
        if self.main_app.history_idx >= 0:
            self.set_entry(self.main_app.history[self.main_app.history_idx])

    def on_link_clicked(self, url):
        self.main_app.on_link_clicked(url)

    def _find_all(self, text, q):
        res, start = [], 0
        ql, tl = q.lower(), text.lower()
        while True:
            i = tl.find(ql, start)
            if i == -1:
                break
            res.append((i, i + len(q)))
            start = i + 1
        return res

    def search_in_answer(self):
        q = self.search_input.text().strip()
        if not q:
            return
        plain = self.text.toPlainText()
        self._matches = self._find_all(plain, q)
        if not self._matches:
            self._match_idx = -1
            self.match_label.setText("0/0")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            QtWidgets.QMessageBox.information(
                self, "Поиск", f"«{q}» не найдено."
            )
            return
        self._match_idx = 0
        self._highlight_and_center()

    def _highlight_and_center(self):
        PDFSearchApp._highlight_and_center(
            self.text, self._matches, self._match_idx
        )
        self.match_label.setText(
            f"{self._match_idx + 1}/{len(self._matches)}"
        )
        self.btn_prev.setEnabled(len(self._matches) > 1)
        self.btn_next.setEnabled(len(self._matches) > 1)

    def next_match(self):
        if not self._matches:
            return
        self._match_idx = (self._match_idx + 1) % len(self._matches)
        self._highlight_and_center()

    def prev_match(self):
        if not self._matches:
            return
        self._match_idx = (self._match_idx - 1 + len(self._matches)) \
            % len(self._matches)
        self._highlight_and_center()


# ============================================================
if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(MENU_QSS)

    window = PDFSearchApp()
    window.show()
    sys.exit(app.exec_())
