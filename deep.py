import os
import base64
import pathlib
from io import BytesIO
from openai import OpenAI
from docx import Document
from docx.oxml.ns import qn

# --- OCR (опционально, нужен установленный tesseract + pytesseract + Pillow) ---
try:
    import pytesseract
    from PIL import Image
    HAS_OCR = True
except ImportError:
    HAS_OCR = False
    print("[!] pytesseract/Pillow не установлены — картинки будут пропущены.")

client = OpenAI(
    api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
    base_url="https://api.deepseek.com",
)

docx_path = pathlib.Path("question.docx")
out = pathlib.Path("answer.md")


# --- 1. Извлечение текста + картинок из docx ---
def extract_docx_text(path: pathlib.Path) -> str:
    doc = Document(str(path))
    parts = []

    for p in doc.paragraphs:
        if p.text.strip():
            parts.append(p.text)

    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))

    # --- картинки ---
    # В docx картинки лежат в doc.part.related_parts по relationship id
    # Удобнее всего пройтись по XML документа и найти все <a:blip r:embed="...">
    rels = doc.part.rels
    img_index = 0
    for rel in rels.values():
        if "image" in rel.reltype:
            img_index += 1
            image_part = rel.target_part
            image_bytes = image_part.blob
            ext = image_part.partname.ext  # png / jpg / emf ...

            if HAS_OCR and ext.lower() in ("png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"):
                try:
                    img = Image.open(BytesIO(image_bytes))
                    # язык OCR настрой под свои документы, например 'rus+eng'
                    ocr_text = pytesseract.image_to_string(img, lang="rus+eng").strip()
                    if ocr_text:
                        parts.append(
                            f"[ИЗОБРАЖЕНИЕ #{img_index} — распознанный текст]\n{ocr_text}"
                        )
                    else:
                        parts.append(
                            f"[ИЗОБРАЖЕНИЕ #{img_index} — текст не распознан, файл {ext}]"
                        )
                except Exception as e:
                    parts.append(f"[ИЗОБРАЖЕНИЕ #{img_index} — ошибка OCR: {e}]")
            else:
                # emf/wmf и прочее, что PIL не открывает — только уведомляем
                parts.append(
                    f"[ИЗОБРАЖЕНИЕ #{img_index} — формат {ext} не поддерживается локальным OCR]"
                )

    return "\n".join(parts)


document_text = extract_docx_text(docx_path)
print(f"Извлечено {len(document_text)} символов из {docx_path}")


# --- 2. Стриминг с автопродолжением, пишем в файл напрямую ---
def stream_completion(prompt: str, out_path: pathlib.Path, max_tokens: int = 16384) -> str:
    messages = [{"role": "user", "content": prompt}]
    full_text = ""
    iteration = 0

    with out_path.open("w", encoding="utf-8") as f_out:
        while True:
            iteration += 1
            print(f"[итерация {iteration}] запрос...")
            got_any = False
            finish_reason = None

            with client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                max_tokens=max_tokens,
                stream=True,
            ) as stream:
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta.content
                    if delta:
                        got_any = True
                        full_text += delta
                        f_out.write(delta)
                        f_out.flush()
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

            if finish_reason == "length" and got_any:
                print("[!] ответ обрезан по лимиту, продолжаем...")
                messages = [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": full_text},
                    {"role": "user", "content":
                        "Продолжи ответ ровно с места, где ты остановился. "
                        "Не повторяй уже написанное, не добавляй вступлений — "
                        "только продолжение."},
                ]
                continue

            break

    return full_text


# --- 3. Основной прогон ---
prompt = (
    "В промпте могут быть вставленные "
    "изображения; их содержимое распознано OCR и помечено блоками "
    "[ИЗОБРАЖЕНИЕ #N — распознанный текст]. Учти это при ответе.\n\n"
    f"{document_text}\n\n"
)

try:
    full = stream_completion(prompt, out)
    print(f"OK, записано в {out.resolve()} ({out.stat().st_size} байт, {len(full)} символов)")
except Exception as e:
    print(f"ОШИБКА: {e}")
    raise
