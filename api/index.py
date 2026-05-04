from flask import Flask, request
import requests

app = Flask(__name__)

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {'User-Agent': 'MotoNavApp/1.0'}


def format_jarak(meter):
    if meter >= 1000:
        return f"{meter/1000:.1f}km"
    return f"{int(meter)}m"


def geocode(nama):
    queries = [
        nama + ", Indonesia",
        nama + ", Jawa Tengah, Indonesia",
        nama,
    ]
    for q in queries:
        try:
            r = requests.get(
                NOMINATIM_URL,
                params={'q': q, 'format': 'json', 'limit': 1, 'countrycodes': 'id'},
                headers=HEADERS, timeout=10
            )
            data = r.json()
            if data:
                return {
                    'lat': float(data[0]['lat']),
                    'lng': float(data[0]['lon']),
                    'nama': data[0].get('display_name', nama).split(',')[0]
                }
        except:
            continue
    return None


def get_instruksi(lat_asal, lng_asal, lat_tujuan, lng_tujuan):
    url = f"{OSRM_URL}/{lng_asal},{lat_asal};{lng_tujuan},{lat_tujuan}"
    r = requests.get(url,
        params={'steps': 'true', 'overview': 'false'},
        headers=HEADERS, timeout=10
    )
    return r.json()


def parse_rute(rute_json, nama_tujuan):
    if rute_json.get('code') != 'Ok':
        return f"ERROR:RUTE_GAGAL|{rute_json.get('message','')}"

    step     = rute_json['routes'][0]['legs'][0]['steps'][0]
    maneuver = step.get('maneuver', {})
    man_type = maneuver.get('type', '')
    man_mod  = maneuver.get('modifier', '')
    jarak    = step.get('distance', 0)
    jalan    = step.get('name', nama_tujuan) or nama_tujuan

    # Deteksi arah
    if 'right' in man_mod:
        arah = 'KANAN'
    elif 'left' in man_mod:
        arah = 'KIRI'
    elif man_type in ['arrive']:
        arah = 'TIBA'
    elif 'uturn' in man_mod or man_type == 'roundabout':
        arah = 'BALIK'
    else:
        arah = 'LURUS'

    return f"NAV:{arah}:{format_jarak(jarak)}:{jalan[:20]}"


# ═══════════════════════════════════════════════════
#  ENDPOINT UTAMA: /nav
#
#  Mode 1 — Pakai nama tempat (geocoding otomatis):
#    /nav?lat=X&lng=Y&dest=Nama+Tempat
#
#  Mode 2 — Pakai koordinat langsung dari Google Maps:
#    /nav?lat=X&lng=Y&dlat=A&dlng=B&dest=NamaTujuan
#
# ═══════════════════════════════════════════════════
@app.route('/nav')
def nav():
    # Posisi sekarang (dari GPS HP)
    lat = request.args.get('lat', '')
    lng = request.args.get('lng', '')

    # Tujuan — bisa nama ATAU koordinat langsung
    dest = request.args.get('dest', 'Tujuan')
    dlat = request.args.get('dlat', '')   # koordinat tujuan langsung
    dlng = request.args.get('dlng', '')   # koordinat tujuan langsung

    if not lat or not lng:
        return "ERROR:GPS_TIDAK_ADA|Pastikan LocationSensor aktif"

    # ── MODE 2: Koordinat langsung ──────────────────
    if dlat and dlng:
        try:
            tujuan_lat = float(dlat)
            tujuan_lng = float(dlng)
        except:
            return "ERROR:FORMAT_KOORDINAT|Contoh: dlat=-7.7123&dlng=109.5678"

        try:
            rute = get_instruksi(float(lat), float(lng), tujuan_lat, tujuan_lng)
            return parse_rute(rute, dest)
        except Exception as e:
            return f"ERROR:ROUTE|{str(e)[:100]}"

    # ── MODE 1: Cari nama tempat ────────────────────
    if not dest or dest == 'Tujuan':
        return "ERROR:TUJUAN_KOSONG|Isi dest= atau dlat=&dlng="

    geo = geocode(dest)
    if not geo:
        return (f"ERROR:LOKASI_TIDAK_DITEMUKAN|{dest}|"
                f"Coba pakai koordinat langsung: "
                f"&dlat=KOORDINAT_LAT&dlng=KOORDINAT_LNG")

    try:
        rute = get_instruksi(float(lat), float(lng), geo['lat'], geo['lng'])
        return parse_rute(rute, geo['nama'])
    except Exception as e:
        return f"ERROR:ROUTE|{str(e)[:100]}"


# ═══════════════════════════════════════════════════
#  ENDPOINT: /cari — test geocoding
# ═══════════════════════════════════════════════════
@app.route('/cari')
def cari():
    q = request.args.get('q', '')
    if not q:
        return "Tulis: /cari?q=nama_tempat"
    geo = geocode(q)
    if not geo:
        return f"TIDAK_DITEMUKAN|{q}|Pakai koordinat manual dari Google Maps"
    return f"DITEMUKAN|lat={geo['lat']}|lng={geo['lng']}|nama={geo['nama']}"


# ═══════════════════════════════════════════════════
#  ENDPOINT: /test — cek server
# ═══════════════════════════════════════════════════
@app.route('/test')
def test():
    return "SERVER_OK|Mode:NamaTempat+KoordinatLangsung|NoAPIKey"


# if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
