# !/usr/bin/env python3

import os
import re
import json
import sys
import argparse
import requests
import urllib3
import jsbeautifier
from pathlib import Path
from collections import defaultdict
from urllib.parse import urljoin, urlparse

BANNER = r"""
     ____._____      __________                       
    |    |/ ____\____\______   \ ____   ____   ____  
    |    \   __\/ __ \|       _// __ \_/ ___\ /  _ \ 
/\__|    ||  | \  ___/|    |   \  ___/\  \___(  <_> )
\________||__|  \___  >____|_  /\___  >\___  >\____/ 
                     \/       \/     \/     \/       

    JS Recon - Secret & Endpoint Discovery
"""
#Last Working one
#1. To add more regex based on known extentions - Done
#2. to add Output directory as the name of the domain - Done
#3. to allow proxy options - Done
#4. to  add exclude libraries to users - Done
#5. To make the scripts under the scripts directory - Done
#6. to separate the findings based txt files - Done
#7 to allows _(underscore) in the output directory - Done
#10. add user-agent to default one - Done
#8. to fix that the API endpoints cant end with .css/.png/, text/html, etc. {List exist internally} Done
#9. adjust regex based on sensitive discover - Done
#10. solution for redirections + javascript content that do not use the <src tag> (run againts lists given) Done
#11. add Request.log that saves the Request in Request.txt -Done
#12. add that the url that been scan will be present (and overwrite the old one) - Done
#13 print url based on the request status - done
#14 print errors into a file + Error handeling- Done
#15 exception where users end the program to perform the JS analysis. - Done
#16 Redirect target.url with windows.locations

#------------------To add/adjust:----------------------
#17 add more regex regarding sensative keywords to PATTERN DB
#18 Find more options of redirection instead on only windows.location - Done


RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
BOLD = "\033[1m"
RESET = "\033[0m"
ORANGE = "\033[33m"

ctrl_c_count = 0

# ===================== Argument Input validation =====================

# Check valid Scheme and domain
def valid_url(value):
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise argparse.ArgumentTypeError(
            f"{BOLD}{RED}URL must start with http:// or https://"
        )
    if not parsed.netloc:
        raise argparse.ArgumentTypeError(f"{BOLD}{RED}Invalid URL")
    return value


# Prevents Infinite waits
def valid_timeout(value):
    value = int(value)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"{BOLD}{RED}Timeout must be > 0")
    if value > 150:
        raise argparse.ArgumentTypeError(f"{BOLD}{RED}Timeout too large (max 150s)")
    return value


# Regex for Output directory safety
SAFE_DIR_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def valid_out_dir(value):
    value = os.path.normpath(value)
    # Prevent directory traversal
    if ".." in value or value.startswith(("/", "\\")):
        raise argparse.ArgumentTypeError(
            f"{BOLD}{RED}Output directory cannot escape current working directory"
        )

    # Only letters and numbers allowed
    if not SAFE_DIR_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"{BOLD}{RED}Output directory can only contain letters and numbers (a-z, A-Z, 0-9)"
        )
    return value


def valid_proxy(value):
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https", "socks5"):
        raise argparse.ArgumentTypeError(
            f"{BOLD}{RED}Proxy must start with http://, https://, or socks5://"
        )
    if not parsed.netloc:
        raise argparse.ArgumentTypeError(f"{BOLD}{RED}Invalid proxy URL")
    return value


# loop over the exclude lib that the user supply
LIB_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")


def valid_exclude(values):
    cleaned = ["jquery", "bootstrap", "angular","npm"]
    for v in values:
        for item in v.split(","):  # allow commas or spaces
            item = item.strip()
            if not LIB_RE.match(item):
                raise argparse.ArgumentTypeError(f"{BOLD}{RED}Invalid library name: {item}")
            cleaned.append(item)
    return list(set(cleaned))


