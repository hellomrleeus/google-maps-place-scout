/**
 * Green Oil Chrome Extension - Content Script for Google Maps
 * Place actions and lifecycle-managed coloring for Google Maps' native markers.
 * Only injects the button when viewing a place. Extracts place details on-demand upon click.
 */

if (window.__greenoil_injected__) {
  if (typeof window.__greenoil_check__ === "function") {
    window.__greenoil_check__();
  }
} else {
  window.__greenoil_injected__ = true;
  (() => {
    const lifetime = new AbortController();
    const timers = new Set();
    const intervals = new Set();
    const frames = new Set();
    let disposed = false;
    const owner = `${Date.now()}-${Math.random()}`;
    const ownerAttribute = "data-greenoil-owner";
    const listen = (target, type, handler, options = {}) => {
      const settings = typeof options === "boolean" ? { capture: options } : options;
      target.addEventListener(type, (...args) => { if (isAlive()) handler(...args); },
        { ...settings, signal: lifetime.signal });
    };
    function isAlive() {
      if (disposed) return false;
      if (!chrome.runtime?.id || document.documentElement.getAttribute(ownerAttribute) !== owner) {
        dispose();
        return false;
      }
      return true;
    }
    function dispose() {
      if (disposed) return;
      disposed = true;
      const ownsDocument = document.documentElement.getAttribute(ownerAttribute) === owner;
      if (ownsDocument && typeof removeAnyPinOverlays === "function") removeAnyPinOverlays();
      lifetime.abort();
      timers.forEach(id => window.clearTimeout(id));
      intervals.forEach(id => window.clearInterval(id));
      frames.forEach(id => window.cancelAnimationFrame(id));
      if (ownsDocument) {
        document.querySelectorAll('[id^="greenoil-"]').forEach(el => el.remove());
        document.documentElement.removeAttribute(ownerAttribute);
        window.__greenoil_injected__ = false;
      }
    }
    function setTimeout(fn, delay) {
      const id = window.setTimeout(() => { timers.delete(id); if (isAlive()) fn(); }, delay);
      timers.add(id);
      return id;
    }
    function setInterval(fn, delay) {
      const id = window.setInterval(() => { if (isAlive()) fn(); }, delay);
      intervals.add(id);
      return id;
    }
    function requestAnimationFrame(fn) {
      const id = window.requestAnimationFrame((time) => {
        frames.delete(id);
        if (isAlive()) fn(time);
      });
      frames.add(id);
      return id;
    }
    document.documentElement.setAttribute(ownerAttribute, owner);
    window.__greenoil_dispose__ = dispose;
    listen(window, "pagehide", (event) => { if (!event.persisted) dispose(); });

    let lastProcessedKey = "";
    let currentTheme = { color: "#059669", hoverColor: "#047857", lightColor: "#ecfdf5", borderColor: "#10b981", name: "绿线" };

    function applyThemeToContainer(container, theme) {
      if (!container || !theme) return;
      container.style.setProperty("--go-theme", theme.color || "#059669");
      container.style.setProperty("--go-hover", theme.hoverColor || "#047857");
      container.style.setProperty("--go-light", theme.lightColor || "#ecfdf5");
      container.style.setProperty("--go-border", theme.borderColor || "#10b981");
    }

    try {
      if (chrome.runtime?.id) {
        chrome.runtime.sendMessage({ action: "getActiveTheme" }, (resp) => {
          if (resp && resp.theme) {
            currentTheme = resp.theme;
            const btnContainer = document.getElementById("greenoil-add-waypoint-btn");
            if (btnContainer && !btnContainer.classList.contains("greenoil-added")) {
              applyThemeToContainer(btnContainer, currentTheme);
            }
            if (typeof rebuildPinElements === "function") rebuildPinElements();
          }
        });
      }
    } catch (_) {}

    if (chrome.runtime?.onMessage) {
      chrome.runtime.onMessage.addListener((message) => {
        if (!isAlive()) return;
        if (message.action === "themeColorChanged" && message.theme) {
          currentTheme = message.theme;
          const btnContainer = document.getElementById("greenoil-add-waypoint-btn");
          if (btnContainer && !btnContainer.classList.contains("greenoil-added")) {
            applyThemeToContainer(btnContainer, currentTheme);
            btnContainer.title = `加入当前地点到【${currentTheme.name}】`;
          }
          lastProcessedKey = "";
          if (typeof checkAndInject === "function") {
            checkAndInject();
          }
          if (typeof refreshWaypointPins === "function") {
            refreshWaypointPins();
          }
        }

        if (message.action === "misAuthChanged" && message.auth) {
          isMisAuthChecked = true;
          isMisLoggedIn = Boolean(message.auth.loggedIn);
          const misContainer = document.getElementById("greenoil-match-mis-btn");
          const misBtn = misContainer?.querySelector("button");
          if (misContainer && misBtn) {
            if (isMisLoggedIn) {
              misContainer.classList.remove("greenoil-mis-disabled");
              misContainer.title = "扫描周边20家餐馆并匹配 MIS 签约客户";
              misBtn.title = "扫描周边20家餐馆并匹配 MIS 签约客户";
              misBtn.setAttribute("aria-label", "匹配MIS");
            } else {
              misContainer.classList.add("greenoil-mis-disabled");
              misContainer.title = "请先登录 MIS 内部系统 (点击前往登录)";
              misBtn.title = "请先登录 MIS 内部系统 (点击前往登录)";
              misBtn.setAttribute("aria-label", "请先登录 MIS 内部系统 (点击前往登录)");
            }
          }
        }

        if (message.action === "didPanToWaypoint" && message.waypoint) {
          showToast("已定位", message.waypoint.name || "途径点");
          lastProcessedKey = "";
          setTimeout(() => {
            if (typeof checkAndInject === "function") {
              checkAndInject();
            }
          }, 500);
        }

        if (message.action === "panToLocation" && message.waypoint) {
          inPagePanToLocation(message.targetPath, message.waypoint);
        }

        if (message.action === "renderMisMatches" && Array.isArray(message.matches)) {
          renderMisPins(message.matches);
        }

        if (message.action === "misMatchProgress") {
          const misLabel = document.querySelector("#greenoil-match-mis-btn .greenoil-action-label");
          if (misLabel && message.total) {
            misLabel.textContent = `匹配中 (${message.current}/${message.total})`;
          }
        }
      });
    }

    // Top-level custom event bridge for main-world communication
    listen(window, "greenoil-cmd", (e) => {
      const { action, data } = e.detail || {};
      if (chrome.runtime?.id && action) {
        chrome.runtime.sendMessage({ action, ...data }, (resp) => {
          window.dispatchEvent(new CustomEvent("greenoil-cmd-resp", { detail: resp }));
        });
      }
    });

    // Window focus and visibility listeners for instant login state sync
    listen(window, "focus", () => {
      const misContainer = document.getElementById("greenoil-match-mis-btn");
      const misBtn = misContainer?.querySelector("button");
      if (misContainer && misBtn) {
        checkAndUpdateMisAuth(misContainer, misBtn, true);
      }
      refreshWaypointPins();
    });

    listen(document, "visibilitychange", () => {
      if (document.visibilityState === "visible") {
        const misContainer = document.getElementById("greenoil-match-mis-btn");
        const misBtn = misContainer?.querySelector("button");
        if (misContainer && misBtn) {
          checkAndUpdateMisAuth(misContainer, misBtn, true);
        }
        refreshWaypointPins();
      }
    });

    listen(window, "message", (event) => {
      if (event.data?.type === "GREENOIL_RENDER_MIS_MATCHES" && Array.isArray(event.data.matches)) {
        renderMisPins(event.data.matches);
      }
      if (event.data?.type === "GREENOIL_OPEN_MIS_MODAL" && event.data.customer) {
        openMisModal(event.data.customer);
      }
    });

  const SVG_PLUS = `<svg viewBox="0 0 24 24"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>`;
  const SVG_CHECK = `<svg viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>`;
  const SVG_INFO = `<svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>`;
  const SVG_MIS_SHIELD = `<svg viewBox="0 0 24 24"><path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm-2 16l-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8z"/></svg>`;
  const SVG_SPINNER = `<svg viewBox="0 0 24 24"><path d="M12 4V2A10 10 0 0 0 2 12h2a8 8 0 0 1 8-8z"/></svg>`;
  const SVG_CLOSE = `<svg viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>`;
  const SVG_EXTERNAL = `<svg viewBox="0 0 24 24"><path d="M19 19H5V5h7V3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z"/></svg>`;
  const SVG_SORT_UP_DOWN = `<svg width="9" height="11" viewBox="0 0 10 12"><path d="M3 0L0 4h2v8h2V4h2L3 0zm4 12l3-4H8V0H6v8H4l3 4z" fill="rgba(255,255,255,0.7)"/></svg>`;
  const SVG_SORT_DOWN = `<svg width="8" height="7" viewBox="0 0 10 8"><path d="M5 8L0 0h10L5 8z" fill="#ffffff"/></svg>`;
  const SVG_CHIP_BOX = `<svg width="12" height="12" viewBox="0 0 24 24"><path d="M4 4h16v4H4V4zm1 6h14v10H5V10z"/></svg>`;
  const SVG_CHIP_DRUM = `<svg width="12" height="12" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 3.79 2 6v12c0 2.21 4.48 4 10 4s10-1.79 10-4V6c0-2.21-4.48-4-10-4zm0 2c4.41 0 8 1.34 8 2s-3.59 2-8 2-8-1.34-8-2 3.59-2 8-2z"/></svg>`;
  const SVG_TRASH = `<svg viewBox="0 0 24 24" width="13" height="13"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>`;

  // State for native Google Maps marker coloring & MIS
  let currentMisMatches = [];
  let currentRouteWaypoints = [];
  let isMisAuthChecked = false;
  let isMisLoggedIn = false;

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function normalizeMapPlaceName(value) {
    return String(value || "")
      .normalize("NFKC")
      .toLocaleLowerCase()
      .replace(/[’'`]/g, "")
      .replace(/[^\p{L}\p{N}]+/gu, " ")
      .trim();
  }

  // ==========================================
  // Map Pin Engine — rAF-driven, camera-interpolated, zero snap-back
  //
  // Pins are a pure function of the (interpolated) map camera each frame.
  // The camera arrives live from the main-world camera bridge
  // (history.replaceState hook) with a location.href poll as backup.
  // While the user drags, pointer deltas are layered on top and rebased
  // onto every fresh camera so pins never visibly jump ("找补").
  // ==========================================

  const pinMath = window.__greenoil_pinMath || null;
  const SVG_MIS_SHIELD_SM = `<svg viewBox="0 0 24 24" width="12" height="12"><path fill="#4f46e5" d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm-2 16l-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8z"/></svg>`;
  const SMOOTH_PAN_MAX_KM = 1.5;

  let camA = null;            // previous camera {lat,lng,zoom,t}
  let camB = null;            // latest camera {lat,lng,zoom,t}
  let pinLoopRunning = false;
  let pinDragging = false;
  let pinDrag = null;         // transient {dx,dy} while dragging
  let pinLastPX = 0;
  let pinLastPY = 0;
  let pinItems = [];          // {el, lat, lng}

  function nowMs() {
    return (typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now();
  }

  function mapCanvasRect() {
    const canvas = document.querySelector("canvas.H1VXrf");
    if (canvas && canvas.getBoundingClientRect) return canvas.getBoundingClientRect();
    return { left: 0, top: 0, width: (window.innerWidth || 0), height: (window.innerHeight || 0) };
  }

  // Visible map area: canvas minus Google's left panel (div[role="main"]).
  // The URL camera is centered on the visible map, so pins must be
  // projected against this rect, not the full canvas.
  function visibleMapRect() {
    if (!pinMath) return mapCanvasRect();
    let panelRect = null;
    try {
      const panel = document.querySelector('div[role="main"]');
      if (panel && panel.getBoundingClientRect) panelRect = panel.getBoundingClientRect();
    } catch (_) {}
    return pinMath.visibleMapRect(mapCanvasRect(), panelRect);
  }

  function ensurePinLayer() {
    let overlay = document.getElementById("greenoil-waypoint-pins-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "greenoil-waypoint-pins-overlay";
      document.body.appendChild(overlay);
    }
    let layer = document.getElementById("greenoil-waypoint-pin-layer");
    if (!layer) {
      layer = document.createElement("div");
      layer.id = "greenoil-waypoint-pin-layer";
      overlay.appendChild(layer);
    }
    return layer;
  }

  function onCameraUpdate(cam) {
    if (!pinMath || !cam) return;
    if (![cam.lat, cam.lng, cam.zoom].every(Number.isFinite)) return;
    const t = nowMs();
    if (camB && pinMath.sameCamera(camB, cam)) return;
    // Rebase the transient drag delta so pins do not jump when the
    // (lagging) URL camera catches up with the map.
    if (pinDrag && camB) {
      try { pinMath.rebaseDragDelta(pinDrag, camB, cam, visibleMapRect()); } catch (_) {}
    }
    camA = camB;
    camB = { lat: cam.lat, lng: cam.lng, zoom: cam.zoom, t };
    if (!camA) camA = { lat: camB.lat, lng: camB.lng, zoom: camB.zoom, t: t - 16 };
    wakePinLoop();
  }

  function pollMapCamera() {
    if (!pinMath) return;
    const cam = pinMath.parseCameraFromUrl(location.href);
    if (cam) onCameraUpdate(cam);
  }

  function cameraNow() {
    if (!pinMath || !camB) return null;
    return pinMath.interpolateCamera(camA, camB, nowMs());
  }

  function wakePinLoop() {
    if (pinLoopRunning || !isAlive()) return;
    pinLoopRunning = true;
    requestAnimationFrame(pinTick);
  }

  function pinTick() {
    if (!isAlive()) { pinLoopRunning = false; return; }
    const cam = cameraNow();
    const layer = document.getElementById("greenoil-waypoint-pin-layer");
    const show = !!(cam && layer && cam.zoom >= 10 && pinItems.length > 0 && pinMath);
    if (layer) {
      if (!show) {
        if (layer.style.display !== "none") layer.style.display = "none";
      } else {
        if (layer.style.display === "none") layer.style.display = "";
        const rect = visibleMapRect();
        const dx = pinDrag ? pinDrag.dx : 0;
        const dy = pinDrag ? pinDrag.dy : 0;
        for (let i = 0; i < pinItems.length; i++) {
          const p = pinItems[i];
          const pt = pinMath.projectToViewport(p.lat, p.lng, cam, rect);
          // Clip pins whose anchor is outside the visible map (e.g. panned
          // away or behind the side panel) instead of letting them float
          // over Google's UI.
          if (!pinMath.pointInRect(pt.x, pt.y, rect, 48)) {
            if (p.el.style.display !== "none") p.el.style.display = "none";
            continue;
          }
          if (p.el.style.display === "none") p.el.style.display = "";
          p.el.style.transform =
            `translate3d(${(pt.x + dx).toFixed(1)}px, ${(pt.y + dy).toFixed(1)}px, 0) translate(-50%, -100%)`;
        }
      }
    }
    const active = pinDragging || (camB && nowMs() - camB.t < 400);
    if (active && isAlive()) {
      requestAnimationFrame(pinTick);
    } else {
      pinLoopRunning = false;
    }
  }

  function rebuildPinElements() {
    const layer = ensurePinLayer();
    layer.innerHTML = "";
    pinItems = [];
    const themeColor = currentTheme?.color || "#059669";

    currentRouteWaypoints.forEach((wp, idx) => {
      const lat = parseFloat(wp.latitude);
      const lng = parseFloat(wp.longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
      const pin = document.createElement("div");
      pin.className = "greenoil-waypoint-map-pin";
      pin.dataset.kind = "waypoint";
      pin.innerHTML = `
        <div class="greenoil-pin-body">
          <svg viewBox="0 0 30 38" class="greenoil-pin-svg" aria-hidden="true">
            <path d="M15 0C6.716 0 0 6.716 0 15c0 10.5 15 23 15 23s15-12.5 15-23c0-8.284-6.716-15-15-15z" fill="${themeColor}"/>
            <circle cx="15" cy="14" r="9" fill="#ffffff"/>
          </svg>
          <span class="greenoil-pin-num" style="color:${themeColor}">${idx + 1}</span>
        </div>`;
      layer.appendChild(pin);
      pinItems.push({ el: pin, lat, lng });
    });

    currentMisMatches.forEach((m) => {
      const lat = parseFloat(m.latitude);
      const lng = parseFloat(m.longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
      const pin = document.createElement("div");
      pin.className = "greenoil-waypoint-map-pin greenoil-mis-map-pin";
      pin.dataset.kind = "mis";
      pin.innerHTML = `
        <div class="greenoil-pin-body">
          <svg viewBox="0 0 30 38" class="greenoil-pin-svg" aria-hidden="true">
            <path d="M15 0C6.716 0 0 6.716 0 15c0 10.5 15 23 15 23s15-12.5 15-23c0-8.284-6.716-15-15-15z" fill="#4f46e5"/>
            <circle cx="15" cy="14" r="9" fill="#ffffff"/>
          </svg>
          <span class="greenoil-pin-shield">${SVG_MIS_SHIELD_SM}</span>
        </div>`;
      layer.appendChild(pin);
      pinItems.push({ el: pin, lat, lng });
    });

    wakePinLoop();
  }

  // ---- Drag augmentation: mirror the pointer 1:1 while dragging ----
  function isMapSurface(t) {
    if (!(t instanceof Element)) return false;
    if (t.closest('[id^="greenoil-"]')) return false; // our own UI
    if (t.closest('div[role="main"]')) return false; // Google left panel
    return true;
  }

  listen(window, "pointerdown", (e) => {
    if (!e.isTrusted || e.button !== 0 || pinDragging) return;
    if (!isMapSurface(e.target)) return;
    pinDragging = true;
    if (!pinDrag) pinDrag = { dx: 0, dy: 0 };
    pinLastPX = e.clientX;
    pinLastPY = e.clientY;
    wakePinLoop();
  }, { capture: true });

  listen(window, "pointermove", (e) => {
    if (!e.isTrusted || !pinDragging || !pinDrag) return;
    pinDrag.dx += e.clientX - pinLastPX;
    pinDrag.dy += e.clientY - pinLastPY;
    pinLastPX = e.clientX;
    pinLastPY = e.clientY;
  }, { capture: true, passive: true });

  const endPinDrag = () => {
    if (!pinDragging) return;
    pinDragging = false;
    // Settle: rebase once against the freshest URL camera, then drop the
    // transient delta. Residual is ~0 thanks to per-update rebasing.
    setTimeout(() => {
      if (pinDragging || !isAlive()) return;
      pollMapCamera();
      pinDrag = null;
      wakePinLoop();
    }, 600);
  };
  listen(window, "pointerup", endPinDrag, { capture: true });
  listen(window, "pointercancel", endPinDrag, { capture: true });

  // ---- Camera feed wiring ----
  listen(window, "message", (event) => {
    if (event.data?.type === "GREENOIL_CAM" && event.data.cam) {
      const c = event.data.cam;
      if (c && Number.isFinite(c.lat) && Number.isFinite(c.lng) && Number.isFinite(c.zoom)) {
        onCameraUpdate({ lat: c.lat, lng: c.lng, zoom: c.zoom });
      }
    }
  });

  function requestCameraBridge() {
    try {
      if (chrome.runtime?.id) {
        chrome.runtime.sendMessage({ action: "injectCameraBridge" }, () => {
          if (chrome.runtime.lastError) {
            console.warn("[GreenOil] camera bridge inject:", chrome.runtime.lastError.message);
          }
        });
      }
    } catch (_) {}
  }

  // ---- Smooth nearby pan: synthesize a real drag gesture ----
  function anchorNavigateTo(targetPath, wp) {
    let p = wp?.mapsUrl || targetPath;
    if (!p && wp?.latitude && wp?.longitude) {
      p = `/maps/place/${encodeURIComponent(wp.name || "")}/@${wp.latitude},${wp.longitude},17z`;
    }
    if (p) {
      try {
        const link = document.createElement("a");
        link.href = p;
        link.style.display = "none";
        document.body.appendChild(link);
        link.click();
        link.remove();
      } catch (err) {
        console.warn("[GreenOil] Navigation failed:", err);
      }
    }
  }

  function smoothDragPanTo(targetPath, tLat, tLng, cam) {
    const canvas = document.querySelector("canvas.H1VXrf");
    if (!canvas || typeof PointerEvent === "undefined" || !pinMath) return false;
    const rect = visibleMapRect();
    const startX = rect.left + rect.width / 2;
    const startY = rect.top + rect.height / 2;
    const pt = pinMath.projectToViewport(tLat, tLng, cam, rect);
    const dx = startX - (rect.left + pt.x);
    const dy = startY - (rect.top + pt.y);
    if (Math.hypot(dx, dy) < 4) return true; // already centered

    const mk = (type, x, y) => new PointerEvent(type, {
      bubbles: true, cancelable: true, clientX: x, clientY: y,
      button: 0, buttons: type === "pointerup" ? 0 : 1,
      pointerId: 7, pointerType: "mouse", isPrimary: true,
    });
    try {
      canvas.dispatchEvent(mk("pointerdown", startX, startY));
    } catch (err) {
      return false;
    }

    const steps = 28;
    const dur = 550;
    let i = 0;
    const before = { lat: cam.lat, lng: cam.lng };
    const wpRef = { targetPath };
    const tickMove = () => {
      if (!isAlive()) return;
      i++;
      const a = Math.min(1, i / steps);
      const e = a < 0.5 ? 2 * a * a : 1 - Math.pow(-2 * a + 2, 2) / 2; // easeInOutQuad
      try {
        canvas.dispatchEvent(mk("pointermove", startX + dx * e, startY + dy * e));
      } catch (_) {}
      if (a < 1) {
        setTimeout(tickMove, dur / steps);
      } else {
        try { canvas.dispatchEvent(mk("pointerup", startX + dx, startY + dy)); } catch (_) {}
        // Watchdog: if the camera never moved, the page ignored the
        // synthetic gesture — fall back to link navigation.
        setTimeout(() => {
          if (!isAlive()) return;
          const nowCam = pinMath.parseCameraFromUrl(location.href);
          const moved = nowCam && pinMath.haversineKm(before.lat, before.lng, nowCam.lat, nowCam.lng) > 0.02;
          if (!moved) {
            console.warn("[GreenOil] synthetic drag pan ineffective; falling back to link navigation");
            anchorNavigateTo(wpRef.targetPath, null);
          }
        }, 900);
      }
    };
    setTimeout(tickMove, dur / steps);
    return true;
  }

  function updatePinsControlBar() {
    let bar = document.getElementById("greenoil-pins-control-bar");
    if (currentMisMatches.length === 0) {
      if (bar) bar.remove();
      return;
    }

    if (!bar) {
      bar = document.createElement("div");
      bar.id = "greenoil-pins-control-bar";
      document.body.appendChild(bar);
    }

    bar.innerHTML = "";

    const misPill = document.createElement("div");
    misPill.className = "greenoil-control-pill mis-pill";
    misPill.innerHTML = `<span>MIS签约</span> <span>${currentMisMatches.length}</span>`;
    misPill.title = "点击查看首个匹配客户";
    misPill.addEventListener("click", () => {
      if (currentMisMatches.length > 0) openMisModal(currentMisMatches[0]);
    });
    bar.appendChild(misPill);

    const clearBtn = document.createElement("div");
    clearBtn.className = "greenoil-control-clear";
    clearBtn.title = "清空 MIS 签约客户记录";
    clearBtn.innerHTML = SVG_TRASH;
    clearBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      currentMisMatches = [];
      const badge = document.getElementById("greenoil-heading-mis-badge");
      if (badge) badge.remove();
      rebuildPinElements();
      updatePinsControlBar();
      showToast("已清空", "MIS 签约记录已清空");
    });
    bar.appendChild(clearBtn);
  }

  let waypointRefreshPending = false;

  function refreshWaypointPins() {
    try {
      if (!isAlive() || waypointRefreshPending) return;
      waypointRefreshPending = true;
      chrome.runtime.sendMessage({ action: "getRouteWaypoints" }, (resp) => {
        waypointRefreshPending = false;
        if (!isAlive()) return;
        if (chrome.runtime.lastError || !resp || !resp.success) return;
        currentRouteWaypoints = Array.isArray(resp.waypoints) ? resp.waypoints : [];
        if (resp.activeTheme) currentTheme = resp.activeTheme;

        rebuildPinElements();
        updatePinsControlBar();

        const mainPanel = document.querySelector('div[role="main"]');
        if (mainPanel) {
          const currentPlace = extractPlaceData();
          if (currentPlace) {
            updateHeadingMisBadge(mainPanel, currentPlace);
          }
        }
      });
    } catch (_) {}
  }

  function renderMisPins(matches) {
    currentMisMatches = Array.isArray(matches) ? matches : [];
    // Render MIS matches as shield pins in the shared pin layer
    // (do NOT wipe the overlay — waypoint pins live there too).
    rebuildPinElements();
    updatePinsControlBar();

    const mainPanel = document.querySelector('div[role="main"]');
    if (mainPanel) {
      const currentPlace = extractPlaceData();
      if (currentPlace) {
        updateHeadingMisBadge(mainPanel, currentPlace);
      }
    }
  }

  function inPagePanToLocation(targetPath, wp) {
    // Nearby target: glide the map with a synthesized drag gesture so the
    // place panel is NOT reloaded and the whole map does NOT refresh.
    // Far target (or no coordinates): fall back to link navigation.
    const tLat = parseFloat(wp?.latitude);
    const tLng = parseFloat(wp?.longitude);
    const cam = (typeof cameraNow === "function" && cameraNow()) ||
                (pinMath && pinMath.parseCameraFromUrl(location.href));
    let smoothDone = false;
    if (pinMath && Number.isFinite(tLat) && Number.isFinite(tLng) && cam &&
        pinMath.shouldSmoothPan(cam, tLat, tLng, SMOOTH_PAN_MAX_KM)) {
      smoothDone = smoothDragPanTo(targetPath, tLat, tLng, cam);
    }
    if (!smoothDone) {
      anchorNavigateTo(targetPath, wp);
    }

    lastProcessedKey = "";
    setTimeout(() => {
      if (typeof checkAndInject === "function") checkAndInject();
      refreshWaypointPins();
    }, 300);

    showToast("已定位", wp?.name || "目标地点");
  }

  function getHaversineDistKm(lat1, lon1, lat2, lon2) {
    const R = 6371;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function extractStreetPrefix(raw) {
    if (!raw || typeof raw !== "string") return "";
    let addr = raw.trim();
    let part = addr.split(",")[0].trim();
    part = part.replace(/^(?:unit|ste|suite|#)\s*[\w\d-]+\s*[-–,]\s*/i, "").trim();
    part = part.replace(/\s+(?:unit|ste|suite|#|bldg|building)\s*[\w\d-]+$/i, "").trim();
    const m = part.match(/^(\d+[\w-]*\s+[A-Za-z0-9\s.]+)/);
    if (m) {
      return m[1].trim().split(/\s+/).slice(0, 5).join(" ");
    }
    return part;
  }

  function findMatchedMisCustomer(placeData) {
    if (!placeData || !Array.isArray(currentMisMatches) || currentMisMatches.length === 0) return null;

    const qName = (placeData.name || "").trim().toLowerCase();
    const qAddr = (placeData.address || "").trim().toLowerCase();
    const qPrefix = extractStreetPrefix(placeData.address).toLowerCase();
    const qLat = parseFloat(placeData.latitude);
    const qLng = parseFloat(placeData.longitude);

    for (const c of currentMisMatches) {
      const cName = (c.name || "").trim().toLowerCase();
      const cAddr = (c.address || "").trim().toLowerCase();
      const cPrefix = (c.matchedPrefix || "").toLowerCase();

      // 1. Name match
      if (qName && cName && (qName === cName || qName.includes(cName) || cName.includes(qName))) {
        return c;
      }

      // 2. Street prefix match
      if (qPrefix && (cAddr.includes(qPrefix) || (cPrefix && qPrefix.includes(cPrefix)))) {
        return c;
      }

      // 3. Proximity match (< 150m)
      const cLat = parseFloat(c.latitude);
      const cLng = parseFloat(c.longitude);
      if (!isNaN(qLat) && !isNaN(qLng) && !isNaN(cLat) && !isNaN(cLng)) {
        if (getHaversineDistKm(qLat, qLng, cLat, cLng) < 0.15) {
          return c;
        }
      }
    }
    return null;
  }

  function findWaypointIndexForPlace(waypoints, placeData) {
    if (!Array.isArray(waypoints) || !placeData) return -1;
    const pName = (placeData.name || "").trim().toLowerCase();
    const pNorm = normalizeMapPlaceName(placeData.name);

    // 1. Direct name match first (highest priority)
    if (pName && pName !== "selected location") {
      const idx = waypoints.findIndex((wp) => {
        const wName = (wp.name || "").trim().toLowerCase();
        const wNorm = normalizeMapPlaceName(wp.name);
        return wName === pName || (wNorm && pNorm && (wNorm === pNorm || wNorm.includes(pNorm) || pNorm.includes(wNorm)));
      });
      if (idx !== -1) return idx;
    }

    // 2. Place ID / CID match (only if names do not contradict)
    const pPid = placeData.placeId || placeData.id || "";
    if (pPid && !pPid.startsWith("custom_")) {
      const idx = waypoints.findIndex((wp) => {
        const wPid = wp.placeId || wp.id || "";
        if (wPid && wPid === pPid) {
          const wName = (wp.name || "").trim().toLowerCase();
          if (!wName || !pName || wName === pName || wName.includes(pName) || pName.includes(wName)) {
            return true;
          }
        }
        return false;
      });
      if (idx !== -1) return idx;
    }

    // 3. Proximity match (< 50m)
    const pLat = parseFloat(placeData.latitude);
    const pLng = parseFloat(placeData.longitude);
    if (!isNaN(pLat) && !isNaN(pLng)) {
      const idx = waypoints.findIndex((wp) => {
        const wLat = parseFloat(wp.latitude);
        const wLng = parseFloat(wp.longitude);
        if (!isNaN(wLat) && !isNaN(wLng)) {
          return getHaversineDistKm(pLat, pLng, wLat, wLng) < 0.05;
        }
        return false;
      });
      if (idx !== -1) return idx;
    }

    return -1;
  }

  function removeHeadingWaypointBadge() {
    const existing = document.getElementById("greenoil-heading-waypoint-badge");
    if (existing) existing.remove();
  }

  function updateHeadingMisBadge(mainPanel, placeData) {
    if (!mainPanel) return;
    const h1 = mainPanel.querySelector('h1.DUwDvf') || mainPanel.querySelector('h1');
    if (!h1) return;

    const existing = document.getElementById("greenoil-heading-mis-badge");
    const matched = findMatchedMisCustomer(placeData);

    if (!matched) {
      if (existing) existing.remove();
      return;
    }

    if (existing) {
      if (existing.dataset.code === matched.code) return;
      existing.remove();
    }

    const badge = document.createElement("span");
    badge.id = "greenoil-heading-mis-badge";
    badge.className = "greenoil-heading-mis-badge";
    badge.dataset.code = matched.code || "";
    badge.title = `【MIS签约客户】${matched.name} (${matched.code}) - 点击查看详细信息`;

    badge.innerHTML = `
      <svg viewBox="0 0 24 24" class="greenoil-heading-mis-icon">
        <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm-2 16l-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8z"/>
      </svg>
      <span>MIS签约</span>
    `;

    badge.addEventListener("click", (e) => {
      e.stopPropagation();
      e.preventDefault();
      openMisModal(matched);
    });

    h1.appendChild(badge);
  }

  function renderContainerChips(c) {
    let html = "";
    const raw = (c.containerRaw || "").toLowerCase();
    const text = (c.container || "").toUpperCase();

    const isYellow = raw.includes("chip-oil") || raw.includes("b o") || text.includes("B O") || text.includes("400B") || text.includes("600B");
    const isGrey = raw.includes("chip-company") || raw.includes("bi-database") || text.includes("D");

    if (isYellow) {
      html += `<span class="chip-container-oil">${SVG_CHIP_BOX} <b>B</b> <b>O</b></span>`;
    }
    if (isGrey) {
      html += `<span class="chip-container-drum">${SVG_CHIP_DRUM} <b>D</b></span>`;
    }
    if (!html) {
      const chipText = text || "D";
      html += `<span class="chip-container-drum">${SVG_CHIP_DRUM} <b>${chipText}</b></span>`;
    }
    return html;
  }

  function openMisModal(c) {
    closeMisModal();

    const modal = document.createElement("div");
    modal.id = "greenoil-mis-modal";

    modal.innerHTML = `
      <div class="greenoil-mis-card">
        <div class="greenoil-mis-header">
          <div class="greenoil-mis-title-row">
            <div class="greenoil-mis-name-group">
              <span class="greenoil-mis-name">${c.name || "MIS 签约客户"}</span>
              <span class="greenoil-mis-code-badge">${c.code || "客户"}</span>
              <span class="greenoil-mis-sts-badge">${c.sts === 'I' ? '已暂停 (Inactive)' : '签约有效 (Active)'}</span>
            </div>
            <button class="greenoil-mis-close-btn" type="button" aria-label="关闭">${SVG_CLOSE}</button>
          </div>
          <div class="greenoil-mis-addr-text">${c.address || ''}${c.city ? ', ' + c.city : ''}</div>
        </div>

        <div class="greenoil-mis-table-wrapper">
          <table class="greenoil-mis-table">
            <thead>
              <tr>
                <th>Payment <span class="th-icon">${SVG_SORT_UP_DOWN}</span></th>
                <th>Rate <span class="th-icon">${SVG_SORT_UP_DOWN}</span></th>
                <th>Container <span class="th-icon">${SVG_SORT_UP_DOWN}</span></th>
                <th>Driver <span class="th-icon">${SVG_SORT_UP_DOWN}</span></th>
                <th>Phone <span class="th-icon">${SVG_SORT_UP_DOWN}</span></th>
                <th>Sts <span class="th-icon">${SVG_SORT_UP_DOWN}</span></th>
                <th>Contract <span class="th-icon">${SVG_SORT_DOWN}</span></th>
                <th>Inactive <span class="th-icon">${SVG_SORT_UP_DOWN}</span></th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td class="td-payment">${c.payment || '-'}</td>
                <td class="td-rate">${c.rate || '-'}</td>
                <td class="td-container">${renderContainerChips(c)}</td>
                <td class="td-driver">${c.driver || '-'}</td>
                <td class="td-phone">${c.phone || '-'}</td>
                <td class="td-sts"><b>${c.sts || 'A'}</b></td>
                <td class="td-contract">${c.contract || '-'}</td>
                <td class="td-inactive">${c.inactive || ''}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="greenoil-mis-footer">
          <a class="greenoil-mis-btn-link" href="https://mis.greenoilinc.com/index_intranet.php?view=customer_list&key_word=${encodeURIComponent(c.code || c.name)}" target="_blank">
            ${SVG_EXTERNAL}
            <span>在 MIS 中查看</span>
          </a>
          <button class="greenoil-mis-btn-add" type="button" style="background-color: ${currentTheme.color || '#059669'};">
            ${SVG_PLUS}
            <span>+ 加入当前路线途径点</span>
          </button>
        </div>
      </div>
    `;

    modal.querySelector(".greenoil-mis-close-btn").addEventListener("click", closeMisModal);
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeMisModal();
    });

    const addBtn = modal.querySelector(".greenoil-mis-btn-add");
    addBtn.addEventListener("click", () => {
      const wp = {
        name: c.name,
        address: c.address + (c.city ? ", " + c.city : ""),
        latitude: c.latitude,
        longitude: c.longitude,
        phone: c.phone || "",
        notes: `MIS编号: ${c.code}, 费率: ${c.rate}, 司机: ${c.driver}`
      };

      if (chrome.runtime?.id) {
        chrome.runtime.sendMessage({ action: "addWaypoint", waypoint: wp }, (resp) => {
          if (resp && resp.success) {
            showToast("已加入路线", `${c.name} 已加入【${resp.theme?.name || currentTheme.name}】`);
            refreshWaypointPins();
            closeMisModal();
          } else {
            showToast("提示", resp?.error || `${c.name} 已在路线中`, false);
          }
        });
      }
    });

    document.body.appendChild(modal);
  }

  function closeMisModal() {
    const m = document.getElementById("greenoil-mis-modal");
    if (m) m.remove();
  }

  function checkAndUpdateMisAuth(container, btn, force = false) {
    try {
      if (!chrome.runtime?.id) {
        container.dataset.authDebug = "no_runtime_id";
        return;
      }
      chrome.runtime.sendMessage({ action: "checkMisAuth", force }, (auth) => {
        if (chrome.runtime.lastError) {
          container.dataset.authDebug = "lastError: " + chrome.runtime.lastError.message;
          return;
        }
        if (!auth) {
          container.dataset.authDebug = "no_auth_resp";
          return;
        }
        container.dataset.authDebug = JSON.stringify(auth);
        isMisAuthChecked = true;
        isMisLoggedIn = Boolean(auth.loggedIn);
        if (isMisLoggedIn) {
          container.classList.remove("greenoil-mis-disabled");
          container.title = "扫描周边20家餐馆并匹配 MIS 签约客户";
          btn.title = "扫描周边20家餐馆并匹配 MIS 签约客户";
          btn.setAttribute("aria-label", "匹配MIS");
        } else {
          container.classList.add("greenoil-mis-disabled");
          container.title = "请先登录 MIS 内部系统 (点击前往登录)";
          btn.title = "请先登录 MIS 内部系统 (点击前往登录)";
          btn.setAttribute("aria-label", "请先登录 MIS 内部系统 (点击前往登录)");
        }
      });
    } catch (e) {
      container.dataset.authDebug = "exception: " + e.message;
    }
  }

  async function handleMisBtnClick(container, circle, label) {
    if (container.classList.contains("greenoil-mis-disabled")) {
      const liveAuth = await new Promise((resolve) => {
        if (!chrome.runtime?.id) return resolve({ loggedIn: false });
        chrome.runtime.sendMessage({ action: "checkMisAuth", force: true }, (auth) => {
          resolve(auth || { loggedIn: false });
        });
      });

      if (liveAuth && liveAuth.loggedIn) {
        isMisLoggedIn = true;
        container.classList.remove("greenoil-mis-disabled");
        container.title = "扫描周边20家餐馆并匹配 MIS 签约客户";
        const b = container.querySelector("button");
        if (b) {
          b.title = "扫描周边20家餐馆并匹配 MIS 签约客户";
          b.setAttribute("aria-label", "匹配MIS");
        }
      } else {
        window.open("https://mis.greenoilinc.com/login_intranet.php", "_blank");
        showToast("请先登录 MIS", "正在前往 MIS 登录页面，登录后返回即可使用", false);
        return;
      }
    }

    const latestPlace = extractPlaceData();
    if (!latestPlace || isNaN(latestPlace.latitude) || isNaN(latestPlace.longitude)) {
      showToast("提示", "未能获取地点坐标，请在地点详情页稍候重试", false);
      return;
    }

    circle.innerHTML = SVG_SPINNER;
    container.classList.add("greenoil-loading");
    label.textContent = "匹配中...";
    showToast("透视扫描中", `正在静默扫描 ${latestPlace.name} 周边 20 家餐馆并匹配 MIS...`);

    try {
      if (!chrome.runtime?.id) {
        showToast("提示", "扩展已重新加载，请刷新网页", false);
        circle.innerHTML = SVG_MIS_SHIELD;
        container.classList.remove("greenoil-loading");
        label.textContent = "匹配MIS";
        return;
      }

      chrome.runtime.sendMessage({
        action: "scanAndMatchMis",
        lat: latestPlace.latitude,
        lng: latestPlace.longitude,
        placeName: latestPlace.name,
        placeAddress: latestPlace.address
      }, (resp) => {
        circle.innerHTML = SVG_MIS_SHIELD;
        container.classList.remove("greenoil-loading");
        label.textContent = "匹配MIS";

        if (chrome.runtime.lastError) {
          showToast("通信异常", "无法连接后台服务，请刷新网页", false);
          return;
        }

        if (resp && resp.notLoggedIn) {
          container.classList.add("greenoil-mis-disabled");
          showToast("登录态失效", "请先登录 MIS 内部系统后再进行匹配", false);
          return;
        }

        if (resp && resp.success) {
          const matches = resp.matches || [];
          showToast("MIS匹配完成", `已扫描周边 ${resp.totalScanned} 家餐馆，匹配到 ${matches.length} 家签约客户！`, true);
          renderMisPins(matches);
          if (matches.length > 0) {
            openMisModal(matches[0]);
          }
        } else {
          showToast("匹配失败", resp?.error || "扫描周边餐馆失败，请稍后重试", false);
        }
      });
    } catch (err) {
      console.error("Failed to scan and match MIS:", err);
      circle.innerHTML = SVG_MIS_SHIELD;
      container.classList.remove("greenoil-loading");
      label.textContent = "匹配MIS";
      showToast("通信异常", "扩展连接失败", false);
    }
  }

  listen(window, "resize", () => {
    // Canvas rect is re-read every frame; just wake the pin loop.
    if (typeof wakePinLoop === "function") wakePinLoop();
  });
  listen(window, "popstate", () => {
    if (typeof pollMapCamera === "function") pollMapCamera();
  });
  listen(window, "keydown", (e) => {
    if (e.key === "Escape") closeMisModal();
  });

  /**
   * Safely extract place name from h1 without being polluted by injected badges
   */
  function getPlaceNameFromH1(h1) {
    if (!h1) return "";
    let text = "";
    for (const child of h1.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        text += child.textContent;
      } else if (child.nodeType === Node.ELEMENT_NODE) {
        if (
          child.id === "greenoil-heading-mis-badge" ||
          child.classList?.contains("greenoil-heading-mis-badge") ||
          child.id === "greenoil-heading-waypoint-badge" ||
          child.classList?.contains("greenoil-heading-waypoint-badge")
        ) {
          continue;
        }
        text += child.textContent;
      }
    }
    return text.trim();
  }

  /**
   * Determine whether a place detail panel is currently open and ready
   */
  function isPlacePage() {
    const mainPanel = document.querySelector('div[role="main"]');
    if (!mainPanel) return false;
    const heading = mainPanel.querySelector('h1.DUwDvf') || mainPanel.querySelector('h1');
    let name = getPlaceNameFromH1(heading);
    if (!name) {
      const urlMatch = location.pathname.match(/\/place\/([^/@]+)/);
      if (urlMatch && urlMatch[1]) {
        name = decodeURIComponent(urlMatch[1].replace(/\+/g, " ")).trim();
      }
    }
    if (!name || name.length === 0) {
      return false;
    }
    const dirBtn = mainPanel.querySelector([
      '[data-item-id="directions"]',
      'button[data-value="Directions"]',
      'button[data-value="路线"]',
      'button[data-value="規劃路線"]',
      'button[aria-label*="Directions" i]',
      'button[aria-label*="路线"]',
      'button[aria-label*="路線"]',
      'button[aria-label*="规划"]',
      'button[aria-label*="規劃"]',
      'a[data-value="Directions"]',
      'a[data-item-id="directions"]'
    ].join(', '));
    if (!dirBtn || !dirBtn.offsetParent) return false;

    return true;
  }

  /**
   * Extract place details on-demand with strict place validation
   */
  function extractPlaceData() {
    const mainPanel = document.querySelector('div[role="main"]') || document.body;

    // 1. Name: Heading text is the single source of truth for the currently viewed place
    let name = "";
    const h1 = mainPanel.querySelector('h1.DUwDvf') || mainPanel.querySelector('h1');
    if (h1) {
      name = getPlaceNameFromH1(h1);
    }
    if (!name) {
      const urlMatch = location.pathname.match(/\/place\/([^/@]+)/);
      if (urlMatch && urlMatch[1]) {
        name = decodeURIComponent(urlMatch[1].replace(/\+/g, " ")).trim();
      }
    }
    if (!name || name === "Selected Location") return null;

    // 2. Latitude & Longitude from URL: Take the LAST !3d!4d match (the currently open place)
    let latitude = 43.76;
    let longitude = -79.41;

    const allCoordMatches = Array.from(location.href.matchAll(/!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)/g));
    if (allCoordMatches.length > 0) {
      const lastMatch = allCoordMatches[allCoordMatches.length - 1];
      latitude = parseFloat(lastMatch[1]);
      longitude = parseFloat(lastMatch[2]);
    } else {
      const centerCoordMatch = location.href.match(/@(-?\d+\.\d+),(-?\d+\.\d+)/);
      if (centerCoordMatch) {
        latitude = parseFloat(centerCoordMatch[1]);
        longitude = parseFloat(centerCoordMatch[2]);
      }
    }

    // 3. Address
    let address = "";
    const addrBtn = mainPanel.querySelector('button[data-item-id="address"]') ||
                    mainPanel.querySelector('[data-item-id="address"]');
    if (addrBtn) {
      const textDiv = addrBtn.querySelector('.fontBodyMedium') || addrBtn.querySelector('div.Io6YTe');
      if (textDiv && textDiv.textContent) {
        address = textDiv.textContent.trim();
      } else if (addrBtn.getAttribute("aria-label")) {
        address = addrBtn.getAttribute("aria-label").replace(/^(Address:\s*|地址：\s*|地址:\s*)/i, "").trim();
      }
    }
    if (!address) {
      const possibleAddress = mainPanel.querySelector('div.Io6YTe');
      if (possibleAddress && possibleAddress.textContent) {
        address = possibleAddress.textContent.trim();
      }
    }
    if (!address) {
      address = name;
    }

    // 4. Place ID / CID: ONLY trust URL's CID if URL matches current place name!
    let placeId = "";
    const pathMatch = location.pathname.match(/\/place\/([^/@]+)/);
    const urlPlaceName = pathMatch && pathMatch[1] ? decodeURIComponent(pathMatch[1].replace(/\+/g, " ")).trim().toLowerCase() : "";
    const nameLower = name.toLowerCase();

    const urlMatchesPlace = !urlPlaceName || urlPlaceName === nameLower || nameLower.includes(urlPlaceName) || urlPlaceName.includes(nameLower);

    if (urlMatchesPlace) {
      const allCidMatches = Array.from(location.href.matchAll(/!1s0x[0-9a-fA-F]+:0x([0-9a-fA-F]+)/g));
      if (allCidMatches.length > 0) {
        placeId = "cid_" + allCidMatches[allCidMatches.length - 1][1];
      } else {
        const allHexMatches = Array.from(location.href.matchAll(/0x[0-9a-fA-F]+:0x([0-9a-fA-F]+)/g));
        if (allHexMatches.length > 0) {
          placeId = "cid_" + allHexMatches[allHexMatches.length - 1][1];
        }
      }
    }

    if (!placeId) {
      placeId = "custom_" + Math.abs(hashCode(name + "|" + address));
    }

    // 5. Rating & Reviews
    let rating = 0;
    let reviews = 0;
    const ratingEl = mainPanel.querySelector('div.F7nice span[aria-hidden="true"]');
    if (ratingEl && ratingEl.textContent) {
      const r = parseFloat(ratingEl.textContent.trim());
      if (!isNaN(r)) rating = r;
    }
    const reviewsEl = mainPanel.querySelector('div.F7nice span:last-child');
    if (reviewsEl && reviewsEl.textContent) {
      const cleanReviews = reviewsEl.textContent.replace(/[(),]/g, "").trim();
      const rev = parseInt(cleanReviews, 10);
      if (!isNaN(rev)) reviews = rev;
    }

    // 6. Opening Hours
    let openingHours = "N/A";
    const ohTable = mainPanel.querySelector("table.eK4R0e, table.WgFkxc, [data-item-id*='oh'] table, div[role='main'] table");
    if (ohTable) {
      const rows = Array.from(ohTable.querySelectorAll("tr")).map(tr => {
        const dayTd = tr.querySelector("td.ylH6lf") || tr.querySelector("td:first-child");
        const dayText = dayTd ? (dayTd.querySelector("div")?.textContent.trim() || dayTd.textContent.trim()) : "";
        const valTd = tr.querySelector("td.mxowUb") || tr.querySelector("td:nth-child(2)");
        const lis = valTd ? Array.from(valTd.querySelectorAll("li")).map(li => li.textContent.trim()) : [];
        const valText = lis.length > 0 ? lis.join(", ") : (valTd ? valTd.textContent.trim() : "");
        if (dayText) return `${dayText}: ${valText}`;
        return tr.textContent.trim();
      }).filter(Boolean);
      if (rows.length > 0) {
        openingHours = rows.join("\n");
      }
    }

    if (!openingHours || openingHours === "N/A") {
      const ohBtn = mainPanel.querySelector("[data-item-id*='oh'], [aria-label*='營業時間'], [aria-label*='营业时间'], [aria-label*='Hours' i], [aria-label*='hours' i]");
      if (ohBtn) {
        const aria = ohBtn.getAttribute("aria-label") || "";
        const text = ohBtn.innerText || ohBtn.textContent || "";
        if (aria.includes(";") || (aria.includes(":") && (aria.includes("星期") || aria.includes("周") || aria.includes("Monday")))) {
          openingHours = aria
            .replace(/^(?:營業時間|营业时间|Hours)[:：]?\s*/i, "")
            .replace(/;\s*/g, "\n")
            .replace(/ 到 /g, "–")
            .replace(/ to /gi, "–");
        } else {
          openingHours = aria.replace(/·.*$/, "").replace(/查看更詳細.*$/, "").trim() ||
                         text.replace(/查看更詳細.*$/, "").replace(/\n/g, " ").trim();
        }
      }
    }

    return {
      id: placeId,
      placeId,
      name,
      address,
      openingHours,
      latitude,
      longitude,
      hasPlaceCoordinates: allCoordMatches.length > 0,
      rating,
      reviews,
      mapsUrl: location.href,
      addedAt: new Date().toISOString()
    };
  }

  function hashCode(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      hash = ((hash << 5) - hash) + str.charCodeAt(i);
      hash |= 0;
    }
    return hash;
  }

  function showToast(title, description, isSuccess = true) {
    let container = document.getElementById("greenoil-toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "greenoil-toast-container";
      container.className = "greenoil-toast-container";
      document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    toast.className = "greenoil-toast";

    const iconSpan = document.createElement("span");
    iconSpan.className = "greenoil-toast-icon";
    iconSpan.innerHTML = isSuccess ? SVG_CHECK : SVG_INFO;

    const bodyDiv = document.createElement("div");
    bodyDiv.className = "greenoil-toast-body";

    const titleEl = document.createElement("div");
    titleEl.className = "greenoil-toast-title";
    titleEl.textContent = title;

    const descEl = document.createElement("div");
    descEl.className = "greenoil-toast-desc";
    descEl.textContent = description;

    bodyDiv.appendChild(titleEl);
    bodyDiv.appendChild(descEl);
    toast.appendChild(iconSpan);
    toast.appendChild(bodyDiv);

    container.appendChild(toast);

    setTimeout(() => {
      toast.classList.add("greenoil-toast-exit");
      setTimeout(() => {
        if (toast.parentElement) toast.parentElement.removeChild(toast);
      }, 250);
    }, 3200);
  }

  /**
   * Check place status and inject [+ 途径点] button into the open place panel
   */
  function checkAndInject() {
    if (!isAlive()) return;
    try {
      // 1. Must be a ready place view
      if (!isPlacePage()) {
        if (lastProcessedKey) {
          lastProcessedKey = "";
          const oldBtn = document.getElementById("greenoil-add-waypoint-btn");
          if (oldBtn) oldBtn.remove();
          const oldMisBtn = document.getElementById("greenoil-match-mis-btn");
          if (oldMisBtn) oldMisBtn.remove();
          const oldBadge = document.getElementById("greenoil-heading-mis-badge");
          if (oldBadge) oldBadge.remove();
          const oldWpBadge = document.getElementById("greenoil-heading-waypoint-badge");
          if (oldWpBadge) oldWpBadge.remove();
        }
        return;
      }

      const mainPanel = document.querySelector('div[role="main"]');
      if (!mainPanel) {
        const oldBtn = document.getElementById("greenoil-add-waypoint-btn");
        if (oldBtn) oldBtn.remove();
        const oldMisBtn = document.getElementById("greenoil-match-mis-btn");
        if (oldMisBtn) oldMisBtn.remove();
        const oldBadge = document.getElementById("greenoil-heading-mis-badge");
        if (oldBadge) oldBadge.remove();
        const oldWpBadge = document.getElementById("greenoil-heading-waypoint-badge");
        if (oldWpBadge) oldWpBadge.remove();
        lastProcessedKey = "";
        return;
      }

      // 2. Extract heading place name
      const h1 = mainPanel.querySelector('h1.DUwDvf') || mainPanel.querySelector('h1');
      let placeName = getPlaceNameFromH1(h1);
      if (!placeName) {
        const urlMatch = location.pathname.match(/\/place\/([^/@]+)/);
        if (urlMatch && urlMatch[1]) {
          placeName = decodeURIComponent(urlMatch[1].replace(/\+/g, " ")).trim();
        }
      }
      if (!placeName) {
        const oldHeadingBadge = document.getElementById("greenoil-heading-mis-badge");
        if (oldHeadingBadge) oldHeadingBadge.remove();
        const oldWpBadge = document.getElementById("greenoil-heading-waypoint-badge");
        if (oldWpBadge) oldWpBadge.remove();
        return;
      }

      // 3. MUST find Directions button in place main actions row
      const dirBtn = mainPanel.querySelector([
        'button[data-value="Directions"]',
        'button[data-value="路线"]',
        'button[data-value="路線"]',
        'button[data-value="規劃路線"]',
        'button[aria-label*="Directions" i]',
        'button[aria-label*="路线"]',
        'button[aria-label*="路線"]',
        'button[aria-label*="规划"]',
        'button[aria-label*="規劃"]',
        'a[data-value="Directions"]',
        'a[data-item-id="directions"]',
        '[data-item-id="directions"]'
      ].join(', '));

      if (!dirBtn || !dirBtn.offsetParent) {
        const oldBtn = document.getElementById("greenoil-add-waypoint-btn");
        if (oldBtn) oldBtn.remove();
        const oldMisBtn = document.getElementById("greenoil-match-mis-btn");
        if (oldMisBtn) oldMisBtn.remove();
        lastProcessedKey = "";
        return;
      }

      const dirItem = dirBtn.closest('.etWJQ') || dirBtn.parentElement;
      const targetRow = dirItem ? dirItem.parentElement : null;

      if (!targetRow || !targetRow.classList.contains('m6QErb')) {
        const oldBtn = document.getElementById("greenoil-add-waypoint-btn");
        if (oldBtn) oldBtn.remove();
        const oldMisBtn = document.getElementById("greenoil-match-mis-btn");
        if (oldMisBtn) oldMisBtn.remove();
        lastProcessedKey = "";
        return;
      }

      const currentKey = placeName;

      // Ensure MIS badge is up to date and clean up any heading waypoint badge
      const currentPlaceObj = extractPlaceData();
      if (currentPlaceObj) {
        removeHeadingWaypointBadge();
        updateHeadingMisBadge(mainPanel, currentPlaceObj);
      }

      // Already injected and in place for THIS place
      const existingBtn = document.getElementById("greenoil-add-waypoint-btn");
      const existingMisBtn = document.getElementById("greenoil-match-mis-btn");
      if (existingBtn && targetRow.contains(existingBtn) && existingMisBtn && targetRow.contains(existingMisBtn) && currentKey === lastProcessedKey) {
        return;
      }

      if (existingBtn) existingBtn.remove();
      if (existingMisBtn) existingMisBtn.remove();
      lastProcessedKey = currentKey;

      // Create container matching Google's .etWJQ.jym1ob.kdfrQc.WY7ZIb
      const container = document.createElement("div");
      container.id = "greenoil-add-waypoint-btn";
      container.className = "etWJQ jym1ob kdfrQc WY7ZIb greenoil-action-container";

      // Initially styled with current active route theme
      applyThemeToContainer(container, currentTheme);

      const btn = document.createElement("button");
      btn.className = "S9kvJb greenoil-action-btn";
      btn.type = "button";
      btn.setAttribute("aria-label", "+ 途径点");
      btn.title = `加入当前地点到【${currentTheme?.name || '当前路线'}】`;

      const circle = document.createElement("span");
      circle.className = "DVeyrd greenoil-action-circle";
      circle.innerHTML = SVG_PLUS;

      const label = document.createElement("div");
      label.className = "R8c4Qb fontLabelMedium greenoil-action-label";
      label.textContent = "+ 途径点";

      btn.appendChild(circle);
      btn.appendChild(label);
      container.appendChild(btn);

      // Check if place is already in ANY of the 5 color routes
      try {
        if (chrome.runtime?.id) {
          const preliminaryData = extractPlaceData();
          if (preliminaryData) {
            chrome.runtime.sendMessage({
              action: "checkPlaceStatus",
              placeId: preliminaryData.placeId,
              name: preliminaryData.name,
              nameEn: preliminaryData.nameEn,
              address: preliminaryData.address,
              latitude: preliminaryData.latitude,
              longitude: preliminaryData.longitude
            }, (resp) => {
              if (chrome.runtime.lastError) return;
              if (resp && resp.inRoute) {
                // Requirement 2: Show the route it belongs to!
                circle.innerHTML = SVG_CHECK;
                if (Array.isArray(resp.waypoints) && resp.waypoints.length > 0) {
                  currentRouteWaypoints = resp.waypoints;
                }
                const stopNum = resp.stopNumber || ((findWaypointIndexForPlace(currentRouteWaypoints, preliminaryData) + 1) || 1);
                label.textContent = stopNum > 0 ? `第 ${stopNum} 站` : "已添加";
                container.classList.add("greenoil-added");
                container.dataset.belongRouteName = resp.belongRouteName || "";
                container.dataset.belongRouteId = resp.belongRouteId || "";
                container.title = `该地点已在【${resp.belongRouteName}】中`;
                applyThemeToContainer(container, resp.belongTheme);
                removeHeadingWaypointBadge();
              } else {
                // Not added: show active route theme!
                circle.innerHTML = SVG_PLUS;
                label.textContent = "+ 途径点";
                container.classList.remove("greenoil-added");
                delete container.dataset.belongRouteName;
                delete container.dataset.belongRouteId;
                const activeTh = resp?.activeTheme || currentTheme;
                container.title = `加入当前地点到【${activeTh?.name || '当前路线'}】`;
                applyThemeToContainer(container, activeTh);
                const oldWpBadge = document.getElementById("greenoil-heading-waypoint-badge");
                if (oldWpBadge) oldWpBadge.remove();
              }
            });
          }
        }
      } catch (_) {}

      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        e.preventDefault();

        if (container.classList.contains("greenoil-added")) {
          const belongName = container.dataset.belongRouteName || "路线";
          showToast("提示", `该地点已存在于【${belongName}】中`, true);
          return;
        }

        circle.style.opacity = "0.7";
        label.textContent = "添加中...";
        const latestPlace = extractPlaceData();
        if (!latestPlace) {
          circle.style.opacity = "1";
          label.textContent = "+ 途径点";
          showToast("提示", "未能获取有效的地点信息", false);
          return;
        }

        try {
          if (!chrome.runtime?.id) {
            showToast("提示", "扩展已重新加载，请刷新网页", false);
            circle.style.opacity = "1";
            label.textContent = "+ 途径点";
            return;
          }

          chrome.runtime.sendMessage({
            action: "addWaypoint",
            waypoint: latestPlace
          }, (response) => {
            circle.style.opacity = "1";
            if (chrome.runtime.lastError) {
              showToast("通信异常", "无法连接后台服务，请刷新网页", false);
              label.textContent = "+ 途径点";
              return;
            }

            if (response && response.success) {
              circle.innerHTML = SVG_CHECK;
              label.textContent = "已添加";
              container.classList.add("greenoil-added");
              container.dataset.belongRouteName = response.theme?.name || "";
              container.title = `该地点已在【${response.theme?.name}】中`;
              applyThemeToContainer(container, response.theme);
              showToast("已加入路线", `${latestPlace.name} (已加入【${response.theme?.name}】，共 ${response.count} 站)`);
              refreshWaypointPins();
            } else if (response && response.alreadyExists) {
              circle.innerHTML = SVG_CHECK;
              label.textContent = "已添加";
              container.classList.add("greenoil-added");
              container.dataset.belongRouteName = response.theme?.name || "";
              applyThemeToContainer(container, response.theme);
              showToast("提示", `${latestPlace.name} 已存在于当前路线中`, false);
            } else if (response && response.alreadyExistsInOther) {
              circle.innerHTML = SVG_CHECK;
              label.textContent = "已添加";
              container.classList.add("greenoil-added");
              container.dataset.belongRouteName = response.belongRouteName || "";
              applyThemeToContainer(container, response.belongTheme);
              showToast("提示", `${latestPlace.name} 已存在于【${response.belongRouteName}】中`, false);
            } else {
              label.textContent = "+ 途径点";
              showToast("添加失败", response?.error || "请稍后重试", false);
            }
          });
        } catch (err) {
          console.error("Failed to add waypoint:", err);
          circle.style.opacity = "1";
          label.textContent = "+ 途径点";
          showToast("通信异常", "扩展连接失败", false);
        }
      });

      // POINT 1: Place [+ 途径点] as the FIRST button in the row (before dirItem)!
      targetRow.insertBefore(container, dirItem);

      // POINT 2: Place [匹配MIS] right after [+ 途径点] (before dirItem)
      let misContainer = document.getElementById("greenoil-match-mis-btn");
      if (misContainer) misContainer.remove();

      misContainer = document.createElement("div");
      misContainer.id = "greenoil-match-mis-btn";
      misContainer.className = "etWJQ jym1ob kdfrQc WY7ZIb greenoil-action-container";

      const misBtn = document.createElement("button");
      misBtn.className = "S9kvJb greenoil-action-btn";
      misBtn.type = "button";
      misBtn.setAttribute("aria-label", "匹配MIS");
      misBtn.title = "扫描周边20家餐馆并匹配 MIS 签约客户";

      const misCircle = document.createElement("span");
      misCircle.className = "DVeyrd greenoil-action-circle greenoil-mis-circle";
      misCircle.innerHTML = SVG_MIS_SHIELD;

      const misLabel = document.createElement("div");
      misLabel.className = "R8c4Qb fontLabelMedium greenoil-action-label greenoil-mis-label";
      misLabel.textContent = "匹配MIS";

      misBtn.appendChild(misCircle);
      misBtn.appendChild(misLabel);
      misContainer.appendChild(misBtn);

      checkAndUpdateMisAuth(misContainer, misBtn);

      misBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        e.preventDefault();
        handleMisBtnClick(misContainer, misCircle, misLabel);
      });

      targetRow.insertBefore(misContainer, dirItem);
    } catch (err) {
      console.warn("[GreenOil] Injection check caught error:", err);
    }
  }

    window.__greenoil_check__ = checkAndInject;

    // Periodic check to inject buttons and sync place state.
    // Also backs up the camera feed (the main-world bridge posts live updates).
    setInterval(() => {
      if (!document.hidden) {
        checkAndInject();
        if (typeof pollMapCamera === "function") pollMapCamera();
      }
    }, 600);

    // Ask the background to inject the main-world camera bridge (one-time per tab).
    if (typeof requestCameraBridge === "function") requestCameraBridge();

    // Initial check immediately on script evaluation
    checkAndInject();
    if (typeof refreshWaypointPins === "function") refreshWaypointPins();

    if (document.readyState !== "complete") {
      listen(window, "load", () => {
        checkAndInject();
        if (typeof refreshWaypointPins === "function") refreshWaypointPins();
      }, { once: true });
    }
  })();
}
