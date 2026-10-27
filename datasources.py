"""Live data for animation scripts: weather, stocks and crypto, YouTube
subscribers, news headlines, or any web address.

Everything is fetched in the background, so a script never waits: a call
returns the latest value it has (None until the first answer arrives) and
starts a refresh when the value is older than `every` seconds. Answers are
also saved to disk, so after a restart the wall shows the last known values
straight away, even before (or without) the internet coming back.
"""
import hashlib, json, os, re, socket, ssl, struct, sys, threading, time, urllib.parse, urllib.request
import html as _html


def _data_dir():
    """Settings, My scripts and the cache: the usual place on each system."""
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "DDP Streamer")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/DDP Streamer")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "ddp-streamer")


DATA_DIR = _data_dir()
CACHE_DIR = os.path.join(DATA_DIR, "cache")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
MIN_EVERY = 15                  # seconds: never ask the same address more often than this
TIMEOUT = 15

_lock = threading.Lock()
_entries = {}                   # key -> dict(value, time, error, busy, every)
_last = {}                      # helper name -> key of its last request (for data_error)


def _key(url, headers, kind):
    return url + "|" + kind + "|" + json.dumps(headers or {}, sort_keys=True)


def _cache_path(key):
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".json")


def _load_disk(key):
    try:
        with open(_cache_path(key), encoding="utf-8") as f:
            d = json.load(f)
        return d["value"], d["time"]
    except (OSError, ValueError, KeyError):
        return None, 0.0


def _save_disk(key, value):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = _cache_path(key) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"time": time.time(), "value": value}, f)
        os.replace(tmp, _cache_path(key))
    except (OSError, TypeError, ValueError):
        pass


def _ssl_context():
    """Mac: python.org's Python doesn't use the system's certificates, so HTTPS
    would fail; use certifi's. Elsewhere the system's (None = default)."""
    if sys.platform != "darwin":
        return None
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):
        return None


_SSL = _ssl_context()


def _get(url, headers=None, kind="json"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8",
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL) as r:
        raw = r.read()
        charset = r.headers.get_content_charset() or "utf-8"
    text = raw.decode(charset, "replace")
    return json.loads(text) if kind == "json" else text


def _worker(key, load, disk):
    try:
        value = load()
        with _lock:
            e = _entries[key]
            e.update(value=value, time=time.time(), error="", busy=False)
        if disk:
            _save_disk(key, value)
    except Exception as ex:                  # network down, bad address, not JSON...
        code = getattr(ex, "code", None)
        if isinstance(ex, NoAnswer):
            err = str(ex)
        elif code:
            err = f"HTTP {code}" + (" (not found)" if code == 404 else "")
        elif isinstance(ex, (TimeoutError, socket.timeout)):
            err = "No answer (timed out)"
        else:
            err = f"{type(ex).__name__}: {getattr(ex, 'reason', None) or ex}"
        with _lock:
            _entries[key].update(error=err, busy=False, tried=time.time())


def _poll(key, every, load, disk=True):
    """Latest value of a background job: runs load() in a thread when the
    value is older than `every` seconds (after an error, again within a minute)."""
    now = time.time()
    with _lock:
        e = _entries.get(key)
        if e is None:
            value, t = _load_disk(key) if disk else (None, 0.0)
            e = _entries[key] = dict(value=value, time=t, error="", busy=False, tried=0.0)
        retry = min(every, 60.0)
        due = now - e["time"] >= every and now - e["tried"] >= (retry if e["error"] else 0)
        if due and not e["busy"]:
            e["busy"] = True
            e["tried"] = now
            threading.Thread(target=_worker, args=(key, load, disk), daemon=True).start()
        return e["value"]


def fetch(url, every=300, kind="json", headers=None):
    """Latest answer from a web address: parsed JSON (kind="json") or text
    (kind="text"). None until the first answer arrives. Refreshed in the
    background every `every` seconds (at least 15)."""
    every = max(MIN_EVERY, float(every))
    return _poll(_key(url, headers, kind), every, lambda: _get(url, headers, kind))