parser = argparse.ArgumentParser(description="JavaScript Recon Tool",epilog=f"{YELLOW}Example usage:\n python3 jsrecon.py https://maglan.com --proxy http://127.0.0.1:8080 --out maglan --exclude node npm \n")
parser.add_argument("url", type=valid_url, help="Target base URL, e.g. https://example.com")
parser.add_argument("--out", type=valid_out_dir, help="Output Directory (default: URL domain)")
parser.add_argument("--timeout", type=valid_timeout, default=10, help="HTTP timeout")
parser.add_argument("--proxy", type=valid_proxy, help="Proxy URL to route requests through")
parser.add_argument("--exclude", nargs="*", default=[], help="Libraries to exclude (space-separated)")
args = parser.parse_args()

TARGET_URL = args.url
TIMEOUT = args.timeout
parsed = urlparse(TARGET_URL)
domain = parsed.netloc.replace(":", "_")
OUTPUT_DIR = args.out if args.out else domain
EXCLUDE_LIBS = valid_exclude(args.exclude)

PROXIES = (
    {
        "http": args.proxy,
        "https": args.proxy,
    }

    if args.proxy
    else None
)

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
}
VERIFY_SSL = not bool(args.proxy)
# Suppresses annoying SSL warnings Only happens when proxy is enabled
if args.proxy:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# check if the target website reachable at all
def check_reachable(url):
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT, proxies=PROXIES, verify=VERIFY_SSL)
        return r.status_code < 500
    except Exception:
        return False


USERNAME_KEYS = ["username", "user", "userid", "login", "email"]
PASSWORD_KEYS = ["password", "passwd", "pwd", "passphrase"]

AUTH_FUNCTION_NAMES = [
    "login", "signin", "signIn", "authenticate",
    "auth", "doLogin", "handleLogin", "submitLogin"]

visited = set()

# ===================== REGEX =====================
# All regex patterns are the “detectors” catch:External JS files, Inline scripts, Hardcoded credentials, Auth calls, Storage secrets
SCRIPT_SRC_REGEX = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']')
INLINE_SCRIPT_REGEX = re.compile(r'<script[^>]*>(.*?)</script>', re.S | re.I)
ELEMENT_SCRIPT_REGEX = re.compile(r'["\']([^"\']+\.js)["\']', re.S | re.I)
#JS_REDIRECT_REGEX = re.compile(r'window\.location(?:\.href)?\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s;]+))', re.I)
JS_REDIRECT_REGEX = re.compile(
    r'''
    (?:window|document)?\.?location(?:\.href)?   # matches window.location, document.location, location, optionally .href
    \s*                                           # optional whitespace
    (?:=|\.(?:assign|replace)\s*\()              # matches =  OR .assign( OR .replace(
    \s*                                           # optional whitespace
    (?:
        "(?P<dq>[^"]+)"                          # double-quoted URL
      | '(?P<sq>[^']+)'                          # single-quoted URL
      | (?P<bare>[^)\s;]+)                       # bare expression / variable
    )
    ''',
    re.IGNORECASE | re.VERBOSE
)

JS_REF_REGEX = re.compile(r'(?:import|require|fetch|axios|System\.import)\s*\(?["\']([^"\']+\.js)["\']')
KEYWORD_ASSIGN_REGEX = re.compile(
    r'(?i)\b(' + '|'.join(USERNAME_KEYS + PASSWORD_KEYS) + r')\b'
                                                           r'\s*[:=]\s*["\'`]([^"\'`\n\r]{6,})["\'`]\s*[;,]?\s*$')
AUTH_CALL_REGEX = re.compile(
    r'(?i)\b(' + '|'.join(AUTH_FUNCTION_NAMES) + r')\b'
                                                 r'\s*\(\s*["\'`]([^"\'`]{3,})["\'`]\s*,\s*["\'`]([^"\'`]{3,})["\'`]\s*\)')
