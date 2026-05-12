from flask import Flask, request
import requests, math

app = Flask(__name__)

ORS_KEY         = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImMyZjlmYTk3YWYxODQyNmQ5YzUxZDkxMGFhYzA2OGMxIiwiaCI6Im11cm11cjY0In0="
NOMINATIM_URL   = "https://nominatim.openstreetmap.org/search"
ORS_GEOJSON_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
OSRM_URL        = "https://router.project-osrm.org/route/v1/driving"
HEADERS_NOM     = {'User-Agent': 'MotoNavApp/1.0'}

TIBA_RADIUS_M   = 8
BELOK_RADIUS_M  = 15


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


# ════════════════════════════════════════════════════
#  ENGINE ORS
# ════════════════════════════════════════════════════
def get_rute_ors(lat_a, lng_a, lat_t, lng_t):
    return requests.post(ORS_GEOJSON_URL,
        headers={'Authorization': ORS_KEY, 'Content-Type': 'application/json'},
        json={"coordinates": [[lng_a, lat_a], [lng_t, lat_t]],
              "language": "en", "instructions": True},
        timeout=15)


def deteksi_arah_ors(instruksi, step_type):
    # ORS step type: 0=kiri 1=kanan 2=tajam kiri 3=tajam kanan
    # 4=sedikit kiri 5=sedikit kanan 6=lurus 7=bundaran 10=tiba 11=depart
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


def parse_ors(resp, nama_tujuan, lat_u, lng_u, lat_t, lng_t):
    if resp.status_code == 429:
        return None  # Signal fallback ke OSRM
    if resp.status_code != 200:
        return f"ERROR:ORS_{resp.status_code}|{resp.text[:60]}"
    data = resp.json()
    try:
        props      = data['features'][0]['properties']
        geo_coords = data['features'][0]['geometry']['coordinates']
        steps      = props['segments'][0]['steps']
    except (KeyError, IndexError) as e:
        return f"ERROR:ORS_PARSE|{str(e)}"

    return hitung_instruksi_ors(steps, geo_coords, lat_u, lng_u, lat_t, lng_t, nama_tujuan)


def hitung_instruksi_ors(steps, geo_coords, lat_u, lng_u, lat_t, lng_t, nama_tujuan):
    jarak_tujuan = haversine(lat_u, lng_u, lat_t, lng_t)
    if jarak_tujuan <= TIBA_RADIUS_M:
        return f"NAV:TIBA:0m:{nama_tujuan[:20]}"

    step_data = []
    for step in steps:
        step_type = step.get('type', 6)
        if step_type == 11: continue
        wp_list = step.get('way_points', [])
        if not wp_list: continue
        wp_idx = wp_list[0]
        if wp_idx >= len(geo_coords): continue
        coord = geo_coords[wp_idx]
        jarak_ke_step = haversine(lat_u, lng_u, coord[1], coord[0])
        step_data.append({
            'type':            step_type,
            'instruksi':       step.get('instruction', ''),
            'jalan':           step.get('name', '') or nama_tujuan,
            'jarak_dari_user': jarak_ke_step
        })

    if not step_data:
        return f"NAV:LURUS:{fmt_jarak(jarak_tujuan)}:{nama_tujuan[:20]}"

    step_data.sort(key=lambda x: x['jarak_dari_user'])
    s    = step_data[0]
    arah = deteksi_arah_ors(s['instruksi'], s['type'])

    if arah == 'TIBA':
        if jarak_tujuan <= TIBA_RADIUS_M:
            return f"NAV:TIBA:0m:{nama_tujuan[:20]}"
        return f"NAV:LURUS:{fmt_jarak(jarak_tujuan)}:{nama_tujuan[:20]}"

    if s['jarak_dari_user'] <= BELOK_RADIUS_M:
        return f"NAV:{arah}:{fmt_jarak(s['jarak_dari_user'])}:{s['jalan'][:20]}"
    return f"NAV:LURUS:{fmt_jarak(s['jarak_dari_user'])}:{s['jalan'][:20]}"


