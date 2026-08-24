import json
import re
import argparse
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional


@dataclass
class MigrationRecord:
    projectKey: str
    projectKey_new_tool: str
    orderToolId: int
    dateMigration: str


def clean_key(key: str, suffix: str) -> str:
    return re.sub(r"[~\-]", "", key).upper() + suffix


def parse_line(line: str, line_no: int, suffix: str, date_migration: str) -> MigrationRecord:
    parts = line.split()
    if len(parts) != 2:
        raise ValueError(f"Строка {line_no}: ожидалось 2 поля, получено {len(parts)}: {line!r}")

    project_key, order_id_raw = parts
    if not order_id_raw.isdigit():
        raise ValueError(f"Строка {line_no}: orderToolId не число: {order_id_raw!r}")

    return MigrationRecord(
        projectKey=project_key,
        projectKey_new_tool=clean_key(project_key, suffix),
        orderToolId=int(order_id_raw),
        dateMigration=date_migration,
    )


def parse_data(raw: str, suffix: str, date_migration: str) -> tuple[list[MigrationRecord], list[str]]:
    records = []
    seen_keys = set()
    warnings = []
    for i, line in enumerate(raw.strip().splitlines(), start=1):
        if not line.strip():
            continue
        rec = parse_line(line, i, suffix, date_migration)
        if rec.projectKey in seen_keys:
            msg = f"⚠️ Предупреждение: дубликат ключа {rec.projectKey} (строка {i})"
            warnings.append(msg)
            print(msg, file=sys.stderr)
        seen_keys.add(rec.projectKey)
        records.append(rec)
    return records, warnings


def process_migration(raw: str, suffix: str, date_migration: str) -> tuple[list[dict], list[str]]:
    records, warnings = parse_data(raw, suffix, date_migration)
    return [asdict(r) for r in records], warnings


