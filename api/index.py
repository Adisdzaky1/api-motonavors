from flask import Flask, request
import requests, math

app = Flask(__name__)

ORS_KEY       = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImMyZjlmYTk3YWYxODQyNmQ5YzUxZDkxMGFhYzA2OGMxIiwiaCI6Im11cm11cjY0In0="
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Pakai endpoint /geojson agar koordinat langsung tersedia
ORS_GEOJSON_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
HEADERS_NOM   = {'User-Agent': 'MotoNavApp/1.0'}
TIBA_RADIUS_M = 8


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


# ── Request ORS pakai endpoint /geojson ─────────────
# Format ini langsung return koordinat array tanpa encoding
def get_rute_ors(lat_a, lng_a, lat_t, lng_t):
    return requests.post(
        ORS_GEOJSON_URL,
        headers={'Authorization': ORS_KEY, 'Content-Type': 'application/json'},
        json={
            "coordinates":  [[lng_a, lat_a], [lng_t, lat_t]],
            "language":     "en",
            "instructions": True
        },
        timeout=15
    )


# ── Deteksi arah dari step type ORS ─────────────────
# ORS step type: 0=left 1=right 2=sharp_left 3=sharp_right
# 4=slight_left 5=slight_right 6=straight 7=roundabout
# 10=arrive 11=depart
def deteksi_arah(instruksi, step_type):
    if step_type in [0, 2, 4]:
        return 'KIRI'
    if step_type in [1, 3, 5]:
        return 'KANAN'
    if step_type == 7:
        return 'BUNDARAN'
    if step_type == 10:
        return 'TIBA'
    # Fallback teks
    s = instruksi.lower()
    if any(k in s for k in ['turn right', 'sharp right', 'slight right']):
        return 'KANAN'
    if any(k in s for k in ['turn left', 'sharp left', 'slight left']):
        return 'KIRI'
    if any(k in s for k in ['u-turn', 'uturn', 'balik']):
        return 'BALIK'
    if any(k in s for k in ['arrive', 'destination']):
        return 'TIBA'
    return 'LURUS'


# ── Cari step aktif pakai way_points + GeoJSON coords ─
def cari_step_aktif(steps, lat_u, lng_u, geo_coords):
    best_step = None
    best_dist = float('inf')
    best_idx  = 0

    for i, step in enumerate(steps):
        wp_list = step.get('way_points', [])
        if not wp_list:
            continue
        wp_idx = wp_list[0]
        if wp_idx < len(geo_coords):
            # GeoJSON coordinates = [lng, lat]
            s_lng = geo_coords[wp_idx][0]
            s_lat = geo_coords[wp_idx][1]
            dist  = haversine(lat_u, lng_u, s_lat, s_lng)
            if dist < best_dist:
                best_dist = dist
                best_step = step
                best_idx  = i

    return best_step, best_dist, best_idx


# ── Parse respons GeoJSON ORS ─────────────────────────
def parse_rute(resp, nama_tujuan, lat_u, lng_u, lat_t, lng_t):
    if haversine(lat_u, lng_u, lat_t, lng_t) <= TIBA_RADIUS_M:
        return f"NAV:TIBA:0m:{nama_tujuan[:20]}"

    if resp.status_code != 200:
        return f"ERROR:ORS_{resp.status_code}|{resp.text[:80]}"

    data = resp.json()

    # GeoJSON response struktur berbeda dari JSON biasa
    # features[0].properties.segments[0].steps
    # features[0].geometry.coordinates = array koordinat
    try:
        props     = data['features'][0]['properties']
        geo_coords = data['features'][0]['geometry']['coordinates']
        steps     = props['segments'][0]['steps']
        total_m   = props['summary']['distance']
    except (KeyError, IndexError) as e:
        return f"ERROR:PARSE_STRUKTUR|{str(e)}"

    if total_m <= TIBA_RADIUS_M:
        return f"NAV:TIBA:0m:{nama_tujuan[:20]}"

    # Cari step aktif
    step_aktif, dist_ke_step, step_idx = cari_step_aktif(
        steps, lat_u, lng_u, geo_coords
    )
    if not step_aktif:
        step_aktif = steps[0]
        step_idx   = 0

    step_type = step_aktif.get('type', 6)
    instruksi = step_aktif.get('instruction', '')
    jarak     = step_aktif.get('distance', 0)
    jalan     = step_aktif.get('name', '') or nama_tujuan

    # Skip step DEPART → ambil step berikutnya
    if step_type == 11 and step_idx + 1 < len(steps):
        nxt       = steps[step_idx + 1]
        step_type = nxt.get('type', 6)
        instruksi = nxt.get('instruction', '')
        jarak     = nxt.get('distance', 0)
        jalan     = nxt.get('name', '') or nama_tujuan

    arah = deteksi_arah(instruksi, step_type)

    # Guard TIBA prematur — kalau ORS bilang TIBA tapi masih jauh
    if arah == 'TIBA':
        sisa = haversine(lat_u, lng_u, lat_t, lng_t)
        if sisa > 50:
            arah  = 'LURUS'
            jarak = sisa

    return f"NAV:{arah}:{fmt_jarak(jarak)}:{jalan[:20]}"


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
        if haversine(lat_f, lng_f, tlat, tlng) <= TIBA_RADIUS_M:
            return f"NAV:TIBA:0m:{dest[:20]}"
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
    if haversine(lat_f, lng_f, geo['lat'], geo['lng']) <= TIBA_RADIUS_M:
        return f"NAV:TIBA:0m:{geo['nama'][:20]}"
    try:
        resp = get_rute_ors(lat_f, lng_f, geo['lat'], geo['lng'])
        return parse_rute(resp, geo['nama'], lat_f, lng_f, geo['lat'], geo['lng'])
    except requests.exceptions.Timeout:
        return "ERROR:TIMEOUT"
    except Exception as e:
        return f"ERROR:ROUTE|{str(e)[:100]}"


# ═══════════════════════════════════════════════════
#  ENDPOINT: /debug
# ═══════════════════════════════════════════════════
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

        hasil  = f"TOTAL:{fmt_jarak(total)}|STEPS:{len(steps)}|GEO_COORDS:{len(geo_c)}\n---\n"
        for i, s in enumerate(steps):
            arah = deteksi_arah(s.get('instruction',''), s.get('type', 6))
            hasil += (f"[{i}] type={s.get('type')} arah={arah} "
                      f"jarak={fmt_jarak(s.get('distance',0))} "
                      f"wp={s.get('way_points',[])} "
                      f"ins={s.get('instruction','?')[:50]}\n")
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
    return f"SERVER_OK|Fix:GeoJSON-endpoint|KeyLen:{len(ORS_KEY)}"

@app.route('/testors')
def testors():
    try:
        r = requests.get("https://api.openrouteservice.org/geocode/search",
            params={'api_key': ORS_KEY, 'text': 'Jakarta', 'size': 1}, timeout=10)
        return f"ORS_{'VALID' if r.status_code==200 else 'INVALID'}|{r.status_code}"
    except Exception as e:
        return f"ORS_ERROR|{e}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
