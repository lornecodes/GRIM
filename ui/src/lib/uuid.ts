/**
 * crypto.randomUUID() polyfill.
 *
 * crypto.randomUUID() is only available in secure contexts (HTTPS or localhost).
 * When accessing GRIM over plain HTTP on a LAN (e.g. http://10.0.0.x:8080),
 * browsers throw "crypto.randomUUID is not a function".
 *
 * This falls back to crypto.getRandomValues() which works everywhere.
 */
export function uuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Fallback: crypto.getRandomValues works in all contexts
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 1
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