# ════════════════════════════════════════════════════
#  ENGINE OSRM (Gratis, tanpa API key, tanpa limit)
# ════════════════════════════════════════════════════
def get_rute_osrm(lat_a, lng_a, lat_t, lng_t):
    url = f"{OSRM_URL}/{lng_a},{lat_a};{lng_t},{lat_t}"
    return requests.get(url,
        params={
            'steps':     'true',
            'overview':  'full',
            'geometries': 'geojson'  # minta format GeoJSON
        },
        headers=HEADERS_NOM,
        timeout=15)


def deteksi_arah_osrm(maneuver_type, maneuver_modifier):
    t = maneuver_type.lower()
    m = maneuver_modifier.lower() if maneuver_modifier else ''

    if t == 'arrive':
        return 'TIBA'
    if t == 'depart':
        return 'DEPART'
    if t in ['roundabout', 'rotary', 'exit roundabout', 'exit rotary']:
        return 'BUNDARAN'
    if t == 'merge':
        return 'LURUS'
    if t in ['turn', 'end of road', 'fork', 'new name', 'notification',
             'on ramp', 'off ramp', 'continue']:
        if 'right' in m:
            return 'KANAN'
        if 'left' in m:
            return 'KIRI'
        if 'uturn' in m or 'u turn' in m:
            return 'BALIK'
        return 'LURUS'
    return 'LURUS'


def parse_osrm(resp, nama_tujuan, lat_u, lng_u, lat_t, lng_t):
    if resp.status_code != 200:
        return f"ERROR:OSRM_{resp.status_code}"

    data = resp.json()
    if data.get('code') != 'Ok':
        return f"ERROR:OSRM_{data.get('code','?')}"

    try:
        route     = data['routes'][0]
        steps     = route['legs'][0]['steps']
        # Geometry keseluruhan rute
        geo_coords = route['geometry']['coordinates']  # [[lng,lat],...]
    except (KeyError, IndexError) as e:
        return f"ERROR:OSRM_PARSE|{str(e)}"

    return hitung_instruksi_osrm(steps, lat_u, lng_u, lat_t, lng_t, nama_tujuan)


def hitung_instruksi_osrm(steps, lat_u, lng_u, lat_t, lng_t, nama_tujuan):
    jarak_tujuan = haversine(lat_u, lng_u, lat_t, lng_t)
    if jarak_tujuan <= TIBA_RADIUS_M:
        return f"NAV:TIBA:0m:{nama_tujuan[:20]}"

    step_data = []
    for step in steps:
        maneuver = step.get('maneuver', {})
        m_type   = maneuver.get('type', '')
        m_mod    = maneuver.get('modifier', '')
        location = maneuver.get('location', [])  # [lng, lat]

        if not location or len(location) < 2:
            continue

        # OSRM maneuver.location = [lng, lat]
        s_lng = location[0]
        s_lat = location[1]
        jarak_ke_step = haversine(lat_u, lng_u, s_lat, s_lng)

        arah = deteksi_arah_osrm(m_type, m_mod)
        if arah == 'DEPART':
            continue  # skip depart

        jalan = step.get('name', '') or nama_tujuan

        step_data.append({
            'arah':            arah,
            'jalan':           jalan,
            'jarak_dari_user': jarak_ke_step,
            'jarak_step':      step.get('distance', 0)
        })

    if not step_data:
        return f"NAV:LURUS:{fmt_jarak(jarak_tujuan)}:{nama_tujuan[:20]}"

    # Urutkan berdasarkan jarak dari user
    step_data.sort(key=lambda x: x['jarak_dari_user'])
    s    = step_data[0]
    arah = s['arah']

    if arah == 'TIBA':
        if jarak_tujuan <= TIBA_RADIUS_M:
            return f"NAV:TIBA:0m:{nama_tujuan[:20]}"
        return f"NAV:LURUS:{fmt_jarak(jarak_tujuan)}:{nama_tujuan[:20]}"

    if s['jarak_dari_user'] <= BELOK_RADIUS_M:
        return f"NAV:{arah}:{fmt_jarak(s['jarak_dari_user'])}:{s['jalan'][:20]}"
    return f"NAV:LURUS:{fmt_jarak(s['jarak_dari_user'])}:{s['jalan'][:20]}"


