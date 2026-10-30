// State
let credentials = null;
let currentPollingId = null;
let selectedLat = 48.8566;
let selectedLng = 2.3522;
let map, marker;

const globalItemCache = {}; // ID -> full item payload

// Local Tracker State
let localOrders = JSON.parse(localStorage.getItem('localOrders')) || [];
function saveLocalOrders() {
    localStorage.setItem('localOrders', JSON.stringify(localOrders));
}

// Timer Loop
setInterval(() => {
    document.querySelectorAll('.timer-display').forEach(el => {
        const timestamp = parseInt(el.getAttribute('data-time'));
        const elapsed = Date.now() - timestamp;
        const remaining = (4 * 60 * 1000) - elapsed;
        
        if (remaining <= 0) {
            el.textContent = "Expired (Auto-aborted)";
            el.style.color = "var(--danger)";
            const btn = el.nextElementSibling;
            if (btn && btn.tagName === 'BUTTON') btn.disabled = true;
        } else {
            const mins = Math.floor(remaining / 60000);
            const secs = Math.floor((remaining % 60000) / 1000).toString().padStart(2, '0');
            el.textContent = `Auto-aborts in ${mins}:${secs}`;
        }
    });
}, 1000);

// DOM Elements
const views = document.querySelectorAll('.view');
const navLinks = document.querySelectorAll('.nav-links li');
const toastEl = document.getElementById('toast');
const authStatusDot = document.getElementById('auth-status-dot');
const authStatusText = document.getElementById('auth-status-text');

// Init
document.addEventListener('DOMContentLoaded', async () => {
    // Navigation
    navLinks.forEach(link => {
        link.addEventListener('click', () => switchView(link.dataset.target));
    });

    // Check credentials on load (does not fetch API lists automatically anymore)
    await checkCredentials();

    // Map Init
    initMap();
});

function switchView(targetId) {
    navLinks.forEach(l => l.classList.remove('active'));
    document.querySelector(`[data-target="${targetId}"]`).classList.add('active');

    views.forEach(v => v.classList.add('hidden'));
    const targetEl = document.getElementById(targetId);
    targetEl.classList.remove('hidden');

    // Invalidate map size if returning to welcome
    if (targetId === 'view-welcome' && map) {
        setTimeout(() => map.invalidateSize(), 100);
    }

    if (targetId === 'view-items') fetchItems();
    if (targetId === 'view-favorites') fetchFavorites();
    if (targetId === 'view-orders') renderLocalOrders();
    if (targetId === 'view-sniper') fetchSniperStatus();
    if (targetId === 'view-auth') updateSessionInfo();
}

function showToast(message, isError = false) {
    toastEl.textContent = message;
    toastEl.style.color = isError ? 'var(--danger)' : 'var(--primary)';
    toastEl.classList.remove('hidden');
    setTimeout(() => toastEl.classList.add('hidden'), 7000);
}

async function accountFetch(url, options) {
    const saved = await fetch('/api/credentials', { cache: 'no-store' });
    if (!saved.ok) throw new Error('Could not load saved login.');
    const latest = await saved.json();
    if (latest.access_token) credentials = latest;
    const payload = JSON.parse(options.body);
    return fetch(url, { ...options, body: JSON.stringify({ ...payload, ...credentials }) });
}

async function updateSessionInfo() {
    try {
        const res = await fetch('/api/auth/status', { cache: 'no-store' });
        if (!res.ok) throw new Error('Could not read session status.');
        const session = await res.json();
        const expires = session.expires_at ? new Date(session.expires_at * 1000) : null;
        document.getElementById('auth-session-info').textContent = expires
            ? `Access token ${expires.getTime() <= Date.now() ? 'expired' : 'expires'}: ${expires.toLocaleString()}. Auto-refresh runs before your next request.`
            : 'Token expiry is unavailable. Authentication failures trigger one refresh attempt.';
        document.getElementById('btn-refresh-session').disabled = !session.can_refresh;
    } catch {
        document.getElementById('auth-session-info').textContent = 'Could not read session status. Try again after reconnecting.';
    }
}

