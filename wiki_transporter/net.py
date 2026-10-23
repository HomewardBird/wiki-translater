# -*- coding: utf-8 -*-
"""出站 HTTP 的唯一入口：带 SSRF 防护的安全抓取。

审计告警（sast_ssrf_fetch_user_url）针对 `urllib.request.urlopen(用户可控 URL)`。
所有出站请求现在都必须经过这里，规则：

1. 只允许 http / https，拒绝 file:、ftp:、gopher: 等；
2. 拒绝 URL 里的用户名/密码（`http://a:b@host/`）；
3. 域名必须解析成功，且解析出的每一个地址都必须是公网地址
   （私网 10/172.16/192.168/100.64、回环、链路本地 169.254、保留、组播、IPv6 ULA 一律拒绝）；
4. 若来源配置声明了 allowed_hosts，则域名必须命中白名单；
5. 重定向每一跳都重新做 3/4 校验（最多 5 跳）；
6. 连接固定到已校验的那个 IP，避免 DNS 重绑定把校验和连接拆成两次解析；
7. 响应体积设上限，防止被超大文件拖死。

用法：
    from .net import safe_get
    r = safe_get(url, allowed_hosts=["prts.wiki"], timeout=30)
    r.text / r.json / r.content / r.content_type / r.url
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "Wiki-Translater/2.0 (personal reading archive; local tool) python-urllib"
)

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 5
ALLOWED_SCHEMES = ("http", "https")


class UnsafeURLError(RuntimeError):
    """URL 未通过安全校验。"""


class NetError(RuntimeError):
    """网络请求失败（超时、HTTP 错误、内容异常）。"""


class Response:
    __slots__ = ("url", "status", "headers", "content", "content_type")

    def __init__(self, url: str, status: int, headers: dict, content: bytes):
        self.url = url
        self.status = status
        self.headers = headers
        self.content = content
        self.content_type = (headers.get("content-type") or "").split(";")[0].strip().lower()

    @property
    def text(self) -> str:
        for enc in ("utf-8", "gb18030"):
            try:
                return self.content.decode(enc)
            except UnicodeDecodeError:
                continue
        return self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)

    def __repr__(self) -> str:  # pragma: no cover
        return "<Response %s %s %d bytes>" % (self.status, self.url, len(self.content))


def _norm_host(host: str) -> str:
    return (host or "").strip().rstrip(".").lower()


def host_allowed(host: str, patterns) -> bool:
    """白名单匹配：精确域名，或 `*.example.com` 形式的子域通配。"""
    if not patterns:
        return True
    host = _norm_host(host)
    for raw in patterns:
        pat = _norm_host(raw)
        if not pat:
            continue
        if pat.startswith("*."):
            if host.endswith(pat[1:]) and host != pat[2:]:
                return True
        elif host == pat:
            return True
    return False


def _is_public_ip(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified
            or getattr(ip, "is_site_local", False)):
        return False
    return ip.is_global


def resolve_public(host: str, port: int) -> list[tuple]:
    """解析域名并校验所有地址都是公网；返回可连接的 sockaddr 列表。"""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise NetError("域名解析失败：%s（%s）" % (host, e)) from e
    if not infos:
        raise NetError("域名解析无结果：%s" % host)
    out, bad = [], []
    for info in infos:
        family, sockaddr = info[0], info[4]
        ip = sockaddr[0]
        if _is_public_ip(ip):
            out.append((family, sockaddr))
        else:
            bad.append(ip)
    if bad:
        raise UnsafeURLError(
            "拒绝访问非公网地址：%s → %s"
            % (host, ", ".join(dict.fromkeys(str(b) for b in bad)))
        )
    return out


class _PinnedMixin:
    """把 TCP 连接固定到已校验的 IP，TLS 仍按域名做 SNI/证书校验。

    http.client 在 __init__ 里把 _create_connection 设成 socket.create_connection
    的实例属性（会遮蔽同名方法），所以这里必须在 __init__ 之后再替换。
    """

    _pinned: tuple = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = self._pinned_connect

    def _pinned_connect(self, address, timeout=None, source_address=None):
        timeout = self.timeout if timeout is None else timeout
        last: OSError | None = None
        for family, sockaddr in self._pinned:
            sock = None
            try:
                sock = socket.socket(family, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                if source_address:
                    sock.bind(source_address)
                sock.connect(sockaddr)
                return sock
            except OSError as e:  # 某个地址不通就换下一个
                if sock is not None:
                    sock.close()
                last = e
        raise last or OSError("无可用地址")


class _HTTPConn(_PinnedMixin, http.client.HTTPConnection):
    default_port = 80


class _HTTPSConn(_PinnedMixin, http.client.HTTPSConnection):
    default_port = 443


class _GuardedHandler(urllib.request.HTTPHandler):
    def __init__(self, allowed_hosts=None, timeout=DEFAULT_TIMEOUT):
        super().__init__()
        self.allowed_hosts = allowed_hosts
        self.default_timeout = timeout

    def _open(self, req, https: bool):
        parts = urllib.parse.urlsplit(req.full_url)
        host = _norm_host(parts.hostname or "")
        if not host:
            raise UnsafeURLError("URL 缺少主机名：%s" % req.full_url)
        if not host_allowed(host, self.allowed_hosts):
            raise UnsafeURLError(
                "该来源只允许访问 %s，拒绝 %s"
                % ("、".join(self.allowed_hosts), host)
            )
        port = parts.port or (443 if https else 80)
        pinned = resolve_public(host, port)
        base = _HTTPSConn if https else _HTTPConn

        class Conn(base):  # noqa: N801
            _pinned = tuple(pinned)

        # do_open 自己会把 req.timeout 传给连接类，这里不再重复传
        return self.do_open(Conn, req)

    def http_open(self, req):
        return self._open(req, https=False)


class _GuardedHTTPSHandler(_GuardedHandler, urllib.request.HTTPSHandler):
    def __init__(self, allowed_hosts=None, timeout=DEFAULT_TIMEOUT):
        urllib.request.HTTPSHandler.__init__(self)
        self.allowed_hosts = allowed_hosts
        self.default_timeout = timeout

    def https_open(self, req):
        return self._open(req, https=True)


class _RedirectGuard(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        # 跳转目标同样要过协议检查；域名/地址检查由 handler 在请求时做
        _validate_url_shape(new_req.full_url)
        return new_req


def _validate_url_shape(url: str) -> urllib.parse.SplitResult:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as e:
        raise UnsafeURLError("URL 无法解析：%s" % url) from e
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError("只允许 http/https，拒绝 %s" % (parts.scheme or "（空）"))
    if parts.username or parts.password:
        raise UnsafeURLError("URL 不允许携带用户名/密码")
    if not parts.hostname:
        raise UnsafeURLError("URL 缺少主机名：%s" % url)
    return parts


def _build_opener(allowed_hosts, timeout):
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler({}),  # 不走系统代理，避免绕过地址校验
        urllib.request.UnknownHandler(),
        _GuardedHandler(allowed_hosts, timeout),
        _GuardedHTTPSHandler(allowed_hosts, timeout),
        urllib.request.HTTPDefaultErrorHandler(),
        _RedirectGuard(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


RETRY_CODES = frozenset({429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 567})


def safe_get(url: str, *, allowed_hosts=None, timeout: float = DEFAULT_TIMEOUT,
             max_bytes: int = DEFAULT_MAX_BYTES, headers: dict | None = None,
             expect: tuple[str, ...] | None = None, attempts: int = 3) -> Response:
    """安全地 GET 一个 URL，返回 Response。任何不安全输入都抛 UnsafeURLError。

    对站点限流 / 风控（429、5xx、站点风控的 567 等）自动退避重试，避免偶发失败中断整本书。
    """
    _validate_url_shape(url)
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update({k: v for k, v in headers.items() if v})
    req = urllib.request.Request(url, headers=hdrs)
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        opener = _build_opener(allowed_hosts, timeout)
        try:
            with opener.open(req, timeout=timeout) as resp:
                content = _read_capped(resp, max_bytes)
                final = resp.geturl()
                _validate_url_shape(final)
                if not host_allowed(urllib.parse.urlsplit(final).hostname or "",
                                    allowed_hosts):
                    raise UnsafeURLError("重定向到了白名单之外的域名：%s" % final)
                return Response(final, getattr(resp, "status", 200),
                                {k.lower(): v for k, v in resp.headers.items()}, content)
        except UnsafeURLError:
            raise
        except urllib.error.HTTPError as e:
            if e.code in RETRY_CODES and attempt + 1 < attempts:
                last = e
                time.sleep(1.5 * (2 ** attempt))
                continue
            if e.code == 567:
                raise NetError("站点风控拦截了这次请求（HTTP 567），过几分钟再试：%s" % url) from e
            raise NetError("HTTP %s：%s" % (e.code, url)) from e
        except (urllib.error.URLError, OSError, ssl.SSLError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, UnsafeURLError):
                raise reason
            if attempt + 1 < attempts:
                last = e
                time.sleep(1.5 * (2 ** attempt))
                continue
            raise NetError("请求失败：%s（%s）" % (url, reason)) from e
    raise NetError("请求失败：%s（%s）" % (url, last))


def _read_capped(resp, max_bytes: int) -> bytes:
    chunks, total = [], 0
    while True:
        chunk = resp.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise NetError("响应超过 %d 字节上限，已中止" % max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def safe_get_json(url: str, **kw) -> dict:
    r = safe_get(url, **kw)
    try:
        return r.json()
    except ValueError as e:
        raise NetError("返回的不是合法 JSON：%s" % url) from e