# ════════════════════════════════════════════════════
#  ROUTER — pilih engine
#  mode=ors   → pakai ORS saja
#  mode=osrm  → pakai OSRM saja
#  mode=auto  → coba ORS dulu, jika 429 fallback ke OSRM (DEFAULT)
# ════════════════════════════════════════════════════
def navigasi(lat_f, lng_f, tlat, tlng, dest, mode):
    # Cek tiba
    if haversine(lat_f, lng_f, tlat, tlng) <= TIBA_RADIUS_M:
        return f"NAV:TIBA:0m:{dest[:20]}"

    if mode == 'osrm':
        # Langsung OSRM
        try:
            resp = get_rute_osrm(lat_f, lng_f, tlat, tlng)
            return parse_osrm(resp, dest, lat_f, lng_f, tlat, tlng)
        except requests.exceptions.Timeout:
            return "ERROR:OSRM_TIMEOUT"
        except Exception as e:
            return f"ERROR:OSRM|{str(e)[:80]}"

    if mode == 'ors':
        # Langsung ORS
        try:
            resp = get_rute_ors(lat_f, lng_f, tlat, tlng)
            result = parse_ors(resp, dest, lat_f, lng_f, tlat, tlng)
            if result is None:
                return "ERROR:ORS_RATE_LIMIT|Ganti mode=osrm"
            return result
        except requests.exceptions.Timeout:
            return "ERROR:ORS_TIMEOUT"
        except Exception as e:
            return f"ERROR:ORS|{str(e)[:80]}"

    # mode=auto (default) — ORS dulu, fallback OSRM jika 429
    try:
        resp   = get_rute_ors(lat_f, lng_f, tlat, tlng)
        result = parse_ors(resp, dest, lat_f, lng_f, tlat, tlng)

        if result is None:
            # ORS kena rate limit → fallback ke OSRM
            try:
                resp2 = get_rute_osrm(lat_f, lng_f, tlat, tlng)
                return parse_osrm(resp2, dest, lat_f, lng_f, tlat, tlng) + "|OSRM"
            except Exception as e2:
                return f"ERROR:FALLBACK_OSRM|{str(e2)[:60]}"

        return result

    except requests.exceptions.Timeout:
        # ORS timeout → fallback OSRM
        try:
            resp2 = get_rute_osrm(lat_f, lng_f, tlat, tlng)
            return parse_osrm(resp2, dest, lat_f, lng_f, tlat, tlng) + "|OSRM_FALLBACK"
        except:
            return "ERROR:SEMUA_TIMEOUT"
    except Exception as e:
        return f"ERROR:AUTO|{str(e)[:80]}"


# ════════════════════════════════════════════════════
#  ENDPOINT: /nav
#
#  Parameter:
#  lat, lng         = posisi user (GPS HP)
#  dlat, dlng       = koordinat tujuan langsung
#  dest             = nama tujuan (label)
#  mode             = ors | osrm | auto (default: auto)
#
#  Contoh:
#  /nav?lat=-7.67&lng=109.65&dlat=-7.41&dlng=109.23&dest=Pantai&mode=osrm
# ════════════════════════════════════════════════════
@app.route('/nav')
@app.route('/api/nav')
def nav():
    lat  = request.args.get('lat',  '')
    lng  = request.args.get('lng',  '')
    dest = request.args.get('dest', 'Tujuan')
    dlat = request.args.get('dlat', '')
    dlng = request.args.get('dlng', '')
    mode = request.args.get('mode', 'auto').lower()

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
        return navigasi(lat_f, lng_f, tlat, tlng, dest, mode)

    # Mode 1 — nama tempat
    if not dest or dest == 'Tujuan':
        return "ERROR:TUJUAN_KOSONG"
    geo = geocode(dest)
    if not geo:
        return f"ERROR:TIDAK_DITEMUKAN|{dest}"
    return navigasi(lat_f, lng_f, geo['lat'], geo['lng'], geo['nama'], mode)


