from flask import Flask, request
import requests

app = Flask(__name__)
ORS_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImMyZjlmYTk3YWYxODQyNmQ5YzUxZDkxMGFhYzA2OGMxIiwiaCI6Im11cm11cjY0In0="

HEADERS_NOMINATIM = {
    'User-Agent': 'MotoNavApp/1.0 (navigasi motor pribadi)'
}

@app.route('/nav')
def nav():
    lat  = request.args.get('lat', '')
    lng  = request.args.get('lng', '')
    dest = request.args.get('dest', '')

    if not lat or not lng or not dest:
        return f"ERROR:PARAM_KOSONG|lat={lat}|lng={lng}|dest={dest}"

    # ── Step 1: Geocode pakai Nominatim (OpenStreetMap) ──
    try:
        geo_resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                'q': dest,
                'format': 'json',
                'limit': 1,
                'countrycodes': 'id'   # prioritas Indonesia
            },
            headers=HEADERS_NOMINATIM,
            timeout=10
        )

        if geo_resp.status_code != 200:
            return f"ERROR:GEOCODE_HTTP_{geo_resp.status_code}"

        geo = geo_resp.json()

        if not geo:
            return f"ERROR:LOKASI_TIDAK_DITEMUKAN|dest={dest}"

        dst_lat = float(geo[0]['lat'])
        dst_lng = float(geo[0]['lon'])
        nama_lokasi = geo[0].get('display_name', dest).split(',')[0]

    except requests.exceptions.Timeout:
        return "ERROR:GEOCODE_TIMEOUT"
    except Exception as e:
        return f"ERROR:GEOCODE|{str(e)[:100]}"

    # ── Step 2: Ambil rute dari ORS ──
    try:
        route_resp = requests.post(
            "https://api.openrouteservice.org/v2/directions/driving-car/json",
            headers={
                'Authorization': ORS_KEY,
                'Content-Type': 'application/json'
            },
            json={
                "coordinates": [
                    [float(lng), float(lat)],
                    [dst_lng, dst_lat]
                ],
                "language": "id"
            },
            timeout=10
        )

        if route_resp.status_code != 200:
            return f"ERROR:ROUTE_HTTP_{route_resp.status_code}|{route_resp.text[:100]}"

        r = route_resp.json()

        if not r.get('routes'):
            return f"ERROR:RUTE_KOSONG"

        step      = r['routes'][0]['segments'][0]['steps'][0]
        instruksi = step['instruction'].lower()
        jarak     = step['distance']
        jalan     = step.get('name', nama_lokasi)

        # Format jarak
        if jarak >= 1000:
            jarak_str = f"{jarak/1000:.1f}km"
        else:
            jarak_str = f"{int(jarak)}m"

        # Deteksi arah
        if any(k in instruksi for k in ['kanan', 'right']):
            arah = 'KANAN'
        elif any(k in instruksi for k in ['kiri', 'left']):
            arah = 'KIRI'
        elif any(k in instruksi for k in ['tiba', 'destination', 'arrive']):
            arah = 'TIBA'
        elif any(k in instruksi for k in ['putar', 'u-turn', 'balik']):
            arah = 'BALIK'
        else:
            arah = 'LURUS'

        return f"NAV:{arah}:{jarak_str}:{jalan[:20]}"

    except requests.exceptions.Timeout:
        return "ERROR:ROUTE_TIMEOUT"
    except KeyError as e:
        return f"ERROR:PARSE|key={str(e)}"
    except Exception as e:
        return f"ERROR:ROUTE|{str(e)[:100]}"


@app.route('/test')
def test():
    return f"SERVER_OK|ORS_KEY_LENGTH:{len(ORS_KEY)}"


@app.route('/testgeo')
def testgeo():
    # Test geocoding Nominatim dengan lokasi Kebumen
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={'q': 'Pantai Sekar Hastina Kebumen', 'format': 'json', 'limit': 1, 'countrycodes': 'id'},
            headers=HEADERS_NOMINATIM,
            timeout=10
        )
        data = r.json()
        if data:
            return f"GEO_OK|lat={data[0]['lat']}|lng={data[0]['lon']}|nama={data[0]['display_name'][:80]}"
        else:
            return "GEO_NOTFOUND|coba nama lain"
    except Exception as e:
        return f"GEO_ERROR|{str(e)}"


@app.route('/testors')
def testors():
    try:
        r = requests.get(
            "https://api.openrouteservice.org/geocode/search",
            params={'api_key': ORS_KEY, 'text': 'Jakarta', 'size': 1},
            timeout=10
        )
        if r.status_code == 200:
            return f"ORS_KEY_VALID|status=200"
        else:
            return f"ORS_KEY_INVALID|status={r.status_code}|{r.text[:100]}"
    except Exception as e:
        return f"ORS_ERROR|{str(e)}"


# if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
