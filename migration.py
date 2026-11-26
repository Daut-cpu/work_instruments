import csv
import io
import json
import re
import argparse
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime
from typing import Optional


@dataclass
class MigrationRecord:
    projectKey: str
    projectKey_new_tool: str
    orderToolId: int
    dateMigration: str


def clean_key(key: str, suffix: str) -> str:
    return re.sub(r"[~\-]", "", key).upper() + suffix


ALLOWED_DOMAINS = ("S1", "D1")
FILE_COLUMNS = ("key", "order_tool_id", "migration_date", "domain")
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y.%m.%d", "%Y/%m/%d")


def build_record(project_key: str, order_id_raw, suffix: str, date_migration: str, where: str) -> MigrationRecord:
    """Единая точка формирования записи — используется и для ручного ввода, и для файлов."""
    order_id = str(order_id_raw).strip()
    if not order_id.isdigit():
        raise ValueError(f"{where}: orderToolId не число: {order_id_raw!r}")

    return MigrationRecord(
        projectKey=project_key,
        projectKey_new_tool=clean_key(project_key, suffix),
        orderToolId=int(order_id),
        dateMigration=date_migration,
    )


def collect_records(items) -> tuple[list[MigrationRecord], list[str]]:
    """items — итерируемое из (where, MigrationRecord). Проверяет дубликаты ключей."""
    records = []
    seen_keys = set()
    warnings = []
    for where, rec in items:
        if rec.projectKey in seen_keys:
            msg = f"⚠️ Предупреждение: дубликат ключа {rec.projectKey} ({where})"
            warnings.append(msg)
            print(msg, file=sys.stderr)
        seen_keys.add(rec.projectKey)
        records.append(rec)
    return records, warnings


def parse_line(line: str, line_no: int, suffix: str, date_migration: str) -> MigrationRecord:
    parts = line.split()
    if len(parts) != 2:
        raise ValueError(f"Строка {line_no}: ожидалось 2 поля, получено {len(parts)}: {line!r}")

    project_key, order_id_raw = parts
    return build_record(project_key, order_id_raw, suffix, date_migration, f"Строка {line_no}")


def parse_data(raw: str, suffix: str, date_migration: str) -> tuple[list[MigrationRecord], list[str]]:
    def items():
        for i, line in enumerate(raw.strip().splitlines(), start=1):
            if line.strip():
                yield f"строка {i}", parse_line(line, i, suffix, date_migration)

    return collect_records(items())


def process_migration(raw: str, suffix: str, date_migration: str) -> tuple[list[dict], list[str]]:
    records, warnings = parse_data(raw, suffix, date_migration)
    return [asdict(r) for r in records], warnings


# ---------- Загрузка из файла Excel / CSV ----------

def _normalize_date(value, where: str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    # Excel иногда отдаёт "2026-11-26 00:00:00"
    text = text.split(" ")[0].split("T")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"{where}: некорректная дата migration_date: {value!r} (ожидается ГГГГ-ММ-ДД или ДД.ММ.ГГГГ)")


def _normalize_order_id(value):
    # Excel хранит числа как float: 345508.0 -> 345508
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _read_csv_rows(content: bytes) -> list[list]:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Не удалось определить кодировку CSV (ожидается UTF-8 или Windows-1251)")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return list(csv.reader(io.StringIO(text), dialect))


def _read_excel_rows(content: bytes) -> list[list]:
    try:
        import openpyxl
    except ImportError:
        raise ValueError("Для чтения Excel установите пакет openpyxl: pip install openpyxl")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as e:
        raise ValueError(f"Не удалось прочитать Excel-файл: {e}")
    try:
        return [list(row) for row in wb.active.iter_rows(values_only=True)]
    finally:
        wb.close()


def read_table(content: bytes, filename: str) -> list[list]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        return _read_excel_rows(content)
    if name.endswith((".csv", ".txt")):
        return _read_csv_rows(content)
    raise ValueError(f"Неподдерживаемый формат файла: {filename!r} (ожидается .xlsx или .csv)")