STORAGE_REGEX = re.compile(
    r'(?i)(localStorage|sessionStorage)\.setItem'
    r'\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']{6,})["\']')

# ===================== STRONG SECRET PATTERN REGISTRY =====================
PATTERN_DB = [
    # ===== JWT / Tokens =====
    {
        "name": "JWT Token",
        "category": "Token",
        "regex": r'eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+',
        "description": "JSON Web Token (JWT)"
    },
    {
        "name": "Internal IP Address",
        "category": "Internal Infrastructure",
        "regex": r'\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.(?:\d{1,3}\.)\d{1,3}|192\.168\.(?:\d{1,3}\.)\d{1,3})\b',
        "description": "Private/internal IPv4 address (RFC1918 range)"
    }
    ,
    {
        "name": "Email Address",
        "category": "Email Address",
        "regex": r'\b[a-zA-Z0-9._%+-]{2,}@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b',
        "description": "Email address (possible PII or credential identifier)"
    }
    ,
    # ===== AWS =====
    {
        "name": "AWS Access Key",
        "category": "Cloud Credential",
        "regex": r'AKIA[0-9A-Z]{16}',
        "description": "AWS Access Key ID"
    },
    {
        "name": "AWS Secret Key",
        "category": "Cloud Credential",
        "regex": r'(?i)aws(.{0,20})?(secret|private).{0,20}["\']([A-Za-z0-9/+=]{40})["\']',
        "description": "AWS Secret Access Key"
    },

    # ===== Google =====
    {
        "name": "Google API Key",
        "category": "Cloud Credential",
        "regex": r'AIza[0-9A-Za-z\-_]{35}',
        "description": "Google API Key"
    },

    # ===== GitHub / GitLab =====
    {
        "name": "GitHub Token",
        "category": "VCS Token",
        "regex": r'ghp_[A-Za-z0-9]{36}',
        "description": "GitHub Personal Access Token"
    },
    {
        "name": "GitLab Token",
        "category": "VCS Token",
        "regex": r'glpat-[A-Za-z0-9\-]{20,}',
        "description": "GitLab Personal Access Token"
    },

    # ===== Slack =====
    {
        "name": "Slack Token",
        "category": "SaaS Token",
        "regex": r'xox[baprs]-[A-Za-z0-9-]{10,}',
        "description": "Slack API Token"
    },

    # ===== Stripe =====
    {
        "name": "Stripe API Key",
        "category": "Payment Token",
        "regex": r'sk_live_[0-9a-zA-Z]{24}',
        "description": "Stripe Live Secret Key"
    },

    # ===== PEM KEYS =====
    {
        "name": "Private Key",
        "category": "Crypto Material",
        "regex": r'-----BEGIN (?:RSA|EC|DSA)? ?PRIVATE KEY-----.*?-----END (?:RSA|EC|DSA)? ?PRIVATE KEY-----',
        "description": "PEM formatted private key",
        "flags": re.DOTALL
    },
    {
        "name": "Public Key",
        "category": "Crypto Material",
        "regex": r'-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----',
        "description": "PEM formatted public key",
        "flags": re.DOTALL
    },
    {
        "name": "Full URL",
        "category": "Full URL",
        "regex": r'["\'](https?:\/\/[^"\']+)["\']',
        "description": "Full URL"
    },
    {
        "name": "WebSocket Endpoint",
        "category": "WebSocket Endpoint",
        "regex": r'["\'](wss?:\/\/[^"\']+)["\']',
        "description": "WebSocket Endpoint"
    },
    {
        "name": "REST API Endpoint",
        "category": "API Endpoint",
        "regex": r'["\'](\/(?:api|v1|v2|v3|rest|internal|admin)\/[a-zA-Z0-9_\-/\.]{2,})["\']',
        "description": "REST API Endpoint"
    },
    {
        "name": "Dynamic Endpoint",
        "category": "API Endpoint",
        "regex": r'["\'](\/[a-zA-Z0-9_\-/\.]+\.(?:php|asp|aspx|jsp|do|action))["\']',
        "description": "Dynamic Endpoint"

    },
    {
        "name": "Query Endpoint",
        "category": "API Endpoint",
        "regex": r'["\'](\/[a-zA-Z0-9_\-/\.]+\?[a-zA-Z0-9_\-&=]{3,})["\']',
        "description": "Query Endpoints"

    }
]

# ===================== ENDPOINT REGEX =====================
# This section is about finding URLs, API paths, and WebSockets in JS/HTML.

PATH_REGEX = re.compile(
    r'["\']('
    r'(?:/[a-zA-Z0-9_\-/\.]{3,})'
    r'|'
    r'(?:[a-zA-Z]+/[a-zA-Z0-9_\-/\.]{3,})'
    r')["\']')

# Improved dynamic concat regex
CONCAT_ENDPOINT_REGEX = re.compile(
    r'\.concat\(\s*(?:[a-zA-Z0-9_]+|["\'][^"\']*["\'])\s*,\s*["\']([a-zA-Z0-9_\-/\.]{3,})["\']')

BLOCKED_ENDPOINT_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
    ".html", ".htm", ".css", ".pdf", ".zip", ".gz", ".tar", ".7z")

