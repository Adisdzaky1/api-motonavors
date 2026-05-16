from flask import Flask, request
import requests, math

app = Flask(__name__)

ORS_KEY         = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImMyZjlmYTk3YWYxODQyNmQ5YzUxZDkxMGFhYzA2OGMxIiwiaCI6Im11cm11cjY0In0="
NOMINATIM_URL   = "https://nominatim.openstreetmap.org/search"
ORS_GEOJSON_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
OSRM_URL        = "https://router.project-osrm.org/route/v1/driving"
HEADERS_NOM     = {'User-Agent': 'MotoNavApp/1.0'}

# Radius (meter) — disesuaikan untuk GPS akurat dari Sketchware
TIBA_RADIUS_M  = 15   # ≤ 15m → TIBA
BELOK_RADIUS_M = 35   # ≤ 35m → tampilkan KANAN/KIRI
SIAP_BELOK_M   = 70   # ≤ 70m → peringatan dini SIAP-KANAN/KIRI

# Tidak ada _tiba_counter → Vercel compatible
# Anti false positive TIBA ditangani di sisi klien (Sketchware)
# dengan mengirim parameter confirm_tiba=1 setelah 3x berturut-turut


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
        return None  # fallback ke OSRM
    if resp.status_code != 200:
        return f"ERROR:ORS_{resp.status_code}"
    data = resp.json()
    try:
        props      = data['features'][0]['properties']
        geo_coords = data['features'][0]['geometry']['coordinates']
        steps      = props['segments'][0]['steps']
    except (KeyError, IndexError) as e:
        return f"ERROR:ORS_PARSE|{str(e)}"
    return hitung_instruksi(steps, geo_coords, lat_u, lng_u,
                            lat_t, lng_t, nama_tujuan, engine='ors')


# ════════════════════════════════════════════════════
#  ENGINE OSRM
# ════════════════════════════════════════════════════
def get_rute_osrm(lat_a, lng_a, lat_t, lng_t):
    url = f"{OSRM_URL}/{lng_a},{lat_a};{lng_t},{lat_t}"
    return requests.get(url,
        params={'steps': 'true', 'overview': 'full', 'geometries': 'geojson'},
        headers=HEADERS_NOM, timeout=15)


def deteksi_arah_osrm(m_type, m_mod):
    t = m_type.lower() if m_type else ''
    m = m_mod.lower()  if m_mod  else ''
    if t == 'arrive':                                  return 'TIBA'
    if t == 'depart':                                  return 'DEPART'
    if t in ['roundabout', 'rotary',
             'exit roundabout', 'exit rotary']:        return 'BUNDARAN'
    if 'right' in m:                                   return 'KANAN'
    if 'left'  in m:                                   return 'KIRI'
    if 'uturn' in m:                                   return 'BALIK'
    return 'LURUS'


def parse_osrm(resp, nama_tujuan, lat_u, lng_u, lat_t, lng_t):
    if resp.status_code != 200:
        return f"ERROR:OSRM_{resp.status_code}"
    data = resp.json()
    if data.get('code') != 'Ok':
        return f"ERROR:OSRM_{data.get('code')}"
    try:
        steps = data['routes'][0]['legs'][0]['steps']
    except (KeyError, IndexError) as e:
        return f"ERROR:OSRM_PARSE|{str(e)}"
    return hitung_instruksi(steps, None, lat_u, lng_u,
                            lat_t, lng_t, nama_tujuan, engine='osrm')