# ════════════════════════════════════════════════════
#  ENDPOINT: /debug
#  Tambah parameter mode=ors|osrm
# ════════════════════════════════════════════════════
@app.route('/debug')
@app.route('/api/debug')
def debug():
    lat  = request.args.get('lat',  '-7.6733')
    lng  = request.args.get('lng',  '109.6519')
    dlat = request.args.get('dlat', '-7.418619')
    dlng = request.args.get('dlng', '109.236737')
    mode = request.args.get('mode', 'osrm').lower()

    try:
        if mode == 'osrm':
            resp = get_rute_osrm(float(lat), float(lng), float(dlat), float(dlng))
            if resp.status_code != 200:
                return f"OSRM_ERROR_{resp.status_code}|{resp.text[:200]}"
            data  = resp.json()
            steps = data['routes'][0]['legs'][0]['steps']
            total = data['routes'][0]['distance']
            hasil = f"ENGINE:OSRM|TOTAL:{fmt_jarak(total)}|STEPS:{len(steps)}\n---\n"
            for i, s in enumerate(steps):
                m    = s.get('maneuver', {})
                arah = deteksi_arah_osrm(m.get('type',''), m.get('modifier',''))
                loc  = m.get('location', [0, 0])
                jarak_user = haversine(float(lat), float(lng), loc[1], loc[0])
                hasil += (f"[{i}] arah={arah} "
                          f"jarak_step={fmt_jarak(s.get('distance',0))} "
                          f"jarak_dari_user={fmt_jarak(jarak_user)} "
                          f"type={m.get('type','')} mod={m.get('modifier','')} "
                          f"jalan={s.get('name','?')[:30]}\n")
            return hasil
        else:
            resp = get_rute_ors(float(lat), float(lng), float(dlat), float(dlng))
            if resp.status_code != 200:
                return f"ORS_ERROR_{resp.status_code}|{resp.text[:200]}"
            data  = resp.json()
            props = data['features'][0]['properties']
            steps = props['segments'][0]['steps']
            geo_c = data['features'][0]['geometry']['coordinates']
            total = props['summary']['distance']
            hasil = f"ENGINE:ORS|TOTAL:{fmt_jarak(total)}|STEPS:{len(steps)}\n---\n"
            for i, s in enumerate(steps):
                arah = deteksi_arah_ors(s.get('instruction',''), s.get('type',6))
                wp   = s.get('way_points', [])
                jarak_user = 0
                if wp and wp[0] < len(geo_c):
                    c = geo_c[wp[0]]
                    jarak_user = haversine(float(lat), float(lng), c[1], c[0])
                hasil += (f"[{i}] type={s.get('type')} arah={arah} "
                          f"jarak_step={fmt_jarak(s.get('distance',0))} "
                          f"jarak_dari_user={fmt_jarak(jarak_user)} "
                          f"ins={s.get('instruction','?')[:40]}\n")
            return hasil
    except Exception as e:
        return f"ERROR|{str(e)}"


@app.route('/')
@app.route('/test')
@app.route('/api/test')
def test():
    return (f"SERVER_OK|"
            f"Mode:auto(ORS→OSRM_fallback)|"
            f"Tiba<={TIBA_RADIUS_M}m|"
            f"Belok<={BELOK_RADIUS_M}m|"
            f"ORS_KeyLen:{len(ORS_KEY)}")


@app.route('/testors')
@app.route('/api/testors')
def testors():
    try:
        r = requests.get("https://api.openrouteservice.org/geocode/search",
            params={'api_key': ORS_KEY, 'text': 'Jakarta', 'size': 1}, timeout=10)
        return f"ORS_{'VALID' if r.status_code==200 else 'INVALID'}|{r.status_code}"
    except Exception as e:
        return f"ORS_ERROR|{e}"


@app.route('/testosrm')
@app.route('/api/testosrm')
def testosrm():
    try:
        r = requests.get(
            f"{OSRM_URL}/106.8272,-6.1751;106.8456,-6.2088",
            params={'steps': 'false', 'overview': 'false'},
            timeout=10)
        d = r.json()
        if d.get('code') == 'Ok':
            dist = d['routes'][0]['distance']
            return f"OSRM_OK|jarak_test={fmt_jarak(dist)}"
        return f"OSRM_ERROR|{d.get('code')}"
    except Exception as e:
        return f"OSRM_ERROR|{e}"


@app.route('/cari')
@app.route('/api/cari')
def cari():
    q = request.args.get('q', '')
    if not q: return "Tulis: /cari?q=nama_tempat"
    geo = geocode(q)
    return (f"DITEMUKAN|lat={geo['lat']}|lng={geo['lng']}|nama={geo['nama']}"
            if geo else f"TIDAK_DITEMUKAN|{q}")