BLOCKED_ENDPOINT_VALUES = {
    "application/json",
    "application/xml",
    "application/x-www-form-urlencoded",
    "application/x-www-form-urlencoded;charset=utf-8",
    "multipart/form-data",
    "multipart/mixed",
    "multipart/related",
    "multipart/alternative",
    "multipart/byteranges",
    "text/plain",
    "text/html",
    "text/xml",
    "text/csv",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/ecmascript",
    "application/graphql",
    "application/ld+json",
    "application/problem+json",
    "application/problem+xml",
    "application/octet-stream",
    "application/pdf",
    "application/zip",
    "application/gzip",
    "application/x-zip-compressed",
    "application/x-tar",
    "application/x-7z-compressed",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/x-protobuf",
    "application/x-msgpack",
    "application/cbor",
    "application/yaml",
    "application/x-yaml",
    "application/soap+xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/x-ndjson",
    "application/jwt",
    "application/x-pkcs12",
    "application/x-pkcs7-mime",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "video/mp4",
    "video/webm",
    "video/ogg",
    "HTTP/1.1",
    "MM/dd/yyyy",
    "image/bmp",
    "image/heif",
    "image/tiff",
    "text/json",
    "text/htm",
    "image/jpg",
    "image/tif",
    "application/vnd.ms-outlook",
    "m/d/yy",
    "application/x-mso",
    "application/vnd.ms-officetheme",
    "application/vnd.ms-excel.sheet.binary.macroEnabled.main",
    "application/vnd.ms-excel.worksheet",
    "application/vnd.ms-excel.binIndexWs",
    "application/vnd.ms-excel.chartsheet",
    "application/vnd.ms-excel.macrosheet",
    "application/vnd.ms-excel.intlmacrosheet",
    "application/vnd.ms-excel.binIndexMs",
    "application/vnd.ms-excel.dialogsheet",
    "application/vnd.ms-excel.sharedStrings",
    "application/vnd.ms-excel.styles",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.customProperty",
    "application/vnd.ms-excel.comments",
    "application/vnd.ms-excel.sheetMetadata",
    "application/vnd.ms-excel.pivotTable",
    "application/vnd.ms-excel.calcChain",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.printerSettings",
    "application/vnd.ms-office.activeX",
    "application/vnd.ms-excel.attachedToolbars",
    "application/vnd.ms-excel.connections",
    "application/vnd.ms-excel.externalLink",
    "application/vnd.ms-excel.pivotCacheDefinition",
    "application/vnd.ms-excel.pivotCacheRecords",
    "application/vnd.ms-excel.queryTable",
    "application/vnd.ms-excel.userNames",
    "application/vnd.ms-excel.revisionHeaders",
    "application/vnd.ms-excel.revisionLog",
    "application/vnd.ms-excel.tableSingleCells",
    "application/vnd.ms-excel.slicer",
    "application/vnd.ms-excel.slicerCache",
    "application/vnd.ms-excel.wsSortMap",
    "application/vnd.ms-excel.table",
    "application/vnd.ms-office.vbaProject",
    "application/vnd.ms-office.vbaProjectSignature",
    "application/vnd.ms-office.volatileDependencies",
    "application/vnd.openxmlformats-officedocument.vmlDrawing",
    "application/vnd.openxmlformats-officedocument.oleObject",
    "image/x-emf",
    "image/x-wmf",
    "application/vnd.oasis.opendocument.spreadsheet",
    "mm/dd/yy",
    "text/rtf",
    "text/jscript",
    "text/uri-list",
    "text/x-moz-url",
    "application/x-moz-file"
}


