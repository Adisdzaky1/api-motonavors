from flask import Flask, request
import requests, re

app = Flask(__name__)
ORS_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImMyZjlmYTk3YWYxODQyNmQ5YzUxZDkxMGFhYzA2OGMxIiwiaCI6Im11cm11cjY0In0="

@app.route('/nav')
def nav():
    lat  = request.args.get('lat', '')
    lng  = request.args.get('lng', '')
    dest = request.args.get('dest', '')
    if not lat or not lng or not dest:
        return "IDLE:0"
    try:
        # Geocode tujuan dulu
        geo = requests.get(
            "https://api.openrouteservice.org/geocode/search",
            params={'api_key': ORS_KEY, 'text': dest, 'size': 1}
        ).json()
        coords = geo['features'][0]['geometry']['coordinates']
        dst_lng, dst_lat = coords[0], coords[1]

        # Ambil rute
        r = requests.post(
            "https://api.openrouteservice.org/v2/directions/driving-car/json",
            headers={'Authorization': ORS_KEY},
            json={"coordinates": [[float(lng), float(lat)], [dst_lng, dst_lat]]}
        ).json()

        step     = r['routes'][0]['segments'][0]['steps'][0]
        instruksi = step['instruction'].lower()
        jarak    = step['distance']
        jalan    = step.get('name', 'Jalan')

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
    except Exception as e:
        return f"IDLE:00"

# if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