def fetch_info(url, kind="json", headers=None):
    """dict(age seconds, error text, loading) for an address fetch() has been asked for."""
    with _lock:
        e = _entries.get(_key(url, headers, kind))
        if e is None:
            return dict(age=None, error="", loading=False)
        return dict(age=time.time() - e["time"] if e["time"] else None, error=e["error"], loading=e["busy"])


def data_error(what):
    """Last error of a helper ("weather", "stock", "youtube", "headlines") or
    of a fetch() address; "" when fine. "game" gives the game listener's error."""
    if what == "game":
        port = int(str(_last.get("game", "game:3000")).split(":")[1])
        return (_servers.get(port) or {}).get("error", "")
    key = _last.get(what)
    if key is None:
        key = next((k for k in _entries if k.startswith(what + "|")), None)
    with _lock:
        e = _entries.get(key) if key else None
        return e["error"] if e else ""


def _remember(name, url, kind="json", headers=None):
    _last[name] = _key(url, headers, kind)


# ---------------------------------------------------------------------------
# Weather (Open-Meteo, free, no account)
# ---------------------------------------------------------------------------
WMO = {0: ("Clear", "clear"), 1: ("Mostly clear", "clear"), 2: ("Partly cloudy", "partly"), 3: ("Cloudy", "cloud"),
       45: ("Fog", "fog"), 48: ("Freezing fog", "fog"), 51: ("Light drizzle", "rain"), 53: ("Drizzle", "rain"),
       55: ("Heavy drizzle", "rain"), 56: ("Freezing drizzle", "rain"), 57: ("Freezing drizzle", "rain"),
       61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"), 66: ("Freezing rain", "rain"),
       67: ("Freezing rain", "rain"), 71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"),
       77: ("Snow grains", "snow"), 80: ("Showers", "rain"), 81: ("Showers", "rain"), 82: ("Heavy showers", "rain"),
       85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"), 95: ("Thunderstorm", "storm"),
       96: ("Thunderstorm, hail", "storm"), 99: ("Thunderstorm, hail", "storm")}
US_STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
             "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
             "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
             "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
             "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
             "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
             "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
             "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
             "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
             "WI": "Wisconsin", "WY": "Wyoming"}


def _place(place):
    """(lat, lon, name) for "Chicago", "Chicago, IL", "Paris, France" or "41.88, -87.63"; None while looking up."""
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", place)
    if m:
        return float(m.group(1)), float(m.group(2)), place.strip()
    name, _, where = (p.strip() for p in place.partition(","))
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": name, "count": 10, "language": "en", "format": "json"})
    _remember("weather", url)
    got = fetch(url, every=7 * 86400)                # places don't move: look up once a week
    if not got:
        return None
    results = got.get("results") or []
    if not results:
        return False
    if where:
        w = US_STATES.get(where.upper(), where).lower()
        for r in results:
            if w in (str(r.get("admin1", "")).lower(), str(r.get("country", "")).lower(),
                     str(r.get("country_code", "")).lower()):
                results = [r]
                break
    r = results[0]
    return r["latitude"], r["longitude"], r.get("name", name)


