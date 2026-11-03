"""Bounded public document retrieval without private-network redirects."""
import ipaddress
import socket
import urllib.parse
import urllib.request


def public_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Expected a public http(s) URL without embedded credentials")
    for entry in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)):
        if not ipaddress.ip_address(entry[4][0]).is_global:
            raise ValueError("Private-network document URLs are not allowed")
    return url


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, public_url(newurl))


def fetch_public_text(url, timeout=30):
    req = urllib.request.Request(public_url(url), headers={"User-Agent": "ResearchWorkflow/1.0"})
    with urllib.request.build_opener(PublicRedirects).open(req, timeout=timeout) as response:
        return response.read(256000).decode("utf-8", errors="replace")[:24000]