# ===================== HELPERS =====================
# Checks if a URL is on the same domain as the target
def same_origin(url):
    return urlparse(url).netloc == urlparse(TARGET_URL).netloc


# Checks if the URL contains a library name in the exclude list
def is_library(url):
    url_lower = url.lower()
    return any(lib in url_lower for lib in EXCLUDE_LIBS)


def fetch_url(url):
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT, proxies=PROXIES, verify=VERIFY_SSL)
        with open(os.path.join(OUTPUT_DIR, "Requests.txt"), "a", encoding="utf-8") as f:
            f.write(f"{url} | {r.status_code}\n")

        match r.status_code:
            case 200:
                sys.stdout.write(
                    f"{BOLD}{GREEN}\r\033[KURL scanned: {url}, Status code: {r.status_code}"
                )
                sys.stdout.flush()

            case status if 300 <= status < 400:
                sys.stdout.write(
                    f"{BOLD}{ORANGE}\r\033[KURL scanned: {url}, Redirect: {r.status_code}"
                )
                sys.stdout.flush()

            case status if 400 <= status < 600:
                sys.stdout.write(
                    f"{BOLD}{RED}\r\033[KURL scanned: {url}, Error: {r.status_code}"
                )
                sys.stdout.flush()

            case _:
                sys.stdout.write(
                    f"{BOLD}\r\033[KURL scanned: {url}, Status: {r.status_code}"
                )
                sys.stdout.flush()

        if r.status_code == 200:
            return r.text
    except requests.RequestException as e:
        print(f"{BOLD}{RED}[-] Failed to fetch {url}: {e}")
        with open(os.path.join(OUTPUT_DIR, "errors.txt"), "a", encoding="utf-8") as f:
            f.write(f"Found error in URL: {url} and the error is: {e}\n")
    return None


# Saves JS content to local file in OUTPUT_DIR
def save_file(url, content):
    name = os.path.basename(urlparse(url).path) or "inline.js"
    path = os.path.join(OUTPUT_DIR, "scripts", name)
    with open(path, "a", encoding="utf-8", errors="ignore") as f:
        f.write(content)


# ===================== DISCOVERY-traversing the site’s JS files and collecting them for scanning. =====================
# Looks for source maps (.js.map) for a JS file
def parse_source_map(js_url):
    content = fetch_url(js_url + ".map")
    if not content:
        return []
    try:
        return json.loads(content).get("sources", [])
    except Exception:
        return []


# run other functions for (Skip JS if needed, Fetch the JS content, Save it locally and more )
def discover_js(js_url):
    try:
        if js_url in visited or not same_origin(js_url) or is_library(js_url):
            return
        visited.add(js_url)

        content = fetch_url(js_url)
        if not content:
            return

        save_file(js_url, content)

        # Handle HTML pages that load js
        if "<script" in content.lower():
            for src in SCRIPT_SRC_REGEX.findall(content):
                discover_js(urljoin(js_url, src))


        for src in ELEMENT_SCRIPT_REGEX.findall(content):
            discover_js(urljoin(js_url, src))

        for src in parse_source_map(js_url):
            if src.endswith(".js"):
                discover_js(urljoin(js_url, src.replace("webpack:///", "")))
    except Exception as e:
        print(f"[-] Failed to scan the {js_url}: {e}")
        with open(os.path.join(OUTPUT_DIR, "errors.txt"), "a", encoding="utf-8") as f:
            f.write(f"Found error in URL: {js_url} and the error is: {e}\n")
        return

# Fetch main page HTML
def discover_from_html(TARGET_URL):

    html = fetch_url(TARGET_URL)
    if not html:
        print("[-] Failed to download main HTML.")
        sys.exit(1)

    redirects = JS_REDIRECT_REGEX.findall(html)
    for src in redirects:
        url = next((s for s in src if s), None)
        if url:
            discover_from_html(urljoin(TARGET_URL, url))
            return

    for src in SCRIPT_SRC_REGEX.findall(html):
        discover_js(urljoin(TARGET_URL, src))


    for src in ELEMENT_SCRIPT_REGEX.findall(html):
        discover_js(urljoin(TARGET_URL, src))

    inline_path = os.path.join(OUTPUT_DIR, "scripts", "inline_scripts.js")
    with open(inline_path, "a", encoding="utf-8", errors="ignore") as f:
        for script in INLINE_SCRIPT_REGEX.findall(html):
            f.write("\n// INLINE SCRIPT\n")
            f.write(script)


