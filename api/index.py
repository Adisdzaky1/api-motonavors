from flask import Flask, request
import requests, math

app = Flask(__name__)

ORS_KEY         = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImMyZjlmYTk3YWYxODQyNmQ5YzUxZDkxMGFhYzA2OGMxIiwiaCI6Im11cm11cjY0In0="
NOMINATIM_URL   = "https://nominatim.openstreetmap.org/search"
ORS_GEOJSON_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
HEADERS_NOM     = {'User-Agent': 'MotoNavApp/1.0'}

TIBA_RADIUS_M   = 10    # ≤ 8m dari tujuan → TIBA
BELOK_RADIUS_M  = 15   # ≤ 15m dari titik belok → tampilkan instruksi belok


def haversine(lat1, lng1, lat2, lng2):
    R = 6371000
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) *
         math.sin((lng2 - lng1) * p / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


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
    return requests.post(ORS_GEOJSON_URL,
        headers={'Authorization': ORS_KEY, 'Content-Type': 'application/json'},
        json={"coordinates": [[lng_a, lat_a], [lng_t, lat_t]],
              "language": "en", "instructions": True},
        timeout=15)


# ── Deteksi arah dari step type ──────────────────────
def deteksi_arah(instruksi, step_type):
    if step_type in [0, 2, 4]:   return 'KIRI'
    if step_type in [1, 3, 5]:   return 'KANAN'
    if step_type == 7:            return 'BUNDARAN'
    if step_type == 10:           return 'TIBA'
    s = instruksi.lower()
    if any(k in s for k in ['turn right', 'sharp right', 'slight right']): return 'KANAN'
    if any(k in s for k in ['turn left',  'sharp left',  'slight left']):  return 'KIRI'
    if any(k in s for k in ['u-turn', 'uturn']):                           return 'BALIK'
    if any(k in s for k in ['arrive', 'destination']):                     return 'TIBA'
    return 'LURUS'


# ════════════════════════════════════════════════════
#  LOGIKA UTAMA — Seperti Google Maps
#
#  1. Cari semua "titik belok" (step yang bukan LURUS/DEPART)
#  2. Dari semua titik belok, cari yang paling dekat DI DEPAN user
#  3. Hitung jarak user → titik belok tersebut
#  4. Jika jarak > BELOK_RADIUS_M → tampilkan LURUS + jarak ke belok
#  5. Jika jarak ≤ BELOK_RADIUS_M → tampilkan KANAN/KIRI sekarang!
# ════════════════════════════════════════════════════
def hitung_instruksi(steps, geo_coords, lat_u, lng_u, lat_t, lng_t, nama_tujuan):

    # Cek apakah sudah sampai tujuan
    jarak_tujuan = haversine(lat_u, lng_u, lat_t, lng_t)
    if jarak_tujuan <= TIBA_RADIUS_M:
        return f"NAV:TIBA:0m:{nama_tujuan[:20]}"

    # ── Kumpulkan semua step dengan koordinat titik beloknya ──
    step_data = []
    for step in steps:
        step_type = step.get('type', 6)
        if step_type == 11:      # skip depart
            continue

        wp_list = step.get('way_points', [])
        if not wp_list:
            continue
        wp_idx = wp_list[0]
        if wp_idx >= len(geo_coords):
            continue

        # Koordinat titik awal step ini
        coord   = geo_coords[wp_idx]
        s_lng   = coord[0]
        s_lat   = coord[1]

        # Jarak dari user ke titik ini
        jarak_ke_step = haversine(lat_u, lng_u, s_lat, s_lng)

        step_data.append({
            'type':      step_type,
            'instruksi': step.get('instruction', ''),
            'jarak_step': step.get('distance', 0),
            'jalan':     step.get('name', '') or nama_tujuan,
            'lat':       s_lat,
            'lng':       s_lng,
            'jarak_dari_user': jarak_ke_step
        })

    if not step_data:
        return f"NAV:LURUS:{fmt_jarak(jarak_tujuan)}:{nama_tujuan[:20]}"

    # ── Cari step belok TERDEKAT yang masih di DEPAN user ──
    # "Di depan" = step dengan jarak_dari_user terkecil
    # Sort berdasarkan jarak dari user
    step_data.sort(key=lambda x: x['jarak_dari_user'])
    step_terdekat = step_data[0]

    arah_terdekat = deteksi_arah(
        step_terdekat['instruksi'],
        step_terdekat['type']
    )
    jarak_ke_belok = step_terdekat['jarak_dari_user']
    jalan          = step_terdekat['jalan']

    # ── Logika tampilan seperti Google Maps ──────────
    if arah_terdekat == 'TIBA':
        # Step arrive, cek jarak ke tujuan
        if jarak_tujuan <= TIBA_RADIUS_M:
            return f"NAV:TIBA:0m:{nama_tujuan[:20]}"
        else:
            return f"NAV:LURUS:{fmt_jarak(jarak_tujuan)}:{nama_tujuan[:20]}"

    if jarak_ke_belok <= BELOK_RADIUS_M:
        # ✅ SUDAH DEKAT titik belok → tampilkan instruksi belok
        return f"NAV:{arah_terdekat}:{fmt_jarak(jarak_ke_belok)}:{jalan[:20]}"
    else:
        # 🔵 MASIH JAUH → tampilkan LURUS + jarak ke titik belok berikutnya
        # Ini seperti Google Maps: "Lurus 500m kemudian belok kanan"
        return f"NAV:LURUS:{fmt_jarak(jarak_ke_belok)}:{jalan[:20]}"


# ── Parse respons GeoJSON ORS ─────────────────────────
def parse_rute(resp, nama_tujuan, lat_u, lng_u, lat_t, lng_t):
    if haversine(lat_u, lng_u, lat_t, lng_t) <= TIBA_RADIUS_M:
        return f"NAV:TIBA:0m:{nama_tujuan[:20]}"

    if resp.status_code != 200:
        return f"ERROR:ORS_{resp.status_code}|{resp.text[:80]}"

    data = resp.json()
    try:
        props      = data['features'][0]['properties']
        geo_coords = data['features'][0]['geometry']['coordinates']
        steps      = props['segments'][0]['steps']
    except (KeyError, IndexError) as e:
        return f"ERROR:PARSE|{str(e)}"

    return hitung_instruksi(steps, geo_coords, lat_u, lng_u, lat_t, lng_t, nama_tujuan)


# ═══════════════════════════════════════════════════
#  ENDPOINT: /nav
# ═══════════════════════════════════════════════════
@app.route('/nav')
def nav():
    lat  = request.args.get('lat',  '')
    lng  = request.args.get('lng',  '')
    dest = request.args.get('dest', 'Tujuan')
    dlat = request.args.get('dlat', '')
    dlng = request.args.get('dlng', '')

    if not lat or not lng:
        return "ERROR:GPS_KOSONG"
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except:
        return "ERROR:FORMAT_GPS"

    # Mode 2 — koordinat langsung
    if dlat and dlng:
        try:
            tlat = float(dlat)
            tlng = float(dlng)
        except:
            return "ERROR:FORMAT_KOORDINAT"
        try:
            resp = get_rute_ors(lat_f, lng_f, tlat, tlng)
            return parse_rute(resp, dest, lat_f, lng_f, tlat, tlng)
        except requests.exceptions.Timeout:
            return "ERROR:TIMEOUT"
        except Exception as e:
            return f"ERROR:ROUTE|{str(e)[:100]}"

    # Mode 1 — nama tempat
    if not dest or dest == 'Tujuan':
        return "ERROR:TUJUAN_KOSONG"
    geo = geocode(dest)
    if not geo:
        return f"ERROR:TIDAK_DITEMUKAN|{dest}"
    try:
        resp = get_rute_ors(lat_f, lng_f, geo['lat'], geo['lng'])
        return parse_rute(resp, geo['nama'], lat_f, lng_f, geo['lat'], geo['lng'])
    except requests.exceptions.Timeout:
        return "ERROR:TIMEOUT"
    except Exception as e:
        return f"ERROR:ROUTE|{str(e)[:100]}"


# ── Endpoints lain ───────────────────────────────────
@app.route('/debug')
def debug():
    lat  = request.args.get('lat',  '-7.6733')
    lng  = request.args.get('lng',  '109.6519')
    dlat = request.args.get('dlat', '-7.418619')
    dlng = request.args.get('dlng', '109.236737')
    try:
        resp = get_rute_ors(float(lat), float(lng), float(dlat), float(dlng))
        if resp.status_code != 200:
            return f"ORS_ERROR_{resp.status_code}|{resp.text[:300]}"
        data      = resp.json()
        props     = data['features'][0]['properties']
        steps     = props['segments'][0]['steps']
        geo_c     = data['features'][0]['geometry']['coordinates']
        total     = props['summary']['distance']
        hasil = f"TOTAL:{fmt_jarak(total)}|STEPS:{len(steps)}\n---\n"
        for i, s in enumerate(steps):
            arah = deteksi_arah(s.get('instruction',''), s.get('type',6))
            wp = s.get('way_points',[])
            jarak_user = 0
            if wp and wp[0] < len(geo_c):
                c = geo_c[wp[0]]
                jarak_user = haversine(float(lat), float(lng), c[1], c[0])
            hasil += (f"[{i}] type={s.get('type')} arah={arah} "
                      f"jarak_step={fmt_jarak(s.get('distance',0))} "
                      f"jarak_dari_user={fmt_jarak(jarak_user)} "
                      f"ins={s.get('instruction','?')[:45]}\n")
        return hasil
    except Exception as e:
        return f"ERROR|{str(e)}"

@app.route('/cari')
def cari():
    q = request.args.get('q', '')
    if not q: return "Tulis: /cari?q=nama_tempat"
    geo = geocode(q)
    return f"DITEMUKAN|lat={geo['lat']}|lng={geo['lng']}|nama={geo['nama']}" if geo else f"TIDAK_DITEMUKAN|{q}"

@app.route('/test')
def test():
    return f"SERVER_OK|Logika:GoogleMaps-style|Belok<={BELOK_RADIUS_M}m|Tiba<={TIBA_RADIUS_M}m|KeyLen:{len(ORS_KEY)}"

@app.route('/testors')
def testors():
    try:
        r = requests.get("https://api.openrouteservice.org/geocode/search",
            params={'api_key': ORS_KEY, 'text': 'Jakarta', 'size': 1}, timeout=10)
        return f"ORS_{'VALID' if r.status_code==200 else 'INVALID'}|{r.status_code}"
    except Exception as e:
        return f"ORS_ERROR|{e}"

#if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
