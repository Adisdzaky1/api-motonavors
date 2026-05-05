from flask import Flask, request
import requests

app = Flask(__name__)

ORS_KEY       = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImMyZjlmYTk3YWYxODQyNmQ5YzUxZDkxMGFhYzA2OGMxIiwiaCI6Im11cm11cjY0In0="
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ORS_ROUTE_URL = "https://api.openrouteservice.org/v2/directions/driving-car/json"
HEADERS_NOM   = {'User-Agent': 'MotoNavApp/1.0'}


def fmt_jarak(meter):
    if meter >= 1000:
        return f"{meter/1000:.1f}km"
    return f"{int(meter)}m"


def geocode(nama):
    for q in [nama + ", Indonesia", nama + ", Jawa Tengah", nama]:
        try:
            r = requests.get(NOMINATIM_URL,
                params={'q': q, 'format': 'json', 'limit': 1, 'countrycodes': 'id'},
                headers=HEADERS_NOM, timeout=10)
            data = r.json()
            if data:
                return {
                    'lat':  float(data[0]['lat']),
                    'lng':  float(data[0]['lon']),
                    'nama': data[0].get('display_name', nama).split(',')[0]
                }
        except:
            continue
    return None


def get_rute_ors(lat_a, lng_a, lat_t, lng_t):
    return requests.post(ORS_ROUTE_URL,
        headers={'Authorization': ORS_KEY, 'Content-Type': 'application/json'},
        json={"coordinates": [[lng_a, lat_a], [lng_t, lat_t]], "language": "id"},
        timeout=10)


def parse_ors(resp, nama_tujuan):
    if resp.status_code != 200:
        return f"ERROR:ORS_{resp.status_code}|{resp.text[:80]}"

    data = resp.json()
    if not data.get('routes'):
        return "ERROR:RUTE_KOSONG"

    steps = data['routes'][0]['segments'][0]['steps']

    # Ambil step pertama yang bukan "head/depart"
    step = None
    for s in steps:
        ins = s.get('instruction', '').lower()
        if not any(k in ins for k in ['head ', 'depart', 'mulai menuju', 'start']):
            step = s
            break
    if not step:
        step = steps[0]

    instruksi = step.get('instruction', '').lower()
    jarak     = step.get('distance', 0)
    jalan     = step.get('name', '') or nama_tujuan

    if any(k in instruksi for k in ['kanan', 'right', 'belok kanan']):
        arah = 'KANAN'
    elif any(k in instruksi for k in ['kiri', 'left', 'belok kiri']):
        arah = 'KIRI'
    elif any(k in instruksi for k in ['tiba', 'arrive', 'destination', 'sampai']):
        arah = 'TIBA'
    elif any(k in instruksi for k in ['putar', 'u-turn', 'balik']):
        arah = 'BALIK'
    else:
        arah = 'LURUS'

    return f"NAV:{arah}:{fmt_jarak(jarak)}:{jalan[:20]}"


# ═══════════════════════════════════════════════════
#  ENDPOINT: /nav
#
#  MODE 1 — Nama tempat (geocoding otomatis):
#    /nav?lat=-7.67&lng=109.65&dest=Alun+Alun+Kebumen
#
#  MODE 2 — Koordinat langsung dari Google Maps:
#    /nav?lat=-7.67&lng=109.65&dlat=-7.7123&dlng=109.5678&dest=Pantai+Sekar
# ═══════════════════════════════════════════════════
@app.route('/nav')
def nav():
    lat  = request.args.get('lat',  '')
    lng  = request.args.get('lng',  '')
    dest = request.args.get('dest', 'Tujuan')
    dlat = request.args.get('dlat', '')   # ← koordinat tujuan langsung
    dlng = request.args.get('dlng', '')   # ← koordinat tujuan langsung

    if not lat or not lng:
        return "ERROR:GPS_KOSONG|Pastikan LocationSensor aktif"

    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except:
        return "ERROR:FORMAT_GPS"

    # ── MODE 2: Koordinat langsung ──────────────────
    if dlat and dlng:
        try:
            tujuan_lat = float(dlat)
            tujuan_lng = float(dlng)
        except:
            return "ERROR:FORMAT_KOORDINAT|Contoh: dlat=-7.7123&dlng=109.5678"

        try:
            resp = get_rute_ors(lat_f, lng_f, tujuan_lat, tujuan_lng)
            return parse_ors(resp, dest)
        except requests.exceptions.Timeout:
            return "ERROR:TIMEOUT"
        except Exception as e:
            return f"ERROR:ROUTE|{str(e)[:80]}"

    # ── MODE 1: Nama tempat ─────────────────────────
    if not dest or dest == 'Tujuan':
        return "ERROR:TUJUAN_KOSONG|Isi dest= atau gunakan dlat= dan dlng="

    geo = geocode(dest)
    if not geo:
        return (f"ERROR:TIDAK_DITEMUKAN|{dest}|"
                f"Coba pakai koordinat: dlat=XX&dlng=YY dari Google Maps")

    try:
        resp = get_rute_ors(lat_f, lng_f, geo['lat'], geo['lng'])
        return parse_ors(resp, geo['nama'])
    except requests.exceptions.Timeout:
        return "ERROR:TIMEOUT"
    except Exception as e:
        return f"ERROR:ROUTE|{str(e)[:80]}"


# ═══════════════════════════════════════════════════
#  ENDPOINT: /cari — test geocoding saja
#  Contoh: /cari?q=Alun+Alun+Kebumen
# ═══════════════════════════════════════════════════
@app.route('/cari')
def cari():
    q = request.args.get('q', '')
    if not q:
        return "Tulis: /cari?q=nama_tempat"
    geo = geocode(q)
    if not geo:
        return f"TIDAK_DITEMUKAN|{q}|Pakai koordinat manual"
    return f"DITEMUKAN|lat={geo['lat']}|lng={geo['lng']}|nama={geo['nama']}"


# ═══════════════════════════════════════════════════
#  ENDPOINT: /test — cek server + ORS key
# ═══════════════════════════════════════════════════
@app.route('/test')
def test():
    return f"SERVER_OK|Mode:NamaTempat+KoordinatLangsung|ORS_KeyLen:{len(ORS_KEY)}"


@app.route('/testors')
def testors():
    try:
        r = requests.get("https://api.openrouteservice.org/geocode/search",
            params={'api_key': ORS_KEY, 'text': 'Jakarta', 'size': 1}, timeout=10)
        return f"ORS_{'VALID' if r.status_code==200 else 'INVALID'}|status={r.status_code}"
    except Exception as e:
        return f"ORS_ERROR|{e}"


#if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