# ===================== SCANNER on every downloaded JS file =====================
def scan_files():
    findings = []
    dedup = set()
    category_counter = defaultdict(int)

    # Helper to reduce repetitive code
    def add_finding(file_path, line, category, value, name="", description=""):
        key = (category, value)
        if key not in dedup:
            dedup.add(key)
            findings.append({
                "file": str(file_path),
                "line": line,
                "category": category,
                "name": name,
                "description": description,
                "value": value
            })
            category_counter[category] += 1

    for js_file in Path(OUTPUT_DIR).rglob("*.js"):
        try:
            raw = js_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        # ===== Strong secret patterns (registry-driven) =====
        # Beautify content for line-by-line scan
        try:
            content = jsbeautifier.beautify(raw)
        except Exception:
            content = raw

        for pat in PATTERN_DB:
            flags = pat.get("flags", 0)
            for m in re.finditer(pat["regex"], content, flags):
                val = m.group(0).strip()
                # Reduce PEM false positives
                if pat["name"] in ["Private Key", "Public Key"] and len(val) < 200:
                    continue
                line_no = content.count("\n", 0, m.start()) + 1
                add_finding(js_file, line_no, pat["category"], val, pat["name"], pat["description"])

        lines = content.splitlines()
        per_file_creds = []

        for lineno, line in enumerate(lines, 1):
            low = line.lower()

            # Skip CSS-only lines
            if re.search(r'\b(font-size|background|border|color|margin|padding)\s*:', low):
                continue

            # ===== Hardcoded literal credentials =====
            kv = KEYWORD_ASSIGN_REGEX.search(line)
            if kv and not any(
                    x in line for x in ["concat(", "JSON.parse", "sessionStorage", "localStorage", "getItem("]):
                keyname, val = kv.groups()
                add_finding(js_file, lineno, "Hardcoded Sensitive Value", f"{keyname}={val}")
                per_file_creds.append((lineno, keyname.lower(), val))

            # ===== Auth function calls =====
            m = AUTH_CALL_REGEX.search(line)
            if m:
                user, pwd = m.group(2), m.group(3)
                add_finding(js_file, lineno, "Hardcoded Auth Call", f"{user}:{pwd}")

            # ===== Web Storage secrets =====
            m = STORAGE_REGEX.search(line)
            if m:
                storage, k, v = m.groups()
                if not (v.startswith("rgb(") or v.isdigit()):
                    add_finding(js_file, lineno, "Web Storage Secret", f"{k}={v}")

            # ===== API / Endpoint scanning =====
            endpoint_patterns = [
                PATH_REGEX,
                CONCAT_ENDPOINT_REGEX
            ]

            for regex in endpoint_patterns:
                for match in regex.findall(line):
                    endpoint = match.strip() if isinstance(match, str) else match[0].strip()

                    # Normalize endpoint for checks
                    endpoint_clean = endpoint.strip().strip('\'"').lower()

                    # Skip blocked content-type / non-endpoint values
                    if endpoint_clean in BLOCKED_ENDPOINT_VALUES:
                        continue

                    # Block endpoints ending with static / binary file extensions
                    if endpoint_clean.endswith(BLOCKED_ENDPOINT_SUFFIXES):
                        continue

                    # For CONCAT regex, ensure it starts with /
                    if regex == CONCAT_ENDPOINT_REGEX and not endpoint.startswith("/") and len(endpoint) <= 3:
                        continue

                    # Skip framework internals
                    if any(endpoint.startswith(p) for p in ["/-", "/_", "."]):
                        continue

                    segments = [seg for seg in endpoint.split("/") if seg]
                    if any(seg.startswith(".") or seg.startswith("-") for seg in segments):
                        continue
                    if len(segments) == 1 and len(segments[0]) <= 3:
                        continue

                    add_finding(js_file, lineno, "API Endpoint", endpoint)

            # ===== Correlate username/password pairs =====
            users = [(l, k, v) for l, k, v in per_file_creds if k in USERNAME_KEYS]
            passes = [(l, k, v) for l, k, v in per_file_creds if k in PASSWORD_KEYS]

            for u in users:
                for p in passes:
                    if abs(u[0] - p[0]) <= 20:
                        add_finding(js_file, f"{u[0]}&{p[0]}", "Credential Pair", f"{u[2]}:{p[2]}")

    return findings, category_counter