def weather(place="Chicago, IL", units="F", every=600):
    """Current weather for a town. dict: temp, feels_like, high, low, wind,
    humidity, description, icon ("clear", "partly", "cloud", "fog", "rain",
    "snow", "storm"), is_day, place, unit ("°F"/"°C"), hours (next 12 hours:
    list of dicts time/temp/icon). None while loading."""
    p = _place(place)
    if p is False:
        _entries.setdefault("weather|notfound", dict(value=None, time=0, error=f"Place not found: {place}",
                                                     busy=False, tried=0))
        _last["weather"] = "weather|notfound"
        return None
    if not p:
        return None
    lat, lon, name = p
    f = str(units).upper().startswith("F")
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": round(lat, 3), "longitude": round(lon, 3),
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
        "hourly": "temperature_2m,weather_code", "forecast_hours": 12,
        "daily": "temperature_2m_max,temperature_2m_min", "forecast_days": 1, "timezone": "auto",
        "temperature_unit": "fahrenheit" if f else "celsius", "wind_speed_unit": "mph" if f else "kmh"})
    _remember("weather", url)
    d = fetch(url, every=every)
    if not d or "current" not in d:
        return None
    cur, day, hr = d["current"], d.get("daily", {}), d.get("hourly", {})
    desc, icon = WMO.get(int(cur.get("weather_code", 0)), ("", "cloud"))
    hours = [dict(time=t[-5:], temp=v, icon=WMO.get(int(c), ("", "cloud"))[1])
             for t, v, c in zip(hr.get("time", []), hr.get("temperature_2m", []), hr.get("weather_code", []))]
    return dict(temp=cur.get("temperature_2m"), feels_like=cur.get("apparent_temperature"),
                humidity=cur.get("relative_humidity_2m"), wind=cur.get("wind_speed_10m"),
                high=(day.get("temperature_2m_max") or [None])[0], low=(day.get("temperature_2m_min") or [None])[0],
                description=desc, icon=icon, is_day=bool(cur.get("is_day", 1)), place=name,
                unit="°F" if f else "°C", wind_unit="mph" if f else "km/h", hours=hours)


# ---------------------------------------------------------------------------
# Stocks, indexes, currencies and crypto (Yahoo Finance quotes)
# ---------------------------------------------------------------------------
def stock(symbol, every=60):
    """Latest quote: "AAPL", "^GSPC" (S&P 500), "BTC-USD", "EURUSD=X"...
    dict: symbol, name, price, change, change_pct, currency, history (prices
    through the day, for a little chart). None while loading."""
    sym = symbol.strip().upper()
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(sym)
           + "?range=1d&interval=15m")
    _remember("stock", url)
    d = fetch(url, every=every)
    try:
        r = d["chart"]["result"][0]
    except (TypeError, KeyError, IndexError):
        if d and d.get("chart", {}).get("error"):
            with _lock:
                _entries[_key(url, None, "json")]["error"] = f"Unknown symbol {sym}"
        return None
    m = r["meta"]
    price = m.get("regularMarketPrice")
    prev = m.get("chartPreviousClose") or m.get("previousClose")
    closes = [v for v in ((r.get("indicators", {}).get("quote") or [{}])[0].get("close") or []) if v is not None]
    change = (price - prev) if price is not None and prev else 0.0
    return dict(symbol=sym, name=m.get("shortName") or m.get("longName") or sym, price=price, change=change,
                change_pct=change / prev * 100 if prev else 0.0, currency=m.get("currency", ""), history=closes)


# ---------------------------------------------------------------------------
# YouTube subscribers
# ---------------------------------------------------------------------------
def _count(text):
    """'12.3K' -> 12300, '1,234' -> 1234"""
    m = re.match(r"([\d.,]+)\s*([KMB]?)", text.strip(), re.I)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return int(round(n * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()]))