def parse_table(rows: list[list]) -> tuple[list[MigrationRecord], list[str]]:
    def is_empty(row):
        return all(c is None or str(c).strip() == "" for c in row)

    rows = list(rows)
    while rows and is_empty(rows[0]):
        rows.pop(0)
    if not rows:
        raise ValueError("Файл пустой")

    header = [str(c).strip().lower() if c is not None else "" for c in rows[0]]
    missing = [c for c in FILE_COLUMNS if c not in header]
    if missing:
        raise ValueError(
            f"В файле отсутствуют колонки: {', '.join(missing)}. "
            f"Ожидаются: {', '.join(FILE_COLUMNS)}"
        )
    idx = {c: header.index(c) for c in FILE_COLUMNS}

    def cell(row, col):
        i = idx[col]
        return row[i] if i < len(row) else None

    def items():
        for row_no, row in enumerate(rows[1:], start=2):
            if is_empty(row):
                continue
            where = f"строка {row_no}"

            key = cell(row, "key")
            key = str(key).strip() if key is not None else ""
            if not key:
                raise ValueError(f"{where}: пустое значение key")

            domain = cell(row, "domain")
            domain = str(domain).strip().upper() if domain is not None else ""
            if domain not in ALLOWED_DOMAINS:
                raise ValueError(
                    f"{where}: недопустимый domain {domain!r} (допустимо: {', '.join(ALLOWED_DOMAINS)})"
                )

            raw_date = cell(row, "migration_date")
            if raw_date is None or str(raw_date).strip() == "":
                raise ValueError(f"{where}: пустое значение migration_date")
            date_migration = _normalize_date(raw_date, where)

            order_id = _normalize_order_id(cell(row, "order_tool_id"))
            yield where, build_record(key, order_id, domain, date_migration, where)

    records, warnings = collect_records(items())
    if not records:
        raise ValueError("В файле нет строк с данными")
    return records, warnings


def process_migration_file(content: bytes, filename: str) -> tuple[list[dict], list[str]]:
    records, warnings = parse_table(read_table(content, filename))
    return [asdict(r) for r in records], warnings


