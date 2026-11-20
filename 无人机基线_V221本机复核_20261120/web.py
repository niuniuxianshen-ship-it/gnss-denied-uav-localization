"""Run the V221 review interface on the local IPv4 loopback address."""

import argparse
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Timer
import webbrowser

from review_core import APP, EXAMPLE, InputError, analyze, load_policy, read_text


MAX_REQUEST_BYTES = 64 * 1024**2


class ReviewHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def send_bytes(self, status, kind, body):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; "
                         "style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, value):
        self.send_bytes(status, 'application/json; charset=utf-8',
                        json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))

    def local_request(self, api=True):
        if (self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}' and
                (not api or self.headers.get('X-V221-Review') == '1')):
            return True
        self.send_json(403, {'error': '请使用启动窗口显示的本机地址。'})
        return False

    def do_GET(self):
        if not self.local_request(self.path != '/'):
            return
        if self.path == '/':
            self.send_bytes(200, 'text/html; charset=utf-8', self.server.page)
        elif self.path == '/api/config':
            self.send_json(200, {'example': EXAMPLE.is_file()})
        elif self.path == '/api/example' and EXAMPLE.is_file():
            self.review([read_text(EXAMPLE)])
        else:
            self.send_json(404, {'error': '所选记录当前不可用。'})

    def do_POST(self):
        if not self.local_request():
            return
        if self.path != '/api/analyze':
            self.send_json(404, {'error': '未知的请求。'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            size = 0
        if not 0 < size <= MAX_REQUEST_BYTES:
            self.send_json(413, {'error': '网页一次可读取 64 MB。请按运行拆分导出表。'})
            return
        if self.headers.get_content_type() != 'application/json':
            self.send_json(415, {'error': '请用页面上的“打开日志”选择文件。'})
            return
        try:
            texts = json.loads(self.rfile.read(size).decode('utf-8'))
            if not isinstance(texts, list) or not all(isinstance(value, str) for value in texts):
                raise ValueError('expected CSV strings')
        except (ValueError, UnicodeError):
            self.send_json(400, {'error': '读取失败。请选择 UTF-8 CSV。'})
            return
        self.review(texts)

    def review(self, texts):
        try:
            runs = analyze(texts, self.server.policy)
        except (InputError, csv.Error) as error:
            self.send_json(400, {'error': str(error)})
            return
        self.send_json(200, {'runs': runs})


def create_server(port=0):
    policy = load_policy()
    page = (APP / 'index.html').read_bytes()
    server = ThreadingHTTPServer(('127.0.0.1', port), ReviewHandler)
    server.policy = policy
    server.page = page
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    with create_server(args.port) as server:
        url = f'http://127.0.0.1:{server.server_port}'
        print(f'V221 本机复核  {url}', flush=True)
        if args.open:
            timer = Timer(0.25, webbrowser.open, args=(url,))
            timer.daemon = True
            timer.start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