window.refreshSession = async function() {
    const button = document.getElementById('btn-refresh-session');
    button.disabled = true;
    button.textContent = 'Refreshing…';
    try {
        const res = await accountFetch('/api/auth/refresh', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({})
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'success') throw new Error(data.detail || 'Refresh failed. Reconnect your account.');
        await checkCredentials();
        showToast('Session refreshed and saved.');
    } catch (error) {
        showToast(error.message || 'Refresh failed. Reconnect your account.', true);
    } finally {
        button.disabled = false;
        button.textContent = 'Refresh session';
        await updateSessionInfo();
    }
};

window.reconnectAccount = function() {
    currentPollingId = null;
    document.getElementById('auth-success').classList.add('hidden');
    document.getElementById('auth-step-2').classList.add('hidden');
    document.getElementById('auth-step-1').classList.remove('hidden');
    document.getElementById('auth-pin').value = '';
};

// ---- Authentication Flow ----
async function checkCredentials() {
    try {
        const res = await fetch('/api/credentials', { cache: 'no-store' });
        const data = await res.json();
        if (data.access_token) {
            credentials = data;
            authStatusDot.classList.add('active');
            authStatusText.textContent = "Login saved";

            document.getElementById('auth-step-1').classList.add('hidden');
            document.getElementById('auth-step-2').classList.add('hidden');
            document.getElementById('auth-success').classList.remove('hidden');
            await updateSessionInfo();
        } else {
            authStatusDot.classList.remove('active');
            authStatusText.textContent = "Not Connected";
        }
    } catch (e) {
        console.error("Failed to load credentials", e);
    }
}

document.getElementById('btn-request-pin').addEventListener('click', async () => {
    const email = document.getElementById('auth-email').value;
    if (!email) return showToast("Enter an email!", true);

    document.getElementById('btn-request-pin').textContent = "Sending...";
    try {
        const res = await fetch('/api/auth/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email })
        });
        const data = await res.json();

        if (data.status === 'success') {
            currentPollingId = data.polling_id;
            document.getElementById('auth-step-1').classList.add('hidden');
            document.getElementById('auth-step-2').classList.remove('hidden');
            showToast("PIN sent to email!");
        } else {
            showToast("Failed to send PIN", true);
        }
    } catch (e) {
        showToast("Error requesting PIN", true);
    }
    document.getElementById('btn-request-pin').textContent = "Send PIN";
});

document.getElementById('btn-verify-pin').addEventListener('click', async () => {
    const email = document.getElementById('auth-email').value;
    const pin = document.getElementById('auth-pin').value;
    if (!pin) return showToast("Enter the PIN!", true);

    document.getElementById('btn-verify-pin').textContent = "Verifying...";
    try {
        const res = await fetch('/api/auth/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, pin, polling_id: currentPollingId })
        });
        const data = await res.json();

        if (data.status === 'success') {
            credentials = data.credentials;
            cachedItems = cachedFavorites = null;
            lastItemsCoord = lastFavsCoord = null;
            await checkCredentials();
            showToast("Login Successful!");
            setTimeout(() => switchView('view-welcome'), 1500); // Send back to map config
        } else {
            showToast(data.detail || "Invalid PIN", true);
        }
    } catch (e) {
        showToast("Error verifying PIN", true);
    }
    document.getElementById('btn-verify-pin').textContent = "Verify & Connect";
});


// ---- Items & Orders ----

let cachedItems = null;
let cachedFavorites = null;
let lastItemsCoord = null;
let lastFavsCoord = null;

function formatPrice(priceObj) {
    if (!priceObj || typeof priceObj.minor_units === 'undefined') return "N/A";
    const amount = priceObj.minor_units / Math.pow(10, priceObj.decimals || 2);

    try {
        // Build an elegant localized currency format
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: priceObj.code || 'EUR'
        }).format(amount);
    } catch (e) {
        return amount.toFixed(priceObj.decimals || 2) + " " + priceObj.code;
    }
}

