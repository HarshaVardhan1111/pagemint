"""JavaScript rendering with Playwright (Chromium). Optional: ``pip install "pagemint[js]"``.

Every request the page makes passes through a route handler that applies the
same SSRF check as plain fetches. Chromium does not route the requests it
makes when following a redirect, so PageMint never lets the browser follow
one: the handler fetches with redirects off and follows them itself, checking
every hop. For the main page a redirect stops the load and PageMint navigates
to the (checked) target, so the page keeps its real URL. Main-frame
navigations also respect robots.txt and the per-host rate limit. Images, media
and fonts are not loaded (they add no text).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit
from urllib.request import getproxies

from pagemint.errors import BrowserUnavailableError, FetchError, FetchTimeoutError, PageMintError, TooLargeError
from pagemint.fetcher import FetchResult

if TYPE_CHECKING:
    from pagemint.config import Settings
    from pagemint.fetcher import Fetcher

log = logging.getLogger("pagemint.browser")

_SKIP_TYPES = {"image", "media", "font"}
_REDIRECTS = {301, 302, 303, 307, 308}
NETWORK_IDLE_WAIT_MS = 2000


class BrowserRenderer:
    def __init__(self, settings: "Settings", fetcher: "Fetcher"):
        self.settings = settings
        self.fetcher = fetcher
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()

    @staticmethod
    def install_hint() -> str:
        return (
            'JavaScript rendering needs the optional browser install: run pip install "pagemint[js]" '
            "and then playwright install chromium."
        )

    def _proxy(self) -> dict | None:
        proxies = {k.lower(): v for k, v in getproxies().items()}
        server = proxies.get("https") or proxies.get("http") or proxies.get("all")
        if not server:
            return None
        proxy = {"server": server}
        bypass = proxies.get("no") or os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
        if bypass:
            proxy["bypass"] = bypass
        return proxy

    async def start(self) -> None:
        """Launch Chromium (once). Raises BrowserUnavailableError if it cannot."""
        async with self._lock:
            if self._browser is not None:
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError as e:
                raise BrowserUnavailableError(self.install_hint()) from e
            try:
                self._pw = await async_playwright().start()
                # No WebRTC traffic outside the proxy (it could reach local addresses).
                kwargs: dict = {"headless": True, "args": ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]}
                if self.settings.browser_executable:
                    kwargs["executable_path"] = self.settings.browser_executable
                proxy = self._proxy()
                if proxy:
                    kwargs["proxy"] = proxy
                self._browser = await self._pw.chromium.launch(**kwargs)
            except Exception as e:
                await self._stop_pw()
                raise BrowserUnavailableError(f"{self.install_hint()} (Chromium could not start: {str(e).splitlines()[0]})") from e

    async def _stop_pw(self) -> None:
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

    async def aclose(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        await self._stop_pw()

    async def render(self, url: str) -> FetchResult:
        """Load ``url`` in Chromium and return the rendered HTML."""
        await self.start()
        s = self.settings
        context = await self._browser.new_context(
            user_agent=s.user_agent, accept_downloads=False, service_workers="block", java_script_enabled=True
        )
        blocked: list[str] = []
        state: dict = {"redirect": None}
        page = await context.new_page()
        timeout_ms = s.read_timeout * 1000

        async def check(req_url: str, navigation: bool) -> None:
            await self.fetcher.check_host(req_url)
            if navigation:
                await self.fetcher.check_allowed(req_url)
                await self.fetcher.rate.wait(urlsplit(req_url).hostname or "")

        async def handle(route, request):
            req_url = request.url
            try:
                if urlsplit(req_url).scheme not in ("http", "https"):
                    await route.continue_()
                    return
                if request.resource_type in _SKIP_TYPES:
                    await route.abort("blockedbyclient")
                    return
                main_nav = request.is_navigation_request() and request.frame == page.main_frame
                await check(req_url, main_nav)
                current = req_url
                for _ in range(s.max_redirects + 1):
                    resp = await route.fetch(url=current, max_redirects=0, timeout=timeout_ms)
                    location = resp.headers.get("location")
                    if resp.status not in _REDIRECTS or not location:
                        break
                    nxt = urljoin(current, location)
                    if main_nav:
                        # Navigate to the target ourselves so the page URL is right.
                        state["redirect"] = nxt
                        await route.fulfill(status=200, content_type="text/html", body="")
                        return
                    if urlsplit(nxt).scheme not in ("http", "https"):
                        raise FetchError(f"redirect to unsupported URL {nxt}")
                    await check(nxt, False)
                    current = nxt
                else:
                    raise FetchError(f"too many redirects from {req_url}")
                body = await resp.body()
                if len(body) > s.max_bytes:
                    raise TooLargeError(f"{req_url} is larger than the {s.max_bytes} byte limit.")
                await route.fulfill(response=resp, body=body)
            except PageMintError as e:
                blocked.append(f"{req_url}: {e.message}")
                await _safe_abort(route)
            except Exception as e:  # network errors etc.
                log.debug("browser request failed %s: %s", req_url, e)
                await _safe_abort(route)

        async def handle_ws(ws_route):
            # page.route does not see WebSockets; check them here.
            try:
                await self.fetcher.check_host(ws_route.url.replace("ws://", "http://", 1).replace("wss://", "https://", 1))
            except Exception as e:
                blocked.append(f"{ws_route.url}: {getattr(e, 'message', e)}")
                try:
                    await ws_route.close()
                except Exception:
                    pass
                return
            ws_route.connect_to_server()

        await context.route_web_socket("**/*", handle_ws)
        await page.route("**/*", handle)
        try:
            target = url
            for hop in range(s.max_redirects + 1):
                state["redirect"] = None
                blocked.clear()
                try:
                    response = await page.goto(target, wait_until="load", timeout=s.page_timeout * 1000)
                except Exception as e:
                    if blocked:
                        raise FetchError(f"The browser could not load {target}: {blocked[0]}") from e
                    if "Timeout" in type(e).__name__:
                        raise FetchTimeoutError(f"Timed out rendering {target} in the browser.") from e
                    raise FetchError(f"The browser could not load {target}: {str(e).splitlines()[0]}") from e
                if state["redirect"]:
                    target = state["redirect"]
                    continue
                break
            else:
                raise FetchError(f"Too many redirects (more than {s.max_redirects}) starting at {url}.")
            try:
                await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_WAIT_MS)
            except Exception:
                pass  # pages that keep polling never go idle; use what has rendered
            html = await page.content()
            status = response.status if response is not None else 200
            final_url = page.url
        finally:
            await context.close()
        if blocked:
            log.info("browser blocked %d request(s) for %s", len(blocked), url)
        return FetchResult(
            url=final_url, status_code=status, headers={}, content=html.encode("utf-8"),
            content_type="text/html", text=html,
        )


async def _safe_abort(route) -> None:
    try:
        await route.abort("blockedbyclient")
    except Exception:
        pass