def youtube(channel="@MrBeast", api_key="", every=300):
    """Subscribers of a channel ("@handle", a channel URL or a UC... id).
    dict: subscribers (number), text ("849", "30.5M"), exact (False when
    YouTube rounded it - it shows every public count over 1,000 to three
    digits, on the page and in its API alike), name, views, videos.
    None while loading. Without api_key the public channel page is read (no
    account needed); a YouTube Data API key adds total views and videos."""
    ch = channel.strip()
    m = re.search(r"youtube\.com/((?:@|channel/)[^/?#]+)", ch)
    if m:
        ch = m.group(1).replace("channel/", "")
    if api_key:
        p = {"part": "statistics,snippet", "key": api_key.strip()}
        p["forHandle" if ch.startswith("@") else "id"] = ch
        url = "https://www.googleapis.com/youtube/v3/channels?" + urllib.parse.urlencode(p)
        _remember("youtube", url)
        d = fetch(url, every=every)
        try:
            it = d["items"][0]
            st = it["statistics"]
            n = int(st.get("subscriberCount", 0))
            return dict(subscribers=n, text=f"{n:,}", exact=n < 1000, name=it["snippet"]["title"],
                        views=int(st.get("viewCount", 0)), videos=int(st.get("videoCount", 0)))
        except (TypeError, KeyError, IndexError, ValueError):
            return None
    url = "https://www.youtube.com/" + (ch if ch.startswith("@") else "channel/" + ch)
    _remember("youtube", url, "text")
    page = fetch(url, every=every, kind="text")
    if not page:
        return None
    m = re.search(r'"([\d.,]+[KMB]?) subscribers"', page) or re.search(r"([\d.,]+[KMB]?) subscribers", page)
    if not m:
        return None
    name = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    return dict(subscribers=_count(m.group(1)), text=m.group(1), exact=not m.group(1)[-1:].isalpha(),
                name=_html.unescape(name.group(1)) if name else ch, views=None, videos=None)


# ---------------------------------------------------------------------------
# News headlines (any RSS or Atom feed)
# ---------------------------------------------------------------------------
def headlines(feed="https://feeds.npr.org/1001/rss.xml", count=10, every=900):
    """Titles from a news feed (RSS or Atom), newest first. [] while loading."""
    _remember("headlines", feed, "text")
    xml = fetch(feed, every=every, kind="text")
    if not xml:
        return []
    items = re.findall(r"<(?:item|entry)\b.*?<title[^>]*>(.*?)</title>", xml, re.S | re.I)
    out = []
    for t in items:
        t = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", t, flags=re.S)
        t = _html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
        if t:
            out.append(" ".join(t.split()))
    return out[:count]


def get_path(data, path):
    """Dig into JSON with a dotted path: get_path(d, "bitcoin.usd"),
    get_path(d, "items.0.name"). None if it isn't there."""
    for part in str(path).split("."):
        if part == "":
            continue
        try:
            data = data[int(part)] if isinstance(data, list) else data[part]
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return data


HELPERS = dict(fetch=fetch, fetch_info=fetch_info, data_error=data_error, weather=weather, stock=stock,
               youtube=youtube, headlines=headlines, get_path=get_path)


# ---------------------------------------------------------------------------
# Game State Integration: Counter-Strike 2 (and Dota 2) send live game data
# to a small web server here while you play. Valve's own feature, VAC-safe.
# ---------------------------------------------------------------------------
_servers = {}                   # port -> dict(state, time, server, error)


def _merge(old, new):
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(old.get(k), dict):
            _merge(old[k], v)
        else:
            old[k] = v


def _start_listener(port, any_computer):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    slot = _servers[port]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n).decode("utf-8", "replace") or "{}")
                with _lock:
                    data.pop("previously", None)
                    data.pop("added", None)
                    slot["state"] = data            # each post is the full picture of what was asked for
                    slot["time"] = time.time()
                    slot["posts"] += 1
                self.send_response(200)
            except ValueError:
                self.send_response(400)
            self.end_headers()

        def log_message(self, *a):
            pass

    try:
        srv = ThreadingHTTPServer(("0.0.0.0" if any_computer else "127.0.0.1", port), Handler)
    except OSError as e:
        slot["error"] = f"Can't listen on port {port}: {e.strerror or e}"
        return
    slot["server"] = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def game_state(port=3000, any_computer=False):
    """Latest data the game sent (a dict like CS2's: map, round, player...),
    or None until it sends something. Start the game after the config file
    is in place (see cs2_setup). any_computer=True also accepts data from
    other computers on your network (the game on another PC)."""
    port = int(port)
    with _lock:
        slot = _servers.get(port)
        if slot is None:
            slot = _servers[port] = dict(state=None, time=0.0, server=None, error="", posts=0)
            start = True
        else:
            start = False
    if start:
        _start_listener(port, any_computer)
    _last["game"] = f"game:{port}"
    return slot["state"]


