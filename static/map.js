// Location picker. No account requests are made until Discover is selected.
function setLocation(lat, lng, center = false) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) return false;
    selectedLat = lat;
    selectedLng = lng;
    marker?.setLatLng([lat, lng]);
    if (center && map) map.setView([lat, lng], 13);
    document.getElementById('location-lat').value = lat.toFixed(6);
    document.getElementById('location-lng').value = lng.toFixed(6);
    document.getElementById('coord-display').textContent = `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
    try { localStorage.setItem('selectedLocation', JSON.stringify([lat, lng])); } catch {}
    return true;
}

function initMap() {
    try {
        const saved = JSON.parse(localStorage.getItem('selectedLocation'));
        if (Array.isArray(saved)) setLocation(saved[0], saved[1]);
    } catch {}
    const status = document.getElementById('map-status');
    document.getElementById('coordinate-form').addEventListener('submit', event => {
        event.preventDefault();
        setLocation(Number(document.getElementById('location-lat').value), Number(document.getElementById('location-lng').value), true);
    });
    document.getElementById('locate-me').addEventListener('click', () => {
        const button = document.getElementById('locate-me');
        if (!navigator.geolocation) {
            status.textContent = 'Location is unavailable. Click the map or enter coordinates.';
            return;
        }
        button.disabled = true;
        status.textContent = 'Finding your location…';
        navigator.geolocation.getCurrentPosition(position => {
            setLocation(position.coords.latitude, position.coords.longitude, true);
            button.disabled = false;
            status.textContent = 'Location updated. Select Discover bags here when ready.';
        }, () => {
            button.disabled = false;
            status.textContent = 'Could not get your location. Click the map or enter coordinates.';
            document.getElementById('location-coordinates').open = true;
        }, { timeout: 10000, maximumAge: 60000 });
    });
    if (typeof L === 'undefined') {
        status.textContent = 'Map could not load. You can still use your location or enter coordinates.';
        document.getElementById('map').classList.add('hidden');
        document.getElementById('location-coordinates').open = true;
        return;
    }
    map = L.map('map', { center: [selectedLat, selectedLng], zoom: 12, worldCopyJump: true });
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19
    }).addTo(map);
    tiles.on('tileerror', () => {
        status.textContent = 'Map tiles unavailable. Use your location or enter coordinates below.';
        document.getElementById('location-coordinates').open = true;
    });
    L.control.scale({ imperial: false }).addTo(map);
    marker = L.marker([selectedLat, selectedLng], { draggable: true }).addTo(map);
    map.on('click', event => { const pos = event.latlng.wrap(); setLocation(pos.lat, pos.lng); });
    marker.on('dragend', () => { const pos = marker.getLatLng().wrap(); setLocation(pos.lat, pos.lng); });
    setTimeout(() => map.invalidateSize(), 100);
}
