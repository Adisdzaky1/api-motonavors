from flask import Flask, request
import requests, urllib.parse

app = Flask(__name__)

# Tidak perlu API key apapun!
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL      = "https://router.project-osrm.org/route/v1/driving"

HEADERS = {'User-Agent': 'MotoNavApp/1.0'}

# ─────────────────────────────────────────
#  GEOCODING — cari koordinat dari nama tempat
# ─────────────────────────────────────────
def geocode(nama_tempat):
    # Coba 3 variasi query agar lebih akurat
    queries = [
        nama_tempat + ", Indonesia",
        nama_tempat + ", Jawa Tengah, Indonesia",
        nama_tempat,
    ]
    for q in queries:
        try:
            r = requests.get(
                NOMINATIM_URL,
                params={'q': q, 'format': 'json', 'limit': 1, 'countrycodes': 'id'},
                headers=HEADERS,
                timeout=10
            )
            data = r.json()
            if data:
                return {
                    'lat':  float(data[0]['lat']),
                    'lng':  float(data[0]['lon']),
                    'nama': data[0].get('display_name', nama_tempat).split(',')[0],
                    'query_used': q
                }
        except:
            continue
    return None


# ─────────────────────────────────────────
#  ROUTING — ambil instruksi langkah pertama
# ─────────────────────────────────────────
def get_route(lat_asal, lng_asal, lat_tujuan, lng_tujuan):
    url = f"{OSRM_URL}/{lng_asal},{lat_asal};{lng_tujuan},{lat_tujuan}"
    r = requests.get(
        url,
        params={'steps': 'true', 'language': 'id', 'overview': 'false'},
        headers=HEADERS,
        timeout=10
    )
    return r.json()


# ─────────────────────────────────────────
#  DETEKSI ARAH dari teks instruksi
# ─────────────────────────────────────────
def deteksi_arah(instruksi):
    s = instruksi.lower()
    if any(k in s for k in ['kanan', 'right', 'turn right']):
        return 'KANAN'
    elif any(k in s for k in ['kiri', 'left', 'turn left']):
        return 'KIRI'
    elif any(k in s for k in ['tiba', 'sampai', 'destination', 'arrive', 'you have arrived']):
        return 'TIBA'
    elif any(k in s for k in ['putar balik', 'u-turn', 'uturn', 'balik']):
        return 'BALIK'
    elif any(k in s for k in ['bundaran', 'roundabout', 'rotary']):
        return 'BUNDARAN'
    else:
        return 'LURUS'


# ─────────────────────────────────────────
#  FORMAT JARAK
# ─────────────────────────────────────────
def format_jarak(meter):
    if meter >= 1000:
        return f"{meter/1000:.1f}km"
    return f"{int(meter)}m"


# ═════════════════════════════════════════
#  ENDPOINT UTAMA: /nav
# ═════════════════════════════════════════
@app.route('/nav')
def nav():
    lat  = request.args.get('lat', '')
    lng  = request.args.get('lng', '')
    dest = request.args.get('dest', '')

    if not lat or not lng or not dest:
        return f"ERROR:PARAM_KOSONG|lat={lat}|lng={lng}|dest={dest}"

    # Step 1: Geocode tujuan
    geo = geocode(dest)
    if not geo:
        return f"ERROR:LOKASI_TIDAK_DITEMUKAN|{dest}|Coba tulis lebih lengkap contoh: Pantai Sekar Hastina, Kebumen"

    # Step 2: Ambil rute via OSRM
    try:
        rute = get_route(float(lat), float(lng), geo['lat'], geo['lng'])

        if rute.get('code') != 'Ok':
            return f"ERROR:RUTE_GAGAL|{rute.get('message','unknown')}"

        langkah = rute['routes'][0]['legs'][0]['steps'][0]
        instruksi = langkah.get('name', '') + ' ' + str(langkah.get('maneuver', {}).get('type', ''))
        
        # OSRM pakai maneuver type
        maneuver  = langkah.get('maneuver', {})
        man_type  = maneuver.get('type', '')
        man_mod   = maneuver.get('modifier', '')
        
        instruksi_full = f"{man_type} {man_mod}"
        jarak  = langkah.get('distance', 0)
        jalan  = langkah.get('name', geo['nama'])

        # Deteksi arah dari maneuver
        if 'right' in man_mod:
            arah = 'KANAN'
        elif 'left' in man_mod:
            arah = 'KIRI'
        elif man_type in ['arrive', 'end of road']:
            arah = 'TIBA'
        elif 'uturn' in man_mod or man_type == 'roundabout':
            arah = 'BALIK'
        else:
            arah = 'LURUS'

        jarak_str = format_jarak(jarak)
        jalan_str = jalan[:20] if jalan else geo['nama'][:20]

        return f"NAV:{arah}:{jarak_str}:{jalan_str}"

    except requests.exceptions.Timeout:
        return "ERROR:ROUTE_TIMEOUT"
    except Exception as e:
        return f"ERROR:ROUTE|{str(e)[:120]}"


# ═════════════════════════════════════════
#  ENDPOINT: /cari — test geocoding saja
# ═════════════════════════════════════════
@app.route('/cari')
def cari():
    dest = request.args.get('q', '')
    if not dest:
        return "ERROR:TULIS ?q=nama_tempat"

    geo = geocode(dest)
    if not geo:
        return f"TIDAK_DITEMUKAN|{dest}"

    return (f"DITEMUKAN|"
            f"lat={geo['lat']}|"
            f"lng={geo['lng']}|"
            f"nama={geo['nama']}|"
            f"query={geo['query_used']}")


# ═════════════════════════════════════════
#  ENDPOINT: /test — cek server hidup
# ═════════════════════════════════════════
@app.route('/test')
def test():
    return "SERVER_OK|Engine:OSRM+Nominatim|NoAPIKey"


# ═════════════════════════════════════════
#  ENDPOINT: /testroute — test rute lengkap
# ═════════════════════════════════════════
@app.route('/testroute')
def testroute():
    # Dari pusat Kebumen ke Pantai Sekar Hastina
    geo = geocode("Pantai Sekar Hastina Kebumen")
    if not geo:
        return "GEOCODE_GAGAL"

    try:
        rute = get_route(-7.6733, 109.6519, geo['lat'], geo['lng'])
        if rute.get('code') != 'Ok':
            return f"ROUTE_GAGAL|{rute}"

        step = rute['routes'][0]['legs'][0]['steps'][0]
        total_km = rute['routes'][0]['distance'] / 1000
        durasi_menit = rute['routes'][0]['duration'] / 60

        return (f"ROUTE_OK|"
                f"tujuan={geo['nama']}|"
                f"total={total_km:.1f}km|"
                f"estimasi={durasi_menit:.0f}menit|"
                f"step1_type={step.get('maneuver',{}).get('type','')}|"
                f"step1_jalan={step.get('name','?')}|"
                f"step1_jarak={format_jarak(step.get('distance',0))}")
    except Exception as e:
        return f"ERROR|{str(e)}"


#if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
