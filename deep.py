from openai import OpenAI
from io import BytesIO
import pathlib, subprocess, sys, os

client = OpenAI(
    api_key="sk-3dc66ffeb3074a30be6819c834271f54",
    base_url="https://api.deepseek.com",
)

docx_path = 'name.docx'

with open(docx_path, 'rb') as f:
    docx_binary = f.read()

binary_stream = BytesIO(docx_binary)

# МЕНЯЕМ purpose с 'user_data' на 'text_recognition' (или 'rich_layout_recognition')
uploaded_file = client.files.create(
    file=("name.docx", binary_stream), 
    purpose="text_recognition" 
)

out = pathlib.Path("answer.md")

with out.open("w", encoding="utf-8") as f_out, \
     client.chat.completions.create(
        model="deepseek-chat",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Пришли готовый исправленный main"},
                {"type": "file", "file_id": uploaded_file.id}
            ]
        }],
        max_tokens=16384,
        stream=True,
    ) as stream:
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            f_out.write(delta)
            f_out.flush()

client.files.delete(uploaded_file.id)
print(f"OK, записано в {out.resolve()} ({out.stat().st_size} байт)")