function createBagCard(item, isFavorite = false, idx = 0) {
    const stock = item?.items_available || 0;
    const isSoldOut = stock === 0;

    const priceStr = formatPrice(item?.item?.item_price);
    const valueStr = formatPrice(item?.item?.item_value);

    const imageUrl = item?.item?.cover_picture?.current_url || 'https://via.placeholder.com/400x200?text=Surprise+Bag';
    const storeName = item?.store?.store_name || 'Unknown Store';
    const itemName = item?.item?.name || 'Surprise Bag';
    const itemId = item?.item?.item_id;
    if (itemId && item) globalItemCache[itemId] = item;

    const ratingObj = item?.item?.average_overall_rating;
    const ratingHtml = ratingObj?.average_overall_rating ?
        `<div class="rating">⭐ ${ratingObj.average_overall_rating.toFixed(1)} <span class="rating-count">(${ratingObj.rating_count})</span></div>` :
        `<div class="rating">⭐ New</div>`;

    return `
        <div class="bag-card glass-panel" style="animation-delay: ${idx * 0.05}s">
            <div class="card-image" style="background-image: url('${imageUrl}')">
                <div class="stock-badge ${isSoldOut ? 'soldout' : 'available'}">
                    ${isSoldOut ? 'SOLD OUT' : `${stock} Left`}
                </div>
            </div>
            <div class="card-content">
                <div class="store-name">${storeName}</div>
                <div class="item-name">${itemName}</div>
                ${ratingHtml}
                
                <div class="price-row">
                    <span class="price">${priceStr}</span>
                    <span class="original-price">${valueStr}</span>
                </div>
                
                <div style="display: flex; gap: 0.5rem; margin-top: 1rem;">
                    <button class="btn btn-reserve" style="flex: 1;" 
                        ${isSoldOut || !itemId ? 'disabled' : ''} 
                        onclick="reserveBag('${itemId}')">
                        ${isSoldOut ? 'Unavailable' : 'Reserve'}
                    </button>
                    <button class="btn btn-snipe" style="flex: 1; ${!itemId ? 'display:none;' : ''}" 
                        onclick="openSnipeModal('${itemId}')">
                        Snipe <span class="target-icon">🎯</span>
                    </button>
                </div>
            </div>
        </div>
    `;
}

function renderBags(itemsArray, containerId, isFavorite = false, availId = null, priceId = null) {
    const container = document.getElementById(containerId);
    container.innerHTML = '';
    
    let filtered = itemsArray || [];
    
    if (availId) {
        const availVal = document.getElementById(availId).value;
        if (availVal === 'available') filtered = filtered.filter(i => (i?.items_available || 0) > 0);
        if (availVal === 'soldout') filtered = filtered.filter(i => (i?.items_available || 0) === 0);
    }
    
    if (priceId) {
        const priceVal = document.getElementById(priceId).value;
        filtered = filtered.filter(i => {
           if (priceVal === 'all') return true;
           const minor = i?.item?.item_price?.minor_units || 0;
           const dec = i?.item?.item_price?.decimals || 2;
           const amount = minor / Math.pow(10, dec);
           if (priceVal === 'under5') return amount < 5;
           if (priceVal === 'under10') return amount < 10;
           return true;
        });
    }

    if (filtered.length) {
        filtered.forEach((i, idx) => container.innerHTML += createBagCard(i, isFavorite, idx));
    } else {
        container.innerHTML = '<p>No bags found matching current filters.</p>';
    }
}

window.applyFilters = function(viewType) {
    if (viewType === 'items') {
        renderBags(cachedItems, 'items-grid', false, 'filter-avail-items', 'filter-price-items');
    } else if (viewType === 'favorites') {
        renderBags(cachedFavorites, 'favorites-grid', true, 'filter-avail-favs', 'filter-price-favs');
    }
};