def game_info(port=3000):
    """dict(age seconds since the game last sent data or None, posts received, error)."""
    slot = _servers.get(int(port)) or {}
    t = slot.get("time") or 0
    return dict(age=time.time() - t if t else None, posts=slot.get("posts", 0), error=slot.get("error", ""))


CS2_CONFIG = '''"DDP Streamer"
{
    "uri"       "http://%s:%d"
    "timeout"   "1.0"
    "buffer"    "0.0"
    "throttle"  "0.05"
    "heartbeat" "5.0"
    "data"
    {
        "provider"            "1"
        "map"                 "1"
        "round"               "1"
        "player_id"           "1"
        "player_state"        "1"
        "player_weapons"      "1"
        "player_match_stats"  "1"
    }
}
'''
CS2_FILE = "gamestate_integration_ddp_streamer.cfg"


def _steam_libraries():
    roots = []
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                roots.append(winreg.QueryValueEx(k, "SteamPath")[0])
        except OSError:
            pass
        roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    else:
        roots += [os.path.expanduser("~/.steam/steam"), os.path.expanduser("~/.local/share/Steam")]
    libs = []
    for r in roots:
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8") as f:
                libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
        except OSError:
            pass
        libs.append(r)
    seen, out = set(), []
    for p in libs:
        k = os.path.normcase(os.path.normpath(p))
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def cs2_setup(port=3000, host="127.0.0.1"):
    """Put the Game State Integration config into CS2's cfg folder (if CS2
    is installed on this computer) and save a copy next to the settings.
    Returns (path written or None, path of the copy). Restart CS2 after."""
    text = CS2_CONFIG % (host, int(port))
    copy = os.path.join(DATA_DIR, "scripts", CS2_FILE)     # My scripts: the Folder button opens it
    try:
        os.makedirs(os.path.dirname(copy), exist_ok=True)
        with open(copy, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass
    for lib in _steam_libraries():
        cfg = os.path.join(lib, "steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo", "cfg")
        if os.path.isdir(cfg):
            path = os.path.join(cfg, CS2_FILE)
            try:
                with open(path, encoding="utf-8") as f:
                    if f.read() == text:
                        return path, copy
            except OSError:
                pass
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                return path, copy
            except OSError:
                return None, copy
    return None, copy


def local_ip():
    """This computer's address on the network (for the game on another PC)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


HELPERS.update(game_state=game_state, game_info=game_info, cs2_setup=cs2_setup, local_ip=local_ip)


# ---------------------------------------------------------------------------
# Game servers: the standard Steam server query (CS2, CS:GO, TF2, Garry's
# Mod, Rust, ARK, Valheim... any server that shows in Steam's server browser)
# ---------------------------------------------------------------------------
A2S_HEADER = b"\xff\xff\xff\xff"


def _split_addr(address, default_port=27015):
    host, _, port = str(address).strip().rpartition(":")
    if not host:
        host, port = port, ""
    return host, int(port) if port.isdigit() else default_port


def _a2s(sock, addr, request):
    """Send a query, answering the server's challenge if it asks for one."""
    sock.sendto(request, addr)
    data, _ = sock.recvfrom(65535)
    if data[:4] == b"\xfe\xff\xff\xff":
        raise ValueError("answer too long (split packets)")
    if data[4:5] == b"A":                                   # challenge: ask again with it
        challenge = data[5:9]
        req = request[:-4] + challenge if request[4:5] == b"U" else request + challenge
        sock.sendto(req, addr)
        data, _ = sock.recvfrom(65535)
    return data[4:]


def _cstr(b, i):
    j = b.index(b"\0", i)
    return b[i:j].decode("utf-8", "replace"), j + 1


class NoAnswer(Exception):
    """Shown as just its message."""


def _query_server(address, players=True):
    try:
        return _query_server2(address, players)
    except (ConnectionResetError, TimeoutError, socket.timeout):
        host, port = _split_addr(address)
        raise NoAnswer(f"No game server answered at {host}:{port}")
    except socket.gaierror:
        raise NoAnswer(f"Unknown address: {_split_addr(address)[0]}")


def _query_server2(address, players=True):
    host, port = _split_addr(address)
    addr = (socket.gethostbyname(host), port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3.0)
    try:
        t0 = time.perf_counter()
        d = _a2s(sock, addr, A2S_HEADER + b"TSource Engine Query\0")
        ping = (time.perf_counter() - t0) * 1000
        if d[:1] != b"I":
            raise ValueError("not a game server answer")
        i = 2
        name, i = _cstr(d, i)
        mapname, i = _cstr(d, i)
        folder, i = _cstr(d, i)
        game, i = _cstr(d, i)
        i += 2                                              # app id
        count, maxp, bots = d[i], d[i + 1], d[i + 2]
        info = dict(name=name, map=mapname, game=game, folder=folder, players=count, max_players=maxp,
                    bots=bots, password=bool(d[i + 5]), ping=round(ping), list=[])
        if players:
            d = _a2s(sock, addr, A2S_HEADER + b"U" + b"\xff\xff\xff\xff")
            if d[:1] == b"D":
                n, i = d[1], 2
                for _ in range(n):
                    i += 1                                  # index
                    pname, i = _cstr(d, i)
                    score, secs = struct.unpack_from("<lf", d, i)
                    i += 8
                    if pname:
                        info["list"].append(dict(name=pname, score=score, time=secs))
                info["list"].sort(key=lambda p: -p["score"])
        return info
    finally:
        sock.close()


def game_server(address, every=5):
    """Live info from a game server ("192.168.1.50:27015"; the port defaults
    to 27015). dict: name, map, game, players, max_players, bots, password,
    ping (ms), list (players: name, score, time in seconds, best first).
    None until the first answer. Works for any server in Steam's server
    browser - CS2, TF2, Rust, ARK, Valheim, Garry's Mod and many more."""
    key = "server|" + str(address).strip()
    _last["server"] = key
    return _poll(key, max(2.0, float(every)), lambda: _query_server(address), disk=False)


# ---------------------------------------------------------------------------
# CS2 server log, sent live by the server with
#   logaddress_add_http "http://<this computer>:3001"
# turned into kills, rounds, the bomb and scores for everyone on the server.
# ---------------------------------------------------------------------------
def _player_re(p):
    return (r'"(?P<' + p + r'name>.+?)<\d+><(?P<' + p + r'id>[^>]*)>(?:<(?P<' + p + r'team>[^>]*)>)?"'   # no team tag on some lines
            r'(?: \[(?P<' + p + r'pos>[^\]]*)\])?')


LOG_RULES = [
    ("kill", re.compile(_player_re("k") + r" killed (?:other )?" + _player_re("v") + r' with "(?P<weapon>[^"]+)"(?P<flags>.*)')),
    ("assist", re.compile(_player_re("a") + r" (?:flash-)?assisted killing " + _player_re("v"))),
    ("suicide", re.compile(_player_re("v") + r' committed suicide with "(?P<weapon>[^"]*)"')),
    ("round_start", re.compile(r'World triggered "Round_Start"')),
    ("round_end", re.compile(r'World triggered "Round_End"')),
    ("match_start", re.compile(r'World triggered "Match_Start" on "(?P<map>[^"]+)"')),
    ("win", re.compile(r'Team "(?P<team>[^"]+)" triggered "(?P<how>[^"]+)" \(CT "(?P<ct>\d+)"\) \(T "(?P<t>\d+)"\)')),
    ("score", re.compile(r'Team "(?P<team>[^"]+)" scored "(?P<score>\d+)"')),
    ("planted", re.compile(_player_re("p") + r' triggered "Planted_The_Bomb"(?: at bombsite (?P<site>\w+))?')),
    ("defused", re.compile(_player_re("p") + r' triggered "Defused_The_Bomb"')),
    ("exploded", re.compile(r'triggered "SFUI_Notice_Target_Bombed"|triggered "Target_Bombed"')),
    ("map", re.compile(r'(?:Loading|Started) map "(?P<map>[^"]+)"')),
    ("join", re.compile(_player_re("p") + r" entered the game")),
    ("leave", re.compile(_player_re("p") + r" disconnected")),
    ("switch", re.compile(_player_re("p") + r" switched from team <(?P<old>[^>]*)> to <(?P<new>[^>]*)>")),
    ("say", re.compile(_player_re("p") + r' say(?P<team_only>_team)? "(?P<text>.*)"')),
    ("attack", re.compile(_player_re("a") + r" attacked " + _player_re("v"))),    # needs mp_logdetail 3
]
MAX_SPOTS = 4000
_logs = {}                      # port -> state dict


def _team(t):
    t = (t or "").upper()
    return "CT" if t == "CT" else "T" if t in ("TERRORIST", "T") else ""


def _new_log_state():
    return dict(map="", round=0, ct=0, t=0, phase="", players={}, events=[], seq=0, time=0.0, lines=0,
                server="", error="", bomb=None, spots=[])


def _pos(s):
    """"x y z" from a log line -> (x, y, z) floats, or None."""
    try:
        x, y, z = (float(v) for v in (s or "").split())
        return x, y, z
    except ValueError:
        return None


def _spot(st, pos, kind, team):
    if pos:
        st["spots"].append((time.time(), pos[0], pos[1], pos[2], kind, _team(team)))
        if len(st["spots"]) > MAX_SPOTS:
            del st["spots"][:len(st["spots"]) - MAX_SPOTS]


def _new_map(st, m):
    if m and m != st["map"]:
        st["spots"] = []
        st["players"] = {}          # everyone reconnects on a new map
    st["map"] = m


def _log_player(st, name, team=None, pid=None):
    p = st["players"].setdefault(name, dict(name=name, team="", kills=0, deaths=0, assists=0, headshots=0,
                                            bot=False))
    if team is not None and _team(team):
        p["team"] = _team(team)
    if pid is not None:
        p["bot"] = pid == "BOT"
    p["seen"] = time.time()
    return p


def _log_line(st, line):
    for kind, rx in LOG_RULES:
        m = rx.search(line)
        if not m:
            continue
        g = m.groupdict()
        ev = dict(type=kind)
        if kind == "kill":
            k, v = _log_player(st, g["kname"], g["kteam"], g["kid"]), _log_player(st, g["vname"], g["vteam"], g["vid"])
            hs = "headshot" in g["flags"]
            k["kills"] += 1
            k["headshots"] += hs
            v["deaths"] += 1
            kp, vp = _pos(g["kpos"]), _pos(g["vpos"])
            _spot(st, kp, "killer", g["kteam"])
            _spot(st, vp, "victim", g["vteam"])
            ev.update(killer=k["name"], killer_team=k["team"], victim=v["name"], victim_team=v["team"],
                      weapon=g["weapon"], headshot=hs, wallbang="penetrated" in g["flags"],
                      noscope="noscope" in g["flags"], through_smoke="throughsmoke" in g["flags"],
                      killer_pos=kp, victim_pos=vp)
        elif kind == "assist":
            _log_player(st, g["aname"], g["ateam"])["assists"] += 1
            return
        elif kind == "suicide":
            _log_player(st, g["vname"], g["vteam"])["deaths"] += 1
            ev.update(victim=g["vname"], victim_team=_team(g["vteam"]), weapon=g["weapon"])
        elif kind == "round_start":
            st["round"] += 1
            st["phase"] = "live"
            st["bomb"] = None
            ev.update(round=st["round"])
        elif kind == "round_end":
            st["phase"] = "over"
        elif kind == "match_start":
            _new_map(st, g["map"])
            st.update(round=0, ct=0, t=0)
            for p in st["players"].values():
                p.update(kills=0, deaths=0, assists=0, headshots=0)
            ev.update(map=g["map"])
        elif kind == "win":
            st["ct"], st["t"] = int(g["ct"]), int(g["t"])
            how = g["how"].replace("SFUI_Notice_", "")
            if "Bombed" in how:
                st["bomb"] = "exploded"
            elif "Defused" in how:
                st["bomb"] = "defused"
            ev.update(winner=_team(g["team"]), how=how, ct=st["ct"], t=st["t"])
        elif kind == "score":
            st["ct" if _team(g["team"]) == "CT" else "t"] = int(g["score"])
            return
        elif kind in ("planted", "defused"):
            _log_player(st, g["pname"], g["pteam"])
            st["bomb"] = kind
            ev.update(player=g["pname"], site=g.get("site") or "")
        elif kind == "exploded":
            st["bomb"] = "exploded"
        elif kind == "map":
            _new_map(st, g["map"])
            return
        elif kind == "attack":
            _log_player(st, g["aname"], g["ateam"], g["aid"])
            _log_player(st, g["vname"], g["vteam"], g["vid"])
            _spot(st, _pos(g["apos"]), "shooter", g["ateam"])
            _spot(st, _pos(g["vpos"]), "hit", g["vteam"])
            return
        elif kind == "join":
            _log_player(st, g["pname"], g["pteam"], g["pid"])
            ev.update(player=g["pname"])
        elif kind == "leave":
            st["players"].pop(g["pname"], None)
            ev.update(player=g["pname"])
        elif kind == "switch":
            _log_player(st, g["pname"], g["new"], g["pid"])
            ev.update(player=g["pname"], team=_team(g["new"]))
        elif kind == "say":
            _log_player(st, g["pname"], g["pteam"], g["pid"])
            ev.update(player=g["pname"], team=_team(g["pteam"]), text=g["text"], team_only=bool(g["team_only"]))
        st["seq"] += 1
        ev.update(seq=st["seq"], time=time.time())
        st["events"] = (st["events"] + [ev])[-60:]
        return


def _start_log_listener(port):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    st = _logs[port]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n).decode("utf-8", "replace")
            with _lock:
                st["server"] = self.headers.get("x-server-addr") or self.client_address[0]
                st["time"] = time.time()
                for line in body.splitlines():
                    if line.strip():
                        st["lines"] += 1
                        _log_line(st, line)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError as e:
        st["error"] = f"Can't listen on port {port}: {e.strerror or e}"
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def server_log(port=3001):
    """Live state of a CS2 server that sends its log here (see the manual):
    dict with map, round, ct and t (team scores), phase ("live"/"over"),
    bomb (None/"planted"/"defused"/"exploded"), players (name -> team,
    kills, deaths, assists, headshots, bot, seen = when last in the log), events (the latest 60, each with
    type, seq and time: kill, round_start, win, planted, defused, exploded,
    join, leave, switch, say, match_start, suicide; kills also have
    killer_pos / victim_pos (x, y, z) or None), spots (positions from
    kills and, with mp_logdetail 3, hits: (time, x, y, z, kind, team), kind
    killer/victim/shooter/hit, the latest 4000 on this map), time (last line
    received, 0 = nothing yet), lines (count), error."""
    port = int(port)
    with _lock:
        st = _logs.get(port)
        start = st is None
        if start:
            st = _logs[port] = _new_log_state()
    if start:
        _start_log_listener(port)
    with _lock:
        out = dict(st)
        out["players"] = {k: dict(v) for k, v in st["players"].items()}
        out["events"] = list(st["events"])
        out["spots"] = list(st["spots"])
        return out


HELPERS.update(game_server=game_server, server_log=server_log)