def main():
    parser = argparse.ArgumentParser(description="Миграция project keys")
    parser.add_argument("--input", type=str,
                        help="Путь к входному файлу: .xlsx/.csv (колонки key, order_tool_id, migration_date, domain) "
                             "или текстовый файл 'KEY ORDER_ID' (по умолчанию — встроенные данные)")
    parser.add_argument("--output", type=str, help="Путь к выходному JSON (по умолчанию — stdout)")
    parser.add_argument("--suffix", type=str, default="S1")
    parser.add_argument("--date", type=str, default=datetime.today().strftime("%Y-%m-%d"))
    parser.add_argument("--ui", action="store_true", help="Запустить веб-интерфейс")
    args = parser.parse_args()

    if args.ui:
        run_ui()
        return

    is_table = bool(args.input) and args.input.lower().endswith((".xlsx", ".xlsm", ".csv"))

    try:
        if is_table:
            # suffix и дата берутся из колонок domain и migration_date файла
            with open(args.input, "rb") as f:
                records, warnings = parse_table(read_table(f.read(), args.input))
        else:
            raw = open(args.input, encoding="utf-8").read() if args.input else DEFAULT_DATA
            records, warnings = parse_data(raw, args.suffix, args.date)
    except ValueError as e:
        sys.exit(f"Ошибка парсинга: {e}")

    output = json.dumps([asdict(r) for r in records], indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Записано {len(records)} записей в {args.output}")
    else:
        print(output)


DEFAULT_DATA = """
DOSRE   345508
"""


def run_ui():
    import base64
    import binascii
    import http.server
    import socketserver
    import urllib.parse
    import webbrowser
    import threading

    PORT = 8080
    
    HTML_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Миграция Project Keys</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 900px; margin: 40px auto; padding: 20px; background: #f5f5f5; }
            .container { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #333; margin-bottom: 20px; }
            label { display: block; margin-top: 15px; font-weight: 600; color: #555; }
            input[type="text"], input[type="date"], select, textarea { width: 100%; padding: 10px; margin-top: 5px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
            textarea { height: 200px; font-family: monospace; }
            button { margin-top: 20px; padding: 12px 24px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }
            button:hover { background: #0056b3; }
            .output { margin-top: 20px; background: #f8f9fa; padding: 15px; border-radius: 4px; border: 1px solid #ddd; }
            .code-wrapper { position: relative; margin-top: 10px; }
            .code-wrapper pre { background-color: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 16px; overflow-x: auto; font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; font-size: 13px; color: #24292f; min-height: 100px; }
            .copy-btn { position: absolute; top: 8px; right: 8px; opacity: 0; transition: opacity 0.2s; background-color: #ffffff; border: 1px solid #d0d7de; border-radius: 6px; padding: 4px 8px; font-size: 12px; cursor: pointer; box-shadow: 0 1px 3px rgba(0,0,0,0.1); z-index: 10; }
            .code-wrapper:hover .copy-btn { opacity: 1; }
            .warning { color: #856404; background: #fff3cd; padding: 10px; border-radius: 4px; margin-top: 10px; }
            .error { color: #721c24; background: #f8d7da; padding: 10px; border-radius: 4px; margin-top: 10px; }
            .hint { font-size: 12px; color: #666; margin-top: 5px; }
            .mode-switch { display: flex; gap: 20px; margin-top: 5px; }
            .mode-switch label { display: inline-flex; align-items: center; gap: 6px; margin-top: 0; font-weight: normal; cursor: pointer; }
            input[type="file"] { margin-top: 5px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔄 Миграция Project Keys</h1>
            <form id="migrationForm">
                <label>Источник данных:</label>
                <div class="mode-switch">
                    <label><input type="radio" name="mode" value="manual" checked> Ручной ввод</label>
                    <label><input type="radio" name="mode" value="file"> Файл Excel / CSV</label>
                </div>

                <div id="fileBlock" style="display:none;">
                    <label for="file">Файл (.xlsx или .csv):</label>
                    <input type="file" id="file" accept=".xlsx,.xlsm,.csv">
                    <div class="hint">Колонки: <code>key</code>, <code>order_tool_id</code>, <code>migration_date</code>, <code>domain</code> (S1 или D1). Суффикс и дата берутся из файла.</div>
                </div>

                <div id="manualBlock">
                <label for="data">Входные данные (формат: KEY ORDER_ID):</label>
                <textarea id="data" placeholder="DOSRE   345508\nPROJECT 123456"></textarea>
                <div class="hint">Каждая строка: ключ проекта и ID заказа, разделённые пробелом</div>
                
                <label for="suffix">Суффикс:</label>
                <select id="suffix">
                    <option value="S1">S1</option>
                    <option value="D1">D1</option>
                </select>
                
                <label for="date">Дата миграции:</label>
                <input type="date" id="date" value="{{ today }}">
                </div>
                
                <button type="submit">Обработать</button>
            </form>
            
            <div id="result" class="output" style="display:none;">
                <h3>Результат:</h3>
                <div id="warnings"></div>
                <div class="code-wrapper" id="codeWrapper">
                    <pre id="jsonOutput"></pre>
                    <button id="copyBtn" type="button" class="copy-btn" onclick="copyResult()">Copy</button>
                </div>
            </div>
        </div>
        
        <script>
            document.querySelectorAll('input[name="mode"]').forEach(r => r.addEventListener('change', () => {
                const isFile = document.querySelector('input[name="mode"]:checked').value === 'file';
                document.getElementById('fileBlock').style.display = isFile ? 'block' : 'none';
                document.getElementById('manualBlock').style.display = isFile ? 'none' : 'block';
            }));

            function readFileAsBase64(file) {
                return new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(reader.result.split(',')[1] || '');
                    reader.onerror = () => reject(reader.error);
                    reader.readAsDataURL(file);
                });
            }

            document.getElementById('migrationForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const mode = document.querySelector('input[name="mode"]:checked').value;
                let url, data;
                if (mode === 'file') {
                    const file = document.getElementById('file').files[0];
                    if (!file) {
                        alert('Выберите файл');
                        return;
                    }
                    url = '/process-file';
                    data = { filename: file.name, content: await readFileAsBase64(file) };
                } else {
                    url = '/process';
                    data = {
                        raw_data: document.getElementById('data').value,
                        suffix: document.getElementById('suffix').value,
                        date: document.getElementById('date').value
                    };
                }
                
                try {
                    const response = await fetch(url, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data)
                    });
                    const result = await response.json();
                    
                    const resultDiv = document.getElementById('result');
                    const warningsDiv = document.getElementById('warnings');
                    const jsonPre = document.getElementById('jsonOutput');
                    
                    resultDiv.style.display = 'block';
                    
                    if (result.warnings && result.warnings.length > 0) {
                        warningsDiv.innerHTML = '<div class="warning"><strong>Предупреждения:</strong><br>' + 
                            result.warnings.map(w => w.replace(/</g, '&lt;').replace(/>/g, '&gt;')).join('<br>') + '</div>';
                    } else {
                        warningsDiv.innerHTML = '';
                    }
                    
                    if (result.error) {
                        warningsDiv.innerHTML += '<div class="error"><strong>Ошибка:</strong> ' + 
                            result.error.replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</div>';
                        jsonPre.textContent = '';
                    } else {
                        jsonPre.textContent = JSON.stringify(result.data, null, 2);
                    }
                } catch (err) {
                    alert('Ошибка запроса: ' + err.message);
                }
            });
            
            function copyResult() {
                const jsonText = document.getElementById('jsonOutput').textContent;
                if (!jsonText || jsonText.trim() === '') {
                    alert('Нет результата для копирования');
                    return;
                }
                navigator.clipboard.writeText(jsonText).then(() => {
                    const copyBtn = document.getElementById('copyBtn');
                    const originalText = copyBtn.innerText;
                    copyBtn.innerText = 'Copied!';
                    setTimeout(() => {
                        copyBtn.innerText = originalText;
                    }, 2000);
                }).catch(err => {
                    alert('Ошибка копирования: ' + err.message);
                });
            }
        </script>
    </body>
    </html>
    """

    today = datetime.today().strftime("%Y-%m-%d")
    html_content = HTML_TEMPLATE.replace("{{ today }}", today)

    class MigrationHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(html_content.encode('utf-8'))
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path in ('/process', '/process-file'):
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length).decode('utf-8')
                
                try:
                    data = json.loads(post_data)
                    if self.path == '/process-file':
                        filename = data.get('filename', '')
                        try:
                            content = base64.b64decode(data.get('content', ''), validate=True)
                        except (binascii.Error, ValueError):
                            raise ValueError('Не удалось декодировать содержимое файла')
                        if not content:
                            raise ValueError('Файл пустой')
                        records, warnings = process_migration_file(content, filename)
                        response = {'data': records, 'warnings': warnings}
                    else:
                        raw_data = data.get('raw_data', '')
                        suffix = data.get('suffix', 'S1')
                        date_migration = data.get('date', today)

                        if not raw_data.strip():
                            response = {'error': 'Входные данные пустые'}
                        else:
                            records, warnings = process_migration(raw_data, suffix, date_migration)
                            response = {'data': records, 'warnings': warnings}
                except json.JSONDecodeError:
                    response = {'error': 'Некорректный JSON'}
                except ValueError as e:
                    response = {'error': str(e)}
                except Exception as e:
                    response = {'error': f'Ошибка сервера: {str(e)}'}

                self.send_response(200)
                self.send_header('Content-type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
            else:
                self.send_error(404)

        def log_message(self, format, *args):
            pass  # Suppress logging

    def open_browser():
        webbrowser.open(f'http://localhost:{PORT}')

    # Попытка найти свободный порт, если 8080 занят
    def find_free_port(start_port):
        port = start_port
        while port < 65535:
            try:
                with socketserver.TCPServer(("", port), MigrationHandler) as test_server:
                    return port
            except OSError:
                port += 1
        raise OSError("Не удалось найти свободный порт")
    
    try:
        actual_port = find_free_port(PORT)
    except OSError as e:
        print(f"❌ Ошибка: не удалось запустить сервер. {e}")
        return
    
    print(f"🌐 Запуск веб-интерфейса на http://localhost:{actual_port}")
    print("Нажмите Ctrl+C для остановки")
    
    timer = threading.Timer(1.0, lambda: webbrowser.open(f'http://localhost:{actual_port}'))
    timer.start()
    
    with socketserver.TCPServer(("", actual_port), MigrationHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nОстановка сервера...")
            timer.cancel()


if __name__ == "__main__":
    main()