def main():
    parser = argparse.ArgumentParser(description="Миграция project keys")
    parser.add_argument("--input", type=str, help="Путь к входному файлу (по умолчанию — встроенные данные)")
    parser.add_argument("--output", type=str, help="Путь к выходному JSON (по умолчанию — stdout)")
    parser.add_argument("--suffix", type=str, default="S1")
    parser.add_argument("--date", type=str, default=datetime.today().strftime("%Y-%m-%d"))
    parser.add_argument("--ui", action="store_true", help="Запустить веб-интерфейс")
    args = parser.parse_args()

    if args.ui:
        run_ui()
        return

    raw = open(args.input, encoding="utf-8").read() if args.input else DEFAULT_DATA

    try:
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
            input[type="text"], input[type="date"], textarea { width: 100%; padding: 10px; margin-top: 5px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
            textarea { height: 200px; font-family: monospace; }
            button { margin-top: 20px; padding: 12px 24px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }
            button:hover { background: #0056b3; }
            .output { margin-top: 20px; background: #f8f9fa; padding: 15px; border-radius: 4px; border: 1px solid #ddd; }
            pre { background: #2d2d2d; color: #f8f8f2; padding: 15px; overflow-x: auto; border-radius: 4px; }
            .warning { color: #856404; background: #fff3cd; padding: 10px; border-radius: 4px; margin-top: 10px; }
            .error { color: #721c24; background: #f8d7da; padding: 10px; border-radius: 4px; margin-top: 10px; }
            .hint { font-size: 12px; color: #666; margin-top: 5px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔄 Миграция Project Keys</h1>
            <form id="migrationForm">
                <label for="data">Входные данные (формат: KEY ORDER_ID):</label>
                <textarea id="data" placeholder="DOSRE   345508\nPROJECT 123456"></textarea>
                <div class="hint">Каждая строка: ключ проекта и ID заказа, разделённые пробелом</div>
                
                <label for="suffix">Суффикс:</label>
                <input type="text" id="suffix" value="S1" placeholder="S1">
                
                <label for="date">Дата миграции:</label>
                <input type="date" id="date" value="{{ today }}">
                
                <button type="submit">Обработать</button>
            </form>
            
            <div id="result" class="output" style="display:none;">
                <h3>Результат:</h3>
                <div id="warnings"></div>
                <div style="position: relative;">
                    <pre id="jsonOutput" style="background-color: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 16px; overflow-x: auto; font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; font-size: 13px; color: #24292f; position: relative;"></pre>
                    <button id="copyBtn" type="button" onclick="copyResult()" style="position: absolute; top: 8px; right: 8px; opacity: 0; transition: opacity 0.2s; background-color: #ffffff; border: 1px solid #d0d7de; border-radius: 6px; padding: 4px 8px; font-size: 12px; cursor: pointer; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">Copy</button>
                </div>
            </div>
        </div>
        
        <script>
            document.getElementById('migrationForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const data = {
                    raw_data: document.getElementById('data').value,
                    suffix: document.getElementById('suffix').value,
                    date: document.getElementById('date').value
                };
                
                try {
                    const response = await fetch('/process', {
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
            
            // Hover effects for copy button
            const jsonPre = document.getElementById('jsonOutput');
            const copyBtn = document.getElementById('copyBtn');
            
            jsonPre.addEventListener('mouseenter', () => {
                copyBtn.style.opacity = '1';
            });
            jsonPre.addEventListener('mouseleave', () => {
                if (copyBtn.innerText !== 'Copied!') {
                    copyBtn.style.opacity = '0';
                }
            });

            function copyResult() {
                const jsonText = document.getElementById('jsonOutput').textContent;
                if (!jsonText) {
                    alert('Нет результата для копирования');
                    return;
                }
                // Оборачиваем JSON в тройные кавычки для форматирования кода в мессенджерах
                const wrappedText = '```\n' + jsonText + '\n```';
                navigator.clipboard.writeText(wrappedText).then(() => {
                    const copyBtn = document.getElementById('copyBtn');
                    const originalText = copyBtn.innerText;
                    copyBtn.innerText = 'Copied!';
                    copyBtn.style.opacity = '1';
                    setTimeout(() => {
                        copyBtn.innerText = originalText;
                        copyBtn.style.opacity = '0';
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

    class MigrationHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            # Извлекаем путь без query параметров
            parsed_path = urllib.parse.urlparse(self.path)
            path = parsed_path.path
            
            if path == '/' or path == '' or path == '/index.html':
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(html_content.encode('utf-8'))
            elif path == '/favicon.ico':
                self.send_response(204)
                self.end_headers()
            else:
                self.send_error(404, f"Not Found - Path: {path}")

        def do_POST(self):
            # Извлекаем путь без query параметров
            parsed_path = urllib.parse.urlparse(self.path)
            path = parsed_path.path
            
            if path == '/process':
                try:
                    content_length = int(self.headers.get('Content-Length', 0))
                    post_data = self.rfile.read(content_length).decode('utf-8')
                    
                    data = json.loads(post_data)
                    raw_data = data.get('raw_data', '')
                    suffix = data.get('suffix', 'S1')
                    date_migration = data.get('date', today)

                    if not raw_data.strip():
                        response = {'error': 'Входные данные пустые', 'data': [], 'warnings': []}
                    else:
                        records, warnings = process_migration(raw_data, suffix, date_migration)
                        response = {'data': records, 'warnings': warnings}
                except ValueError as e:
                    response = {'error': str(e), 'data': [], 'warnings': []}
                except json.JSONDecodeError:
                    response = {'error': 'Некорректный JSON', 'data': [], 'warnings': []}
                except Exception as e:
                    response = {'error': f'Ошибка сервера: {str(e)}', 'data': [], 'warnings': []}

                self.send_response(200)
                self.send_header('Content-type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
            else:
                self.send_error(404, f"Not Found - Path: {path}")

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
