"""Client for the open Meteo API of the Autonomous Province of Bolzano.

https://geoservices.buergernetz.bz.it/services/meteo/v1/
(the old daten.buergernetz.bz.it host was retired in 2026)

The API serves 10-minute values back to at least 2015, so it is the archive
of the raw data; the pipeline only keeps a rolling window of it.

Stdlib only, so it runs on a bare CI runner.
"""

import gzip
import json
import os
import ssl
import threading
import time
from base64 import b64encode
from http.client import HTTPException, HTTPSConnection
from urllib.parse import unquote, urlencode, urlsplit

from .timeutil import api_bound

BASE = "https://geoservices.buergernetz.bz.it/services/meteo/v1"

# LT = air temperature, N = precipitation, WG = wind speed, WR = wind direction,
# WG.BOE = gust, LF = relative humidity, LD.RED = pressure (sea level),
# HS = snow depth, SD = sunshine duration, GS = global radiation
SENSORS = ["LT", "N", "WG", "WR", "WG.BOE", "LF", "LD.RED", "HS", "SD", "GS"]

# Compressed answers are a tenth of the size; on the long way from a CI runner
# that, more than the server, sets the pace of a backfill.
HEADERS = {"User-Agent": "coldfront/0.2", "Accept": "application/json", "Accept-Encoding": "gzip"}

# One kept-alive connection per worker thread: a run makes ~700 requests,
# and a fresh TLS handshake for each makes the server reset connections.
_local = threading.local()


def _connect():
    base = urlsplit(BASE)
    ctx = ssl.create_default_context()
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not proxy:
        return HTTPSConnection(base.hostname, base.port or 443, timeout=60, context=ctx)
    p = urlsplit(proxy)
    headers = {}
    if p.username:
        cred = f"{unquote(p.username)}:{unquote(p.password or '')}".encode()
        headers["Proxy-Authorization"] = "Basic " + b64encode(cred).decode()
    conn = HTTPSConnection(p.hostname, p.port or 8080, timeout=60, context=ctx)
    conn.set_tunnel(base.hostname, base.port or 443, headers=headers)
    return conn


def get_json(path, params=None, retries=5):
    url = f"{urlsplit(BASE).path}/{path}" + ("?" + urlencode(params) if params else "")
    for attempt in range(retries):
        try:
            if getattr(_local, "conn", None) is None:
                _local.conn = _connect()
            _local.conn.request("GET", url, headers=HEADERS)
            resp = _local.conn.getresponse()
            body = resp.read()
            if (resp.getheader("Content-Encoding") or "").lower() == "gzip":
                body = gzip.decompress(body)
            if resp.status != 200:
                raise HTTPException(f"HTTP {resp.status}")
            return json.loads(body.decode("utf-8"))
        except (OSError, HTTPException, json.JSONDecodeError) as exc:
            if getattr(_local, "conn", None) is not None:
                _local.conn.close()
                _local.conn = None
            if attempt == retries - 1:
                raise RuntimeError(f"{path} {params or ''}: {exc}") from exc
            time.sleep(2 ** attempt)


def stations():
    data = get_json("stations")
    if not isinstance(data, dict) or "features" not in data:
        raise RuntimeError("stations: unexpected response format")
    return data


def sensors():
    """Newest value of every sensor at every station, in one call."""
    data = get_json("sensors")
    if not isinstance(data, list) or (data and not {"SCODE", "TYPE", "DATE"} <= set(data[0])):
        raise RuntimeError("sensors: unexpected response format")
    return data


def available_pairs(sensor_list, wanted=SENSORS):
    """(station, sensor) pairs the API reports as existing, limited to `wanted`."""
    return sorted({(s["SCODE"], s["TYPE"]) for s in sensor_list
                   if s.get("SCODE") and s.get("TYPE") in wanted})


def timeseries(code, sensor, start, end):
    """[{'DATE': ..., 'VALUE': ...}, ...] between two aware datetimes."""
    data = get_json("timeseries", {
        "station_code": code,
        "sensor_code": sensor,
        "output_format": "JSON",
        "date_from": api_bound(start),
        "date_to": api_bound(end),
    })
    if not isinstance(data, list):
        raise RuntimeError(f"{code}/{sensor}: unexpected response")
    return data


def station_table(geojson):
    """Station metadata keyed by code, as the later steps use it."""
    out = {}
    for f in geojson["features"]:
        p = f["properties"]
        x, y = f["geometry"]["coordinates"]
        out[p["SCODE"]] = {
            "code": p["SCODE"], "name": p["NAME_D"], "name_it": p["NAME_I"],
            "alt": p["ALT"], "x": round(x, 1), "y": round(y, 1),
            "lon": p["LONG"], "lat": p["LAT"],
        }
    return out