CATEGORY_FILES = {
    # ===== Endpoints / URLs =====
    "API Endpoint": "endpoints.txt",
    "WebSocket Endpoint": "websockets.txt",
    "Full URL": "urls.txt",
    # ===== Secrets / Credentials =====
    "Cloud Credential": "cloud_keys.txt",
    "Token": "tokens.txt",
    "VCS Token": "vcs_tokens.txt",
    "SaaS Token": "saas_tokens.txt",
    "Payment Token": "payment_tokens.txt",
    "Crypto Material": "crypto_keys.txt",
    "Web Storage Secret": "secrets.txt",
    "Hardcoded Sensitive Value": "secrets.txt",
    "Hardcoded Auth Call": "credentials.txt",
    "Credential Pair": "credentials.txt",
    # ===== Email Address / Infra =====
    "Email Address": "email Address.txt",
    "Internal Infrastructure": "internal_infra.txt",
}


# ===================== MAIN =====================

def main():
    print(BANNER)
    print(f"[+] Target: {TARGET_URL}")
    print("[+] Checking target reachability...")
    if not check_reachable(TARGET_URL):
        print(f"{BOLD}{RED}[-] Target is NOT reachable. Exiting.")
        sys.exit(1)
    print(f"{BOLD}{GREEN}[+] Target reachable. Starting recon.")
    print(f"{BOLD}{YELLOW}[+] Excluded Libraries:", EXCLUDE_LIBS)
    print(f"{BOLD}{RED}[+] Stop jsrecon and generate report by Clicking CTRL C once")
    reports_dir = os.path.join(OUTPUT_DIR, "reports")
    scripts_dir = os.path.join(OUTPUT_DIR, "scripts")
    os.makedirs(reports_dir, exist_ok=True)
    os.makedirs(scripts_dir, exist_ok=True)
    json_report_path = os.path.join(reports_dir, "jsrecon_full_report.json")
    try:
        discover_from_html(TARGET_URL)
    except KeyboardInterrupt:
        print(f"\n{RED}[!] Scan interrupted by user (Ctrl+C). Proceeding to reporting...\n")
    finally:
        findings, summary = scan_files()

        # Write seperated text files reports
        grouped = defaultdict(list)
        for x in findings:
            grouped[x["category"]].append(x)

        for category, items in grouped.items():
            filename = CATEGORY_FILES.get(category, "misc.txt")
            path = os.path.join(reports_dir, filename)

            with open(path, "w", encoding="utf-8") as f:
                for x in items:
                    f.write(
                        f"{x['file']} | Line {x['line']} | "
                        f"{x.get('name', '')} | {x['value']}\n"
                    )
        # Write Full JSON report
        with open(json_report_path, "w", encoding="utf-8") as f:
            json.dump(findings, f, indent=2)

        # Print summary
        print(f"{BOLD}{BLUE}\n========= Finding Summary =========")
        total = 0
        for cat, count in summary.items():
            print(f"{cat:30} : {count}")
            total += count
        print("----------------------------------")
        print(f"Total findings: {total}")

        print("\n[✓] JS saved in:", OUTPUT_DIR)
        print(f"[✓] Full Json report saved in: {json_report_path}")
        print(f"[✓] Text reports saved in : {OUTPUT_DIR}/")
        print(f"[✓] Requests logs saved in : {OUTPUT_DIR}/Requests.txt")
        sys.exit(0)

if __name__ == "__main__":
    main()