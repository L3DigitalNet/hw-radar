import json
import subprocess
import sys
import textwrap
from pathlib import Path

# Runs in a fresh interpreter, not in-process: the Twisted reactor is a
# process-wide singleton bound to the loop present at install, and the
# regression has two halves that only a clean process shows — the crawl must
# resolve a hostname, and the process must then EXIT (a non-daemon reactor
# pool thread hangs interpreter shutdown, which pytest's own process would
# mask). The spider targets `localhost`, never 127.0.0.1, so the request goes
# through the reactor's name resolver; that resolver runs on the reactor
# thread pool, which never started when the reactor is driven by asyncio.run()
# instead of reactor.run(), and every request died at DOWNLOAD_TIMEOUT.
CHILD = textwrap.dedent(
    """
    import asyncio, http.server, json, threading, time

    import scrapy

    from hw_radar.acquisition.scrapy_support import run_spider

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><title>ok</title></html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://localhost:{server.server_address[1]}/"

    class LocalSpider(scrapy.Spider):
        name = "local"

        def __init__(self, start_url, **kwargs):
            super().__init__(**kwargs)
            self.start_urls = [start_url]

        def parse(self, response):
            yield {"status": response.status}

    async def main():
        runs = []
        # Two crawls on one loop: the poller calls run_spider repeatedly in
        # one process, so pool startup must be idempotent.
        for _ in range(2):
            started = time.monotonic()
            result = await run_spider(
                LocalSpider,
                # Production robots handling stays on (the server 200s
                # /robots.txt too, so it parses as allow-all); a short timeout
                # keeps the pre-fix failure fast instead of 30 s per request.
                settings_override={"DOWNLOAD_TIMEOUT": 3, "RETRY_ENABLED": False},
                start_url=url,
            )
            runs.append({"items": result.items, "seconds": time.monotonic() - started})
        return runs

    print(json.dumps(asyncio.run(main())))
    """
)


def test_run_spider_resolves_hostnames_and_process_exits(tmp_path: Path) -> None:
    script = tmp_path / "crawl_localhost.py"
    script.write_text(CHILD)
    # timeout is the exit-hang detector: the crawls themselves finish in ~1 s.
    proc = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.returncode == 0, proc.stderr
    runs = json.loads(proc.stdout.strip().splitlines()[-1])
    assert [run["items"] for run in runs] == [[{"status": 200}], [{"status": 200}]]
    # Below DOWNLOAD_TIMEOUT (3 s): a crawl that only succeeded after a timeout
    # and retry would still be the defect.
    assert all(run["seconds"] < 3 for run in runs), runs