# ════════════════════════════════════════════════════
#  HITUNG INSTRUKSI — logika 3 zona
# ════════════════════════════════════════════════════
def hitung_instruksi(steps, geo_coords, lat_u, lng_u,
                     lat_t, lng_t, nama_tujuan, engine):

    jarak_tujuan = haversine(lat_u, lng_u, lat_t, lng_t)

    # Kumpulkan step dengan posisi titik belok
    step_data = []
    for step in steps:
        if engine == 'ors':
            step_type = step.get('type', 6)
            if step_type == 11: continue
            wp_list = step.get('way_points', [])
            if not wp_list or not geo_coords: continue
            wp_idx = wp_list[0]
            if wp_idx >= len(geo_coords): continue
            coord = geo_coords[wp_idx]
            s_lat, s_lng = coord[1], coord[0]
            arah  = deteksi_arah_ors(step.get('instruction', ''), step_type)
            jalan = step.get('name', '') or nama_tujuan

        else:  # osrm
            maneuver = step.get('maneuver', {})
            location = maneuver.get('location', [])
            if not location or len(location) < 2: continue
            s_lng, s_lat = location[0], location[1]
            arah  = deteksi_arah_osrm(
                        maneuver.get('type', ''),
                        maneuver.get('modifier', ''))
            if arah == 'DEPART': continue
            jalan = step.get('name', '') or nama_tujuan

        jarak_ke_step = haversine(lat_u, lng_u, s_lat, s_lng)
        step_data.append({
            'arah':            arah,
            'jalan':           jalan if jalan.strip() else nama_tujuan,
            'jarak_dari_user': jarak_ke_step,
        })

    if not step_data:
        return f"NAV:LURUS:{fmt_jarak(jarak_tujuan)}:{nama_tujuan[:20]}"

    step_data.sort(key=lambda x: x['jarak_dari_user'])
    s              = step_data[0]
    arah           = s['arah']
    jarak_ke_belok = s['jarak_dari_user']
    jalan          = s['jalan']

    # Guard TIBA prematur dari step
    if arah == 'TIBA':
        if jarak_tujuan <= TIBA_RADIUS_M:
            return f"NAV:TIBA:0m:{nama_tujuan[:20]}"
        return f"NAV:LURUS:{fmt_jarak(jarak_tujuan)}:{nama_tujuan[:20]}"

    # ── 3 Zona ──────────────────────────────────────
    if jarak_ke_belok <= BELOK_RADIUS_M:
        # Zona belok aktif
        return f"NAV:{arah}:{fmt_jarak(jarak_ke_belok)}:{jalan[:20]}"
    elif jarak_ke_belok <= SIAP_BELOK_M:
        # Zona peringatan dini
        return f"NAV:SIAP-{arah}:{fmt_jarak(jarak_ke_belok)}:{jalan[:20]}"
    else:
        # Masih jauh
        return f"NAV:LURUS:{fmt_jarak(jarak_ke_belok)}:{jalan[:20]}"


# ════════════════════════════════════════════════════
#  ROUTER
# ════════════════════════════════════════════════════
def navigasi(lat_f, lng_f, tlat, tlng, dest, mode, acc_f, confirm_tiba):
    jarak_tujuan = haversine(lat_f, lng_f, tlat, tlng)

    # Cek TIBA
    # confirm_tiba=1 dikirim klien setelah 3x berturut-turut ≤ TIBA_RADIUS_M
    if jarak_tujuan <= TIBA_RADIUS_M:
        if confirm_tiba:
            return f"NAV:TIBA:0m:{dest[:20]}"
        return f"NAV:LURUS:{fmt_jarak(jarak_tujuan)}:{dest[:20]}"

    if mode == 'osrm':
        try:
            resp = get_rute_osrm(lat_f, lng_f, tlat, tlng)
            return parse_osrm(resp, dest, lat_f, lng_f, tlat, tlng)
        except requests.exceptions.Timeout: return "ERROR:OSRM_TIMEOUT"
        except Exception as e: return f"ERROR:OSRM|{str(e)[:80]}"

    if mode == 'ors':
        try:
            resp   = get_rute_ors(lat_f, lng_f, tlat, tlng)
            result = parse_ors(resp, dest, lat_f, lng_f, tlat, tlng)
            return result if result else "ERROR:ORS_429|Ganti mode=osrm"
        except requests.exceptions.Timeout: return "ERROR:ORS_TIMEOUT"
        except Exception as e: return f"ERROR:ORS|{str(e)[:80]}"

    # auto — ORS dulu, fallback OSRM
    try:
        resp   = get_rute_ors(lat_f, lng_f, tlat, tlng)
        result = parse_ors(resp, dest, lat_f, lng_f, tlat, tlng)
        if result is None:
            resp2 = get_rute_osrm(lat_f, lng_f, tlat, tlng)
            return parse_osrm(resp2, dest, lat_f, lng_f, tlat, tlng)
        return result
    except:
        try:
            resp2 = get_rute_osrm(lat_f, lng_f, tlat, tlng)
            return parse_osrm(resp2, dest, lat_f, lng_f, tlat, tlng)
        except Exception as e:
            return f"ERROR:SEMUA_GAGAL|{str(e)[:60]}"


