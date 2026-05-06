from flask import Flask, request
import requests, math

app = Flask(__name__)

ORS_KEY       = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImMyZjlmYTk3YWYxODQyNmQ5YzUxZDkxMGFhYzA2OGMxIiwiaCI6Im11cm11cjY0In0="
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ORS_ROUTE_URL = "https://api.openrouteservice.org/v2/directions/driving-car/json"
HEADERS_NOM   = {'User-Agent': 'MotoNavApp/1.0'}

TIBA_RADIUS_M = 8   # Jarak ≤ 8 meter → TIBA


# ── Hitung jarak 2 koordinat (meter) ───────────────
def haversine(lat1, lng1, lat2, lng2):
    R = 6371000
    p  = math.pi / 180
    a  = (math.sin((lat2 - lat1) * p / 2) ** 2 +
          math.cos(lat1 * p) * math.cos(lat2 * p) *
          math.sin((lng2 - lng1) * p / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


# ── Format jarak ────────────────────────────────────
def fmt_jarak(meter):
    if meter >= 1000:
        return f"{meter/1000:.1f}km"
    return f"{int(meter)}m"


# ── Geocode via Nominatim ────────────────────────────
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


# ── Ambil rute dari ORS ──────────────────────────────
def get_rute_ors(lat_a, lng_a, lat_t, lng_t):
    return requests.post(ORS_ROUTE_URL,
        headers={'Authorization': ORS_KEY, 'Content-Type': 'application/json'},
        json={
            "coordinates": [[lng_a, lat_a], [lng_t, lat_t]],
            "language":    "en",        # English lebih konsisten untuk parsing
            "instructions": True,
            "geometry":    True         # butuh koordinat tiap step
        },
        timeout=15)


# ── Deteksi arah dari teks instruksi ────────────────
def deteksi_arah(instruksi):
    s = instruksi.lower()
    # Belok kanan
    if any(k in s for k in ['turn right', 'keep right', 'exit right',
                              'kanan', 'right']):
        return 'KANAN'
    # Belok kiri
    if any(k in s for k in ['turn left', 'keep left', 'exit left',
                              'kiri', 'left']):
        return 'KIRI'
    # Putar balik
    if any(k in s for k in ['u-turn', 'uturn', 'make a u', 'putar balik', 'balik']):
        return 'BALIK'
    # Bundaran
    if any(k in s for k in ['roundabout', 'rotary', 'bundaran']):
        if 'right' in s:
            return 'KANAN'
        elif 'left' in s:
            return 'KIRI'
        return 'BUNDARAN'
    # Tiba
    if any(k in s for k in ['arrive', 'destination', 'you have arrived',
                              'tiba', 'sampai', 'reached']):
        return 'TIBA'
    # Lurus / default
    return 'LURUS'


# ── Cari step terdekat dengan posisi sekarang ────────
# ORS menyediakan way_points per step
# Kita cari step yang paling dekat dengan posisi user
def cari_step_aktif(steps, lat_user, lng_user, geometry_coords):
    """
    Cari step yang sedang aktif berdasarkan:
    1. Jarak user ke titik awal setiap step
    2. Ambil step dengan jarak terpendek
    3. Skip step tipe 'depart'
    """
    best_step  = None
    best_dist  = float('inf')
    best_index = 0

    for i, step in enumerate(steps):
        # way_points berisi index koordinat di geometry
        wp = step.get('way_points', [0])
        wp_index = wp[0] if wp else 0

        if wp_index < len(geometry_coords):
            coord  = geometry_coords[wp_index]
            # ORS: koordinat dalam format [lng, lat]
            s_lng  = coord[0]
            s_lat  = coord[1]
            dist   = haversine(lat_user, lng_user, s_lat, s_lng)

            if dist < best_dist:
                best_dist  = dist
                best_step  = step
                best_index = i

    return best_step, best_dist, best_index


# ── Parse hasil ORS + posisi user ───────────────────
def parse_ors_dengan_posisi(resp, nama_tujuan, lat_user, lng_user, lat_tujuan, lng_tujuan):

    # Cek apakah sudah sampai tujuan (≤ 8 meter)
    jarak_ke_tujuan = haversine(lat_user, lng_user, lat_tujuan, lng_tujuan)
    if jarak_ke_tujuan <= TIBA_RADIUS_M:
        return f"NAV:TIBA:0m:{nama_tujuan[:20]}"

    if resp.status_code != 200:
        return f"ERROR:ORS_{resp.status_code}|{resp.text[:80]}"

    data = resp.json()
    if not data.get('routes'):
        return "ERROR:RUTE_KOSONG"

    route    = data['routes'][0]
    steps    = route['segments'][0]['steps']
    geometry = route.get('geometry', {}).get('coordinates', [])

    # Cek total jarak tersisa
    total_jarak_m = route.get('summary', {}).get('distance', 0)
    if total_jarak_m <= TIBA_RADIUS_M:
        return f"NAV:TIBA:0m:{nama_tujuan[:20]}"

    # Cari step aktif berdasarkan posisi user
    step_aktif, dist_ke_step, step_idx = cari_step_aktif(
        steps, lat_user, lng_user, geometry
    )

    if not step_aktif:
        step_aktif = steps[0]

    instruksi = step_aktif.get('instruction', '')
    jarak     = step_aktif.get('distance', 0)
    jalan     = step_aktif.get('name', '') or nama_tujuan

    # Jika step aktif adalah step terakhir atau instruksi "arrive"
    step_type = step_aktif.get('type', 0)
    # ORS step type 10 = arrive/destination
    if step_type == 10 or 'arrive' in instruksi.lower():
        return f"NAV:TIBA:{fmt_jarak(jarak)}:{jalan[:20]}"

    # Skip step depart (type 11) — ambil step berikutnya
    if step_type == 11 and step_idx + 1 < len(steps):
        step_aktif = steps[step_idx + 1]
        instruksi  = step_aktif.get('instruction', '')
        jarak      = step_aktif.get('distance', 0)
        jalan      = step_aktif.get('name', '') or nama_tujuan

    arah = deteksi_arah(instruksi)
    return f"NAV:{arah}:{fmt_jarak(jarak)}:{jalan[:20]}"


# ═══════════════════════════════════════════════════════
#  ENDPOINT UTAMA: /nav
#
#  Mode 1 — Nama tempat:
#    /nav?lat=X&lng=Y&dest=Nama+Tempat
#
#  Mode 2 — Koordinat langsung dari Google Maps:
#    /nav?lat=X&lng=Y&dlat=A&dlng=B&dest=Label
# ═══════════════════════════════════════════════════════
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

    # ── MODE 2: Koordinat langsung ──────────────────
    if dlat and dlng:
        try:
            tujuan_lat = float(dlat)
            tujuan_lng = float(dlng)
        except:
            return "ERROR:FORMAT_KOORDINAT"

        # Cek jarak ke tujuan dulu sebelum request ke ORS
        jarak_sisa = haversine(lat_f, lng_f, tujuan_lat, tujuan_lng)
        if jarak_sisa <= TIBA_RADIUS_M:
            return f"NAV:TIBA:0m:{dest[:20]}"

        try:
            resp = get_rute_ors(lat_f, lng_f, tujuan_lat, tujuan_lng)
            return parse_ors_dengan_posisi(resp, dest, lat_f, lng_f, tujuan_lat, tujuan_lng)
        except requests.exceptions.Timeout:
            return "ERROR:TIMEOUT"
        except Exception as e:
            return f"ERROR:ROUTE|{str(e)[:80]}"

    # ── MODE 1: Nama tempat ─────────────────────────
    if not dest or dest == 'Tujuan':
        return "ERROR:TUJUAN_KOSONG"

    geo = geocode(dest)
    if not geo:
        return f"ERROR:TIDAK_DITEMUKAN|{dest}|Coba isi koordinat manual: dlat=XX&dlng=YY"

    jarak_sisa = haversine(lat_f, lng_f, geo['lat'], geo['lng'])
    if jarak_sisa <= TIBA_RADIUS_M:
        return f"NAV:TIBA:0m:{geo['nama'][:20]}"

    try:
        resp = get_rute_ors(lat_f, lng_f, geo['lat'], geo['lng'])
        return parse_ors_dengan_posisi(resp, geo['nama'], lat_f, lng_f, geo['lat'], geo['lng'])
    except requests.exceptions.Timeout:
        return "ERROR:TIMEOUT"
    except Exception as e:
        return f"ERROR:ROUTE|{str(e)[:80]}"


# ═══════════════════════════════════════════════════════
#  ENDPOINT: /debug — lihat raw data ORS untuk diagnosa
#  /debug?lat=X&lng=Y&dlat=A&dlng=B
# ═══════════════════════════════════════════════════════
@app.route('/debug')
def debug():
    lat  = request.args.get('lat',  '-7.6733')
    lng  = request.args.get('lng',  '109.6519')
    dlat = request.args.get('dlat', '-7.7234')
    dlng = request.args.get('dlng', '109.5891')

    try:
        resp = get_rute_ors(float(lat), float(lng), float(dlat), float(dlng))
        if resp.status_code != 200:
            return f"ORS_ERROR_{resp.status_code}|{resp.text[:200]}"

        data  = resp.json()
        steps = data['routes'][0]['segments'][0]['steps']
        total = data['routes'][0]['summary']['distance']

        # Tampilkan semua step
        hasil = f"TOTAL_JARAK:{fmt_jarak(total)}\n"
        hasil += f"JUMLAH_STEP:{len(steps)}\n"
        hasil += "---\n"
        for i, s in enumerate(steps):
            hasil += (f"[{i}] type={s.get('type')} "
                      f"jarak={fmt_jarak(s.get('distance',0))} "
                      f"instruksi={s.get('instruction','?')[:60]}"
                      f" jalan={s.get('name','?')}\n")
        return hasil

    except Exception as e:
        return f"ERROR|{str(e)}"


# ── Endpoint lain ────────────────────────────────────
@app.route('/cari')
def cari():
    q = request.args.get('q', '')
    if not q: return "Tulis: /cari?q=nama_tempat"
    geo = geocode(q)
    if not geo: return f"TIDAK_DITEMUKAN|{q}"
    return f"DITEMUKAN|lat={geo['lat']}|lng={geo['lng']}|nama={geo['nama']}"

@app.route('/test')
def test():
    return f"SERVER_OK|ORS+Nominatim|Tiba<=8m|KeyLen:{len(ORS_KEY)}"

@app.route('/testors')
def testors():
    try:
        r = requests.get("https://api.openrouteservice.org/geocode/search",
            params={'api_key': ORS_KEY, 'text': 'Jakarta', 'size': 1}, timeout=10)
        return f"ORS_{'VALID' if r.status_code==200 else 'INVALID'}|{r.status_code}"
    except Exception as e:
        return f"ORS_ERROR|{e}"

# if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