async function fetchItems(forceRefresh = false) {
    if (!credentials) return switchView('view-auth');

    // Cache Check
    const currentCoord = `${selectedLat},${selectedLng}`;
    if (!forceRefresh && cachedItems && lastItemsCoord === currentCoord) {
        applyFilters('items');
        return;
    }

    const container = document.getElementById('items-grid');
    container.innerHTML = '<div class="loader">Fetching bags nearby...</div>';

    try {
        const payload = {
            ...credentials,
            latitude: selectedLat,
            longitude: selectedLng
        };

        const res = await accountFetch('/api/items', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (!res.ok || data.status !== 'success') throw new Error(data.detail || 'Could not load bags.');
        cachedItems = data.items || [];
        lastItemsCoord = currentCoord;
        applyFilters('items');
    } catch (e) {
        showToast(e.message || "Request failed", true);
        container.innerHTML = '<p style="color:var(--danger)">Failed to load items. Are your tokens valid?</p>';
    }
}

async function fetchFavorites(forceRefresh = false) {
    if (!credentials) return switchView('view-auth');

    // Cache Check
    const currentCoord = `${selectedLat},${selectedLng}`;
    if (!forceRefresh && cachedFavorites && lastFavsCoord === currentCoord) {
        applyFilters('favorites');
        return;
    }

    const container = document.getElementById('favorites-grid');
    container.innerHTML = '<div class="loader">Loading your favorites...</div>';

    try {
        const payload = {
            ...credentials,
            latitude: selectedLat,
            longitude: selectedLng
        };

        const res = await accountFetch('/api/favorites', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (!res.ok || data.status !== 'success') throw new Error(data.detail || 'Could not load favorites.');
        cachedFavorites = data.items || [];
        lastFavsCoord = currentCoord;
        applyFilters('favorites');
    } catch (e) {
        showToast(e.message || "Request failed", true);
        container.innerHTML = '<p style="color:var(--danger)">Failed to load favorites. Are your tokens valid?</p>';
    }
}

window.reserveBag = async function (itemId) {
    if (!confirm("Are you sure you want to reserve this bag? You will need to complete payment manually on the mobile app afterwards.")) return;

    showToast("Reserving...");
    try {
        const payload = {
            ...credentials,
            item_id: itemId,
            item_count: 1
        };
        const res = await accountFetch('/api/order/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        console.log("Order Creation Payload:", data);

        if (data.status === 'success') {
            const orderId = data.order?.id || data.order?.order_id || data.order?.order?.id || data.order?.order?.order_id || 'Unknown (Check Console)';
            
            const targetItem = cachedItems?.find(i => i.item?.item_id === itemId) || cachedFavorites?.find(i => i.item?.item_id === itemId);
            
            localOrders.push({
                id: orderId,
                storeName: targetItem?.store?.store_name || "Unknown Store",
                itemName: targetItem?.item?.name || "Surprise Bag",
                timestamp: Date.now()
            });
            saveLocalOrders();
            
            showToast(`Success! Order ID: ${orderId}`);
            if (document.getElementById('view-orders').classList.contains('hidden') === false) {
                renderLocalOrders();
            }
        } else {
            showToast(data.detail || "Failed to reserve", true);
        }
    } catch (e) {
        console.error(e);
        showToast("Error creating order", true);
    }
}

function renderLocalOrders() {
    const container = document.getElementById('orders-grid');
    container.innerHTML = '';
    
    // Clean up cleanly expired orders (e.g. older than 5 minutes to give a buffer)
    localOrders = localOrders.filter(o => Date.now() - o.timestamp < 5 * 60 * 1000);
    saveLocalOrders();

    if (localOrders.length === 0) {
        container.innerHTML = '<p>No recent local orders found.</p>';
        return;
    }

    localOrders.forEach(order => {
        container.innerHTML += `
        <div class="bag-card glass-panel" style="border: 1px solid var(--primary);" id="order-${order.id}">
            <div class="card-content">
                <div class="store-name">${order.storeName}</div>
                <div class="item-name">${order.itemName}</div>
                <div class="price-row">
                    <span class="rating-count" style="font-size:0.7rem; opacity:0.7;">ID: ${order.id}</span>
                </div>
                
                <div class="timer-display" style="color: var(--warning); margin-top: 0.5rem; font-weight: bold;" data-time="${order.timestamp}">
                    Calculating...
                </div>
                
                <button class="btn btn-reserve" style="background: var(--danger); margin-top: 1rem;" 
                    onclick="abortOrder('${order.id}')">
                    Abort Order
                </button>
            </div>
        </div>
        `;
    });
}

window.abortOrder = async function (orderId) {
    if (!confirm("Are you sure you want to abort this order?")) return;

    showToast("Aborting order...");
    try {
        const payload = {
            ...credentials,
            order_id: orderId
        };
        const res = await accountFetch('/api/order/abort', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.status === 'success') {
            localOrders = localOrders.filter(o => o.id !== orderId);
            saveLocalOrders();
            showToast("Order aborted successfully!");
            renderLocalOrders();
        } else {
            showToast(data.detail || "Failed to abort", true);
        }
    } catch (e) {
        console.error(e);
        showToast("Error aborting order", true);
    }
}

// --- SNIPER LOGIC ---

window.openSnipeModal = function(itemId) {
    document.getElementById('sniper-item-id').value = itemId;
    document.getElementById('sniper-time').value = '';
    document.getElementById('sniper-modal').classList.remove('hidden');
}

window.closeSnipeModal = function() {
    document.getElementById('sniper-modal').classList.add('hidden');
}

window.confirmSnipe = async function() {
    const itemId = document.getElementById('sniper-item-id').value;
    const timeVal = document.getElementById('sniper-time').value; // HH:MM string
    
    let targetTime = null;
    if (timeVal) {
        const now = new Date();
        const [hours, minutes] = timeVal.split(':');
        const targetDate = new Date(now.getFullYear(), now.getMonth(), now.getDate(), hours, minutes, 0, 0);
        
        if (targetDate.getTime() < now.getTime()) {
            targetDate.setDate(targetDate.getDate() + 1);
        }
        targetTime = Math.floor(targetDate.getTime() / 1000);
    }
    
    const payload = {
        ...credentials,
        item_id: itemId,
        target_time: targetTime
    };
    
    document.getElementById('sniper-modal').classList.add('hidden');
    showToast("Deploying Sniper...");
    
    try {
        const res = await accountFetch('/api/bot/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        
        if (data.status === 'success') {
            showToast("Sniper deployed! Watching target.");
            switchView('view-sniper');
        } else {
            showToast(data.detail || "Refused by Sniper HQ", true);
        }
    } catch(e) {
        console.error(e);
        showToast("Backend connection error", true);
    }
}

window.fetchSniperStatus = async function() {
    if (!credentials || document.getElementById('view-sniper').classList.contains('hidden')) return;
    try {
        const res = await fetch('/api/bot/status');
        const data = await res.json();
        if (data.status !== 'success') return;
        const snipers = data.snipers || [];
        let ordersChanged = false;
        for (const bot of snipers) {
            if (bot.status === 'RESERVED' && bot.order_id && !localOrders.some(order => order.id === bot.order_id)) {
                const targetItem = globalItemCache[bot.item_id];
                localOrders.push({
                    id: bot.order_id,
                    storeName: targetItem?.store?.store_name || 'Auto-Sniped Target',
                    itemName: targetItem?.item?.name || 'Surprise Bag',
                    timestamp: bot.reserved_at ? bot.reserved_at * 1000 : Date.now()
                });
                ordersChanged = true;
                showToast(`Bag reserved. Order ID: ${bot.order_id}`);
            }
            if (bot.status === 'ABORTED_REMOTELY' && bot.order_id) {
                const index = localOrders.findIndex(order => order.id === bot.order_id);
                if (index !== -1) {
                    localOrders.splice(index, 1);
                    ordersChanged = true;
                    showToast('Order synced: canceled via Telegram.');
                }
            }
        }
        if (ordersChanged) {
            saveLocalOrders();
            if (!document.getElementById('view-orders').classList.contains('hidden')) renderLocalOrders();
        }
        renderSniperGrid(snipers);
    } catch (error) {
        console.error('Could not load bot status', error);
    }
};

setInterval(fetchSniperStatus, 2000);

function renderSniperGrid(snipers) {
    const container = document.getElementById('sniper-grid');
    
    if (!snipers || snipers.length === 0) {
        container.innerHTML = '<p>No snipers deployed right now. Go to Discover and target a bag!</p>';
        return;
    }
    
    container.innerHTML = '';
    snipers.forEach((bot, idx) => {
        const targetItem = globalItemCache[bot.item_id];
        const storeName = targetItem?.store?.store_name || "Target System";
        const itemName = targetItem?.item?.name || "Surprise Bag";
        const imageUrl = targetItem?.item?.cover_picture?.current_url || 'https://via.placeholder.com/400x200?text=Target';
        
        let scheduleText = "Live (Polling Instantly)";
        if (bot.target_time) {
            const tDate = new Date(bot.target_time * 1000);
            scheduleText = "Target Drop: " + tDate.toLocaleTimeString();
        }
        
        const logsHtml = (bot.logs || []).map(l => `<div class="sniper-log">${l}</div>`).join('');
        
        let statusColor = "var(--success)";
        let animClass = "";
        if (bot.status === "SCHEDULED") statusColor = "var(--text-muted)";
        if (bot.status === "WATCHING") { statusColor = "var(--warning)"; animClass = "bot-watching"; }
        if (bot.status === "SNIPING") { statusColor = "var(--primary)"; animClass = "bot-sniping"; }
        if (bot.status === "FAILED" || bot.status === "STOPPED" || bot.status === "ABORTED_REMOTELY" || bot.status === "UNKNOWN") statusColor = "var(--danger)";
        
        let actionButtonHtml = '';
        if (bot.status === "RESERVED") {
            actionButtonHtml = `
                <button class="btn btn-success" style="margin-top: 1rem; width: 100%; display: flex; align-items: center; justify-content: center; gap: 0.5rem;" 
                    onclick="stopSniper('${bot.item_id}')">
                    Bag Secured! (Dismiss) ✨
                </button>`;
        } else if (bot.status === "STOPPED" || bot.status === "FAILED" || bot.status === "ABORTED_REMOTELY" || bot.status === "UNKNOWN") {
            actionButtonHtml = `
                <button class="btn btn-secondary" style="margin-top: 1rem; width: 100%; display: flex; align-items: center; justify-content: center; gap: 0.5rem;" 
                    onclick="stopSniper('${bot.item_id}')">
                    Clear Log 🗑️
                </button>`;
        } else {
            actionButtonHtml = `
                <button class="btn btn-halt" style="margin-top: 1rem; width: 100%; display: flex; align-items: center; justify-content: center; gap: 0.5rem;" 
                    onclick="stopSniper('${bot.item_id}')">
                    Halt Bot 🛑
                </button>`;
        }
        
        container.innerHTML += `
        <div class="bag-card glass-panel ${animClass}" style="border: 1px solid ${statusColor}; animation-delay: ${idx * 0.05}s">
            <div class="card-image" style="background-image: url('${imageUrl}')">
                <div class="stock-badge" style="background: ${statusColor}; color: black;">
                    ${bot.status}
                </div>
            </div>
            <div class="card-content">
                <div class="store-name">${storeName}</div>
                <div class="item-name">${itemName}</div>
                <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.5rem;">${scheduleText}</div>
                
                <div class="sniper-console">
                    ${logsHtml}
                </div>
                
                ${actionButtonHtml}
            </div>
        </div>
        `;
    });
}

window.stopSniper = async function(itemId) {
    showToast("Halting Sniper...");
    try {
        const payload = { item_id: itemId };
        const res = await fetch('/api/bot/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast("Sniper halted.");
            fetchSniperStatus();
        }
    } catch(e) {
        showToast("Error stopping sniper", true);
    }
}
