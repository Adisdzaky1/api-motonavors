from flask import Flask, request
import requests

app = Flask(__name__)
ORS_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImMyZjlmYTk3YWYxODQyNmQ5YzUxZDkxMGFhYzA2OGMxIiwiaCI6Im11cm11cjY0In0="

@app.route('/nav')
def nav():
    lat  = request.args.get('lat', '')
    lng  = request.args.get('lng', '')
    dest = request.args.get('dest', '')

    # Cek parameter
    if not lat or not lng or not dest:
        return f"ERROR:PARAM_KOSONG|lat={lat}|lng={lng}|dest={dest}"

    # Step 1: Geocode tujuan
    try:
        geo_url = "https://api.openrouteservice.org/geocode/search"
        geo_params = {'api_key': ORS_KEY, 'text': dest, 'size': 1}
        geo_resp = requests.get(geo_url, params=geo_params, timeout=10)
        
        if geo_resp.status_code != 200:
            return f"ERROR:GEOCODE_HTTP_{geo_resp.status_code}|{geo_resp.text[:100]}"
        
        geo = geo_resp.json()
        
        if not geo.get('features'):
            return f"ERROR:GEOCODE_NOTFOUND|dest={dest}|response={str(geo)[:150]}"
        
        coords  = geo['features'][0]['geometry']['coordinates']
        dst_lng = coords[0]
        dst_lat = coords[1]
        label   = geo['features'][0]['properties'].get('label', dest)

    except requests.exceptions.Timeout:
        return "ERROR:GEOCODE_TIMEOUT"
    except Exception as e:
        return f"ERROR:GEOCODE_EXCEPTION|{str(e)[:150]}"

    # Step 2: Ambil rute
    try:
        route_url = "https://api.openrouteservice.org/v2/directions/driving-car/json"
        route_headers = {
            'Authorization': ORS_KEY,
            'Content-Type': 'application/json'
        }
        route_body = {
            "coordinates": [
                [float(lng), float(lat)],
                [dst_lng, dst_lat]
            ]
        }
        route_resp = requests.post(
            route_url,
            headers=route_headers,
            json=route_body,
            timeout=10
        )

        if route_resp.status_code != 200:
            return f"ERROR:ROUTE_HTTP_{route_resp.status_code}|{route_resp.text[:150]}"

        r = route_resp.json()

        if not r.get('routes'):
            return f"ERROR:ROUTE_EMPTY|{str(r)[:150]}"

        step      = r['routes'][0]['segments'][0]['steps'][0]
        instruksi = step['instruction'].lower()
        jarak     = step['distance']
        jalan     = step.get('name', 'Jalan')

        # Format jarak
        if jarak >= 1000:
            jarak_str = f"{jarak/1000:.1f}km"
        else:
            jarak_str = f"{int(jarak)}m"

        # Deteksi arah
        if 'kanan' in instruksi or 'right' in instruksi:
            arah = 'KANAN'
        elif 'kiri' in instruksi or 'left' in instruksi:
            arah = 'KIRI'
        elif 'tiba' in instruksi or 'destination' in instruksi or 'arrive' in instruksi:
            arah = 'TIBA'
        elif 'putar' in instruksi or 'u-turn' in instruksi:
            arah = 'BALIK'
        else:
            arah = 'LURUS'

        return f"NAV:{arah}:{jarak_str}:{jalan[:20]}"

    except requests.exceptions.Timeout:
        return "ERROR:ROUTE_TIMEOUT"
    except KeyError as e:
        return f"ERROR:PARSE_KEY|{str(e)}|{str(r)[:150]}"
    except Exception as e:
        return f"ERROR:ROUTE_EXCEPTION|{str(e)[:150]}"


@app.route('/test')
def test():
    # Endpoint cek server hidup
    return f"SERVER_OK|ORS_KEY_LENGTH:{len(ORS_KEY)}"


@app.route('/testors')
def testors():
    # Endpoint cek API key ORS valid atau tidak
    try:
        r = requests.get(
            "https://api.openrouteservice.org/geocode/search",
            params={'api_key': ORS_KEY, 'text': 'Jakarta', 'size': 1},
            timeout=10
        )
        if r.status_code == 200:
            return f"ORS_KEY_VALID|status={r.status_code}"
        else:
            return f"ORS_KEY_INVALID|status={r.status_code}|msg={r.text[:200]}"
    except Exception as e:
        return f"ORS_TEST_ERROR|{str(e)}"


# if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