# ════════════════════════════════════════════════════
#  ENDPOINT: /nav
#
#  Parameter:
#  lat, lng          posisi user dari GPS
#  dlat, dlng        koordinat tujuan
#  dest              nama tujuan (label)
#  mode              osrm | ors | auto (default: osrm)
#  acc               akurasi GPS meter (opsional)
#  confirm_tiba      1 jika klien sudah 3x dekat tujuan
# ════════════════════════════════════════════════════
@app.route('/nav')
@app.route('/api/nav')
def nav():
    lat           = request.args.get('lat',           '')
    lng           = request.args.get('lng',           '')
    dest          = request.args.get('dest',          'Tujuan')
    dlat          = request.args.get('dlat',          '')
    dlng          = request.args.get('dlng',          '')
    mode          = request.args.get('mode',          'osrm').lower()
    acc           = request.args.get('acc',           '10')
    confirm_tiba  = request.args.get('confirm_tiba',  '0')

    if not lat or not lng:
        return "ERROR:GPS_KOSONG"
    try:
        lat_f        = float(lat)
        lng_f        = float(lng)
        acc_f        = float(acc)
        konfirmasi   = confirm_tiba == '1'
    except:
        return "ERROR:FORMAT_GPS"

    # Filter GPS sangat tidak akurat
    if acc_f > 150:
        return f"WAIT:GPS_LEMAH:{int(acc_f)}m"

    if dlat and dlng:
        try:
            tlat = float(dlat)
            tlng = float(dlng)
        except:
            return "ERROR:FORMAT_KOORDINAT"
        return navigasi(lat_f, lng_f, tlat, tlng, dest, mode, acc_f, konfirmasi)

    if not dest or dest == 'Tujuan':
        return "ERROR:TUJUAN_KOSONG"
    geo = geocode(dest)
    if not geo:
        return f"ERROR:TIDAK_DITEMUKAN|{dest}"
    return navigasi(lat_f, lng_f, geo['lat'], geo['lng'],
                    geo['nama'], mode, acc_f, konfirmasi)


@app.route('/')
@app.route('/test')
@app.route('/api/test')
def test():
    return (f"SERVER_OK|Vercel-Compatible|"
            f"OSRM-default|"
            f"Belok<={BELOK_RADIUS_M}m|"
            f"Siap<={SIAP_BELOK_M}m|"
            f"Tiba<={TIBA_RADIUS_M}m")

@app.route('/testosrm')
@app.route('/api/testosrm')
def testosrm():
    try:
        r = requests.get(
            f"{OSRM_URL}/106.8272,-6.1751;106.8456,-6.2088",
            params={'steps': 'false', 'overview': 'false'}, timeout=10)
        d = r.json()
        if d.get('code') == 'Ok':
            return f"OSRM_OK|{fmt_jarak(d['routes'][0]['distance'])}"
        return f"OSRM_ERROR|{d.get('code')}"
    except Exception as e:
        return f"OSRM_ERROR|{e}"

@app.route('/testors')
@app.route('/api/testors')
def testors():
    try:
        r = requests.get(
            "https://api.openrouteservice.org/geocode/search",
            params={'api_key': ORS_KEY, 'text': 'Jakarta', 'size': 1},
            timeout=10)
        return f"ORS_{'VALID' if r.status_code==200 else 'INVALID'}|{r.status_code}"
    except Exception as e:
        return f"ORS_ERROR|{e}"

@app.route('/cari')
@app.route('/api/cari')
def cari():
    q = request.args.get('q', '')
    if not q: return "Tulis: /cari?q=nama_tempat"
    geo = geocode(q)
    return (f"DITEMUKAN|lat={geo['lat']}|lng={geo['lng']}|nama={geo['nama']}"
            if geo else f"TIDAK_DITEMUKAN|{q}")
