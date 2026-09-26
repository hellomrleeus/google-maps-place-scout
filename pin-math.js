/**
 * Green Oil — pin math helpers (pure functions, no DOM).
 *
 * Loaded before content.js in the isolated world AND required directly by
 * node tests. Keep this file free of browser globals.
 */

function parseCameraFromUrl(url) {
  const m = String(url || "").match(/@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(\d+(?:\.\d+)?)z/);
  if (!m) return null;
  const lat = parseFloat(m[1]);
  const lng = parseFloat(m[2]);
  const zoom = parseFloat(m[3]);
  if (!Number.isFinite(lat) || !Number.isFinite(lng) || !Number.isFinite(zoom)) return null;
  return { lat, lng, zoom };
}

function sameCamera(a, b) {
  return !!a && !!b && a.lat === b.lat && a.lng === b.lng && a.zoom === b.zoom;
}

function mercatorWorld(lat, lng, zoom) {
  const s = 256 * Math.pow(2, zoom);
  const x = (s * (lng + 180)) / 360;
  const siny = Math.sin((lat * Math.PI) / 180);
  const c = Math.max(-0.9999, Math.min(0.9999, siny));
  const y = s * (0.5 - Math.log((1 + c) / (1 - c)) / (4 * Math.PI));
  return { x, y };
}

/**
 * Project lat/lng to viewport CSS pixels, given the camera and the map
 * canvas rect (from getBoundingClientRect, viewport coordinates).
 */
function projectToViewport(lat, lng, cam, rect) {
  const p = mercatorWorld(lat, lng, cam.zoom);
  const c = mercatorWorld(cam.lat, cam.lng, cam.zoom);
  return {
    x: rect.left + rect.width / 2 + (p.x - c.x),
    y: rect.top + rect.height / 2 + (p.y - c.y),
  };
}

/**
 * Interpolate between two timestamped cameras with smoothstep easing.
 * camA / camB: {lat, lng, zoom, t}. Returns a plain {lat, lng, zoom}.
 */
function interpolateCamera(camA, camB, nowMs) {
  if (!camB) return null;
  if (!camA || nowMs >= camB.t) return { lat: camB.lat, lng: camB.lng, zoom: camB.zoom };
  const span = camB.t - camA.t;
  if (!(span > 0)) return { lat: camB.lat, lng: camB.lng, zoom: camB.zoom };
  let a = (nowMs - camA.t) / span;
  a = Math.max(0, Math.min(1, a));
  a = a * a * (3 - 2 * a); // smoothstep
  return {
    lat: camA.lat + (camB.lat - camA.lat) * a,
    lng: camA.lng + (camB.lng - camA.lng) * a,
    zoom: camA.zoom + (camB.zoom - camA.zoom) * a,
  };
}

/**
 * Rebase a transient drag delta when a fresher camera arrives, so pins do
 * not visually jump. Exact while zoom is unchanged (the drag case);
 * a close approximation otherwise.
 */
function rebaseDragDelta(drag, camOld, camNew, rect) {
  const a = projectToViewport(camNew.lat, camNew.lng, camOld, rect);
  const b = projectToViewport(camNew.lat, camNew.lng, camNew, rect);
  drag.dx += a.x - b.x;
  drag.dy += a.y - b.y;
  return drag;
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) *
      Math.sin(dLon / 2);
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function shouldSmoothPan(cam, lat, lng, maxKm) {
  if (!cam || !Number.isFinite(lat) || !Number.isFinite(lng)) return false;
  return haversineKm(cam.lat, cam.lng, lat, lng) < maxKm;
}

/**
 * Visible map rect: the canvas rect minus the area covered by Google's
 * left panel (div[role="main"]). The URL camera (@lat,lng) is the center of
 * the VISIBLE map, not the full canvas, so without this correction every
 * pin is shifted left by half the panel width when the panel is open.
 * canvasRect / panelRect are plain {left,top,width,height} (viewport px).
 */
function visibleMapRect(canvasRect, panelRect) {
  const r = {
    left: canvasRect.left,
    top: canvasRect.top,
    width: canvasRect.width,
    height: canvasRect.height,
  };
  if (
    panelRect &&
    panelRect.width > 50 &&
    panelRect.height > 50 &&
    panelRect.left <= r.left + 2
  ) {
    const overlap = Math.max(0, Math.min(panelRect.width, r.width));
    r.left += overlap;
    r.width -= overlap;
  }
  return r;
}

/** True when a projected point lies inside rect (with an optional margin). */
function pointInRect(x, y, rect, margin) {
  const m = margin || 0;
  return (
    x >= rect.left - m &&
    x <= rect.left + rect.width + m &&
    y >= rect.top - m &&
    y <= rect.top + rect.height + m
  );
}

const api = {
  parseCameraFromUrl,
  sameCamera,
  projectToViewport,
  interpolateCamera,
  rebaseDragDelta,
  haversineKm,
  shouldSmoothPan,
  visibleMapRect,
  pointInRect,
};

if (typeof module !== "undefined" && module.exports) {
  module.exports = api;
} else if (typeof window !== "undefined") {
  window.__greenoil_pinMath = api;
}
